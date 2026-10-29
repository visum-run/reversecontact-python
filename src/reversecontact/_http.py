import asyncio
import logging
import math
import os
import platform
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Self
from urllib.parse import quote
from uuid import uuid4

import httpx

from ._models import AppInfo, Model
from ._version import __version__
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    DailyLimitError,
    KeyCreditLimitError,
    RateLimitError,
    ReverseContactError,
    api_error,
    as_dict,
    retry_after_header,
    seconds,
)

logger = logging.getLogger("reversecontact")

Timeout = float | httpx.Timeout


class NotGiven:
    """Distinguish an omitted parameter from an explicit JSON null."""


NOT_GIVEN = NotGiven()


@dataclass(frozen=True)
class RequestOptions:
    timeout: Timeout | None = None
    max_retries: int | None = None
    headers: Mapping[str, str] | None = None
    idempotency_key: str | None = None
    deadline: float | None = None


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    has_body: bool = False
    lookup: bool = False
    idempotency: bool = False
    poll: bool = False


@dataclass
class Response:
    http_response: httpx.Response
    body: object
    no_match: bool = False


def normalize(value: object, aliases: Mapping[str, str]) -> object:
    if isinstance(value, Model):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {
            aliases.get(str(k), str(k)): normalize(v, aliases)
            for k, v in value.items()
            if not isinstance(v, NotGiven)
        }
    if isinstance(value, (list, tuple)):
        return [normalize(v, aliases) for v in value]
    return value


def parameters(known: Mapping[str, object], extra: Mapping[str, object]) -> dict[str, object]:
    return {**{k: v for k, v in known.items() if not isinstance(v, NotGiven)}, **extra}


def backoff(attempt: int) -> float:
    return min(math.pow(2, min(attempt, 4)), 15) + random.uniform(0, 0.25)


def retry_delay(error: APIError, attempt: int, *, idempotent: bool = False) -> float | None:
    if isinstance(error, (DailyLimitError, KeyCreditLimitError)):
        return None
    details = as_dict(error.details)
    hints = (
        retry_after_header(error.headers),
        seconds(details.get("retryAfter")),
        seconds(details.get("retryAfterSeconds")),
    )
    if any(delay is not None and delay > 60 for delay in hints):
        return None
    if isinstance(error, RateLimitError):
        delay = error.retry_after
        if delay is not None:
            return delay if delay <= 60 else None
        return backoff(attempt)
    if error.status in {500, 502, 503, 504} or (
        idempotent and error.status == 409 and error.code == "IDEMPOTENCY_KEY_IN_USE"
    ):
        delay = retry_after_header(error.headers)
        if delay is None:
            delay = seconds(as_dict(error.details).get("retryAfterSeconds"))
        return (delay if delay <= 60 else None) if delay is not None else backoff(attempt)
    return None


def parse_response(response: httpx.Response, op: Operation) -> Response:
    is_json = True
    try:
        body: object = response.json()
    except ValueError:
        body = response.text
        is_json = False
    if not response.is_success:
        error = api_error(response, body, is_json)
        if (
            op.lookup
            and response.status_code == 404
            and error.code not in {"NOT_FOUND", "HTTP_404"}
        ):
            return Response(response, body, no_match=True)
        raise error
    if not is_json:
        if op.idempotency:
            raise APIConnectionError("The response is not JSON", status=response.status_code)
        raise ReverseContactError("The response is not JSON")
    return Response(response, body)


def validate_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(not 32 <= ord(character) <= 126 for character in value)
    ):
        raise ReverseContactError(
            "idempotency_key must contain 1 to 255 printable ASCII characters "
            "with no leading or trailing whitespace"
        )
    return value


def validate_idempotency_headers(headers: Mapping[str, str] | None) -> None:
    for name, value in (headers or {}).items():
        if name.lower() == "idempotency-key":
            validate_idempotency_key(value)


