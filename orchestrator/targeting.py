"""Resolve eligible hosts, groups and worker clocks within a zone."""

from sqlalchemy import select

from .models import Collector, Host, HostGroup, Worker, Zone


def host_active(s, h):
    zone = s.get(Zone, h.zone)
    return h.enabled and not h.deleted and not h.discovered and zone and zone.enabled


def origin_groups_active(s, w):
    ids = w.snapshot.get("host_group_ids") or (
        [w.snapshot["host_group_id"]] if w.snapshot.get("host_group_id") else []
    )
    return not ids or any(
        (g := s.get(HostGroup, gid)) and g.enabled and not g.deleted for gid in ids
    )


def rule_group_ids(rule):
    return rule.host_group_ids or [rule.host_group_id]


def resolve_targets(s, collector_id, group_ids, zones=None, allow_inactive=False):
    c = s.get(Collector, collector_id)
    if not c:
        raise ValueError("Unknown collector")
    if not group_ids:
        raise ValueError("Choose at least one host group")
    if any(not s.get(Zone, z) for z in (zones or [])):
        raise ValueError("Unknown target zone")
    targets = []
    skipped = []
    seen = set()
    for gid in dict.fromkeys(group_ids):
        g = s.get(HostGroup, gid)
        if not g or g.deleted:
            raise ValueError("Unknown host group")
        if c.zone and g.zone and c.zone != g.zone:
            raise ValueError("Collector and host group must belong to the same zone")
        if not g.enabled and not allow_inactive:
            raise ValueError("Host group is disabled")
        for hid in g.host_ids:
            if hid in seen:
                continue
            seen.add(hid)
            h = s.get(Host, hid)
            reason = (
                "Unknown host"
                if not h
                else "Host deleted"
                if h.deleted
                else "Host disabled"
                if not h.enabled or h.discovered
                else "Zone inactive"
                if not host_active(s, h)
                else "Outside selected zones"
                if zones and h.zone not in zones
                else "Scope mismatch"
                if h.scope != c.scope
                else "Zone mismatch"
                if c.zone and h.zone != c.zone
                else ""
            )
            if reason:
                skipped.append({"host": hid, "reason": reason})
            else:
                targets.append(h)
    return c, targets, skipped


def rule_targets(s, rule):
    groups = [s.get(HostGroup, g) for g in rule_group_ids(rule)]
    if any(not g or g.deleted or not g.enabled for g in groups):
        return []
    return resolve_targets(s, rule.collector_id, rule_group_ids(rule), rule.zones)[1]


def clock_worker(s, h):
    """Keep the preferred satellite's clock stable even when it is offline."""
    candidates = [
        s.get(Worker, wid) for wid in [h.preferred_worker, h.fallback_worker] if wid
    ]
    if not h.preferred_worker and not h.fallback_worker:
        candidates = list(s.scalars(select(Worker).order_by(Worker.id)))
    for worker in candidates:
        if worker and worker.zones == [h.zone] and h.scope in worker.scopes:
            # An unknown preferred clock must not silently adopt a different one.
            return worker if worker.timezone else None
    return None
