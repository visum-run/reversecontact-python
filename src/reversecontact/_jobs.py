import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from typing import Generic, TypeVar

from pydantic import Field

from ._http import (
    AsyncTransport,
    RequestOptions,
    Response,
    SyncTransport,
    Timeout,
    backoff,
    retry_delay,
)
from ._models import ContactValue, Model, Result, TaskSnapshot
from ._operations import ALIASES, OPERATIONS
from ._resource import AsyncResource, SyncResource, result
from .errors import (
    APIConnectionError,
    APIError,
    ReverseContactError,
    TaskFailedError,
    TaskTimeoutError,
    as_dict,
    retry_after_header,
    seconds,
)
from .types import GetListOperationData, GetTaskData, GetTaskRowsData, TaskAckData, TaskResultLine

T = TypeVar("T")


def parse_contacts(value: object) -> list[ContactValue | None]:
    return (
        [ContactValue.model_validate(item) if item is not None else None for item in value]
        if isinstance(value, list)
        else []
    )


def snapshot(response: Response, parser: Callable[[object], T]) -> Result[TaskSnapshot[T]]:
    def parse(value: object) -> TaskSnapshot[T]:
        data = dict(as_dict(value))
        if data.get("result") is not None:
            data["result"] = parser(data["result"])
        return TaskSnapshot[T].model_validate(data)

    return result(response, parse)


