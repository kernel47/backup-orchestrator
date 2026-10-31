"""Idempotent upgrade from shared configuration to zone-owned objects.
Existing IDs remain in the first owning zone. Shared definitions get copies in
other zones; execution and attempt IDs are preserved.
"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from .models import (
    Collector,
    Host,
    HostGroup,
    InventorySource,
    InventorySync,
    Schedule,
    Window,
    Zone,
)


def migrate():
    from . import db

    with db.Session.begin() as s:
        zones = [z.id for z in s.scalars(select(Zone))]
        if not zones:
            return
        if not any(
            s.scalar(select(model.id).where(model.zone == "").limit(1))
            for model in (
                Collector,
                HostGroup,
                Schedule,
                InventorySource,
                InventorySync,
                Window,
            )
        ):
            return

        def clone_id(kind, oid, z):
            return str(uuid5(NAMESPACE_URL, f"backup-orchestrator/{kind}/{oid}/{z}"))

        def copy(obj, oid, zone):
            values = {col.name: getattr(obj, col.name) for col in obj.__table__.columns}
            values.update(id=oid, zone=zone)
            item = type(obj)(**values)
            s.add(item)
            return item

        hs = {h.id: h for h in s.scalars(select(Host))}
        cm = {}
        gm = {}
        sm = {}
        src = {}
        for c in list(s.scalars(select(Collector))):
            if c.zone:
                cm[(c.id, c.zone)] = c.id
                continue
            owned = {h.zone for h in hs.values() if h.scope == c.scope} or {zones[0]}
            for i, z in enumerate(z for z in zones if z in owned):
                item = c if i == 0 else copy(c, clone_id("collector", c.id, z), z)
                item.zone = z
                cm[(c.id, z)] = item.id
        for g in list(s.scalars(select(HostGroup))):
            if g.zone:
                gm[(g.id, g.zone)] = g.id
                continue
            members = list(g.host_ids)
            owned = {hs[hid].zone for hid in members if hid in hs} or {zones[0]}
            for i, z in enumerate(z for z in zones if z in owned):
                item = g if i == 0 else copy(g, clone_id("group", g.id, z), z)
                item.zone = z
                item.host_ids = [
                    hid for hid in members if hid in hs and hs[hid].zone == z
                ]
                gm[(g.id, z)] = item.id
        s.flush()
        for r in list(s.scalars(select(Schedule))):
            if r.zone:
                sm[(r.id, r.zone)] = r.id
                continue
            gids = r.host_group_ids or [r.host_group_id]
            selected = list(r.zones)
            collector_id = r.collector_id
            owned = {
                z for gid, z in gm if gid in gids and (not selected or z in selected)
            }
            for i, z in enumerate(z for z in zones if z in owned):
                item = r if i == 0 else copy(r, clone_id("schedule", r.id, z), z)
                item.zone = z
                item.collector_id = cm.get((collector_id, z), collector_id)
                item.host_group_ids = [gm[(gid, z)] for gid in gids if (gid, z) in gm]
                item.host_group_id = item.host_group_ids[0]
                item.zones = [z]
                sm[(r.id, z)] = item.id
            if not owned:
                r.zone = zones[0]
                r.enabled = False
        for source in list(s.scalars(select(InventorySource))):
            if source.zone:
                continue
            owned = {h.zone for h in hs.values() if h.source_id == source.id} or {
                zones[0]
            }
            for i, z in enumerate(z for z in zones if z in owned):
                item = (
                    source
                    if i == 0
                    else copy(source, clone_id("source", source.id, z), z)
                )
                item.zone = z
                src[(source.id, z)] = item.id
        s.flush()
        for h in hs.values():
            h.source_id = src.get((h.source_id, h.zone), h.source_id)
        for w in s.scalars(select(Window)):
            z = hs[w.host_id].zone
            w.zone = z
            w.collector_id = cm.get((w.collector_id, z), w.collector_id)
            payload = dict(w.snapshot)
            if payload.get("schedule_id"):
                payload["schedule_id"] = sm.get(
                    (payload["schedule_id"], z), payload["schedule_id"]
                )
            if payload.get("host_group_id"):
                payload["host_group_id"] = gm.get(
                    (payload["host_group_id"], z), payload["host_group_id"]
                )
            if payload.get("host_group_ids"):
                payload["host_group_ids"] = [
                    gm.get((gid, z), gid) for gid in payload["host_group_ids"]
                ]
            w.snapshot = payload
        for sync in s.scalars(select(InventorySync).where(InventorySync.zone == "")):
            records = sync.payload.get("records", [])
            owned = {r["zone"] for r in records}
            sync.zone = next(iter(owned)) if len(owned) == 1 else zones[0]
            sync.source_id = src.get((sync.source_id, sync.zone), sync.source_id)
            if len(owned) > 1:
                sync.status = "EXPIRED"  # Re-preview under the chosen workspace.
