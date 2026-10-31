from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .cadences import INTERVALS


class CollectorResult(BaseModel):
    """Small execution report; business payloads belong at the collector's destination."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["SUCCESS", "FAILURE", "UNKNOWN", "TIMEOUT"]
    summary: str = Field(min_length=1, max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    attempt_number: int | None = Field(default=None, ge=1)
    task_id: str | None = None
    collector_id: str | None = None
    host: str | None = None
    worker_id: str | None = None
    records_collected: int = Field(default=0, ge=0)
    records_sent: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=2000)
    # Transport metadata filled by the worker; collectors need not supply it.
    period_start: datetime | None = None
    period_end: datetime | None = None

    @field_validator("details")
    @classmethod
    def small_details(cls, value):
        import json

        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("Report details exceed 16 KB")
        return value

    @field_validator(
        "collected_at", "period_start", "period_end", "started_at", "finished_at"
    )
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Timezone-aware date required")
        return value

    @model_validator(mode="after")
    def execution_dates(self):
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        return self


class WorkerRequest(BaseModel):
    worker_id: str


class WorkerScriptInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=80)
    kind: Literal["command", "python"]
    available: bool


class WorkerRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(max_length=255)
    python_version: str = Field(max_length=32)
    master_endpoint: str = Field(max_length=512)
    concurrency: int = Field(ge=1, le=32)
    scripts: list[WorkerScriptInfo] = Field(default_factory=list, max_length=64)

    @field_validator("master_endpoint")
    @classmethod
    def endpoint_without_credentials(cls, value):
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError(
                "Report only the HTTP endpoint origin, without credentials or path"
            )
        return value


class WorkerHeartbeat(WorkerRequest):
    hostname: str | None = Field(default=None, max_length=255)
    runtime: WorkerRuntime | None = None
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        if value is not None:
            from .scheduling import valid_timezone

            valid_timezone(value)
        return value


class ResultRequest(WorkerRequest):
    result: CollectorResult
    logs: str = Field(default="", max_length=20000)


class RetryRequest(BaseModel):
    mode: Literal["retry", "rerun"] = "retry"

    @field_validator("filters", check_fields=False)
    @classmethod
    def zone_filters_only(cls, value):
        if "region" in value:
            raise ValueError("Use zone instead of region")
        if "technology" in value:
            value = dict(value)
            old = value.pop("technology")
            if value.get("scope", old) != old:
                raise ValueError("Conflicting scope filters")
            value["scope"] = old
        return value

    ids: list[str] | None = None
    filters: dict[str, str] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collector_id: str
    host_group_id: str = ""
    schedule_id: str = ""
    date: str
    zone: str = ""
    host: str = ""
    parameters: dict = Field(default_factory=dict)


class CollectorUpdate(BaseModel):
    interval_seconds: int

    @field_validator("interval_seconds")
    @classmethod
    def supported_cadence(cls, value):
        if value not in INTERVALS:
            raise ValueError("Schedule missing from /api/catalog")
        return value

    timeout_seconds: int = Field(ge=1, le=3600)
    max_retries: int = Field(ge=0, le=10)
    enabled: bool
    parameters: dict = Field(default_factory=dict)


class CollectorCreate(CollectorUpdate):
    zone: str = ""
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=1, max_length=60)
    scope: str = Field(validation_alias=AliasChoices("scope", "technology"))
    script: str


class HostGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    host_ids: list[str] = Field(min_length=1, max_length=10000)
    enabled: bool = True


class ScheduleInput(BaseModel):
    kind: Literal["interval", "daily_local"] = "interval"
    local_time: str = Field(default="07:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

    @model_validator(mode="after")
    def daily_cadence(self):
        if self.kind == "daily_local":
            self.interval_seconds = 86400
        return self

    name: str = Field(min_length=1, max_length=80)
    collector_id: str
    host_group_id: str = ""
    host_group_ids: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    interval_seconds: int
    enabled: bool = True
    parameters: dict = Field(default_factory=dict)

    @field_validator("interval_seconds")
    @classmethod
    def supported_interval(cls, value):
        if value not in INTERVALS:
            raise ValueError("Unsupported schedule interval")
        return value


class GroupRunRequest(BaseModel):
    collector_id: str
    host_group_id: str = ""
    host_group_ids: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
