"""Explicit, idempotent conversion of legacy collector cadences into visible schedules."""

import json
import time

from sqlalchemy import select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.models import Attempt, Collector, Host, HostGroup, Schedule, Window


def migrate():
    converted = []
    with db.transaction() as session:
        for collector in session.scalars(
            select(Collector).where(Collector.legacy_schedule == True)
        ):
            members = sorted(
                session.scalars(
                    select(Host.id).where(
                        Host.scope == collector.scope, Host.deleted == False
                    )
                )
            )
            if not members:
                continue
            group = next(
                (
                    g
                    for g in session.scalars(select(HostGroup))
                    if not g.deleted and g.enabled and sorted(g.host_ids) == members
                ),
                None,
            )
            if not group:
                group = HostGroup(
                    id=svc.uid(),
                    name=collector.scope + " · All hosts",
                    description="Targets preserved from the previous automatic collector cadence.",
                    host_ids=members,
                    enabled=True,
                    deleted=False,
                )
                session.add(group)
                session.flush()
            rule = Schedule(
                id=svc.uid(),
                name=collector.scope + " " + collector.name,
                collector_id=collector.id,
                host_group_id=group.id,
                host_group_ids=[group.id],
                zones=[],
                interval_seconds=collector.interval_seconds,
                kind="interval",
                local_time="07:00",
                created_at=time.time(),
                enabled=collector.enabled,
                parameters={},
            )
            session.add(rule)
            collector.legacy_schedule = False
            session.flush()
            for window in session.scalars(
                select(Window).where(
                    Window.collector_id == collector.id, Window.status == "SCHEDULED"
                )
            ):
                if session.scalar(
                    select(Attempt.id).where(Attempt.window_id == window.id)
                ):
                    continue
                host = session.get(Host, window.host_id)
                if (
                    host.id in members
                    and window.snapshot.get("interval_seconds") == rule.interval_seconds
                ):
                    window.snapshot = dict(
                        svc.execution_snapshot(collector, host, rule, session=session),
                        scheduled_for=window.period_end,
                    )
            converted.append(
                {"collector": collector.id, "schedule": rule.id, "group": group.id}
            )
        svc.validate_groups_and_schedules(session, require_targets=False)
    return converted


if __name__ == "__main__":
    db.init()
    print(json.dumps({"converted": migrate()}, indent=2))
