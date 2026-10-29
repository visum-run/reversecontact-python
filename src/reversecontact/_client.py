import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Self

import httpx

from ._http import (
    NOT_GIVEN,
    AsyncTransport,
    NotGiven,
    SyncTransport,
    Timeout,
    validate_idempotency_headers,
)
from ._models import AppInfo
from .resources import async_, sync


class ReverseContact:
    """Synchronous Reverse Contact client. Timeouts are expressed in seconds."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: Timeout = 90,
        max_retries: int = 4,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
        app_info: AppInfo | Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = SyncTransport.configure(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
            app_info=app_info,
            sleep=sleep,
            clock=clock,
        )
        self.enrich = sync.Enrich(self._transport)
        self.fetch = sync.Fetch(self._transport)
        self.search = sync.Search(self._transport)
        self.contact = sync.Contact(self._transport)
        self.jobs = sync.Jobs(self._transport)
        self.usage = sync.Usage(self._transport)

    def with_options(
        self,
        *,
        api_key: str | NotGiven = NOT_GIVEN,
        base_url: str | NotGiven = NOT_GIVEN,
        timeout: Timeout | NotGiven = NOT_GIVEN,
        max_retries: int | NotGiven = NOT_GIVEN,
        default_headers: Mapping[str, str] | None = None,
        app_info: AppInfo | Mapping[str, str] | NotGiven | None = NOT_GIVEN,
        sleep: Callable[[float], None] | NotGiven = NOT_GIVEN,
        clock: Callable[[], float] | NotGiven = NOT_GIVEN,
    ) -> Self:
        """Copy options while sharing the connection pool; closing the copy leaves it open."""
        transport = self._transport
        headers = httpx.Headers(transport.headers)
        # Rebuild these two defaults when their source option is overridden.
        if not isinstance(api_key, NotGiven):
            headers.pop("Authorization", None)
        if not isinstance(app_info, NotGiven):
            headers.pop("User-Agent", None)
        validate_idempotency_headers(default_headers)
        headers.update(default_headers or {})
        return type(self)(
            api_key=transport.api_key if isinstance(api_key, NotGiven) else api_key,
            base_url=transport.base_url if isinstance(base_url, NotGiven) else base_url,
            timeout=transport.timeout if isinstance(timeout, NotGiven) else timeout,
            max_retries=transport.max_retries if isinstance(max_retries, NotGiven) else max_retries,
            default_headers=headers,
            http_client=transport.client,
            app_info=transport.app_info if isinstance(app_info, NotGiven) else app_info,
            sleep=transport.sleep if isinstance(sleep, NotGiven) else sleep,
            clock=transport.clock if isinstance(clock, NotGiven) else clock,
        )

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncReverseContact:
    """Asynchronous client with the same resources as ReverseContact."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: Timeout = 90,
        max_retries: int = 4,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        app_info: AppInfo | Mapping[str, str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = AsyncTransport.configure(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
            app_info=app_info,
            sleep=sleep,
            clock=clock,
        )
        self.enrich = async_.AsyncEnrich(self._transport)
        self.fetch = async_.AsyncFetch(self._transport)
        self.search = async_.AsyncSearch(self._transport)
        self.contact = async_.AsyncContact(self._transport)
        self.jobs = async_.AsyncJobs(self._transport)
        self.usage = async_.AsyncUsage(self._transport)

    def with_options(
        self,
        *,
        api_key: str | NotGiven = NOT_GIVEN,
        base_url: str | NotGiven = NOT_GIVEN,
        timeout: Timeout | NotGiven = NOT_GIVEN,
        max_retries: int | NotGiven = NOT_GIVEN,
        default_headers: Mapping[str, str] | None = None,
        app_info: AppInfo | Mapping[str, str] | NotGiven | None = NOT_GIVEN,
        sleep: Callable[[float], Awaitable[None]] | NotGiven = NOT_GIVEN,
        clock: Callable[[], float] | NotGiven = NOT_GIVEN,
    ) -> Self:
        """Copy options while sharing the connection pool; closing the copy leaves it open."""
        transport = self._transport
        headers = httpx.Headers(transport.headers)
        # Rebuild these two defaults when their source option is overridden.
        if not isinstance(api_key, NotGiven):
            headers.pop("Authorization", None)
        if not isinstance(app_info, NotGiven):
            headers.pop("User-Agent", None)
        validate_idempotency_headers(default_headers)
        headers.update(default_headers or {})
        return type(self)(
            api_key=transport.api_key if isinstance(api_key, NotGiven) else api_key,
            base_url=transport.base_url if isinstance(base_url, NotGiven) else base_url,
            timeout=transport.timeout if isinstance(timeout, NotGiven) else timeout,
            max_retries=transport.max_retries if isinstance(max_retries, NotGiven) else max_retries,
            default_headers=headers,
            http_client=transport.client,
            app_info=transport.app_info if isinstance(app_info, NotGiven) else app_info,
            sleep=transport.sleep if isinstance(sleep, NotGiven) else sleep,
            clock=transport.clock if isinstance(clock, NotGiven) else clock,
        )

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
