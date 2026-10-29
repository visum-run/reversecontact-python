import asyncio
from collections.abc import Awaitable, Callable, Mapping
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
from ._models import ContactValue, JobSnapshot, Model, Result
from ._operations import ALIASES, OPERATIONS
from ._resource import AsyncResource, SyncResource, result
from .errors import (
    APIConnectionError,
    APIError,
    JobFailedError,
    JobTimeoutError,
    ReverseContactError,
    as_dict,
    retry_after_header,
    seconds,
)
from .types import JobAckData

T = TypeVar("T")


def parse_contacts(value: object) -> list[ContactValue | None]:
    return (
        [ContactValue.model_validate(item) if item is not None else None for item in value]
        if isinstance(value, list)
        else []
    )


def snapshot(response: Response, parser: Callable[[object], T]) -> Result[JobSnapshot[T]]:
    def parse(value: object) -> JobSnapshot[T]:
        data = dict(as_dict(value))
        if data.get("result") is not None:
            data["result"] = parser(data["result"])
        return JobSnapshot[T].model_validate(data)

    return result(response, parse)


class PollState:
    def __init__(
        self, job_id: str, timeout: float, poll_wait: int, clock: Callable[[], float]
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
        self.job_id = job_id
        self.clock = clock
        self.deadline = clock() + duration
        self.poll_wait = poll_wait
        self.last_status: str | None = None
        self.failures = 0

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise JobTimeoutError(self.job_id, self.last_status)
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
            raise JobTimeoutError(self.job_id, self.last_status) from error


def terminal(job_id: str, value: JobSnapshot[T]) -> bool:
    if value.status == "errored":
        raise JobFailedError(job_id, value.error_code, value.credits_consumed, value.result)
    return value.status == "succeeded"


class JobBase(Generic[T]):
    def __init__(self, response: Response, parser: Callable[[object], T]) -> None:
        self.data = JobAckData.model_validate(as_dict(response.body).get("data") or {})
        job_id = self.data.webhook_id
        if not job_id:
            raise ReverseContactError("The job acknowledgement is missing webhookId")
        self.id = job_id
        self.webhook_id = job_id
        self.status = self.data.status
        self.poll_url = self.data.poll_url
        self.http_response = response.http_response
        self.replayed: bool = (
            self.http_response.headers.get("Idempotent-Replayed", "").lower() == "true"
        )
        envelope = result(response, JobAckData.model_validate)
        self.quotas = envelope.quotas
        self.metadata = envelope.metadata
        self._parser = parser


class Job(JobBase[T]):
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
    ) -> Result[JobSnapshot[T]]:
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
        on_poll: Callable[[Result[JobSnapshot[T]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[JobSnapshot[T]]:
        """Wait using HTTP timeouts capped by the remaining overall deadline.

        Synchronous polling runs in the caller's thread. The deadline may be
        exceeded by one bounded HTTP phase before JobTimeoutError is raised.
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


class AsyncJob(JobBase[T]):
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
    ) -> Result[JobSnapshot[T]]:
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
        on_poll: Callable[[Result[JobSnapshot[T]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[JobSnapshot[T]]:
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


def existing(job_id: str) -> Response:
    # Construct a local acknowledgement; resuming never creates a remote job.
    import httpx

    return Response(httpx.Response(200), {"data": {"webhookId": job_id}})


class JobsHelpers(SyncResource):
    def wait(
        self,
        job_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[JobSnapshot[object]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[JobSnapshot[object]]:
        """Resume polling in the caller's thread with bounded HTTP timeouts.

        The overall deadline may be exceeded by one bounded HTTP phase before
        JobTimeoutError is raised, preserving the job id and last status.
        """
        return Job(self._transport, existing(job_id), lambda value: value).wait(
            timeout=timeout, poll_wait=poll_wait, on_poll=on_poll, headers=headers
        )


class AsyncJobsHelpers(AsyncResource):
    async def wait(
        self,
        job_id: str,
        *,
        timeout: float = 1800,
        poll_wait: int = 20,
        on_poll: Callable[[Result[JobSnapshot[object]]], None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Result[JobSnapshot[object]]:
        return await AsyncJob(self._transport, existing(job_id), lambda value: value).wait(
            timeout=timeout, poll_wait=poll_wait, on_poll=on_poll, headers=headers
        )


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
