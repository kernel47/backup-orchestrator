"""Read-only health summaries for the active zone workspace."""

from collections import Counter
from datetime import datetime, timedelta, timezone


def regular_collection():
    """Missing flags on older records mean regular collection, across both databases."""
    from .models import Window

    return Window.snapshot["historical_backfill"].as_boolean().is_not(True)


def host_overdue(session, collector, host, window, rule):
    """Only periods due after schedule creation can be overdue."""
    from . import service as svc
    from .scheduling import latest_daily_period

    if window and window.status in ("RUNNING", "PENDING"):
        return False
    if rule:
        if rule.kind == "daily_local":
            worker = svc.clock_worker(session, host)
            if not worker:
                return False
            _, due = latest_daily_period(svc.now(), rule.local_time, worker.timezone)
        else:
            due = datetime.fromtimestamp(
                int(svc.now().timestamp())
                // rule.interval_seconds
                * rule.interval_seconds,
                timezone.utc,
            )
        return (
            due.timestamp() >= rule.created_at
            and due + timedelta(seconds=collector.timeout_seconds + 300) < svc.now()
            and (not window or svc.dt(window.period_end) < due)
        )
    return bool(
        window
        and collector.legacy_schedule
        and svc.now()
        > svc.dt(window.period_end)
        + timedelta(
            seconds=window.snapshot.get("interval_seconds", collector.interval_seconds)
            + 300
        )
    )


def latest_collector_health(session):
    """One latest recorded period per host, across each satellite's local clock."""
    from sqlalchemy import or_, select

    from . import service as svc
    from .models import Collector, Host, Schedule, Window

    latest_id = (
        select(Window.id)
        .where(
            Window.host_id == Host.id,
            Window.zone == Host.zone,
            Window.collector_id == Collector.id,
            Window.status.notin_(["SCHEDULED", "DISABLED"]),
            regular_collection(),
        )
        .order_by(
            Window.period_start.desc(), Window.created_at.desc(), Window.id.desc()
        )
        .limit(1)
        .correlate(Host, Collector)
        .scalar_subquery()
    )
    latest_ids = (
        select(latest_id)
        .select_from(Host)
        .join(Collector, or_(Collector.zone == Host.zone, Collector.zone == ""))
    )
    latest = list(session.scalars(select(Window).where(Window.id.in_(latest_ids))))
    by_collector = {}
    for window in latest:
        by_collector.setdefault(window.collector_id, {})[window.host_id] = window
    result = {}
    for collector in session.scalars(select(Collector)):
        recorded = by_collector.get(collector.id, {})
        rules = list(
            session.scalars(
                select(Schedule).where(
                    Schedule.collector_id == collector.id, Schedule.enabled.is_(True)
                )
            )
        )
        target_hosts, target_rules = {}, {}
        for rule in rules:
            for host in svc.rule_targets(session, rule):
                target_hosts[host.id] = host
                target_rules[host.id] = rule
        if collector.legacy_schedule and collector.enabled:
            target_hosts.update(
                (h.id, h)
                for h in session.scalars(
                    select(Host).where(Host.scope == collector.scope)
                )
                if svc.host_active(session, h)
            )
        targets = set(target_hosts)
        # With no active schedule, show manual/paused execution history explicitly.
        ids = (
            targets
            if rules or collector.legacy_schedule and collector.enabled
            else set(recorded)
        )
        counts = Counter(
            recorded[hid].status if hid in recorded else "UNRECORDED" for hid in ids
        )
        failures = counts["FAILED"] + counts["TIMEOUT"] + counts["UNKNOWN"]
        status = (
            "FAILED"
            if failures
            else "MISSING"
            if counts["MISSING"]
            else "RUNNING"
            if counts["RUNNING"] + counts["PENDING"]
            else "SUCCESS"
            if counts["SUCCESS"] and counts["SUCCESS"] == len(ids)
            else "DISABLED"
        )
        stale = (
            sum(
                host_overdue(
                    session, collector, host, recorded.get(hid), target_rules.get(hid)
                )
                for hid, host in target_hosts.items()
            )
            if collector.enabled
            else 0
        )
        if stale and status in ("SUCCESS", "DISABLED"):
            status = "MISSING"
        last = collector.last_success_at
        result[collector.id] = {
            "counts": counts,
            "expected": len(ids),
            "last_period": max(
                (w.period_start[:10] for w in recorded.values()), default=""
            ),
            "scheduled": bool(rules or collector.legacy_schedule and collector.enabled),
            "status": status,
            "stale": stale,
            "last_success": svc.iso(datetime.fromtimestamp(last, timezone.utc))
            if last
            else None,
        }
    return result


