import httpx
import pytest
import respx

from reversecontact import AsyncReverseContact, ReverseContact


@pytest.mark.parametrize("asynchronous", [False, True])
@respx.mock
async def test_default_transport_and_env_url(monkeypatch, asynchronous):
    monkeypatch.setenv("RC_API_KEY", "oat_integration")
    monkeypatch.setenv("RC_BASE_URL", "https://mock.reversecontact.test/")
    route = respx.get("https://mock.reversecontact.test/v2/usage").mock(
        return_value=httpx.Response(200, json={"data": None})
    )
    if asynchronous:
        async with AsyncReverseContact(default_headers={"X-Integration": "test"}) as client:
            assert (await client.usage.retrieve()).data is None
    else:
        with ReverseContact(default_headers={"X-Integration": "test"}) as client:
            assert client.usage.retrieve().data is None
    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer oat_integration"
    assert route.calls[0].request.headers["x-integration"] == "test"
    assert client._transport.client.is_closed


@pytest.mark.parametrize("asynchronous", [False, True])
@respx.mock
async def test_copy_shares_owned_pool_without_owning_it(asynchronous):
    route = respx.get("https://api.reversecontact.com/v2/usage").mock(
        return_value=httpx.Response(200, json={"data": None})
    )
    if asynchronous:
        async with AsyncReverseContact(api_key="owner") as client:
            async with client.with_options(timeout=3) as copied:
                assert copied._transport.client is client._transport.client
                assert not copied._transport.owned
                await copied.usage.retrieve()
            assert not client._transport.client.is_closed
            await client.usage.retrieve()
    else:
        with ReverseContact(api_key="owner") as client:
            with client.with_options(timeout=3) as copied:
                assert copied._transport.client is client._transport.client
                assert not copied._transport.owned
                copied.usage.retrieve()
            assert not client._transport.client.is_closed
            client.usage.retrieve()
    assert client._transport.client.is_closed
    assert route.call_count == 2
