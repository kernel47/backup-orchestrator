"""Inventory administration and explicit discovery/apply endpoints."""

import hashlib
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from . import db
from . import inventory_service as inv
from . import service as svc
from .models import (
    Collector,
    Host,
    HostGroup,
    InventorySource,
    InventorySync,
    Schedule,
    Scope,
    Worker,
    Zone,
)
from .security import admin
from .workspaces import current_zone, require_zone

router = APIRouter()


class ZoneInput(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True


class HostState(BaseModel):
    enabled: bool
    restore: bool = False


class HostsState(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=10000)
    enabled: bool


class GroupState(BaseModel):
    enabled: bool


class SourceInput(BaseModel):
    zone: str = ""
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["file"] = "file"
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class DiscoveryInput(BaseModel):
    source_id: str = Field(default="manual", min_length=1, max_length=128)
    version: Literal[1] = 1
    hosts: list[inv.HostRecord] = Field(max_length=10000)


class WorkerInput(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    zones: list[str] = Field(min_length=1, max_length=1)

    @model_validator(mode="before")
    @classmethod
    def single_zone(cls, value):
        if isinstance(value, dict) and "zone" in value:
            value = dict(value)
            zone = value.pop("zone")
            if "zones" in value and value["zones"] != [zone]:
                raise ValueError("Conflicting zone assignments")
            value["zones"] = [zone]
        return value

    scopes: list[str] = Field(min_length=1)
    concurrency: int = Field(default=2, ge=1, le=32)


@router.get("/api/zones")
def zones():
    with db.transaction() as session:
        return [svc.dump(z) for z in session.scalars(select(Zone).order_by(Zone.id))]


@router.post("/api/zones", dependencies=[Depends(admin)])
def save_zone(body: ZoneInput):
    with db.transaction() as session:
        zone = session.get(Zone, body.id)
        if not zone:
            zone = Zone(id=body.id)
            session.add(zone)
        zone.name = body.name
        zone.enabled = body.enabled
        session.flush()
        return svc.dump(zone)


@router.delete("/api/zones/{zone_id}", dependencies=[Depends(admin)])
def delete_zone(zone_id: str):
    with db.transaction() as session:
        zone = session.get(Zone, zone_id)
        if not zone:
            raise HTTPException(404, "Unknown zone")
        if any(
            session.scalar(select(model.id).where(model.zone == zone_id))
            for model in (
                Collector,
                HostGroup,
                Schedule,
                InventorySource,
                InventorySync,
            )
        ):
            raise HTTPException(
                409, "Zone contains configuration or history; disable it instead"
            )
        if session.scalar(select(Host).where(Host.zone == zone_id)) or any(
            zone_id in w.zones for w in session.scalars(select(Worker))
        ):
            raise HTTPException(
                409,
                "Zone is referenced by hosts or satellites; disable it to preserve history",
            )
        session.delete(zone)
        return {"ok": True}


@router.put("/api/hosts/{host_id}/state", dependencies=[Depends(admin)])
def host_state(host_id: str, body: HostState):
    try:
        with db.transaction() as session:
            host = session.get(Host, host_id)
            if not host:
                raise HTTPException(404, "Unknown host")
            if host.deleted and not body.restore:
                raise HTTPException(409, "Restore this deleted host explicitly")
            zone = session.get(Zone, host.zone)
            if body.enabled and (not zone or not zone.enabled):
                raise HTTPException(422, "Host zone is disabled")
            if body.enabled or body.restore:
                inv.validate_routing(session, svc.dump(host))
            if body.restore:
                host.deleted = False
            host.enabled = body.enabled
            host.discovered = False
            session.flush()
            svc.validate_groups_and_schedules(session, require_targets=False)
            return svc.dump(host)
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.put("/api/hosts/state", dependencies=[Depends(admin)])
def hosts_state(body: HostsState):
    try:
        with db.transaction() as session:
            changed = 0
            ids = list(dict.fromkeys(body.ids))
            for hid in ids:
                host = session.get(Host, hid)
                if not host or host.deleted:
                    raise ValueError("Host is missing or deleted: " + hid)
                if body.enabled:
                    zone = session.get(Zone, host.zone)
                    if not zone or not zone.enabled:
                        raise ValueError(
                            "Host " + hid + " belongs to a disabled or missing zone"
                        )
                    inv.validate_routing(session, svc.dump(host))
                if host.enabled != body.enabled or host.discovered:
                    host.enabled = body.enabled
                    host.discovered = False
                    changed += 1
            session.flush()
            svc.validate_groups_and_schedules(session, require_targets=False)
            return {"enabled": body.enabled, "changed": changed, "total": len(ids)}
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.delete("/api/hosts/{host_id}", dependencies=[Depends(admin)])
def archive_host(host_id: str):
    with db.transaction() as session:
        host = session.get(Host, host_id)
        if not host:
            raise HTTPException(404, "Unknown host")
        host.deleted = True
        host.enabled = False
        return {"ok": True, "history_preserved": True}


@router.patch("/api/host-groups/{group_id}", dependencies=[Depends(admin)])
def group_state(group_id: str, body: GroupState):
    try:
        with db.transaction() as session:
            group = session.get(HostGroup, group_id)
            if not group or group.deleted:
                raise HTTPException(404, "Unknown host group")
            group.enabled = body.enabled
            session.flush()
            svc.validate_groups_and_schedules(session, require_targets=False)
            return svc.dump(group)
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.delete("/api/host-groups/{group_id}", dependencies=[Depends(admin)])
def archive_group(group_id: str):
    with db.transaction() as session:
        group = session.get(HostGroup, group_id)
        if not group:
            raise HTTPException(404, "Unknown host group")
        group.deleted = True
        group.enabled = False
        for rule in session.scalars(select(Schedule)):
            if group_id in svc.rule_group_ids(rule):
                rule.enabled = False
        return {"ok": True, "history_preserved": True}


@router.post("/api/satellites", dependencies=[Depends(admin)])
def provision_worker(body: WorkerInput):
    for zone in body.zones:
        require_zone(zone)
    with db.transaction() as session:
        if session.get(Worker, body.id):
            raise HTTPException(409, "Satellite already exists")
        if any(not session.get(Zone, z) for z in body.zones):
            raise HTTPException(422, "Unknown satellite zone")
        token = secrets.token_urlsafe(32)
        worker = Worker(
            **body.model_dump(),
            heartbeat=0,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            token_env="",
        )
        session.add(worker)
        session.flush()
        return {
            "id": worker.id,
            "zone": worker.zones[0],
            "worker_token": token,
            "message": "Copy this token into the satellite .env. It is shown only once.",
        }


@router.get("/api/inventory/sources")
def sources():
    with db.transaction() as session:
        return [svc.dump(s) for s in session.scalars(select(InventorySource))]


@router.post("/api/inventory/sources", dependencies=[Depends(admin)])
def create_source(body: SourceInput):
    return save_source(None, body)


@router.put("/api/inventory/sources/{source_id}", dependencies=[Depends(admin)])
def update_source(source_id: str, body: SourceInput):
    return save_source(source_id, body)


def save_source(source_id, body):
    require_zone(body.zone)
    try:
        inv.inventory_path(body.config.get("path"))
    except ValueError as error:
        raise HTTPException(422, str(error))
    with db.transaction() as session:
        if not source_id and not (current_zone.get() or body.zone):
            ids = [z.id for z in session.scalars(select(Zone))]
            if not ids:
                raise HTTPException(422, "Create a zone first")
            body.zone = ids[0]
        source = (
            session.get(InventorySource, source_id)
            if source_id
            else InventorySource(id=svc.uid())
        )
        if not source:
            raise HTTPException(404, "Unknown source")
        for key, value in body.model_dump().items():
            setattr(source, key, value)
        session.add(source)
        session.flush()
        return svc.dump(source)


@router.post(
    "/api/inventory/sources/{source_id}/discover", dependencies=[Depends(admin)]
)
def discover(source_id: str):
    with db.transaction() as session:
        source = session.get(InventorySource, source_id)
        if not source:
            raise HTTPException(404, "Unknown source")
        spec = svc.dump(source)
    try:
        return inv.preview(source_id, inv.read_source(spec))
    except Exception as error:
        import logging

        logging.getLogger("inventory").warning(
            "Discovery failed for source %s: %s", source_id, type(error).__name__
        )
        raise HTTPException(
            422,
            "Discovery failed. Check the inventory file path and version-1 JSON/YAML format.",
        )


@router.post("/api/inventory/discover", dependencies=[Depends(admin)])
def push_discovery(body: DiscoveryInput):
    try:
        return inv.preview(
            body.source_id,
            {"version": 1, "hosts": [h.model_dump() for h in body.hosts]},
        )
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.get("/api/inventory/syncs")
def syncs():
    with db.transaction() as session:
        return [
            dict(row._mapping)
            for row in session.execute(
                select(
                    InventorySync.id,
                    InventorySync.source_id,
                    InventorySync.created_at,
                    InventorySync.status,
                )
                .order_by(InventorySync.created_at.desc())
                .limit(50)
            )
        ]


@router.get("/api/inventory/syncs/{sync_id}")
def sync_detail(sync_id: str):
    with db.transaction() as session:
        sync = session.get(InventorySync, sync_id)
        if not sync:
            raise HTTPException(404, "Unknown sync")
        return inv.sync_dump(sync)


@router.post("/api/inventory/syncs/{sync_id}/apply", dependencies=[Depends(admin)])
def apply_sync(sync_id: str):
    try:
        return inv.apply(sync_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/api/satellites/{worker_id}/rotate-token", dependencies=[Depends(admin)])
def rotate_worker_token(worker_id: str):
    with db.transaction() as session:
        worker = session.get(Worker, worker_id)
        if not worker:
            raise HTTPException(404, "Unknown satellite")
        token = secrets.token_urlsafe(32)
        worker.token_hash = hashlib.sha256(token.encode()).hexdigest()
        return {
            "id": worker.id,
            "worker_token": token,
            "message": "Update the satellite .env. The previous token is invalid.",
        }


@router.put("/api/satellites/{worker_id}", dependencies=[Depends(admin)])
def update_worker_policy(worker_id: str, body: WorkerInput):
    for zone in body.zones:
        require_zone(zone)
    with db.transaction() as session:
        worker = session.get(Worker, worker_id)
        if not worker:
            raise HTTPException(404, "Unknown satellite")
        if body.id != worker_id:
            raise HTTPException(422, "Satellite ID cannot be changed")
        if any(not session.get(Zone, z) for z in body.zones):
            raise HTTPException(422, "Unknown zone")
        references = list(session.scalars(select(Host).where(Host.deleted == False)))
        for host in references:
            if worker_id in (host.preferred_worker, host.fallback_worker) and (
                host.zone != body.zones[0] or host.scope not in body.scopes
            ):
                raise HTTPException(
                    409,
                    "Satellite is assigned to host "
                    + host.id
                    + ". Reassign its hosts before changing zone or removing their scope.",
                )
        worker.zones = body.zones
        worker.scopes = body.scopes
        worker.concurrency = body.concurrency
        return svc.dump(worker)


class ScopeInput(BaseModel):
    id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$"
    )


@router.post("/api/scopes", dependencies=[Depends(admin)])
def create_scope(body: ScopeInput):
    name = body.id.strip()
    with db.transaction() as session:
        if not session.get(Scope, name):
            session.add(Scope(id=name))
    return {"id": name}


@router.delete("/api/scopes/{scope_id}", dependencies=[Depends(admin)])
def delete_scope(scope_id: str):
    with db.transaction() as session:
        if (
            session.scalar(select(Host).where(Host.scope == scope_id))
            or session.scalar(select(Collector).where(Collector.scope == scope_id))
            or any(scope_id in w.scopes for w in session.scalars(select(Worker)))
        ):
            raise HTTPException(
                409, "This scope is used by hosts, collectors or satellites."
            )
        scope = session.get(Scope, scope_id)
        if not scope:
            raise HTTPException(404, "Unknown scope")
        session.delete(scope)
    return {"deleted": scope_id}
