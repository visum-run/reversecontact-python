from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Generic, TypeVar

from ._http import Operation, Response
from ._models import Metadata, Quotas
from .errors import ReverseContactError, as_dict
from .types import SearchOrganizationsPromptDataInterpretation, SearchPeoplePromptDataInterpretation

T = TypeVar("T")


class PageBase(Generic[T]):
    def __init__(
        self,
        response: Response,
        parser: Callable[[object], T],
        operation: Operation | None = None,
    ) -> None:
        envelope = as_dict(response.body)
        payload = as_dict(envelope.get("data"))
        paging = as_dict(payload.get("metadata"))
        items = payload.get("data")
        self.data = (
            [parser(item) if item is not None else None for item in items]
            if isinstance(items, list)
            else []
        )
        cursor = paging.get("nextCursor")
        self.next_cursor = cursor if isinstance(cursor, str) else None
        self.http_response = response.http_response
        self.quotas = (
            Quotas.model_validate(envelope["quotas"])
            if envelope.get("quotas") is not None
            else None
        )
        self.metadata = (
            Metadata.model_validate(envelope["metadata"])
            if envelope.get("metadata") is not None
            else None
        )
        self.count = number(paging.get("count"))
        self.per_page = number(paging.get("perPage"))
        self.interpretation: (
            SearchPeoplePromptDataInterpretation
            | SearchOrganizationsPromptDataInterpretation
            | None
        ) = None
        if isinstance(payload.get("interpretation"), dict):
            if operation is not None and "/organizations" in operation.path:
                self.interpretation = SearchOrganizationsPromptDataInterpretation.model_validate(
                    payload["interpretation"]
                )
            else:
                self.interpretation = SearchPeoplePromptDataInterpretation.model_validate(
                    payload["interpretation"]
                )
        self.extras = {k: v for k, v in payload.items() if k not in {"data", "metadata"}}

    def has_next_page(self) -> bool:
        return self.next_cursor is not None


class Page(PageBase[T]):
    def __init__(
        self,
        response: Response,
        parser: Callable[[object], T],
        fetch: Callable[[str], Page[T]],
        *,
        operation: Operation | None = None,
    ) -> None:
        super().__init__(response, parser, operation)
        self._fetch = fetch

    def get_next_page(self) -> Page[T] | None:
        return self._fetch(self.next_cursor) if self.next_cursor is not None else None


class AsyncPage(PageBase[T]):
    def __init__(
        self,
        response: Response,
        parser: Callable[[object], T],
        fetch: Callable[[str], Awaitable[AsyncPage[T]]],
        *,
        operation: Operation | None = None,
    ) -> None:
        super().__init__(response, parser, operation)
        self._fetch = fetch

    async def get_next_page(self) -> AsyncPage[T] | None:
        return await self._fetch(self.next_cursor) if self.next_cursor is not None else None


def number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def iteration_params(
    params: Mapping[str, object], max_results: int
) -> tuple[dict[str, object], int]:
    if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 0:
        raise ReverseContactError("max_results must be a non-negative integer")
    result = deepcopy(dict(params))
    size = result.pop("per_page", result.get("perPage", 25))
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 100:
        raise ReverseContactError("per_page must be an integer from 1 to 100")
    return result, size