def overview_activity(session):
    """Seven days of collection periods, grouped by day and collector in SQL."""
    from sqlalchemy import func, select

    from . import service as svc
    from .models import Collector, Window

    today = svc.now().replace(hour=0, minute=0, second=0, microsecond=0)
    days = [(today - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(6, -1, -1)]
    grouped = {day: Counter() for day in days}
    by_collector = {}
    day_column = func.substr(Window.period_start, 1, 10)
    for day, collector_id, status, count in session.execute(
        select(day_column, Window.collector_id, Window.status, func.count())
        .where(
            Window.period_start >= days[0] + "T00:00:00Z",
            Window.period_start < svc.iso(today + timedelta(days=1)),
            Window.status.notin_(["SCHEDULED", "DISABLED"]),
            regular_collection(),
        )
        .group_by(day_column, Window.collector_id, Window.status)
    ):
        grouped[day][status] += count
        by_collector.setdefault(collector_id, Counter())[status] += count
    trend = [
        {"day": day, "counts": counts, "total": sum(counts.values())}
        for day, counts in grouped.items()
    ]
    activity = []
    for cid, name, scope in session.execute(
        select(Collector.id, Collector.name, Collector.scope).order_by(
            Collector.scope, Collector.name, Collector.id
        )
    ):
        counts = by_collector.get(cid, Counter())
        activity.append(
            {
                "id": cid,
                "name": name,
                "scope": scope,
                "counts": counts,
                "total": sum(counts.values()),
            }
        )
    return {
        "trend": trend,
        "trend_max": max((d["total"] for d in trend), default=0) or 1,
        "collector_activity": activity,
        "collector_activity_max": max((c["total"] for c in activity), default=0) or 1,
    }


def overview_operations(session):
    """Small SQL aggregates for live capacity and completed executions in 24 hours."""
    from sqlalchemy import func, select

    from . import service as svc
    from .models import Attempt, Window

    current = svc.now().timestamp()
    recent = (
        select(Window.id, Window.status)
        .join(Attempt, Attempt.window_id == Window.id)
        .where(
            Attempt.finished_at >= current - 86400,
            Attempt.finished_at <= current,
            Window.status.in_(["SUCCESS", "FAILED", "TIMEOUT", "UNKNOWN"]),
            regular_collection(),
        )
        .distinct()
        .subquery()
    )
    completed = Counter(
        dict(
            session.execute(
                select(recent.c.status, func.count()).group_by(recent.c.status)
            ).all()
        )
    )
    active = Counter(
        dict(
            session.execute(
                select(Window.status, func.count())
                .where(Window.status.in_(["RUNNING", "PENDING"]))
                .group_by(Window.status)
            ).all()
        )
    )
    backfills = Counter(
        dict(
            session.execute(
                select(Window.status, func.count())
                .where(
                    Window.status.in_(["RUNNING", "PENDING"]),
                    Window.snapshot["historical_backfill"].as_boolean().is_(True),
                )
                .group_by(Window.status)
            ).all()
        )
    )
    oldest = session.scalar(
        select(
            func.min(
                func.coalesce(
                    Window.snapshot["queued_at"].as_float(),
                    Window.created_at,
                )
            )
        ).where(Window.status == "PENDING")
    )
    waiting = max(0, int(current - oldest)) if oldest is not None else None
    age = (
        "—"
        if waiting is None
        else f"{waiting // 86400}d {waiting % 86400 // 3600}h"
        if waiting >= 86400
        else f"{waiting // 3600}h {waiting % 3600 // 60}m"
        if waiting >= 3600
        else f"{waiting // 60}m"
        if waiting >= 60
        else "< 1m"
    )
    return {
        "completed": completed.total(),
        "successful": completed["SUCCESS"],
        "success_rate": round(completed["SUCCESS"] / completed.total() * 100, 1)
        if completed
        else None,
        "running": active["RUNNING"],
        "pending": active["PENDING"],
        "backfills_running": backfills["RUNNING"],
        "backfills_pending": backfills["PENDING"],
        "oldest_wait": age,
    }
