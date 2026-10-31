"""Zone workspace context, applied to every ORM read and dashboard write."""

from contextvars import ContextVar

from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session as ORMSession
from sqlalchemy.orm import with_loader_criteria

from .models import (
    Attempt,
    Collector,
    Host,
    HostGroup,
    InventorySource,
    InventorySync,
    Schedule,
    Window,
    Worker,
    Zone,
)

current_zone = ContextVar("orchestrator_zone", default=None)
allowed_zones = ContextVar("orchestrator_allowed_zones", default=None)
OWNED = (Host, Collector, HostGroup, Schedule, Window, InventorySource, InventorySync)


@event.listens_for(ORMSession, "do_orm_execute")
def scope_reads(state):
    zone = current_zone.get()
    if not state.is_select or state.execution_options.get("workspace_global"):
        return
    statement = state.statement
    allowed = allowed_zones.get()
    if allowed is not None:
        statement = statement.options(
            with_loader_criteria(Zone, Zone.id.in_(allowed), include_aliases=True)
        )
    if not zone:
        state.statement = statement
        return
    for model in OWNED:
        statement = statement.options(
            with_loader_criteria(model, model.zone == zone, include_aliases=True)
        )
    statement = statement.options(
        with_loader_criteria(Worker, Worker.zones == [zone], include_aliases=True),
        with_loader_criteria(
            Attempt,
            Attempt.window_id.in_(select(Window.id).where(Window.zone == zone)),
            include_aliases=True,
        ),
    )
    state.statement = statement


@event.listens_for(ORMSession, "before_flush")
def scope_writes(session, context, instances):
    zone = current_zone.get()
    for obj in session.new | session.dirty | session.deleted:
        if isinstance(obj, OWNED):
            assigned = getattr(obj, "zone", None)
            if isinstance(obj, Window) and not assigned:
                host = session.get(Host, obj.host_id)
                assigned = host.zone if host else None
            history = inspect(obj).attrs.zone.history
            if (
                history.deleted
                and history.deleted[0]
                and assigned != history.deleted[0]
            ):
                raise HTTPException(
                    409,
                    "Zone ownership cannot be moved; create the object in the target workspace",
                )
            if not assigned:
                assigned = zone
            if not assigned:
                # System integration calls must specify ownership or have one zone.
                zones = list(session.scalars(select(Zone.id)))
                if len(zones) == 1:
                    assigned = zones[0]
            if zone and assigned != zone:
                raise HTTPException(409, "Object belongs to another zone workspace")
            if assigned:
                obj.zone = assigned
        if zone and isinstance(obj, Worker) and obj.zones != [zone]:
            raise HTTPException(409, "Satellite belongs to another zone workspace")


def require_zone(zone):
    active = current_zone.get()
    if active and zone and zone != active:
        raise HTTPException(
            409, "Choose this zone workspace before changing its objects"
        )
