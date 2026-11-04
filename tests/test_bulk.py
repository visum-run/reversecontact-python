import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok

ITEMS = [{"email": "jane@example.com"}]


def job(status, **extra):
    return {"id": "job_test", "status": status, **extra}


@pytest.mark.parametrize("provided", [None, "customer-key"])
async def test_bulk_enrich_idempotency(h, provided):
    h.responses.extend([failure(503), ok({"job": job("queued")})])
    await h.call(h.client.bulk.enrich_persons, items=ITEMS, idempotency_key=provided)
    keys = [request.headers["Idempotency-Key"] for request in h.requests]
    assert keys[0] == keys[1]
    assert keys[0] == provided if provided else len(keys[0]) == 36


async def test_bulk_enrich_submits_to_the_bulk_route_and_follows_under_jobs(h):
    h.responses.extend([ok({"job": job("queued")}), ok(job("running"))])
    await h.call(h.client.bulk.enrich_persons, items=ITEMS)
    await h.call(h.client.bulk.retrieve, "job_test")
    assert [request.url.path for request in h.requests] == [
        "/v2/enrich/persons/bulk",
        "/v2/jobs/job_test",
    ]


async def test_bulk_enrich_gets_a_fresh_key_per_call(h):
    h.responses.extend([ok({}), ok({})])
    await h.call(h.client.bulk.enrich_persons, items=ITEMS)
    await h.call(h.client.bulk.enrich_persons, items=ITEMS)
    keys = [request.headers["Idempotency-Key"] for request in h.requests]
    assert keys[0] != keys[1]


async def test_wait_polls_while_the_job_moves(h):
    h.responses.extend(
        [
            ok(job("queued"), headers={"Retry-After": "3"}),
            ok(job("running")),
            ok(job("completed")),
        ]
    )
    result = await h.call(h.client.bulk.wait, "job_test", poll_wait=10)
    assert result.data.status == "completed"
    assert h.clock.sleeps == [3, 2]
    assert [request.url.path for request in h.requests] == ["/v2/jobs/job_test"] * 3
    assert h.requests[0].url.params["wait"] == "10"


@pytest.mark.parametrize("status", ["paused", "cancelled", "failed"])
async def test_wait_returns_a_job_that_stopped_moving(h, status):
    h.responses.append(ok(job(status)))
    result = await h.call(h.client.bulk.wait, "job_test")
    assert result.data.status == status
    assert len(h.requests) == 1
    assert not h.clock.sleeps


async def test_wait_deadline_keeps_the_job_id_and_last_status(h):
    h.responses.append(ok(job("running"), headers={"Retry-After": "9"}))
    with pytest.raises(rc.JobTimeoutError) as caught:
        await h.call(h.client.bulk.wait, "job_test", timeout=3)
    assert caught.value.job_id == "job_test"
    assert caught.value.last_status == "running"
    assert h.clock.sleeps == [3]


async def test_results_give_the_csv_as_text(h):
    body = "email,externalId,rc_status\r\na@b.co,1,enriched\r\n"
    h.responses.append(
        httpx.Response(200, text=body, headers={"content-type": "text/csv; charset=utf-8"})
    )
    assert await h.call(h.client.bulk.results, "job_test") == body
    assert h.requests[0].url.path == "/v2/jobs/job_test/results"


async def test_list_all_follows_the_cursor(h):
    h.responses.extend(
        [
            ok({"items": [job("completed"), job("running")], "nextCursor": "next"}),
            ok({"items": [job("failed")], "nextCursor": None}),
        ]
    )
    statuses = [
        item.status for item in await h.collect(h.client.bulk.list_all(type="enrich_persons"))
    ]
    assert statuses == ["completed", "running", "failed"]
    assert h.requests[1].url.params["cursor"] == "next"
    assert h.requests[1].url.params["type"] == "enrich_persons"
