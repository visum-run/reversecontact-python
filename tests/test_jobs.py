import json

import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok


def poll(status, **extra):
    return {"webhookId": "job_test", "status": status, "result": None, **extra}


async def test_job_handle_wait_and_pacing(h):
    def held(request):
        h.clock.now += 10
        return ok(poll("in_progress"), headers={"Retry-After": "2"})

    h.responses.extend(
        [
            ok({"webhookId": "job_test", "status": "created", "pollUrl": "/v2/webhooks/job_test"}),
            ok(poll("created"), headers={"Retry-After": "3"}),
            held,
            ok(
                poll(
                    "succeeded",
                    result={"firstName": "Jane"},
                    creditsConsumed=2,
                    deliveryStatus="webhook-url-timeout",
                )
            ),
        ]
    )
    job = await h.call(
        h.client.fetch.person_live,
        url="https://social.com/in/jane",
        webhook_url="https://receiver.example.test",
    )
    assert job.id == "job_test"
    assert job.data.webhook_id == "job_test"
    assert job.poll_url == "/v2/webhooks/job_test"
    observed = []
    result = await h.call(job.wait, on_poll=observed.append)
    assert result.data.status == "succeeded"
    assert result.data.result.first_name == "Jane"
    assert result.data.credits_consumed == 2
    assert result.data.delivery_status == "webhook-url-timeout"
    assert result.http_response.status_code == 200
    assert h.clock.sleeps == [3]
    assert len(observed) == 3
    assert observed[-1] is result
    assert all(value.quotas.credits_consumed == 0 for value in observed)
    assert all(value.metadata.request_id == "req_test" for value in observed)
    assert len([request for request in h.requests if request.method == "POST"]) == 1
    assert all(request.url.params["wait"] == "20" for request in h.requests[1:])
    assert json.loads(h.requests[0].content)["webhookUrl"] == "https://receiver.example.test"


@pytest.mark.parametrize("method", ["find_email", "find_phone"])
async def test_contact_job_typed_array(h, method):
    h.responses.extend(
        [
            ok({"webhookId": "job_test"}),
            ok(poll("succeeded", result=[{"value": "jane@example.com", "type": "professional"}])),
        ]
    )
    job = await h.call(getattr(h.client.contact, method), url="https://social.com/in/jane")
    snapshot = await h.call(job.retrieve, wait=10)
    assert snapshot.data.result[0].value == "jane@example.com"
    assert h.requests[-1].url.params["wait"] == "10"


@pytest.mark.parametrize("method", ["find_email", "find_phone"])
async def test_contact_job_accepts_a_person_id(h, method):
    h.responses.append(ok({"webhookId": "job_test"}))
    await h.call(getattr(h.client.contact, method), id="prs_01jbd5vewyenebxgaz869ffe5t")
    assert json.loads(h.requests[0].content) == {"id": "prs_01jbd5vewyenebxgaz869ffe5t"}


@pytest.mark.parametrize(
    "code,not_found",
    [
        ("person-not-found", True),
        ("company-not-found", True),
        ("data-not-found", True),
        ("email-not-found", True),
        ("phone-not-found", True),
        ("fetch-data-error", False),
        ("result-unavailable", False),
    ],
)
async def test_failed_jobs_never_recreated(h, code, not_found):
    h.responses.append(
        ok(poll("errored", errorCode=code, result={"partial": True}, creditsConsumed=2))
    )
    with pytest.raises(rc.JobFailedError) as caught:
        await h.call(h.client.jobs.wait, "job_test")
    error = caught.value
    assert error.job_id == "job_test"
    assert error.error_code == code
    assert error.not_found is not_found
    assert error.credits_consumed == 2
    assert error.result == {"partial": True}
    assert len(h.requests) == 1
    assert h.requests[0].method == "GET"


async def test_local_deadline_with_last_status(h):
    h.responses.append(ok(poll("in_progress")))
    with pytest.raises(rc.JobTimeoutError) as caught:
        await h.call(h.client.jobs.wait, "job_test", timeout=1)
    assert caught.value.job_id == "job_test"
    assert caught.value.last_status == "in_progress"
    assert h.clock.now == 1
    assert h.requests[0].url.params["wait"] == "1"
    assert h.clock.sleeps == [1]


@pytest.mark.parametrize("status,error", [(404, rc.NotFoundError), (410, rc.GoneError)])
async def test_poll_gone_or_not_found(h, status, error):
    h.responses.append(failure(status))
    with pytest.raises(error):
        await h.call(h.client.jobs.wait, "job_test")
    assert not h.clock.sleeps


async def test_poll_retries_reset_after_good_response(h):
    h.client._transport.max_retries = 0
    h.responses.extend([failure(503, headers={"Retry-After": "1"}) for _ in range(4)])
    h.responses.append(ok(poll("in_progress")))
    h.responses.extend([failure(502, headers={"Retry-After": "1"}) for _ in range(4)])
    h.responses.append(ok(poll("succeeded", result={})))
    value = await h.call(h.client.jobs.wait, "job_test")
    assert value.data.status == "succeeded"
    assert len(h.requests) == 10
    assert h.clock.sleeps == [1, 1, 1, 1, 2, 1, 1, 1, 1]


async def test_poll_five_consecutive_failures(h):
    h.responses.extend([failure(503) for _ in range(5)])
    with pytest.raises(rc.InternalServerError):
        await h.call(h.client.jobs.wait, "job_test")
    assert len(h.requests) == 5
    assert len(h.clock.sleeps) == 4


async def test_poll_deadline_during_retry_sleep(h):
    h.responses.append(failure(503, headers={"Retry-After": "4"}))
    with pytest.raises(rc.JobTimeoutError):
        await h.call(h.client.jobs.wait, "job_test", timeout=2)
    assert h.clock.now == 2
    assert len(h.requests) == 1


async def test_poll_timeout_floor(h):
    h.client._transport.timeout = httpx.Timeout(1, connect=2)
    h.responses.append(ok(poll("succeeded")))
    await h.call(h.client.jobs.wait, "job_test")
    assert all(value >= 35 for value in h.requests[0].extensions["timeout"].values())
    h.responses.append(ok(poll("succeeded")))
    await h.call(h.client.jobs.retrieve, "job_test", wait=20, timeout=0.5)
    assert h.requests[-1].extensions["timeout"]["read"] >= 35


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"webhookId": "", "data": {}},
        {"webhookId": "id", "data": 42},
        "invalid JSON",
        b"\xff",
    ],
)
def test_webhook_malformed(payload):
    with pytest.raises(rc.ReverseContactError):
        rc.parse_webhook_delivery(payload)


def test_webhook_delivery():
    body = {"webhookId": "job_test", "data": {"errorCode": "person-not-found"}}
    delivery = rc.parse_webhook_delivery(json.dumps(body))
    assert delivery.webhook_id == "job_test"
    assert delivery.is_error
    assert delivery.to_dict() == body
    assert not rc.parse_webhook_delivery(
        {"webhookId": "job_test", "data": [{"value": "a@example.com"}]}
    ).is_error


async def test_null_contact_items(h):
    h.responses.extend(
        [ok({"webhookId": "job_test"}), ok(poll("succeeded", result=[None, {"value": None}]))]
    )
    job = await h.call(h.client.contact.find_email, first_name="Jane")
    value = await h.call(job.wait)
    assert value.data.result[0] is None
    assert value.data.result[1].value is None