class TransportBase:
    client: httpx.Client | httpx.AsyncClient

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: Timeout,
        max_retries: int,
        default_headers: Mapping[str, str] | None,
        app_info: AppInfo | Mapping[str, str] | None,
        clock: Callable[[], float],
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("RC_API_KEY")
        if not key:
            raise ReverseContactError("Provide api_key or set the RC_API_KEY environment variable.")
        self.base_url = (
            base_url or os.environ.get("RC_BASE_URL") or "https://api.reversecontact.com"
        ).rstrip("/")
        if os.environ.get("RC_LOG", "").lower() == "debug":
            logger.setLevel(logging.DEBUG)
            if not logger.hasHandlers():
                logger.addHandler(logging.StreamHandler())
        self.api_key = key
        self.app_info = app_info
        self.timeout = timeout
        self.max_retries = self.check_retries(max_retries)
        self.clock = clock
        user_agent = (
            f"reversecontact-python/{__version__} "
            f"(python/{platform.python_version()}; {platform.system()})"
        )
        if app_info:
            info = app_info if isinstance(app_info, AppInfo) else AppInfo.model_validate(app_info)
            user_agent += f" {info.name}" + (f"/{info.version}" if info.version else "")
        self.headers = httpx.Headers({"Authorization": f"Bearer {key}", "User-Agent": user_agent})
        validate_idempotency_headers(default_headers)
        self.headers.update(default_headers or {})
        self.headers.pop("X-Request-Origin", None)

    @staticmethod
    def check_retries(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReverseContactError("max_retries must be a non-negative integer")
        return value

    def prepare(
        self,
        op: Operation,
        path_params: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        aliases: Mapping[str, str],
    ) -> tuple[str, dict[str, object], httpx.Headers, httpx.Timeout, int]:
        path = op.path
        for name, value in path_params.items():
            encoded = quote(value, safe="")
            if value in {".", ".."}:
                encoded = value.replace(".", "%2E")
            path = path.replace("{" + name + "}", encoded)
        payload = as_dict(normalize(params, aliases))
        if op.idempotency:
            validate_idempotency_headers(options.headers)
        headers = httpx.Headers(self.headers)
        headers.update(options.headers or {})
        if op.idempotency:
            key = options.idempotency_key
            if key is None:
                key = headers.get("Idempotency-Key")
            if key is None:
                key = self.client.headers.get("Idempotency-Key")
            headers["Idempotency-Key"] = (
                validate_idempotency_key(key) if key is not None else str(uuid4())
            )
        timeout = httpx.Timeout(options.timeout if options.timeout is not None else self.timeout)
        if op.poll:
            wait = payload.get("wait", 0)
            if isinstance(wait, bool) or not isinstance(wait, int) or not 0 <= wait <= 20:
                raise ReverseContactError("wait must be an integer from 0 to 20")
            floor = wait + 15
            for part in ("connect", "read", "write", "pool"):
                current = getattr(timeout, part)
                if current is not None:
                    setattr(timeout, part, max(current, floor))
        if options.deadline is not None:
            remaining = max(0, options.deadline - self.clock())
            for part in ("connect", "read", "write", "pool"):
                current = getattr(timeout, part)
                setattr(timeout, part, remaining if current is None else min(current, remaining))
        retries = (
            self.max_retries
            if options.max_retries is None
            else self.check_retries(options.max_retries)
        )
        return self.base_url + path, payload, headers, timeout, retries

    def request_error(
        self, error: httpx.RequestError, response: httpx.Response | None
    ) -> APIError | APIConnectionError:
        if response is not None and not response.is_success:
            return api_error(response, None, False)
        error_type = (
            APITimeoutError if isinstance(error, httpx.TimeoutException) else APIConnectionError
        )
        return error_type(
            "The request timed out"
            if error_type is APITimeoutError
            else "The API connection failed",
            status=response.status_code if response is not None else None,
        )

    def failure_delay(
        self,
        error: APIError | APIConnectionError,
        op: Operation,
        attempt: int,
        retries: int,
        cause: httpx.RequestError | None = None,
    ) -> float | None:
        if attempt >= retries:
            return None
        if isinstance(error, APIError):
            return retry_delay(error, attempt + 1, idempotent=op.idempotency)
        if error.status is not None:
            safe = op.method == "GET" or op.idempotency
        else:
            safe = (
                op.idempotency
                or op.method in {"GET", "DELETE"}
                or isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout))
            )
        return backoff(attempt + 1) if safe else None

    @staticmethod
    def log(
        op: Operation,
        attempt: int,
        response: httpx.Response | None,
        delay: float | None,
        body: object = None,
    ) -> None:
        # Only explicit diagnostics: never interpolate headers, queries, bodies or exceptions.
        request_id = as_dict(as_dict(body).get("metadata")).get("requestId")
        if request_id is None and response is not None:
            request_id = response.headers.get("x-request-id")
        logger.debug(
            "method=%s path=%s status=%s attempt=%s delay=%s requestId=%s",
            op.method,
            op.path,
            response.status_code if response is not None else None,
            attempt + 1,
            delay,
            request_id,
        )


