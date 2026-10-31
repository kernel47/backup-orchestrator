"""Collector definitions, host groups and infrastructure status endpoints."""

import time
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from . import db
from . import service as svc
from .cadences import CADENCES
from .config import load, settings
from .models import (
    Attempt,
    Collector,
    Host,
    HostGroup,
    Schedule,
    Scope,
    Window,
    Worker,
    Zone,
)
from .scheduling import local_label
from .schemas import CollectorCreate, CollectorUpdate, HostGroupInput
from .security import admin
from .workspaces import current_zone, require_zone

router = APIRouter()


@router.get("/api/catalog")
def catalog():
    with db.transaction() as s:
        scopes = list(
            dict.fromkeys(
                [c.scope for c in s.scalars(select(Collector))]
                + [
                    h.scope
                    for h in s.scalars(select(Host).where(Host.deleted == False))
                ]
                + [scope for w in s.scalars(select(Worker)) for scope in w.scopes]
                + list(s.scalars(select(Scope.id)))
            )
        )
        return {
            "scopes": scopes,
            "zones": [svc.dump(z) for z in s.scalars(select(Zone))],
            "cadences": [
                {"seconds": seconds, "label": label} for seconds, label in CADENCES
            ],
            "scripts": list(load("worker_scripts.yaml")),
        }


@router.get("/api/hosts")
def hosts(include_archived: bool = False):
    with db.transaction() as s:
        return [
            svc.dump(h)
            for h in s.scalars(select(Host))
            if include_archived or not h.deleted
        ]


@router.get("/api/workers")
def workers():
    with db.transaction() as s:
        active = Counter(
            a.worker_id
            for a in s.scalars(select(Attempt).where(Attempt.status == "RUNNING"))
        )
        next_runs = {}
        for rule in s.scalars(select(Schedule).where(Schedule.enabled == True)):
            if not s.get(Collector, rule.collector_id).enabled:
                continue
            for timing in svc.schedule_timing(s, rule):
                wid = timing["worker_id"]
                if (
                    wid not in next_runs
                    or timing["next_execution"] < next_runs[wid]["next_execution"]
                ):
                    next_runs[wid] = dict(timing, schedule=rule.name)
        return [
            dict(
                svc.dump(w),
                online=time.time() - w.heartbeat <= settings.offline_seconds,
                active=active[w.id],
                last_seen_seconds=max(0, int(time.time() - w.heartbeat))
                if w.heartbeat
                else None,
                local_time=local_label(svc.now(), w.timezone),
                clock_epoch=svc.now().timestamp() * 1000,
                next_run=next_runs.get(w.id),
            )
            for w in s.scalars(select(Worker))
        ]


@router.get("/api/host-groups")
def host_groups():
    with db.transaction() as s:
        return [
            svc.dump(g)
            for g in s.scalars(
                select(HostGroup)
                .where(HostGroup.deleted == False)
                .order_by(HostGroup.name)
            )
        ]


@router.post("/api/host-groups", dependencies=[Depends(admin)])
def create_group(body: HostGroupInput):
    return save_group(None, body)


@router.put("/api/host-groups/{group_id}", dependencies=[Depends(admin)])
def update_group(group_id: str, body: HostGroupInput):
    return save_group(group_id, body)


def save_group(group_id, body):
    try:
        with db.transaction() as s:
            ids = list(dict.fromkeys(body.host_ids))
            if any(not s.get(Host, hid) or s.get(Host, hid).deleted for hid in ids):
                raise HTTPException(422, "Unknown host in group")
            group = s.get(HostGroup, group_id) if group_id else HostGroup(id=svc.uid())
            if not group:
                raise HTTPException(404, "Unknown host group")
            member_zones = {s.get(Host, hid).zone for hid in ids}
            if len(member_zones) != 1:
                raise ValueError("A host group must belong to exactly one zone")
            group.zone = next(iter(member_zones))
            require_zone(group.zone)
            group.name = body.name
            group.description = body.description
            group.host_ids = ids
            group.enabled = body.enabled
            group.deleted = False
            s.add(group)
            s.flush()
            svc.validate_groups_and_schedules(s, require_targets=False)
            return svc.dump(group)
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.get("/api/collectors")
def collectors():
    with db.transaction() as s:
        return [svc.dump(c) for c in s.scalars(select(Collector))]


@router.post("/api/collectors", dependencies=[Depends(admin)])
def create_collector(body: CollectorCreate):
    cat = catalog()
    if body.scope not in cat["scopes"]:
        raise HTTPException(
            422, "Scope is not registered by an inventory host or satellite"
        )
    if body.script not in cat["scripts"]:
        raise HTTPException(422, "Script is not in the allowed catalog")
    with db.transaction() as s:
        if s.get(Collector, body.id):
            raise HTTPException(409, "Collector ID already exists")
        require_zone(body.zone)
        if not (body.zone or current_zone.get()):
            body.zone = cat["zones"][0]["id"] if cat["zones"] else ""
        values = body.model_dump()
        values["zone"] = body.zone or current_zone.get()
        if not s.get(Zone, values["zone"]):
            raise HTTPException(422, "Unknown zone workspace")
        # Names may repeat across workspaces; internal identifiers stay unambiguous.
        existing = s.scalar(
            select(Collector)
            .where(Collector.id == body.id)
            .execution_options(workspace_global=True)
        )
        if existing and existing.zone != values["zone"]:
            values["id"] = values["zone"].lower() + "__" + body.id
        c = Collector(**values, revision=1, legacy_schedule=False)
        s.add(c)
        s.flush()
        return svc.dump(c)


@router.put("/api/collectors/{collector_id}", dependencies=[Depends(admin)])
def update_collector(collector_id: str, body: CollectorUpdate):
    with db.transaction() as s:
        c = s.get(Collector, collector_id)
        if not c:
            raise HTTPException(404, "Unknown collector")
        for k, v in body.model_dump().items():
            setattr(c, k, v)
        c.revision += 1
        # Future windows can use the new cadence; historical/active windows remain immutable.
        for w in s.scalars(
            select(Window).where(
                Window.collector_id == c.id,
                Window.status.in_(["SCHEDULED", "DISABLED"]),
            )
        ):
            if not w.snapshot.get("schedule_id") and svc.dt(w.period_end) > svc.now():
                s.delete(w)
        return svc.dump(c)
