"""Read-only health summaries for the active zone workspace."""

from collections import Counter
from datetime import datetime, timedelta, timezone


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
        targets = {h.id for rule in rules for h in svc.rule_targets(session, rule)}
        if collector.legacy_schedule and collector.enabled:
            targets.update(
                h.id
                for h in session.scalars(
                    select(Host).where(Host.scope == collector.scope)
                )
                if svc.host_active(session, h)
            )
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
                1
                for hid in targets
                if hid in recorded
                and recorded[hid].status not in ("RUNNING", "PENDING")
                and svc.now()
                > svc.dt(recorded[hid].period_end)
                + timedelta(
                    seconds=recorded[hid].snapshot.get(
                        "interval_seconds", collector.interval_seconds
                    )
                    + 300
                )
            )
            if rules or collector.legacy_schedule and collector.enabled
            else 0
        )
        if stale and status == "SUCCESS":
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