class SyncTransport(TransportBase):
    client: httpx.Client
    owned: bool
    sleep: Callable[[float], None]

    @classmethod
    def configure(
        cls,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: Timeout,
        max_retries: int,
        default_headers: Mapping[str, str] | None,
        app_info: AppInfo | Mapping[str, str] | None,
        http_client: httpx.Client | None,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> Self:
        instance = cls(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            app_info=app_info,
            clock=clock,
        )
        instance.client = http_client if http_client is not None else httpx.Client()
        instance.owned = http_client is None
        instance.sleep = sleep
        return instance

    def request(
        self,
        op: Operation,
        path_params: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        aliases: Mapping[str, str],
    ) -> Response:
        url, payload, headers, timeout, retries = self.prepare(
            op, path_params, params, options, aliases
        )
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            try:
                request = self.client.build_request(
                    op.method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    json=payload if op.has_body else None,
                    params=None
                    if op.has_body
                    else httpx.QueryParams(
                        {
                            k: str(v).lower() if isinstance(v, bool) else str(v)
                            for k, v in payload.items()
                            if v is not None
                        }
                    ),
                )
                # httpx merges injected client defaults during build_request.
                request.headers.pop("X-Request-Origin", None)
                response = self.client.send(request, stream=True)
                response.read()
                parsed = parse_response(response, op)
                self.log(op, attempt, response, None, parsed.body)
                return parsed
            except httpx.RequestError as exc:
                error = self.request_error(exc, response)
                delay = self.failure_delay(error, op, attempt, retries, exc)
                self.log(op, attempt, response, delay)
                if delay is None:
                    raise error from exc
            except (APIError, APIConnectionError) as exc:
                delay = self.failure_delay(exc, op, attempt, retries)
                self.log(
                    op, attempt, response, delay, exc.body if isinstance(exc, APIError) else None
                )
                if delay is None:
                    raise
            except ReverseContactError:
                self.log(op, attempt, response, None)
                raise
            finally:
                if response is not None:
                    response.close()
            self.sleep(delay)
        raise AssertionError("Unreachable retry state")

    def close(self) -> None:
        if self.owned:
            self.client.close()


class AsyncTransport(TransportBase):
    client: httpx.AsyncClient
    owned: bool
    sleep: Callable[[float], Awaitable[None]]

    @classmethod
    def configure(
        cls,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout: Timeout,
        max_retries: int,
        default_headers: Mapping[str, str] | None,
        app_info: AppInfo | Mapping[str, str] | None,
        http_client: httpx.AsyncClient | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> Self:
        instance = cls(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            app_info=app_info,
            clock=clock,
        )
        instance.client = http_client if http_client is not None else httpx.AsyncClient()
        instance.owned = http_client is None
        instance.sleep = sleep
        return instance

    async def request(
        self,
        op: Operation,
        path_params: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        aliases: Mapping[str, str],
    ) -> Response:
        url, payload, headers, timeout, retries = self.prepare(
            op, path_params, params, options, aliases
        )
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            try:
                request = self.client.build_request(
                    op.method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    json=payload if op.has_body else None,
                    params=None
                    if op.has_body
                    else httpx.QueryParams(
                        {
                            k: str(v).lower() if isinstance(v, bool) else str(v)
                            for k, v in payload.items()
                            if v is not None
                        }
                    ),
                )
                # httpx merges injected client defaults during build_request.
                request.headers.pop("X-Request-Origin", None)
                response = await self.client.send(request, stream=True)
                await response.aread()
                parsed = parse_response(response, op)
                self.log(op, attempt, response, None, parsed.body)
                return parsed
            except httpx.RequestError as exc:
                error = self.request_error(exc, response)
                delay = self.failure_delay(error, op, attempt, retries, exc)
                self.log(op, attempt, response, delay)
                if delay is None:
                    raise error from exc
            except (APIError, APIConnectionError) as exc:
                delay = self.failure_delay(exc, op, attempt, retries)
                self.log(
                    op, attempt, response, delay, exc.body if isinstance(exc, APIError) else None
                )
                if delay is None:
                    raise
            except ReverseContactError:
                self.log(op, attempt, response, None)
                raise
            finally:
                if response is not None:
                    await response.aclose()
            await self.sleep(delay)
        raise AssertionError("Unreachable retry state")

    async def close(self) -> None:
        if self.owned:
            await self.client.aclose()
