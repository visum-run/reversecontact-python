from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """Forward-compatible API model, preserving extra fields and explicit nulls."""

    model_config = ConfigDict(populate_by_name=True, extra="allow", arbitrary_types_allowed=True)

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_unset=True, mode="json")


class LimitInfo(Model):
    limit: float | None = None
    used: float | None = None
    left: float | None = None
    next_reset: str | None = Field(None, alias="nextReset")


class Credits(Model):
    total: float | None = None
    used: float | None = None
    left: float | None = None


class WorkspaceQuota(Model):
    id: str | None = None
    credits: Credits | None = None
    has_unlimited_credits: bool | None = Field(None, alias="hasUnlimitedCredits")
    allow_daily_overcost: bool | None = Field(None, alias="allowDailyOvercost")
    minute_rate_limit: LimitInfo | None = Field(None, alias="minuteRateLimit")
    daily_limit: LimitInfo | None = Field(None, alias="dailyLimit")


class KeyCreditLimits(Model):
    daily: LimitInfo | None = None
    monthly: LimitInfo | None = None
    total: LimitInfo | None = None


class KeyQuota(Model):
    id: str | None = None
    daily_limit: LimitInfo | None = Field(None, alias="dailyLimit")
    minute_rate_limit: LimitInfo | None = Field(None, alias="minuteRateLimit")
    credit_limits: KeyCreditLimits | None = Field(None, alias="creditLimits")


class Quotas(Model):
    credits_consumed: float | None = Field(None, alias="creditsConsumed")
    workspace: WorkspaceQuota | None = None
    key: KeyQuota | None = None


class Metadata(Model):
    request_id: str | None = Field(None, alias="requestId")
    execution_time_ms: float | None = Field(None, alias="executionTimeMs")
    updated_at: str | None = Field(None, alias="updatedAt")


T = TypeVar("T")


class Result(Model, Generic[T]):
    data: T | None = None
    quotas: Quotas | None = None
    metadata: Metadata | None = None
    http_response: httpx.Response = Field(exclude=True)


class ContactValue(Model):
    value: str | None = None
    type: str | None = None


class JobSnapshot(Model, Generic[T]):
    webhook_id: str | None = Field(None, alias="webhookId")
    status: str | None = None
    result: T | None = None
    event_type: str | None = Field(None, alias="eventType")
    error_code: str | None = Field(None, alias="errorCode")
    credits_consumed: float | None = Field(None, alias="creditsConsumed")
    delivery_status: str | None = Field(None, alias="deliveryStatus")
    created_at: str | None = Field(None, alias="createdAt")


class AppInfo(Model):
    name: str
    version: str | None = None
    url: str | None = None
