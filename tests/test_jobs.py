import json

import httpx
import pytest

import reversecontact as rc
from conftest import failure, ok


def poll(status, **extra):
    return {"webhookId": "job_test", "status": status, "result": None, **extra}


async def test_task_handle_wait_and_pacing(h):
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
async def test_contact_task_typed_array(h, method):
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
async def test_contact_task_accepts_a_person_id(h, method):
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
async def test_failed_tasks_never_recreated(h, code, not_found):
    h.responses.append(
        ok(poll("errored", errorCode=code, result={"partial": True}, creditsConsumed=2))
    )
    with pytest.raises(rc.TaskFailedError) as caught:
        await h.call(h.client.tasks.wait, "job_test")
    error = caught.value
    assert error.task_id == "job_test"
    assert error.error_code == code
    assert error.not_found is not_found
    assert error.credits_consumed == 2
    assert error.result == {"partial": True}
    assert len(h.requests) == 1
    assert h.requests[0].method == "GET"


async def test_local_deadline_with_last_status(h):
    h.responses.append(ok(poll("in_progress")))
    with pytest.raises(rc.TaskTimeoutError) as caught:
        await h.call(h.client.tasks.wait, "job_test", timeout=1)
    assert caught.value.task_id == "job_test"
    assert caught.value.last_status == "in_progress"
    assert str(caught.value) == (
        "Timed out waiting for task job_test. Resume with tasks.wait using this id."
    )
    assert h.clock.now == 1
    assert h.requests[0].url.params["wait"] == "1"
    assert h.clock.sleeps == [1]


@pytest.mark.parametrize("status,error", [(404, rc.NotFoundError), (410, rc.GoneError)])
async def test_poll_gone_or_not_found(h, status, error):
    h.responses.append(failure(status))
    with pytest.raises(error):
        await h.call(h.client.tasks.wait, "job_test")
    assert not h.clock.sleeps


async def test_poll_retries_reset_after_good_response(h):
    h.client._transport.max_retries = 0
    h.responses.extend([failure(503, headers={"Retry-After": "1"}) for _ in range(4)])
    h.responses.append(ok(poll("in_progress")))
    h.responses.extend([failure(502, headers={"Retry-After": "1"}) for _ in range(4)])
    h.responses.append(ok(poll("succeeded", result={})))
    value = await h.call(h.client.tasks.wait, "job_test")
    assert value.data.status == "succeeded"
    assert len(h.requests) == 10
    assert h.clock.sleeps == [1, 1, 1, 1, 2, 1, 1, 1, 1]


async def test_poll_five_consecutive_failures(h):
    h.responses.extend([failure(503) for _ in range(5)])
    with pytest.raises(rc.InternalServerError):
        await h.call(h.client.tasks.wait, "job_test")
    assert len(h.requests) == 5
    assert len(h.clock.sleeps) == 4


async def test_poll_deadline_during_retry_sleep(h):
    h.responses.append(failure(503, headers={"Retry-After": "4"}))
    with pytest.raises(rc.TaskTimeoutError):
        await h.call(h.client.tasks.wait, "job_test", timeout=2)
    assert h.clock.now == 2
    assert len(h.requests) == 1


async def test_poll_timeout_floor(h):
    h.client._transport.timeout = httpx.Timeout(1, connect=2)
    h.responses.append(ok(poll("succeeded")))
    await h.call(h.client.tasks.wait, "job_test")
    assert all(value >= 35 for value in h.requests[0].extensions["timeout"].values())
    h.responses.append(ok(poll("succeeded")))
    await h.call(h.client.tasks.retrieve, "job_test", wait=20, timeout=0.5)
    assert h.requests[-1].extensions["timeout"]["read"] >= 35


@pytest.mark.parametrize("action", ["done", "resume", "reconcile"])
async def test_list_operation_wait_actions(h, action):
    h.responses.extend(
        [
            ok(
                {
                    "operationId": "op_test",
                    "clientAction": "poll",
                    "status": "running",
                    "pollAfterSeconds": 7,
                },
                headers={"Retry-After": "99"},
            ),
            ok({"operationId": "op_test", "clientAction": action, "status": "paused"}),
        ]
    )
    result = await h.call(h.client.lists.operations.wait, "list_test", "op_test")
    assert result.data.client_action == action
    assert h.clock.sleeps == [7]
    assert len(h.requests) == 2
    assert all(request.method == "GET" for request in h.requests)
    assert h.requests[0].url.path == "/v2/lists/list_test/operations/op_test/progress"


async def test_list_operation_header_pacing_and_deadline(h):
    h.responses.append(
        ok({"clientAction": "poll", "status": "running"}, headers={"Retry-After": "9"})
    )
    with pytest.raises(rc.TaskTimeoutError) as caught:
        await h.call(h.client.lists.operations.wait, "list_test", "op_test", timeout=3)
    assert caught.value.task_id == "op_test"
    assert caught.value.last_status == "running"
    assert "Resume with lists.operations.wait" in str(caught.value)
    assert h.clock.sleeps == [3]


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


async def test_list_poll_missing_pacing_falls_back_to_two_seconds(h):
    h.responses.extend(
        [
            ok({"clientAction": "poll", "status": "running"}),
            ok({"clientAction": "done", "status": "completed"}),
        ]
    )
    observed = []
    final = await h.call(
        h.client.lists.operations.wait, "list_test", "op_test", on_poll=observed.append
    )
    assert observed[-1] is final
    assert all(value.metadata.request_id == "req_test" for value in observed)
    assert h.clock.sleeps == [2]
    assert len(h.requests) == 2


async def test_list_poll_retries(h):
    h.responses.extend([failure(503, headers={"Retry-After": "4"}), ok({"clientAction": "done"})])
    assert (
        await h.call(h.client.lists.operations.wait, "list_test", "op_test")
    ).data.client_action == "done"
    assert h.clock.sleeps == [4]


async def test_deprecated_job_names_are_the_task_names(h):
    assert h.client.jobs is h.client.tasks
    assert rc.Job is rc.Task
    assert rc.AsyncJob is rc.AsyncTask
    assert rc.JobSnapshot is rc.TaskSnapshot
    assert rc.JobFailedError is rc.TaskFailedError
    assert rc.JobTimeoutError is rc.TaskTimeoutError
    assert rc.types.JobAckData is rc.types.TaskAckData
    h.responses.append(ok(poll("succeeded", result={})))
    value = await h.call(h.client.jobs.wait, "job_test")
    assert value.data.status == "succeeded"
    assert h.requests[0].url.path == "/v2/webhooks/job_test"


def test_deprecated_job_id_reads_the_task_id():
    timeout = rc.JobTimeoutError("task_test", "in_progress")
    assert isinstance(timeout, rc.TaskTimeoutError)
    assert isinstance(rc.TaskTimeoutError("task_test", None), rc.JobTimeoutError)
    assert timeout.job_id == timeout.task_id == "task_test"
    failed = rc.JobFailedError("task_test", "email-not-found", 1, None)
    assert isinstance(failed, rc.TaskFailedError)
    assert failed.job_id == "task_test"
    assert str(failed) == "Task task_test failed: email-not-found"
    with pytest.raises(AttributeError):
        failed.job_id = "other"
