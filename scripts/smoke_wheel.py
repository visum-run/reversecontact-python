"""Exercise the installed package without network access (also works with an editable install)."""

import asyncio
from importlib.resources import files

import httpx

from reversecontact import AsyncReverseContact, ReverseContact, __version__


def respond(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer smoke-key"
    if request.url.path in {"/v2/contact/email", "/v2/contact/phone"}:
        assert request.headers["Idempotency-Key"] == "smoke-contact"
        return httpx.Response(
            200,
            json={"data": {"webhookId": "smoke-job", "status": "created"}},
            headers={"Idempotent-Replayed": "true"},
        )
    assert request.url.path == "/v2/enrich/persons"
    return httpx.Response(
        200, json={"data": {"person": {"firstName": "Jane"}, "isFullProfile": False}}
    )


def sync_smoke() -> None:
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with ReverseContact(api_key="smoke-key", http_client=http) as client:
            result = client.enrich.person(email="jane@example.com")
            assert result is not None and result.data is not None
            assert result.data.person is not None and result.data.person.first_name == "Jane"
            assert result.http_response.status_code == 200
            job = client.contact.find_email(
                first_name="Jane", last_name="Doe", idempotency_key="smoke-contact"
            )
            assert job.id == "smoke-job" and job.replayed
        assert not http.is_closed


async def async_smoke() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with AsyncReverseContact(api_key="smoke-key", http_client=http) as client:
            result = await client.enrich.person(email="jane@example.com")
            assert result is not None and result.data is not None
            assert result.data.person is not None and result.data.person.first_name == "Jane"
            job = await client.contact.find_phone(
                url="https://social.com/in/jane", idempotency_key="smoke-contact"
            )
            assert job.id == "smoke-job" and job.replayed
        assert not http.is_closed


if __name__ == "__main__":
    assert files("reversecontact").joinpath("py.typed").is_file()
    sync_smoke()
    asyncio.run(async_smoke())
    print(f"reversecontact {__version__}: sync and async package smoke passed")
