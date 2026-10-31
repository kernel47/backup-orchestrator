"""Shared validation for HTML and API collection filters."""

from datetime import timedelta

from fastapi import HTTPException, Request

from . import service as svc
from .workspaces import current_zone


def filters(
    request: Request,
    date: str = "",
    zone: str = "",
    scope: str = "",
    collector_id: str = "",
    host: str = "",
    status: str = "",
    worker: str = "",
    host_group_id: str = "",
    schedule_id: str = "",
    start: str = "",
    end: str = "",
    hour: str = "",
):
    zone = current_zone.get() or zone
    legacy = request.query_params.get("technology", "")
    if legacy:
        if scope and scope != legacy:
            raise HTTPException(422, "Conflicting scope and legacy technology filters")
        scope = legacy
    if "region" in request.query_params:
        raise HTTPException(422, "Use the zone filter instead of region")
    if date:
        try:
            svc.bounds(date)
        except ValueError:
            raise HTTPException(422, "Expected date: YYYY-MM-DD")
    if hour:
        if not hour.isdigit() or not 0 <= int(hour) < 24:
            raise HTTPException(422, "Hour must be between 0 and 23")
        anchor = svc.dt((date or svc.now().strftime("%Y-%m-%d")) + "T00:00:00Z")
        if request.query_params.get("period", "day") == "day":
            start = svc.iso(anchor + timedelta(hours=int(hour)))
            end = svc.iso(anchor + timedelta(hours=int(hour) + 1))
    return {
        k: v
        for k, v in {
            "date": date,
            "zone": zone,
            "scope": scope,
            "collector_id": collector_id,
            "host": host,
            "status": status,
            "worker": worker,
            "host_group_id": host_group_id,
            "schedule_id": schedule_id,
            "start": start,
            "end": end,
        }.items()
        if v
    }
