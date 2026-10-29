import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok


@pytest.mark.parametrize(
    "details,headers,delay",
    [
        ({"retryAfter": 3}, {"Retry-After": "9"}, 3),
        (None, {"Retry-After": "4"}, 4),
        ({"retryAfter": 0}, {}, 0),
        (None, {}, None),
    ],
)
async def test_rate_limit_retries(h, details, headers, delay):
    h.responses.extend([failure(429, details=details, headers=headers), ok()])
    await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 2
    if delay is None:
        assert 2 <= h.clock.sleeps[0] <= 2.25
    else:
        assert h.clock.sleeps == [delay]


@pytest.mark.parametrize(
    "code", ["DAILY_LIMIT_EXCEEDED", "DAILY_EXTRACTION_LIMIT_EXCEEDED", "KEY_CREDIT_LIMIT_REACHED"]
)
async def test_budget_errors_never_retry(h, code):
    h.responses.append(failure(429, code, {"retryAfter": 1}))
    with pytest.raises(rc.APIError):
        await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 1
    assert h.clock.sleeps == []


@pytest.mark.parametrize(
    "details,headers", [({"retryAfter": 61}, {}), (None, {"Retry-After": "61"})]
)
async def test_long_rate_limit_raises_immediately(h, details, headers):
    h.responses.append(failure(429, details=details, headers=headers))
    with pytest.raises(rc.RateLimitError):
        await h.call(h.client.usage.retrieve)
    assert h.clock.sleeps == []


@pytest.mark.parametrize(
    "status,details,headers,error_type",
    [
        (503, {"retryAfterSeconds": 61}, {"Retry-After": "0"}, rc.InternalServerError),
        (429, {"retryAfter": 0}, {"Retry-After": "61"}, rc.RateLimitError),
        (429, {"retryAfterSeconds": 61}, {}, rc.RateLimitError),
        (503, {"retryAfter": 61}, {}, rc.InternalServerError),
    ],
)
async def test_any_long_retry_hint_prevents_retry(h, status, details, headers, error_type):
    h.responses.append(failure(status, details=details, headers=headers))
    with pytest.raises(error_type):
        await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 1
    assert h.clock.sleeps == []


@pytest.mark.parametrize(
    "status,details,headers,delay",
    [
        (429, {"retryAfter": 3, "retryAfterSeconds": 60}, {"Retry-After": "7"}, 3),
        (503, {"retryAfter": 60, "retryAfterSeconds": 7}, {"Retry-After": "3"}, 3),
        (429, {"retryAfterSeconds": 60}, {}, None),
        (503, {"retryAfter": 60}, {}, None),
    ],
)
async def test_acceptable_hints_preserve_retry_priority(h, status, details, headers, delay):
    h.responses.extend([failure(status, details=details, headers=headers), ok()])
    await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 2
    if delay is None:
        assert len(h.clock.sleeps) == 1
        assert 2 <= h.clock.sleeps[0] <= 2.25
    else:
        assert h.clock.sleeps == [delay]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_backoff(h, status):
    h.responses.extend([failure(status) for _ in range(4)])
    h.responses.append(ok())
    await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 5
    for actual, base in zip(h.clock.sleeps, [2, 4, 8, 15], strict=True):
        assert base <= actual <= base + 0.25


@pytest.mark.parametrize(
    "details,headers,delay",
    [
        (None, {"Retry-After": "8"}, 8),
        ({"retryAfterSeconds": 7}, {}, 7),
    ],
)
async def test_server_retry_hints(h, details, headers, delay):
    h.responses.extend([failure(503, "SERVICE_UNAVAILABLE", details, headers=headers), ok()])
    await h.call(h.client.usage.retrieve)
    assert h.clock.sleeps == [delay]


async def test_no_retries_and_exhaustion(h):
    h.responses.append(failure(503))
    with pytest.raises(rc.InternalServerError):
        await h.call(h.client.usage.retrieve, max_retries=0)
    assert len(h.requests) == 1
    assert h.clock.sleeps == []
    h.responses.extend([failure(503) for _ in range(5)])
    with pytest.raises(rc.InternalServerError):
        await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 6
    assert len(h.clock.sleeps) == 4


async def test_timeouts_and_connections(h):
    h.responses.append(httpx.ReadTimeout("timeout"))
    with pytest.raises(rc.APITimeoutError):
        await h.call(h.client.enrich.person, email="test@example.com")
    assert len(h.requests) == 1
    assert h.clock.sleeps == []
    h.responses.extend([httpx.ReadTimeout("timeout"), ok()])
    await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 3
    h.responses.extend([httpx.ConnectError("offline"), ok({})])
    await h.call(h.client.enrich.person, email="test@example.com")
    assert len(h.requests) == 5
    h.responses.append(httpx.ConnectError("offline"))
    with pytest.raises(rc.APIConnectionError) as caught:
        await h.call(h.client.usage.retrieve, max_retries=0)
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 409, 410, 422, 451, 501])
async def test_other_statuses_do_not_retry(h, status):
    h.responses.append(failure(status))
    with pytest.raises(rc.APIError):
        await h.call(h.client.usage.retrieve)
    assert len(h.requests) == 1
    assert not h.clock.sleeps
