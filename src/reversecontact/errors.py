import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast

import httpx


class ReverseContactError(Exception):
    """Base class for SDK errors."""


def as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def retry_after_header(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    numeric = seconds(value)
    if numeric is not None or not value:
        return numeric
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return float(max(0, (date - datetime.now(UTC)).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None


class APIError(ReverseContactError):
    def __init__(self, response: httpx.Response, body: object, *, is_json: bool = True) -> None:
        payload = as_dict(body)
        error = payload.get("error")
        fields = as_dict(error)
        self.status = response.status_code
        self.code = str(
            fields.get("code") or (error if isinstance(error, str) else f"HTTP_{self.status}")
        )
        self.message = str(
            fields.get("message")
            or payload.get("message")
            or (f"HTTP {self.status}" if is_json else "The response is not JSON")
        )
        self.details = fields.get("details")
        details = as_dict(self.details)
        self.request_id = as_dict(payload.get("metadata")).get("requestId")
        self.headers = response.headers
        self.body = body
        self.http_response = response
        self.alternative = payload.get("alternative", details.get("alternative"))
        self.retry_after = seconds(details.get("retryAfter"))
        if self.retry_after is None:
            self.retry_after = retry_after_header(response.headers)
        self.reset_at = details.get("resetAt")
        super().__init__(self.message)


class BadRequestError(APIError):
    pass


class AuthenticationError(APIError):
    pass


class InsufficientCreditsError(APIError):
    pass


class PermissionDeniedError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    pass


class GoneError(APIError):
    pass


class UnprocessableEntityError(APIError):
    pass


class RateLimitError(APIError):
    pass


class DailyLimitError(RateLimitError):
    pass


class KeyCreditLimitError(APIError):
    def __init__(self, response: httpx.Response, body: object, *, is_json: bool = True) -> None:
        super().__init__(response, body, is_json=is_json)
        details = as_dict(self.details)
        self.period = details.get("period")
        self.limit = details.get("limit")
        self.used = details.get("used")
        self.required = details.get("required")


class UnavailableForLegalReasonsError(APIError):
    pass


class InternalServerError(APIError):
    pass


class APIConnectionError(ReverseContactError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


class APITimeoutError(APIConnectionError):
    pass


class JobFailedError(ReverseContactError):
    def __init__(
        self,
        job_id: str,
        error_code: str | None,
        credits_consumed: int | float | None,
        result: object,
    ) -> None:
        self.job_id = job_id
        self.error_code = error_code
        self.credits_consumed = credits_consumed
        self.result = result
        self.not_found = error_code in {
            "data-not-found",
            "person-not-found",
            "company-not-found",
            "email-not-found",
            "phone-not-found",
        }
        super().__init__(f"Job {job_id} failed: {error_code or 'unknown error'}")


class JobTimeoutError(ReverseContactError):
    def __init__(self, job_id: str, last_status: str | None) -> None:
        self.job_id = job_id
        self.last_status = last_status
        super().__init__(
            f"Timed out waiting for job {job_id}. Resume with jobs.wait using this id."
        )


def api_error(response: httpx.Response, body: object, is_json: bool) -> APIError:
    code = as_dict(as_dict(body).get("error")).get("code")
    error_type: type[APIError]
    if code == "KEY_CREDIT_LIMIT_REACHED":
        error_type = KeyCreditLimitError
    elif response.status_code == 429 and code in {
        "DAILY_LIMIT_EXCEEDED",
        "DAILY_EXTRACTION_LIMIT_EXCEEDED",
    }:
        error_type = DailyLimitError
    else:
        error_type = {
            400: BadRequestError,
            401: AuthenticationError,
            402: InsufficientCreditsError,
            403: PermissionDeniedError,
            404: NotFoundError,
            409: ConflictError,
            410: GoneError,
            422: UnprocessableEntityError,
            429: RateLimitError,
            451: UnavailableForLegalReasonsError,
        }.get(
            response.status_code,
            InternalServerError if 500 <= response.status_code < 600 else APIError,
        )
    return error_type(response, body, is_json=is_json)
