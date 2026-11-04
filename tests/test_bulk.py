import json

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


async def test_list_all_asks_pages_the_jobs_route_accepts(h):
    h.responses.append(ok({"items": [job("completed")], "nextCursor": None}))
    await h.collect(h.client.bulk.list_all())
    assert h.requests[0].url.params["perPage"] == "50"

    h.responses.append(ok({"items": [job("completed")], "nextCursor": None}))
    await h.collect(h.client.bulk.list_all(per_page=100))
    assert h.requests[1].url.params["perPage"] == "100"

    with pytest.raises(rc.ReverseContactError):
        await h.collect(h.client.bulk.list_all(per_page=200))
    assert len(h.requests) == 2


def line(row):
    return {
        "row": row,
        "input": {"email": f"p{row}@example.com"},
        "status": "enriched",
        "message": "Enriched (1 credit)",
        "creditsConsumed": 1,
        "person": None,
        "isFullProfile": False,
    }


async def test_results_give_the_ndjson_as_text_when_asked(h):
    body = json.dumps(line(1)) + "\n" + json.dumps(line(2)) + "\n"
    h.responses.append(
        httpx.Response(200, text=body, headers={"content-type": "application/x-ndjson"})
    )
    text = await h.call(h.client.bulk.results, "job_test", format="ndjson")
    assert text == body
    assert [json.loads(value)["row"] for value in text.splitlines()] == [1, 2]
    assert h.requests[0].url.path == "/v2/jobs/job_test/results"
    assert h.requests[0].url.params["format"] == "ndjson"


async def test_rows_reads_one_page_with_its_cursor_limit_and_job_status(h):
    h.responses.extend(
        [
            ok({"items": [line(3), line(4)], "nextCursor": "after-4", "status": "running"}),
            ok({"items": [line(5)], "nextCursor": None, "status": "completed"}),
        ]
    )
    page = await h.call(h.client.bulk.rows, "job /1", cursor="after-2", limit=2)
    assert isinstance(page, rc.AsyncPage if h.asynchronous else rc.Page)
    assert [item.row for item in page.data] == [3, 4]
    assert isinstance(page.data[0], rc.types.JobResultLine)
    assert page.next_cursor == "after-4"
    assert page.extras["status"] == "running"
    assert h.requests[0].url.raw_path.split(b"?")[0] == b"/v2/jobs/job%20%2F1/rows"
    assert dict(h.requests[0].url.params) == {"cursor": "after-2", "limit": "2"}
    following = await h.call(page.get_next_page)
    assert [item.row for item in following.data] == [5]
    assert not following.has_next_page()
    assert dict(h.requests[1].url.params) == {"cursor": "after-4", "limit": "2"}


async def test_rows_retries_a_busy_server_after_retry_after(h):
    h.responses.extend(
        [
            failure(429, "JOB_ROWS_BUSY", {"retryAfter": 1}, headers={"Retry-After": "1"}),
            ok({"items": [line(1)], "nextCursor": None, "status": "completed"}),
        ]
    )
    page = await h.call(h.client.bulk.rows, "job_test")
    assert len(page.data) == 1
    assert len(h.requests) == 2
    assert h.clock.sleeps == [1]


async def test_iter_rows_waits_on_an_empty_page_and_reads_its_cursor_again(h):
    h.responses.extend(
        [
            ok({"items": [line(1), line(2)], "nextCursor": "at-3", "status": "running"}),
            ok(
                {"items": [], "nextCursor": "at-3", "status": "running"},
                headers={"Retry-After": "5"},
            ),
            ok({"items": [], "nextCursor": "at-3", "status": "running"}),
            ok({"items": [line(3)], "nextCursor": "at-4", "status": "running"}),
            ok({"items": [line(4)], "nextCursor": None, "status": "completed"}),
        ]
    )
    rows = await h.collect(h.client.bulk.iter_rows("job_test", limit=2))
    assert [item.row for item in rows] == [1, 2, 3, 4]
    assert [request.url.path for request in h.requests] == ["/v2/jobs/job_test/rows"] * 5
    assert [request.url.params.get("cursor") for request in h.requests] == [
        None,
        "at-3",
        "at-3",
        "at-3",
        "at-4",
    ]
    assert all(request.url.params["limit"] == "2" for request in h.requests)
    assert h.clock.sleeps == [5, 2]


async def test_iter_rows_stops_on_a_paused_job_without_waiting(h):
    h.responses.extend(
        [
            ok({"items": [line(1)], "nextCursor": "at-2", "status": "running"}),
            ok(
                {"items": [], "nextCursor": "at-2", "status": "paused"},
                headers={"Retry-After": "2"},
            ),
        ]
    )
    rows = await h.collect(h.client.bulk.iter_rows("job_test"))
    assert [item.row for item in rows] == [1]
    assert len(h.requests) == 2
    assert not h.clock.sleeps


async def test_iter_rows_stops_once_the_cursor_is_null_even_on_an_empty_page(h):
    h.responses.append(ok({"items": [], "nextCursor": None, "status": "failed"}))
    assert await h.collect(h.client.bulk.iter_rows("job_test")) == []
    assert len(h.requests) == 1
    assert not h.clock.sleeps
