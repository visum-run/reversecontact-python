import json

import httpx

from conftest import ok


async def test_organization_search_calls_the_companies_path(h):
    h.responses.append(ok({"count": 3}))
    await h.call(h.client.search.count_organizations, company_name="Acme")
    assert h.requests[0].url.path == "/v2/search/companies/count"


async def test_a_moved_route_is_followed_with_the_same_request(h):
    h.responses.append(
        httpx.Response(
            308,
            headers={"Location": "/v2/search/companies/count?probe=1"},
            json={"error": {"code": "MOVED_PERMANENTLY", "message": "Moved"}},
        )
    )
    h.responses.append(ok({"count": 3}))
    result = await h.call(h.client.search.count_organizations, company_name="Acme")
    first, second = h.requests
    assert second.method == first.method == "POST"
    assert second.url.path == "/v2/search/companies/count"
    assert json.loads(second.content) == json.loads(first.content) == {"companyName": "Acme"}
    assert second.headers["Authorization"] == first.headers["Authorization"]
    assert result.data.count == 3
