from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, TypeVar

from ._http import AsyncTransport, Operation, RequestOptions, Response, SyncTransport
from ._models import Metadata, Quotas, Result
from ._operations import ALIASES
from ._pagination import AsyncPage, Page, iteration_params
from .errors import ReverseContactError, as_dict

T = TypeVar("T")


def result(response: Response, parser: Callable[[object], T]) -> Result[T]:
    body = as_dict(response.body)
    return Result[T](
        data=parser(body["data"]) if body.get("data") is not None else None,
        quotas=Quotas.model_validate(body["quotas"]) if body.get("quotas") is not None else None,
        metadata=Metadata.model_validate(body["metadata"])
        if body.get("metadata") is not None
        else None,
        http_response=response.http_response,
    )


class SyncResource:
    def __init__(self, transport: SyncTransport) -> None:
        self._transport = transport

    def _result(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Result[T]:
        return result(self._transport.request(op, paths, params, options, ALIASES), parser)

    def _lookup(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Result[T] | None:
        response = self._transport.request(op, paths, params, options, ALIASES)
        return None if response.no_match else result(response, parser)

    def _page(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Page[T]:
        params = deepcopy(dict(params))
        paths = dict(paths)
        response = self._transport.request(op, paths, params, options, ALIASES)
        return Page(
            response,
            parser,
            lambda cursor: self._page(op, paths, {**params, "cursor": cursor}, options, parser),
            operation=op,
        )

    def _iterate(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
        max_results: int,
    ) -> Iterator[T | None]:
        query, size = iteration_params(params, max_results)
        paths = dict(paths)

        def iterate() -> Iterator[T | None]:
            delivered = 0
            cursor = query.get("cursor")
            seen: set[str] = {cursor} if isinstance(cursor, str) else set()
            while delivered < max_results:
                query["perPage"] = min(size, max_results - delivered)
                page = self._page(op, paths, query, options, parser)
                for item in page.data:
                    if delivered >= max_results:
                        return
                    delivered += 1
                    yield item
                if page.next_cursor is None:
                    return
                if page.next_cursor in seen:
                    raise ReverseContactError("The API repeated a pagination cursor")
                seen.add(page.next_cursor)
                query["cursor"] = page.next_cursor

        return iterate()

    def _job(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Job[T]:
        response = self._transport.request(op, paths, params, options, ALIASES)
        from ._jobs import Job

        return Job(self._transport, response, parser)


class AsyncResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def _result(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Result[T]:
        return result(await self._transport.request(op, paths, params, options, ALIASES), parser)

    async def _lookup(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> Result[T] | None:
        response = await self._transport.request(op, paths, params, options, ALIASES)
        return None if response.no_match else result(response, parser)

    async def _page(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> AsyncPage[T]:
        params = deepcopy(dict(params))
        paths = dict(paths)
        response = await self._transport.request(op, paths, params, options, ALIASES)
        return AsyncPage(
            response,
            parser,
            lambda cursor: self._page(op, paths, {**params, "cursor": cursor}, options, parser),
            operation=op,
        )

    def _iterate(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
        max_results: int,
    ) -> AsyncIterator[T | None]:
        query, size = iteration_params(params, max_results)
        paths = dict(paths)

        async def iterate() -> AsyncIterator[T | None]:
            delivered = 0
            cursor = query.get("cursor")
            seen: set[str] = {cursor} if isinstance(cursor, str) else set()
            while delivered < max_results:
                query["perPage"] = min(size, max_results - delivered)
                page = await self._page(op, paths, query, options, parser)
                for item in page.data:
                    if delivered >= max_results:
                        return
                    delivered += 1
                    yield item
                if page.next_cursor is None:
                    return
                if page.next_cursor in seen:
                    raise ReverseContactError("The API repeated a pagination cursor")
                seen.add(page.next_cursor)
                query["cursor"] = page.next_cursor

        return iterate()

    async def _job(
        self,
        op: Operation,
        paths: Mapping[str, str],
        params: Mapping[str, object],
        options: RequestOptions,
        parser: Callable[[object], T],
    ) -> AsyncJob[T]:
        response = await self._transport.request(op, paths, params, options, ALIASES)
        from ._jobs import AsyncJob

        return AsyncJob(self._transport, response, parser)


if TYPE_CHECKING:
    from ._jobs import AsyncJob, Job
