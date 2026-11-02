"""Shared UTC and database-record serialization for the execution domain."""

from datetime import datetime, timedelta, timezone

from .models import Worker

UTC = timezone.utc


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def dt(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC timezone required")
    return result.astimezone(UTC)


def dump(obj):
    data = {
        col.name: getattr(obj, col.name)
        for col in obj.__table__.columns
        if col.name not in ("token_hash", "token_env")
    }
    if isinstance(obj, Worker):
        data["zone"] = obj.zones[0] if len(obj.zones) == 1 else None
    return data


def bounds(day):
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return start, start + timedelta(days=1)
