import asyncio
import json
import logging
import threading
import time

import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok
from reversecontact.errors import retry_after_header


class BrokenSyncBody(httpx.SyncByteStream):
    def __init__(self):
        self.closed = False

    def __iter__(self):
        yield b'{"data":'
        raise httpx.ReadError("The peer closed the body")

    def close(self):
        self.closed = True


class BrokenAsyncBody(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'{"data":'
        raise httpx.ReadError("The peer closed the body")

    async def aclose(self):
        self.closed = True


def method_for(h, method):
    if method == "POST":
        return h.client.enrich.person, (), {"email": "jane@example.com"}
    return h.client.usage.retrieve, (), {}


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [200, 401, 429, 500, 502, 503, 504])
async def test_broken_body_preserves_status_and_safe_retry_policy(h, method, status):
    stream = BrokenAsyncBody() if h.asynchronous else BrokenSyncBody()
    h.responses.extend([httpx.Response(status, stream=stream), ok({})])
    function, args, kwargs = method_for(h, method)
    retry = status in {429, 500, 502, 503, 504} or (status == 200 and method == "GET")
    if retry:
        await h.call(function, *args, **kwargs)
        assert len(h.requests) == 2
        assert len(h.clock.sleeps) == 1
    else:
        error_type = rc.AuthenticationError if status == 401 else rc.APIConnectionError
        with pytest.raises(error_type) as caught:
            await h.call(function, *args, **kwargs)
        assert caught.value.status == status
        if status == 401:
            assert caught.value.code == "HTTP_401"
        assert len(h.requests) == 1
        assert not h.clock.sleeps
    assert stream.closed


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_unreadable_error_body_without_retries(h, status):
    stream = BrokenAsyncBody() if h.asynchronous else BrokenSyncBody()
    h.responses.append(httpx.Response(status, stream=stream, headers={"Retry-After": "3"}))
    with pytest.raises(rc.APIError) as caught:
        await h.call(h.client.enrich.person, email="a@example.com", max_retries=0)
    assert caught.value.code == f"HTTP_{status}"
    assert caught.value.status == status
    assert caught.value.headers["retry-after"] == "3"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
    ],
)
async def test_before_headers_network_retry_policy(h, method, error_type):
    h.responses.extend([error_type("network failure"), ok({})])
    function, args, kwargs = method_for(h, method)
    if method == "GET" or error_type in {httpx.ConnectError, httpx.ConnectTimeout}:
        await h.call(function, *args, **kwargs)
        assert len(h.requests) == 2
    else:
        with pytest.raises(rc.APIConnectionError) as caught:
            await h.call(function, *args, **kwargs)
        assert caught.value.status is None
        assert len(h.requests) == 1
        assert not h.clock.sleeps


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
@pytest.mark.parametrize("hint", ["header", "details"])
@pytest.mark.parametrize("delay", [60, 61])
async def test_server_delay_ceiling(h, status, hint, delay):
    headers = {"Retry-After": str(delay)} if hint == "header" else {}
    field = "retryAfter" if status == 429 else "retryAfterSeconds"
    details = {field: delay} if hint == "details" else None
    h.responses.extend([failure(status, details=details, headers=headers), ok()])
    if delay > 60:
        with pytest.raises(rc.APIError):
            await h.call(h.client.usage.retrieve)
        assert len(h.requests) == 1
        assert not h.clock.sleeps
    else:
        await h.call(h.client.usage.retrieve)
        assert h.clock.sleeps == [60]


@pytest.mark.parametrize("entry", ["job", "jobs"])
async def test_overall_deadline_during_inflight_poll(h, entry):
    if entry == "job":
        h.responses.append(ok({"webhookId": "job_test"}))
        job = await h.call(h.client.fetch.person_live, url="https://social.com/in/jane")
        function, args = job.wait, ()
    else:
        function, args = h.client.jobs.wait, ("job_test",)
    h.client._transport.clock = time.monotonic
    caller_thread = threading.get_ident()
    finished = False

    def held(request):
        nonlocal finished
        assert threading.get_ident() == caller_thread
        time.sleep(request.extensions["timeout"]["read"] + 0.005)
        finished = True
        raise httpx.ReadTimeout("The bounded HTTP phase exceeded the deadline")

    async def aheld(request):
        nonlocal finished
        try:
            await asyncio.sleep(2)
            return ok({"status": "succeeded"})
        finally:
            finished = True

    h.handler = aheld if h.asynchronous else held
    started = time.monotonic()
    with pytest.raises(rc.JobTimeoutError) as caught:
        await h.call(function, *args, timeout=0.03)
    elapsed = time.monotonic() - started
    assert 0.015 <= elapsed < 0.3
    assert caught.value.job_id == "job_test"
    request = h.requests[-1]
    assert request.url.params["wait"] == "0"
    assert all(0 < value <= 0.03 for value in request.extensions["timeout"].values())
    assert finished
    assert not h.clock.sleeps


