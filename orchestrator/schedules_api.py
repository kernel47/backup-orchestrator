"""Schedule configuration, target previews and immediate group runs."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from . import db
from . import service as svc
from .models import HostGroup, Schedule, Window
from .schemas import GroupRunRequest, ScheduleInput
from .security import admin
from .workspaces import current_zone, require_zone

router = APIRouter()


@router.get("/api/schedules")
def schedules():
    with db.transaction() as s:
        return [
            dict(svc.dump(rule), timing=svc.schedule_timing(s, rule))
            for rule in s.scalars(select(Schedule).order_by(Schedule.name))
        ]


@router.post("/api/schedules", dependencies=[Depends(admin)])
def create_schedule(body: ScheduleInput):
    return save_schedule(None, body)


@router.put("/api/schedules/{schedule_id}", dependencies=[Depends(admin)])
def update_schedule(schedule_id: str, body: ScheduleInput):
    return save_schedule(schedule_id, body)


def save_schedule(schedule_id, body):
    for z in body.zones:
        require_zone(z)
    if current_zone.get():
        body.zones = [current_zone.get()]
    try:
        with db.transaction() as s:
            gids = list(
                dict.fromkeys(
                    body.host_group_ids
                    or ([body.host_group_id] if body.host_group_id else [])
                )
            )
            c, targets, _ = svc.resolve_targets(
                s, body.collector_id, gids, body.zones, allow_inactive=not body.enabled
            )
            if len({h.zone for h in targets}) > 1:
                raise ValueError("Schedules cannot span zone workspaces")
            if body.enabled and not targets:
                raise ValueError("No enabled hosts match this collector scope")
            rule = (
                s.get(Schedule, schedule_id)
                if schedule_id
                else Schedule(id=svc.uid(), created_at=svc.now().timestamp())
            )
            if not rule:
                raise HTTPException(404, "Unknown schedule")
            if schedule_id and rule.collector_id != body.collector_id:
                raise ValueError("Create a new schedule to change its collector")
            changed = schedule_id and (
                rule.interval_seconds != body.interval_seconds
                or rule.kind != body.kind
                or rule.local_time != body.local_time
                or set(svc.rule_group_ids(rule)) != set(gids)
                or set(rule.zones) != set(body.zones)
            )
            if schedule_id and (
                rule.kind != body.kind or rule.local_time != body.local_time
            ):
                rule.created_at = svc.now().timestamp()
            for k, v in body.model_dump().items():
                setattr(rule, k, v)
            if (
                body.enabled
                and body.kind == "daily_local"
                and any(not svc.clock_worker(s, h) for h in targets)
            ):
                raise ValueError(
                    "Every target needs an authorized satellite reporting its local timezone. Start the workers first."
                )
            rule.zone = targets[0].zone if targets else s.get(HostGroup, gids[0]).zone
            rule.zones = [rule.zone]
            require_zone(rule.zone)
            rule.host_group_id = gids[0]
            rule.host_group_ids = gids
            s.add(rule)
            s.flush()
            svc.validate_groups_and_schedules(s, require_targets=False)
            if changed:
                for w in s.scalars(
                    select(Window).where(
                        Window.collector_id == c.id,
                        Window.status.in_(["SCHEDULED", "DISABLED"]),
                    )
                ):
                    if (
                        w.snapshot.get("schedule_id") == rule.id
                        and svc.dt(w.period_end) > svc.now()
                    ):
                        s.delete(w)
            if rule.enabled and c.legacy_schedule:
                c.legacy_schedule = False
                for w in s.scalars(
                    select(Window).where(
                        Window.collector_id == c.id, Window.status == "SCHEDULED"
                    )
                ):
                    if (
                        not w.snapshot.get("schedule_id")
                        and svc.dt(w.period_end) > svc.now()
                    ):
                        s.delete(w)
            return svc.dump(rule)
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.get("/api/targets")
def targets(
    collector_id: str,
    host_group_id: str = "",
    host_group_ids: str = "",
    zones: str = "",
):
    try:
        with db.transaction() as s:
            gids = [g for g in host_group_ids.split(",") if g] or (
                [host_group_id] if host_group_id else []
            )
            selected_zones = [z for z in zones.split(",") if z]
            if current_zone.get():
                for z in selected_zones:
                    require_zone(z)
                selected_zones = [current_zone.get()]
            c, hosts, skipped = svc.resolve_targets(
                s, collector_id, gids, selected_zones
            )
            rule = next(
                (
                    r
                    for r in s.scalars(
                        select(Schedule).where(
                            Schedule.collector_id == c.id, Schedule.enabled == True
                        )
                    )
                    if set(svc.rule_group_ids(r)) == set(gids)
                    and set(r.zones) == set(selected_zones)
                ),
                None,
            )
            return {
                "collector": c.name,
                "scope": c.scope,
                "groups": gids,
                "hosts": [svc.dump(h) for h in hosts],
                "skipped": skipped,
                "run_interval_seconds": rule.interval_seconds
                if rule
                else c.interval_seconds,
            }
    except ValueError as error:
        raise HTTPException(422, str(error))


@router.post("/api/group-runs", dependencies=[Depends(admin)])
def group_run(body: GroupRunRequest):
    for z in body.zones:
        require_zone(z)
    if current_zone.get():
        body.zones = [current_zone.get()]
    try:
        return svc.run_group(
            body.collector_id,
            body.host_group_id,
            body.parameters,
            body.host_group_ids,
            body.zones,
        )
    except ValueError as error:
        raise HTTPException(422, str(error))
