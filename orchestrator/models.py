from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Worker(Base):
    hostname: Mapped[str] = mapped_column(default="")
    runtime: Mapped[dict] = mapped_column(JSON, default=dict)
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scopes: Mapped[list] = mapped_column(JSON)
    zones: Mapped[list] = mapped_column(JSON, default=list)
    concurrency: Mapped[int]
    heartbeat: Mapped[float] = mapped_column(default=0)
    timezone: Mapped[str] = mapped_column(default="")
    token_hash: Mapped[str] = mapped_column(default="")
    token_env: Mapped[str] = mapped_column(default="")


class Host(Base):
    __tablename__ = "hosts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str]
    zone: Mapped[str] = mapped_column(default="")
    enabled: Mapped[bool]
    preferred_worker: Mapped[str]
    fallback_worker: Mapped[str]
    parameters: Mapped[dict] = mapped_column(JSON)
    discovered: Mapped[bool] = mapped_column(default=False)
    deleted: Mapped[bool] = mapped_column(default=False)
    source_id: Mapped[str] = mapped_column(default="")


class Collector(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "collectors"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    scope: Mapped[str]
    script: Mapped[str]
    interval_seconds: Mapped[int]
    timeout_seconds: Mapped[int]
    max_retries: Mapped[int]
    enabled: Mapped[bool]
    parameters: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    legacy_schedule: Mapped[bool] = mapped_column(default=True)


class Window(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "windows"
    __table_args__ = (UniqueConstraint("host_id", "collector_id", "period_start"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id"))
    collector_id: Mapped[str] = mapped_column(ForeignKey("collectors.id"))
    period_start: Mapped[str] = mapped_column(index=True)
    period_end: Mapped[str]
    status: Mapped[str] = mapped_column(index=True)
    available_at: Mapped[float] = mapped_column(default=0)
    retry_base: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[float]
    snapshot: Mapped[dict] = mapped_column(JSON)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("window_id", "number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(ForeignKey("windows.id"))
    worker_id: Mapped[str]
    number: Mapped[int]
    status: Mapped[str]
    started_at: Mapped[float]
    finished_at: Mapped[float | None]
    lease_until: Mapped[float]
    result: Mapped[dict | None] = mapped_column(JSON)
    logs: Mapped[str] = mapped_column(default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)


class HostGroup(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "host_groups"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    host_ids: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted: Mapped[bool] = mapped_column(default=False)


class Schedule(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    collector_id: Mapped[str] = mapped_column(ForeignKey("collectors.id"))
    host_group_id: Mapped[str] = mapped_column(ForeignKey("host_groups.id"))
    kind: Mapped[str] = mapped_column(default="interval")
    local_time: Mapped[str] = mapped_column(default="07:00")
    created_at: Mapped[float] = mapped_column(default=0)
    host_group_ids: Mapped[list] = mapped_column(JSON, default=list)
    zones: Mapped[list] = mapped_column(JSON, default=list)
    interval_seconds: Mapped[int]
    enabled: Mapped[bool] = mapped_column(default=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)


class InventorySource(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "inventory_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    kind: Mapped[str]
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)


class InventorySync(Base):
    zone: Mapped[str] = mapped_column(String, default="", index=True)
    __tablename__ = "inventory_syncs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str]
    created_at: Mapped[float]
    status: Mapped[str] = mapped_column(default="PREVIEW")
    payload: Mapped[dict] = mapped_column(JSON)


class Scope(Base):
    __tablename__ = "scopes"
    id: Mapped[str] = mapped_column(String, primary_key=True)


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(default="viewer")
    zones: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