class PollState:
    def __init__(
        self,
        task_id: str,
        timeout: float,
        poll_wait: int,
        clock: Callable[[], float],
        resume: str = "tasks.wait",
    ) -> None:
        if (
            isinstance(poll_wait, bool)
            or not isinstance(poll_wait, int)
            or not 0 <= poll_wait <= 20
        ):
            raise ReverseContactError("poll_wait must be an integer from 0 to 20")
        duration = seconds(timeout)
        if duration is None:
            raise ReverseContactError("timeout must be a finite non-negative number")
        self.task_id = task_id
        self.resume = resume
        self.clock = clock
        self.deadline = clock() + duration
        self.poll_wait = poll_wait
        self.last_status: str | None = None
        self.failures = 0

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TaskTimeoutError(self.task_id, self.last_status, resume=self.resume)
        return remaining

    def wait_seconds(self) -> int:
        return min(self.poll_wait, int(self.remaining()))

    def failure_delay(self, error: Exception) -> float:
        self.remaining()
        self.failures += 1
        delay = (
            retry_delay(error, self.failures)
            if isinstance(error, APIError)
            else backoff(self.failures)
        )
        if self.failures >= 5 or delay is None:
            raise error
        return min(delay, self.remaining())

    def pause(self, started: float, pacing: float) -> float:
        return min(max(0, started + pacing - self.clock()), self.remaining())

    async def arun(self, call: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(self.remaining()):
                return await call
        except TimeoutError as error:
            raise TaskTimeoutError(self.task_id, self.last_status, resume=self.resume) from error


def terminal(task_id: str, value: TaskSnapshot[T]) -> bool:
    if value.status == "errored":
        raise TaskFailedError(task_id, value.error_code, value.credits_consumed, value.result)
    return value.status == "succeeded"


class TaskBase(Generic[T]):
    def __init__(self, response: Response, parser: Callable[[object], T]) -> None:
        self.data = TaskAckData.model_validate(as_dict(response.body).get("data") or {})
        task_id = self.data.webhook_id
        if not task_id:
            raise ReverseContactError("The task acknowledgement is missing webhookId")
        self.id = task_id
        self.webhook_id = task_id
        self.status = self.data.status
        self.poll_url = self.data.poll_url
        self.http_response = response.http_response
        self.replayed: bool = (
            self.http_response.headers.get("Idempotent-Replayed", "").lower() == "true"
        )
        envelope = result(response, TaskAckData.model_validate)
        self.quotas = envelope.quotas
        self.metadata = envelope.metadata
        self._parser = parser


class Task(TaskBase[T]):
    """The handle of an async task (live fetch, contact lookup): `retrieve` and `wait`."""

    def __init__(
        self, transport: SyncTransport, response: Response, parser: Callable[[object], T]
    ) -> None:
        super().__init__(response, parser)
        self._transport = transport

    def retrieve(
        self,
        *,
        wait: int = 0,
        timeout: Timeout | None = None,
        max_retries: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[T]]:
        response = self._transport.request(
            OPERATIONS["pollWebhook"],
            {"webhookId": self.id},
            {"wait": wait},
            RequestOptions(timeout, max_retries, headers),
            ALIASES,
        )
        return snapshot(response, self._parser)

    def wait(
        self,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[TaskSnapshot[T]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[T]]:
        """Wait using HTTP timeouts capped by the remaining overall deadline.

        Synchronous polling runs in the caller's thread. The deadline may be
        exceeded by one bounded HTTP phase before TaskTimeoutError is raised.
        """
        state = PollState(self.id, timeout, poll_wait, self._transport.clock)
        while True:
            wait = state.wait_seconds()
            started = self._transport.clock()
            try:
                response = self._transport.request(
                    OPERATIONS["pollWebhook"],
                    {"webhookId": self.id},
                    {"wait": wait},
                    RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                    ALIASES,
                )
                value = snapshot(response, self._parser)
            except (APIError, APIConnectionError) as error:
                self._transport.sleep(state.failure_delay(error))
                continue
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None and terminal(self.id, value.data):
                return value
            pacing = (
                retry_after_header(value.http_response.headers)
                if value.http_response is not None
                else None
            )
            delay = state.pause(started, pacing if pacing is not None else 2)
            if delay:
                self._transport.sleep(delay)


class AsyncTask(TaskBase[T]):
    """The asynchronous handle of an async task (live fetch, contact lookup)."""

    def __init__(
        self, transport: AsyncTransport, response: Response, parser: Callable[[object], T]
    ) -> None:
        super().__init__(response, parser)
        self._transport = transport

    async def retrieve(
        self,
        *,
        wait: int = 0,
        timeout: Timeout | None = None,
        max_retries: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[T]]:
        response = await self._transport.request(
            OPERATIONS["pollWebhook"],
            {"webhookId": self.id},
            {"wait": wait},
            RequestOptions(timeout, max_retries, headers),
            ALIASES,
        )
        return snapshot(response, self._parser)

    async def wait(
        self,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[TaskSnapshot[T]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[T]]:
        state = PollState(self.id, timeout, poll_wait, self._transport.clock)
        while True:
            wait = state.wait_seconds()
            started = self._transport.clock()
            try:
                response = await state.arun(
                    self._transport.request(
                        OPERATIONS["pollWebhook"],
                        {"webhookId": self.id},
                        {"wait": wait},
                        RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                        ALIASES,
                    )
                )
                value = snapshot(response, self._parser)
            except (APIError, APIConnectionError) as error:
                await self._transport.sleep(state.failure_delay(error))
                continue
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None and terminal(self.id, value.data):
                return value
            pacing = (
                retry_after_header(value.http_response.headers)
                if value.http_response is not None
                else None
            )
            delay = state.pause(started, pacing if pacing is not None else 2)
            if delay:
                await self._transport.sleep(delay)


def existing(task_id: str) -> Response:
    # Construct a local acknowledgement; resuming never creates a remote task.
    import httpx

    return Response(httpx.Response(200), {"data": {"webhookId": task_id}})


Job = Task
"""Deprecated: the former name of `Task`, the same class."""
AsyncJob = AsyncTask
"""Deprecated: the former name of `AsyncTask`, the same class."""


class TasksHelpers(SyncResource):
    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[TaskSnapshot[object]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[object]]:
        """Resume polling in the caller's thread with bounded HTTP timeouts.

        The overall deadline may be exceeded by one bounded HTTP phase before
        TaskTimeoutError is raised, preserving the task id and last status.
        """
        return Task(self._transport, existing(task_id), lambda value: value).wait(
            timeout=timeout, poll_wait=poll_wait, on_poll=on_poll, headers=headers
        )


class AsyncTasksHelpers(AsyncResource):
    async def wait(
        self,
        task_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[TaskSnapshot[object]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[TaskSnapshot[object]]:
        return await AsyncTask(self._transport, existing(task_id), lambda value: value).wait(
            timeout=timeout, poll_wait=poll_wait, on_poll=on_poll, headers=headers
        )


def list_pacing(value: float | None) -> float:
    parsed = seconds(value)
    return parsed if parsed is not None else 2


class OperationsHelpers(SyncResource):
    def wait(
        self,
        list_id: str,
        operation_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[GetListOperationData]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[GetListOperationData]:
        """Wait in the caller's thread with each HTTP phase capped by the deadline.

        The overall deadline may be exceeded by one bounded HTTP phase before
        TaskTimeoutError is raised, preserving the operation id and last status.
        """
        state = PollState(
            operation_id, timeout, poll_wait, self._transport.clock, "lists.operations.wait"
        )
        while True:
            wait = state.wait_seconds()
            try:
                response = self._transport.request(
                    OPERATIONS["getListOperation"],
                    {"listId": list_id, "operationId": operation_id},
                    {"wait": wait},
                    RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                    ALIASES,
                )
            except (APIError, APIConnectionError) as error:
                self._transport.sleep(state.failure_delay(error))
                continue
            value = result(response, GetListOperationData.model_validate)
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None:
                if value.data.client_action != "poll":
                    return value
                pacing: float | None = value.data.poll_after_seconds
            else:
                pacing = None
            if pacing is None:
                pacing = retry_after_header(response.http_response.headers)
            self._transport.sleep(min(list_pacing(pacing), state.remaining()))


class AsyncOperationsHelpers(AsyncResource):
    async def wait(
        self,
        list_id: str,
        operation_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[GetListOperationData]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[GetListOperationData]:
        state = PollState(
            operation_id, timeout, poll_wait, self._transport.clock, "lists.operations.wait"
        )
        while True:
            wait = state.wait_seconds()
            try:
                response = await state.arun(
                    self._transport.request(
                        OPERATIONS["getListOperation"],
                        {"listId": list_id, "operationId": operation_id},
                        {"wait": wait},
                        RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                        ALIASES,
                    )
                )
            except (APIError, APIConnectionError) as error:
                await self._transport.sleep(state.failure_delay(error))
                continue
            value = result(response, GetListOperationData.model_validate)
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None:
                if value.data.client_action != "poll":
                    return value
                pacing: float | None = value.data.poll_after_seconds
            else:
                pacing = None
            if pacing is None:
                pacing = retry_after_header(response.http_response.headers)
            await self._transport.sleep(min(list_pacing(pacing), state.remaining()))


# A Bulk task in these states still moves; any other state is where wait() stops.
MOVING_BULK_TASK_STATUSES = {"queued", "running"}


class BulkHelpers(SyncResource):
    def iter_rows(
        self,
        task_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        timeout: Timeout | None = None,
        max_retries: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[TaskResultLine | None]:
        """The results one item at a time, in the order sent, as they become ready.

        Reads `rows` page by page whatever the task is doing: while the next item runs,
        it waits `Retry-After` (2 seconds by default) and reads the same cursor again.
        Ends when `next_cursor` is null, or when the task is paused, since nothing moves
        until someone resumes it: read on later with `rows` and the last cursor. A
        cancelled task still finishes the items it had started, within the hour: it
        waits for them too.
        """
        options = RequestOptions(timeout, max_retries, headers)
        while True:
            response = self._transport.request(
                OPERATIONS["getTaskRows"],
                {"taskId": task_id},
                {"cursor": cursor, "limit": limit},
                options,
                ALIASES,
            )
            page = result(response, GetTaskRowsData.model_validate).data
            items = (page.items or []) if page is not None else []
            yield from items
            cursor = page.next_cursor if page is not None else None
            if page is None or cursor is None:
                return
            if not items:
                if page.status == "paused":
                    return
                self._transport.sleep(
                    list_pacing(retry_after_header(response.http_response.headers))
                )

    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[GetTaskData]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[GetTaskData]:
        """Wait until the task no longer moves: completed, cancelled, failed, or paused.

        The overall deadline may be exceeded by one bounded HTTP phase before
        TaskTimeoutError is raised, preserving the task id and last status.
        """
        state = PollState(task_id, timeout, poll_wait, self._transport.clock, "bulk.wait")
        while True:
            wait = state.wait_seconds()
            try:
                response = self._transport.request(
                    OPERATIONS["getTask"],
                    {"taskId": task_id},
                    {"wait": wait},
                    RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                    ALIASES,
                )
            except (APIError, APIConnectionError) as error:
                self._transport.sleep(state.failure_delay(error))
                continue
            value = result(response, GetTaskData.model_validate)
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None and value.data.status not in MOVING_BULK_TASK_STATUSES:
                return value
            pacing = retry_after_header(response.http_response.headers)
            self._transport.sleep(min(list_pacing(pacing), state.remaining()))


class AsyncBulkHelpers(AsyncResource):
    async def iter_rows(
        self,
        task_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        timeout: Timeout | None = None,
        max_retries: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[TaskResultLine | None]:
        """The results one item at a time, in the order sent, as they become ready.

        Reads `rows` page by page whatever the task is doing: while the next item runs,
        it waits `Retry-After` (2 seconds by default) and reads the same cursor again.
        Ends when `next_cursor` is null, or when the task is paused, since nothing moves
        until someone resumes it: read on later with `rows` and the last cursor. A
        cancelled task still finishes the items it had started, within the hour: it
        waits for them too.
        """
        options = RequestOptions(timeout, max_retries, headers)
        while True:
            response = await self._transport.request(
                OPERATIONS["getTaskRows"],
                {"taskId": task_id},
                {"cursor": cursor, "limit": limit},
                options,
                ALIASES,
            )
            page = result(response, GetTaskRowsData.model_validate).data
            items = (page.items or []) if page is not None else []
            for item in items:
                yield item
            cursor = page.next_cursor if page is not None else None
            if page is None or cursor is None:
                return
            if not items:
                if page.status == "paused":
                    return
                await self._transport.sleep(
                    list_pacing(retry_after_header(response.http_response.headers))
                )

    async def wait(
        self,
        task_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[GetTaskData]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[GetTaskData]:
        state = PollState(task_id, timeout, poll_wait, self._transport.clock, "bulk.wait")
        while True:
            wait = state.wait_seconds()
            try:
                response = await state.arun(
                    self._transport.request(
                        OPERATIONS["getTask"],
                        {"taskId": task_id},
                        {"wait": wait},
                        RequestOptions(max_retries=0, headers=headers, deadline=state.deadline),
                        ALIASES,
                    )
                )
            except (APIError, APIConnectionError) as error:
                await self._transport.sleep(state.failure_delay(error))
                continue
            value = result(response, GetTaskData.model_validate)
            state.failures = 0
            state.last_status = value.data.status if value.data is not None else None
            state.remaining()
            if on_poll:
                on_poll(value)
            if value.data is not None and value.data.status not in MOVING_BULK_TASK_STATUSES:
                return value
            pacing = retry_after_header(response.http_response.headers)
            await self._transport.sleep(min(list_pacing(pacing), state.remaining()))


class WebhookDelivery(Model):
    webhook_id: str = Field(alias="webhookId")
    data: object

    @property
    def is_error(self) -> bool:
        code = as_dict(self.data).get("errorCode")
        return isinstance(code, str) and bool(code)


def parse_webhook_delivery(payload: object) -> WebhookDelivery:
    import json

    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, UnicodeDecodeError) as error:
            raise ReverseContactError("Malformed webhook delivery: expected JSON") from error
    value = as_dict(payload)
    webhook_id = value.get("webhookId")
    data = value.get("data")
    if (
        not isinstance(webhook_id, str)
        or not webhook_id.strip()
        or not isinstance(data, (dict, list))
    ):
        raise ReverseContactError(
            "Malformed webhook delivery: expected webhookId and object or array data"
        )
    code = as_dict(data).get("errorCode")
    if code is not None and not isinstance(code, str):
        raise ReverseContactError("Malformed webhook delivery: errorCode must be a string or null")
    return WebhookDelivery(webhookId=webhook_id, data=data)
