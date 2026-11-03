"""Collection history, replay requests and worker task protocol."""

import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select

from . import db
from . import service as svc
from .config import settings
from .filters import filters
from .history_queries import HistoryStore, window_query
from .models import Attempt, Collector, Host, HostGroup, Schedule, Window, Worker
from .records import dt, iso
from .schemas import (
    BackfillRequest,
    PlanRequest,
    ResultRequest,
    RetryRequest,
    WorkerHeartbeat,
    WorkerRequest,
)
from .security import admin, worker_auth
from .workspaces import current_zone, require_zone

router = APIRouter()


def refresh_legacy_history(session, selected):
    # Preserve the legacy API's expectation planning; dashboard reads do not use it.
    svc.recover(session)
    if selected.get("date"):
        svc.expected(
            session,
            selected["date"],
            selected.get("collector_id"),
            selected.get("host", ""),
            zone=selected.get("zone", ""),
        )


@router.get("/api/collections")
def collections(
    response: Response,
    f: dict = Depends(filters),
    limit: int = Query(1000, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    with db.transaction() as session:
        refresh_legacy_history(session, f)
        total = session.scalar(window_query(f).with_only_columns(func.count(Window.id)))
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Limit"] = str(limit)
        response.headers["X-Offset"] = str(offset)
        response.headers["X-Has-More"] = str(offset + limit < total).lower()
        return svc.rows(session, f, limit=limit, offset=offset)


@router.get("/api/collections/summary")
def summary(f: dict = Depends(filters)):
    f.pop("status", None)
    with db.transaction() as session:
        refresh_legacy_history(session, f)
        counts, _ = HistoryStore(session).summary(f)
    counts.pop("RECOVERED", None)
    return {
        "expected": sum(
            v for k, v in counts.items() if k not in ("SCHEDULED", "DISABLED")
        ),
        "counts": dict(counts),
    }


@router.get("/api/collections/{collection_id}")
def collection(collection_id: str):
    with db.Session() as s:
        w = s.get(Window, collection_id)
        if not w:
            raise HTTPException(404, "Unknown collection")
        return next(
            r
            for r in svc.rows(s, window_ids=[collection_id])
            if r["id"] == collection_id
        )


@router.post("/api/collections/backfill", dependencies=[Depends(admin)])
def backfill(body: BackfillRequest):
    """Explicit, bounded backfill of one host/day using the current schedule."""
    if body.date >= svc.now().date():
        raise HTTPException(422, "Choose a past date (UTC).")
    try:
        with db.transaction() as s:
            c = s.get(Collector, body.collector_id)
            h = s.get(Host, body.host)
            if not c or not h:
                raise HTTPException(
                    404, "Collector or host not found in this workspace"
                )
            if not c.enabled or not svc.host_active(s, h):
                raise ValueError("Collector and host must be enabled.")
            rule = svc.host_rule(s, c, h)
            if h.scope != c.scope or rule is False:
                raise ValueError(
                    "Host must be targeted by an enabled schedule for this collector."
                )
            start, end = svc.bounds(body.date.isoformat())
            if rule and rule.kind == "daily_local":
                if not svc.clock_worker(s, h):
                    raise ValueError(
                        "A satellite with a timezone is required for this daily schedule."
                    )
            else:
                interval = rule.interval_seconds if rule else c.interval_seconds
                if 86400 / interval > 500:
                    raise ValueError(
                        "This day exceeds the limit of 500 periods per request."
                    )
            pending = select(Window.id).where(
                Window.period_start >= iso(start),
                Window.period_start < iso(end),
                Window.collector_id == c.id,
                Window.host_id == h.id,
                Window.status == "PENDING",
            )
            before = set(s.scalars(pending))
            ids = svc.expected(
                s,
                body.date.isoformat(),
                c.id,
                h.id,
                enqueue=True,
                zone=h.zone,
                queue_statuses=("MISSING",),
            )
            if len(ids) > 500:
                raise ValueError(
                    "This day exceeds the limit of 500 periods per request."
                )
            queued = sorted(set(s.scalars(pending)) - before)
            for window in s.scalars(select(Window).where(Window.id.in_(queued))):
                window.snapshot = dict(window.snapshot, historical_backfill=True)
            return {"ids": queued, "queued": len(queued), "date": body.date.isoformat()}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/collections/plan", dependencies=[Depends(admin)])
def plan(body: PlanRequest):
    require_zone(body.zone)
    body.zone = current_zone.get() or body.zone
    try:
        with db.transaction() as s:
            if not s.get(Collector, body.collector_id):
                raise HTTPException(404, "Unknown collector")
            gids = [body.host_group_id] if body.host_group_id else []
            zones = [body.zone] if body.zone else []
            if body.schedule_id:
                rule = s.get(Schedule, body.schedule_id)
                if not rule or rule.collector_id != body.collector_id:
                    raise ValueError("Schedule does not match collector")
                if gids and gids[0] not in svc.rule_group_ids(rule):
                    raise ValueError("Schedule does not match host group")
                gids = gids or svc.rule_group_ids(rule)
                if zones and rule.zones and zones[0] not in rule.zones:
                    raise ValueError("Zone outside schedule targets")
                zones = zones or rule.zones
            if gids:
                c, targets, skipped = svc.resolve_targets(
                    s, body.collector_id, gids, zones
                )
                ids = []
                for h in targets:
                    if body.host and h.id != body.host:
                        continue
                    ids.extend(
                        svc.expected(
                            s,
                            body.date,
                            c.id,
                            h.id,
                            True,
                            body.parameters,
                            zone=body.zone,
                        )
                    )
                for cid in ids:
                    w = s.get(Window, cid)
                    if (
                        w.status == "PENDING"
                        and not w.snapshot.get("host_group_id")
                        and not s.scalar(
                            select(Attempt.id).where(Attempt.window_id == cid).limit(1)
                        )
                    ):
                        memberships = [
                            gid
                            for gid in gids
                            if w.host_id in s.get(HostGroup, gid).host_ids
                        ]
                        w.snapshot = dict(
                            w.snapshot,
                            host_group_id=memberships[0],
                            host_group_ids=memberships,
                        )
                return {"ids": list(dict.fromkeys(ids)), "skipped": skipped}
            return {
                "ids": svc.expected(
                    s,
                    body.date,
                    body.collector_id,
                    body.host,
                    True,
                    body.parameters,
                    zone=body.zone,
                )
            }
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.post("/api/collections/retry", dependencies=[Depends(admin)])
def retry(body: RetryRequest):
    require_zone(body.filters.get("zone"))
    if current_zone.get():
        body.filters["zone"] = current_zone.get()
    return {"ids": svc.retry(body.filters, body.ids, body.mode)}


@router.post("/api/workers/heartbeat")
def heartbeat(body: WorkerHeartbeat, authorization: str = Header(default="")):
    wid = worker_auth(body.worker_id, authorization)
    with db.transaction() as s:
        worker = s.get(Worker, wid)
        worker.heartbeat = time.time()
        if body.hostname is not None:
            worker.hostname = body.hostname
        if body.runtime is not None:
            worker.runtime = body.runtime.model_dump()
        if body.timezone is not None:
            if worker.timezone and worker.timezone != body.timezone:
                for window in s.scalars(
                    select(Window).where(Window.status.in_(["SCHEDULED", "PENDING"]))
                ):
                    if (
                        window.snapshot.get("schedule_kind") == "daily_local"
                        and window.snapshot.get("clock_worker_id") == wid
                        and dt(window.period_end) > svc.now()
                        and not s.scalar(
                            select(Attempt.id).where(Attempt.window_id == window.id)
                        )
                    ):
                        s.delete(window)
            worker.timezone = body.timezone
    return {"ok": True}


@router.post("/api/workers/claim")
def claim(body: WorkerRequest, authorization: str = Header(default="")):
    return svc.claim(worker_auth(body.worker_id, authorization))


@router.post("/api/tasks/{task_id}/heartbeat")
def task_heartbeat(
    task_id: str, body: WorkerRequest, authorization: str = Header(default="")
):
    wid = worker_auth(body.worker_id, authorization)
    with db.transaction() as s:
        a = s.get(Attempt, task_id)
        if (
            not a
            or a.worker_id != wid
            or a.status != "RUNNING"
            or time.time() > a.lease_until
        ):
            raise HTTPException(409, "Expired lease")
        a.lease_until = time.time() + settings.lease_seconds
        s.get(Worker, wid).heartbeat = time.time()
    return {"ok": True}


@router.post("/api/tasks/{task_id}/result")
def result(task_id: str, body: ResultRequest, authorization: str = Header(default="")):
    wid = worker_auth(body.worker_id, authorization)
    try:
        return svc.result(task_id, wid, body.result.model_dump(mode="json"), body.logs)
    except ValueError as error:
        raise HTTPException(409, str(error))
