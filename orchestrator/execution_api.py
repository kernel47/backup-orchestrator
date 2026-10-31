"""Collection history, replay requests and worker task protocol."""

import time
from collections import Counter

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from . import db
from . import service as svc
from .config import settings
from .filters import filters
from .models import Attempt, Collector, HostGroup, Schedule, Window, Worker
from .schemas import (
    PlanRequest,
    ResultRequest,
    RetryRequest,
    WorkerHeartbeat,
    WorkerRequest,
)
from .security import admin, worker_auth
from .workspaces import current_zone, require_zone

router = APIRouter()


@router.get("/api/collections")
def collections(f: dict = Depends(filters)):
    with db.transaction() as s:
        svc.recover(s)
        if f.get("date"):
            svc.expected(
                s,
                f["date"],
                f.get("collector_id"),
                f.get("host", ""),
                zone=f.get("zone", ""),
            )
        return svc.rows(s, f)


@router.get("/api/collections/summary")
def summary(f: dict = Depends(filters)):
    f.pop("status", None)
    rs = collections(f)
    return {
        "expected": sum(r["status"] not in ("SCHEDULED", "DISABLED") for r in rs),
        "counts": dict(Counter(r["status"] for r in rs)),
    }


@router.get("/api/collections/{collection_id}")
def collection(collection_id: str):
    with db.transaction() as s:
        w = s.get(Window, collection_id)
        if not w:
            raise HTTPException(404, "Unknown collection")
        return next(
            r
            for r in svc.rows(
                s, {"date": w.period_start[:10], "collector_id": w.collector_id}
            )
            if r["id"] == collection_id
        )


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
                            select(Attempt).where(Attempt.window_id == cid)
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
                        and svc.dt(window.period_end) > svc.now()
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
