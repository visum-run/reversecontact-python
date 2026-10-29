import json
from uuid import UUID

import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok
from test_revision2 import BrokenAsyncBody, BrokenSyncBody


@pytest.fixture(params=["find_email", "find_phone"])
def submit(h, request):
    return getattr(h.client.contact, request.param)


def acknowledgement(**kwargs):
    return ok({"webhookId": "job_test", "status": "created"}, **kwargs)


async def test_client_has_no_lists_resource(h):
    assert not hasattr(h.client, "lists")


async def test_generated_key_is_reused_per_call_and_fresh_for_next_call(h, submit):
    h.responses.extend(
        [failure(503), httpx.ReadTimeout("after send"), acknowledgement(), acknowledgement()]
    )
    await h.call(submit, first_name="Jane")
    await h.call(submit, first_name="Jane")
    keys = [request.headers["Idempotency-Key"] for request in h.requests]
    assert keys[0] == keys[1] == keys[2]
    assert keys[3] != keys[0]
    assert UUID(keys[0]).version == UUID(keys[3]).version == 4
    assert all(json.loads(request.content) == {"firstName": "Jane"} for request in h.requests)


@pytest.mark.parametrize("key", ["x", "customer-request:42", "x" * 255])
@pytest.mark.parametrize("source", ["option", "header", "default", "injected"])
async def test_caller_keys_are_respected_across_attempts(h, submit, source, key):
    kwargs = {}
    if source == "option":
        kwargs["idempotency_key"] = key
    elif source == "header":
        kwargs["headers"] = {"iDeMpOtEnCy-KeY": key}
    elif source == "default":
        client = h.client.with_options(default_headers={"Idempotency-Key": key})
        submit = getattr(client.contact, submit.__name__)
    else:
        h.http.headers["Idempotency-Key"] = key
    h.responses.extend([failure(503), acknowledgement()])
    await h.call(submit, **kwargs)
    assert [request.headers["Idempotency-Key"] for request in h.requests] == [key, key]
    assert all(json.loads(request.content) == {} for request in h.requests)


async def test_option_overrides_header_defaults_without_mutating_them(h, submit):
    h.http.headers["Idempotency-Key"] = "injected"
    client = h.client.with_options(default_headers={"Idempotency-Key": "default"})
    submit = getattr(client.contact, submit.__name__)
    headers = {"idempotency-key": "header"}
    h.responses.extend([acknowledgement() for _ in range(3)])
    await h.call(submit, idempotency_key="option", headers=headers)
    await h.call(submit, headers=headers)
    await h.call(submit)
    assert [request.headers["Idempotency-Key"] for request in h.requests] == [
        "option",
        "header",
        "default",
    ]
    assert headers == {"idempotency-key": "header"}
    assert h.http.headers["Idempotency-Key"] == "injected"


@pytest.mark.parametrize(
    "key",
    [
        "",
        " ",
        " leading",
        "trailing ",
        "inner space",
        "a\tb",
        "a\nb",
        "\x00",
        "é",
        "\x7f",
        "x" * 256,
        42,
        True,
    ],
)
@pytest.mark.parametrize("source", ["option", "header", "default"])
async def test_invalid_key_is_rejected_before_any_request(h, submit, key, source):
    with pytest.raises(rc.ReverseContactError, match="idempotency_key"):
        if source == "default":
            h.client.with_options(default_headers={"Idempotency-Key": key})
        else:
            kwargs = (
                {"idempotency_key": key}
                if source == "option"
                else {"headers": {"Idempotency-Key": key}}
            )
            await h.call(submit, **kwargs)
    assert h.requests == []
    assert h.clock.sleeps == []


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ReadTimeout,
        httpx.ReadError,
        httpx.WriteTimeout,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ],
)
async def test_contact_retries_after_send_but_other_post_does_not(h, submit, error_type):
    h.responses.extend([error_type("after send"), acknowledgement()])
    await h.call(submit)
    assert len(h.requests) == 2
    assert h.requests[0].headers["Idempotency-Key"] == h.requests[1].headers["Idempotency-Key"]
    assert 2 <= h.clock.sleeps[0] <= 2.25
    h.responses.append(error_type("after send"))
    with pytest.raises(rc.APIConnectionError):
        await h.call(h.client.enrich.person, headers={"Idempotency-Key": "unrelated"})
    assert len(h.requests) == 3
    assert len(h.clock.sleeps) == 1


