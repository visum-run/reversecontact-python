import json

import httpx
import pytest

import reversecontact as rc
from conftest import ok
from reversecontact.types import EnrichPersonData, Person, PollWebhookData


async def test_page_and_next_page(h):
    h.responses.extend(
        [
            ok(
                {
                    "data": [{"firstName": "Jane"}],
                    "metadata": {"count": 1, "perPage": 1, "nextCursor": "next"},
                    "interpretation": {"warnings": []},
                }
            ),
            ok({"data": [{"firstName": "John"}], "metadata": {"count": 1, "nextCursor": None}}),
        ]
    )
    page = await h.call(
        h.client.search.people_by_prompt, prompt="engineers", per_page=1, headers={"x-page": "same"}
    )
    assert page.has_next_page()
    assert page.next_cursor == "next"
    assert page.count == 1
    assert page.metadata.request_id == "req_test"
    assert page.quotas.credits_consumed == 0
    assert page.interpretation.warnings == []
    assert page.data[0].first_name == "Jane"
    next_page = await h.call(page.get_next_page)
    assert next_page.data[0].first_name == "John"
    assert not next_page.has_next_page()
    assert await h.call(next_page.get_next_page) is None
    assert json.loads(h.requests[1].content) == {
        "prompt": "engineers",
        "perPage": 1,
        "cursor": "next",
    }
    assert h.requests[1].headers["x-page"] == "same"


async def test_integers_stay_integers(h):
    # Credit amounts are `number` in the API spec: an integer must not come back as 2.0,
    # while a decimal stays a float. Counts and sizes are `integer` and come back as int.
    h.responses.append(
        httpx.Response(
            200,
            json={
                "data": {
                    "data": [
                        {"firstName": "Jane", "followersCount": 1200, "connectionsCount": 500}
                    ],
                    "metadata": {"count": 21216014, "perPage": 1, "nextCursor": None},
                },
                "quotas": {
                    "creditsConsumed": 1,
                    "workspace": {
                        "credits": {"total": 1000, "left": 644401.5},
                        "minuteRateLimit": {"left": 2990},
                    },
                },
                "metadata": {"requestId": "req_test", "executionTimeMs": 12},
            },
        )
    )
    page = await h.call(h.client.search.people, first_name="Jane", per_page=1)
    assert type(page.count) is int and page.count == 21216014
    assert type(page.per_page) is int
    assert type(page.quotas.credits_consumed) is int
    assert type(page.quotas.workspace.credits.total) is int
    assert type(page.quotas.workspace.credits.left) is float
    assert type(page.quotas.workspace.minute_rate_limit.left) is int
    assert type(page.metadata.execution_time_ms) is int
    assert type(page.data[0].followers_count) is int
    assert type(page.data[0].connections_count) is int


@pytest.mark.parametrize(
    "helper",
    [
        "iter_people",
        "iter_people_by_prompt",
        "iter_people_lookalike",
        "iter_organizations",
        "iter_organizations_by_prompt",
    ],
)
async def test_search_credit_guard(h, helper):
    def handle(request):
        params = json.loads(request.content)
        size = params["perPage"]
        return ok(
            {
                "data": [{"id": "item"} for _ in range(size)],
                "metadata": {"nextCursor": f"cursor-{len(h.requests)}"},
            }
        )

    h.handler = handle
    items = await h.collect(getattr(h.client.search, helper)(max_results=105, perPage=60))
    assert len(items) == 105
    assert [json.loads(request.content)["perPage"] for request in h.requests] == [60, 45]
    assert sum(json.loads(request.content)["perPage"] for request in h.requests) == 105


async def test_zero_budget_and_required_budget(h):
    assert await h.collect(h.client.search.iter_people(max_results=0)) == []
    assert not h.requests
    with pytest.raises(TypeError):
        h.client.search.iter_people()
    with pytest.raises(rc.ReverseContactError):
        await h.collect(h.client.search.iter_people(max_results=-1))


async def test_pagination_stops_at_null_and_detects_loop(h):
    h.responses.append(ok({"data": [], "metadata": {"nextCursor": None}}))
    assert await h.collect(h.client.search.iter_people(max_results=100)) == []
    h.responses.extend([ok({"data": [], "metadata": {"nextCursor": "same"}}) for _ in range(2)])
    with pytest.raises(rc.ReverseContactError, match="repeated"):
        await h.collect(h.client.search.iter_people(max_results=100))


def test_lenient_models_round_trip():
    payload = {
        "firstName": None,
        "hasPremium": None,
        "followersCount": 0,
        "skills": [],
        "newField": {"surprise": True},
    }
    person = Person.model_validate(payload)
    assert person.first_name is None
    assert person.id is None
    assert person.newField == {"surprise": True}
    assert person.skills == []
    assert person.followers_count == 0
    assert person.to_dict() == payload
    assert Person(first_name="Jane").to_dict() == {"firstName": "Jane"}
    job = PollWebhookData.model_validate({"status": "new-status", "result": None})
    assert job.status == "new-status"
    assert job.to_dict() == {"status": "new-status", "result": None}
    nested = EnrichPersonData.model_validate(
        {
            "person": {"firstName": "Jane", "location": None},
            "isFullProfile": False,
            "alternativePersons": [],
        }
    )
    assert nested.person.first_name == "Jane"
    assert nested.person.location is None
    assert nested.is_full_profile is False
    assert nested.alternative_persons == []


async def test_null_quotas_preserved(h):
    payload = {
        "creditsConsumed": 0,
        "workspace": {
            "credits": {"total": None, "used": 0, "left": None},
            "hasUnlimitedCredits": None,
            "allowDailyOvercost": None,
            "minuteRateLimit": None,
            "dailyLimit": None,
        },
        "key": {"creditLimits": {"daily": None, "monthly": None, "total": {"nextReset": None}}},
    }
    import httpx

    h.responses.append(httpx.Response(200, json={"data": None, "quotas": payload}))
    response = await h.call(h.client.usage.retrieve)
    assert response.data is None
    assert response.quotas.to_dict() == payload
    assert response.quotas.workspace.credits.total is None


async def test_unexpected_null_array_elements(h):
    person = Person.model_validate(
        {"skills": [None, "Python"], "experience": [None, {"title": None}]}
    )
    assert person.skills == [None, "Python"]
    assert person.experience[0] is None
    assert person.to_dict() == {"skills": [None, "Python"], "experience": [None, {"title": None}]}
    h.responses.append(
        ok({"data": [None, {"firstName": "Jane"}], "metadata": {"nextCursor": None}})
    )
    page = await h.call(h.client.search.people, first_name="Jane")
    assert page.data[0] is None
    assert page.data[1].first_name == "Jane"