async def test_deadline_caps_http_components_and_preserves_last_status(h):
    h.client._transport.timeout = httpx.Timeout(None, read=4)
    h.responses.append(ok({"status": "running"}))

    def expired(request):
        h.clock.now = 3
        raise httpx.ReadTimeout("deadline")

    h.responses.append(expired)
    with pytest.raises(rc.JobTimeoutError) as caught:
        await h.call(h.client.jobs.wait, "job_test", timeout=3)
    assert caught.value.last_status == "running"
    assert isinstance(caught.value.__context__, rc.APITimeoutError)
    assert h.requests[0].extensions["timeout"] == dict.fromkeys(
        ["connect", "read", "write", "pool"], 3
    )
    assert h.requests[1].url.params["wait"] == "1"
    assert h.requests[1].extensions["timeout"]["read"] == 1


@pytest.mark.parametrize("budget", [None, -1, 1.5, "1", True])
async def test_search_budget_rejected_at_iterator_creation(h, budget):
    with pytest.raises(rc.ReverseContactError, match="max_results"):
        h.client.search.iter_people(first_name="Jane", max_results=budget)
    assert not h.requests


async def test_iterator_page_sizes(h):
    h.responses.append(ok({"data": [], "metadata": {"nextCursor": None}}))
    await h.collect(h.client.search.iter_people(max_results=2000, first_name="Jane"))
    request = h.requests[-1]
    payload = json.loads(request.content)
    assert payload["perPage"] == 25
    with pytest.raises(rc.ReverseContactError, match="per_page"):
        h.client.search.iter_people(max_results=2000, per_page=101)
    assert len(h.requests) == 1


async def test_page_and_iterator_deep_copy_filters(h):
    location = {"country": "France", "nestedFutureFilter": ["original"]}
    h.responses.extend(
        [
            ok({"data": [], "metadata": {"nextCursor": "next"}}),
            ok({"data": [], "metadata": {"nextCursor": None}}),
        ]
    )
    page = await h.call(h.client.search.people, location=location)
    location["country"] = "Germany"
    location["nestedFutureFilter"].append("mutated")
    await h.call(page.get_next_page)
    assert json.loads(h.requests[-1].content)["location"] == {
        "country": "France",
        "nestedFutureFilter": ["original"],
    }
    h.requests.clear()
    h.responses.extend(
        [
            ok({"data": [{"id": "first"}], "metadata": {"nextCursor": "next"}}),
            ok({"data": [{"id": "second"}], "metadata": {"nextCursor": None}}),
        ]
    )
    items = h.client.search.iter_people(location=location, max_results=2, per_page=1)
    location["country"] = "Canada"
    if h.asynchronous:
        await items.__anext__()
    else:
        next(items)
    location["nestedFutureFilter"].append("another mutation")
    await h.collect(items)
    bodies = [json.loads(request.content) for request in h.requests]
    assert (
        bodies[0]["location"]
        == bodies[1]["location"]
        == {"country": "Germany", "nestedFutureFilter": ["original", "mutated"]}
    )


async def test_page_extras_and_typed_known_counts(h):
    h.responses.append(
        ok(
            {
                "data": [],
                "metadata": {"count": 1, "perPage": 100, "nextCursor": None},
                "future": {"value": True},
            }
        )
    )
    page = await h.call(h.client.search.people, first_name="Jane")
    assert page.count == 1
    assert page.per_page == 100
    assert page.extras == {
        "future": {"value": True},
    }


async def test_reserved_origin_removed_after_all_header_merges(h):
    h.http.headers["x-request-origin"] = "injected"
    client = h.client.with_options(default_headers={"X-Request-Origin": "default", "X-Keep": "yes"})
    h.responses.extend([ok(), ok()])
    await h.call(client.usage.retrieve)
    await h.call(client.usage.retrieve, headers={"X-ReQuEsT-OrIgIn": "per-request"})
    assert all("x-request-origin" not in request.headers for request in h.requests)
    assert all(request.headers["X-Keep"] == "yes" for request in h.requests)
    assert h.http.headers["x-request-origin"] == "injected"


