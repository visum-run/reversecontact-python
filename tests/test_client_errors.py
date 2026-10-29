import re
from pathlib import Path

import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok


@pytest.mark.parametrize("factory", [rc.ReverseContact, rc.AsyncReverseContact])
def test_missing_key_and_environment(factory, monkeypatch):
    monkeypatch.delenv("RC_API_KEY", raising=False)
    with pytest.raises(rc.ReverseContactError, match="RC_API_KEY"):
        factory()
    monkeypatch.setenv("RC_API_KEY", "oat_token")
    monkeypatch.setenv("RC_BASE_URL", "https://staging.example.test/")
    # Injected transports avoid opening sockets.
    transport = httpx.MockTransport(lambda request: ok())
    http = (
        httpx.Client(transport=transport)
        if factory is rc.ReverseContact
        else httpx.AsyncClient(transport=transport)
    )
    client = factory(http_client=http)
    assert client.base_url == "https://staging.example.test"
    assert client._transport.headers["Authorization"] == "Bearer oat_token"


async def test_options_headers_and_raw_response(h):
    h.client._transport.headers["x-default"] = "default"
    h.responses.append(ok())
    result = await h.call(
        h.client.usage.retrieve, timeout=12, max_retries=0, headers={"x-call": "call"}
    )
    assert result.http_response.status_code == 200
    assert result.metadata.request_id == "req_test"
    request = h.requests[-1]
    assert request.headers["x-default"] == "default"
    assert request.headers["x-call"] == "call"
    assert request.extensions["timeout"]["read"] == 12


def test_app_info():
    with rc.ReverseContact(
        api_key="key",
        app_info={"name": "my-product", "version": "1.2", "url": "https://private.test"},
    ) as client:
        agent = client._transport.headers["User-Agent"]
        assert agent.endswith(" my-product/1.2")
        assert "private.test" not in agent


ERRORS = [
    (400, "UNKNOWN", rc.BadRequestError),
    (401, "UNKNOWN", rc.AuthenticationError),
    (402, "UNKNOWN", rc.InsufficientCreditsError),
    (403, "UNKNOWN", rc.PermissionDeniedError),
    (404, "UNKNOWN", rc.NotFoundError),
    (409, "UNKNOWN", rc.ConflictError),
    (410, "UNKNOWN", rc.GoneError),
    (422, "UNKNOWN", rc.UnprocessableEntityError),
    (429, "UNKNOWN", rc.RateLimitError),
    (451, "UNKNOWN", rc.UnavailableForLegalReasonsError),
    (500, "UNKNOWN", rc.InternalServerError),
    (599, "UNKNOWN", rc.InternalServerError),
    (418, "UNKNOWN", rc.APIError),
    (429, "DAILY_LIMIT_EXCEEDED", rc.DailyLimitError),
    (429, "DAILY_EXTRACTION_LIMIT_EXCEEDED", rc.DailyLimitError),
    (429, "KEY_CREDIT_LIMIT_REACHED", rc.KeyCreditLimitError),
    (403, "KEY_CREDIT_LIMIT_REACHED", rc.KeyCreditLimitError),
]


@pytest.mark.parametrize("status,code,expected", ERRORS)
async def test_error_mapping(h, status, code, expected):
    details = {
        "errors": [{"suggestion": "email"}],
        "period": "daily",
        "limit": 5,
        "used": 5,
        "required": 1,
        "retryAfter": 7,
        "resetAt": "tomorrow",
    }
    response = failure(status, code, details, headers={"x-error": "kept"})
    h.responses.append(response)
    with pytest.raises(expected) as caught:
        await h.call(h.client.usage.retrieve, max_retries=0)
    error = caught.value
    assert type(error) is expected
    assert error.status == status
    assert error.code == code
    assert error.message == "Test error"
    assert error.details == details
    assert error.request_id == "req_error"
    assert error.headers["x-error"] == "kept"
    assert error.body == response.json()
    assert error.retry_after == 7
    assert error.reset_at == "tomorrow"
    if expected is rc.KeyCreditLimitError:
        assert (error.period, error.limit, error.used, error.required) == ("daily", 5, 5, 1)


async def test_non_envelope_errors(h):
    h.responses.append(httpx.Response(502, text="<html>Proxy failed</html>"))
    with pytest.raises(rc.InternalServerError) as caught:
        await h.call(h.client.usage.retrieve, max_retries=0)
    assert caught.value.code == "HTTP_502"
    assert caught.value.message == "The response is not JSON"
    assert caught.value.body == "<html>Proxy failed</html>"
    h.responses.append(
        httpx.Response(
            410,
            json={
                "error": "endpoint_unavailable",
                "message": "Gone",
                "alternative": {"endpoint": "/new", "docs": "/docs"},
            },
        )
    )
    with pytest.raises(rc.GoneError) as caught:
        await h.call(h.client.usage.retrieve)
    assert caught.value.code == "endpoint_unavailable"
    assert caught.value.alternative["endpoint"] == "/new"
    h.responses.append(
        httpx.Response(504, json={"error": {"code": "GATEWAY_TIMEOUT", "message": "Deadline"}})
    )
    with pytest.raises(rc.InternalServerError) as caught:
        await h.call(h.client.usage.retrieve, max_retries=0)
    assert caught.value.request_id is None


@pytest.mark.parametrize(
    "path", ["enrich.person", "enrich.company", "fetch.person", "fetch.company"]
)
async def test_lookup_no_match(h, path):
    resource, name = path.split(".")
    method = getattr(getattr(h.client, resource), name)
    h.responses.append(failure(404))
    assert await h.call(method) is None
    h.responses.append(failure(404, "NOT_FOUND"))
    with pytest.raises(rc.NotFoundError):
        await h.call(method)


@pytest.mark.parametrize(
    "path", ["enrich.person", "enrich.company", "fetch.person", "fetch.company"]
)
async def test_lookup_html_404_raises(h, path):
    resource, name = path.split(".")
    method = getattr(getattr(h.client, resource), name)
    body = "<html><body>Proxy route not found</body></html>"
    h.responses.append(httpx.Response(404, text=body, headers={"Content-Type": "text/html"}))
    with pytest.raises(rc.NotFoundError) as caught:
        await h.call(method)
    assert caught.value.code == "HTTP_404"
    assert caught.value.status == 404
    assert caught.value.body == body
    assert len(h.requests) == 1
    assert h.clock.sleeps == []


async def test_context_ownership(h):
    if h.asynchronous:
        async with h.client:
            pass
    else:
        with h.client:
            pass
    assert not h.http.is_closed
    owned = (
        rc.AsyncReverseContact(api_key="test")
        if h.asynchronous
        else rc.ReverseContact(api_key="test")
    )
    if h.asynchronous:
        async with owned:
            pass
    else:
        with owned:
            pass
    assert owned._transport.client.is_closed


def test_version():
    manifest = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"', manifest, re.M).group(1) == rc.__version__