async def test_unreadable_acknowledgement_body_retries_and_closes_stream(h, submit):
    stream = BrokenAsyncBody() if h.asynchronous else BrokenSyncBody()
    h.responses.extend([httpx.Response(200, stream=stream), acknowledgement()])
    await h.call(submit)
    assert stream.closed
    assert len(h.requests) == 2
    assert h.requests[0].headers["Idempotency-Key"] == h.requests[1].headers["Idempotency-Key"]


async def test_invalid_json_acknowledgement_is_retried(h, submit):
    h.responses.extend([httpx.Response(200, content=b'{"data":'), acknowledgement()])
    await h.call(submit)
    assert len(h.requests) == 2
    assert h.requests[0].headers["Idempotency-Key"] == h.requests[1].headers["Idempotency-Key"]


async def test_in_use_retries_after_header_then_returns_replayed_acknowledgement(h, submit):
    h.responses.extend(
        [
            failure(409, "IDEMPOTENCY_KEY_IN_USE", headers={"Retry-After": "1"}),
            acknowledgement(headers={"Idempotent-Replayed": "true"}),
        ]
    )
    job = await h.call(submit, idempotency_key="customer-key")
    assert job.replayed is True
    assert job.id == "job_test"
    assert job.http_response.headers["Idempotent-Replayed"] == "true"
    assert h.clock.sleeps == [1]
    assert [request.headers["Idempotency-Key"] for request in h.requests] == [
        "customer-key",
        "customer-key",
    ]


@pytest.mark.parametrize("header,expected", [(None, False), ("false", False), ("true", True)])
async def test_replay_flag(h, submit, header, expected):
    h.responses.append(acknowledgement(headers={"Idempotent-Replayed": header} if header else {}))
    assert (await h.call(submit)).replayed is expected


@pytest.mark.parametrize(
    "status,code,error_type",
    [
        (422, "IDEMPOTENCY_KEY_REUSED", rc.UnprocessableEntityError),
        (400, "INVALID_IDEMPOTENCY_KEY", rc.BadRequestError),
        (409, "OTHER_CONFLICT", rc.ConflictError),
    ],
)
async def test_permanent_errors_keep_their_code_without_retry(h, submit, status, code, error_type):
    h.responses.append(failure(status, code, headers={"Retry-After": "1"}))
    with pytest.raises(error_type) as caught:
        await h.call(submit)
    assert caught.value.code == code
    assert len(h.requests) == 1
    assert h.clock.sleeps == []


async def test_in_use_does_not_change_other_operations(h):
    h.responses.append(failure(409, "IDEMPOTENCY_KEY_IN_USE", headers={"Retry-After": "1"}))
    with pytest.raises(rc.ConflictError):
        await h.call(h.client.enrich.person)
    assert len(h.requests) == 1
    assert not h.clock.sleeps


async def test_conflict_retries_count_towards_max_retries(h, submit):
    h.responses.extend(
        [failure(409, "IDEMPOTENCY_KEY_IN_USE", headers={"Retry-After": "1"}) for _ in range(3)]
    )
    with pytest.raises(rc.ConflictError):
        await h.call(submit, max_retries=2)
    assert len(h.requests) == 3
    assert len({request.headers["Idempotency-Key"] for request in h.requests}) == 1
    assert h.clock.sleeps == [1, 1]


@pytest.mark.parametrize("kind", ["timeout", "body", "conflict"])
async def test_zero_retries_is_respected(h, submit, kind):
    response = {
        "timeout": httpx.ReadTimeout("after send"),
        "body": httpx.Response(200, content=b"invalid JSON"),
        "conflict": failure(409, "IDEMPOTENCY_KEY_IN_USE", headers={"Retry-After": "1"}),
    }[kind]
    h.responses.append(response)
    with pytest.raises(rc.ConflictError if kind == "conflict" else rc.APIConnectionError):
        await h.call(submit, max_retries=0)
    assert len(h.requests) == 1
    assert not h.clock.sleeps