@pytest.mark.parametrize("code", [0, 1, False, True, {}, [], ["error"]])
def test_malformed_webhook_error_code(code):
    with pytest.raises(rc.ReverseContactError, match="errorCode"):
        rc.parse_webhook_delivery({"webhookId": "job", "data": {"errorCode": code}})


@pytest.mark.parametrize(
    "code,is_error", [(None, False), ("", False), ("error", True), (" ", True)]
)
def test_webhook_error_code_semantics(code, is_error):
    delivery = rc.parse_webhook_delivery({"webhookId": "job", "data": {"errorCode": code}})
    assert delivery.is_error is is_error


async def test_client_copy_overrides_without_mutating_or_closing_parent(h):
    parent = h.client
    clone = parent.with_options(
        api_key="clone-key",
        base_url="https://clone.test",
        timeout=7,
        max_retries=0,
        default_headers={"X-Clone": "yes"},
        app_info={"name": "clone-app", "version": "2"},
    )
    assert clone._transport.client is parent._transport.client
    assert not clone._transport.owned
    h.responses.extend([ok(), ok()])
    await h.call(clone.usage.retrieve)
    await h.call(clone.close)
    await h.call(parent.usage.retrieve)
    copied, original = h.requests
    assert copied.headers["Authorization"] == "Bearer clone-key"
    assert "clone-app/2" in copied.headers["User-Agent"]
    assert copied.headers["X-Clone"] == "yes"
    assert copied.extensions["timeout"]["read"] == 7
    assert copied.url.host == "clone.test"
    assert original.headers["Authorization"] == "Bearer rc_test"
    assert "X-Clone" not in original.headers
    assert original.extensions["timeout"]["read"] == 90
    assert not h.http.is_closed


async def test_debug_logging_is_opt_in_and_redacts_secrets(h, caplog, monkeypatch):
    logger = logging.getLogger("reversecontact")
    caplog.set_level(logging.DEBUG, logger="reversecontact")
    logger.setLevel(logging.WARNING)
    monkeypatch.delenv("RC_LOG", raising=False)
    h.responses.append(ok({}))
    await h.call(h.client.enrich.person, email="private@example.com")
    assert not caplog.records
    monkeypatch.setenv("RC_LOG", "debug")
    client = h.client.with_options(api_key="private-api-key")
    h.responses.extend([failure(503, headers={"Retry-After": "2"}), ok({})])
    await h.call(client.enrich.person, email="private@example.com")
    assert len(caplog.records) == 2
    text = caplog.text
    for expected in [
        "method=POST",
        "path=/v2/enrich/persons",
        "status=503",
        "attempt=1",
        "delay=2",
        "requestId=req_error",
        "attempt=2",
        "requestId=req_test",
    ]:
        assert expected in text
    for secret in ["private-api-key", "private@example.com", "Authorization", "rc_test"]:
        assert secret not in text


def test_retry_after_date_is_a_float():
    assert (
        retry_after_header(httpx.Headers({"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"})) == 0.0
    )
    assert isinstance(
        retry_after_header(httpx.Headers({"Retry-After": "Thu, 01 Jan 2099 00:00:00 GMT"})), float
    )


@pytest.mark.parametrize("organizations", [False, True])
async def test_prompt_interpretation_has_typed_filters(h, organizations):
    filters = {"companyName": "Acme"} if organizations else {"firstName": "Jane"}
    h.responses.append(
        ok(
            {
                "data": [],
                "metadata": {},
                "interpretation": {
                    "filters": filters,
                    "warnings": [None, "partial"],
                    "future": True,
                },
            }
        )
    )
    function = (
        h.client.search.organizations_by_prompt
        if organizations
        else h.client.search.people_by_prompt
    )
    page = await h.call(function, prompt="Acme" if organizations else "Jane")
    assert page.interpretation.filters.to_dict() == filters
    assert page.interpretation.warnings == [None, "partial"]
    assert page.extras["interpretation"]["future"] is True


@pytest.mark.parametrize("debug", [False, True])
def test_debug_environment_works_without_application_handlers(debug):
    import os
    import subprocess
    import sys

    env = {key: value for key, value in os.environ.items() if key != "RC_LOG"}
    if debug:
        env["RC_LOG"] = "debug"
    code = """
import httpx
from reversecontact import ReverseContact
transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": None}))
with httpx.Client(transport=transport) as http:
    ReverseContact(api_key="hidden-key", http_client=http).usage.retrieve()
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True
    )
    assert ("method=GET path=/v2/usage status=200" in completed.stderr) is debug
    assert "hidden-key" not in completed.stderr
    if not debug:
        assert completed.stderr == ""
