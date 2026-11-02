"""Bounded, read-only dashboard queries; report bodies belong on detail pages."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from .models import Attempt, Collector, Host, Window
from .records import bounds, dump, iso
from .scheduling import local_label
from .targeting import clock_worker


def group_membership(group_id):
    from . import db

    if db.engine.dialect.name == "postgresql":
        snapshot = cast(Window.snapshot, JSONB)
        return or_(
            snapshot["host_group_ids"].contains([group_id]),
            and_(
                snapshot["host_group_ids"].is_(None),
                snapshot["host_group_id"].astext == group_id,
            ),
        )
    groups = func.json_each(Window.snapshot, "$.host_group_ids").table_valued("value")
    membership = exists(
        select(1)
        .select_from(groups)
        .where(groups.c.value == group_id)
        .correlate(Window)
    )
    return or_(
        membership,
        and_(
            func.json_type(Window.snapshot, "$.host_group_ids").is_(None),
            Window.snapshot["host_group_id"].as_string() == group_id,
        ),
    )


def latest_attempt(column):
    return (
        select(column)
        .where(Attempt.window_id == Window.id)
        .order_by(Attempt.number.desc())
        .limit(1)
        .correlate(Window)
        .scalar_subquery()
    )


def failed_before():
    return exists(
        select(Attempt.id)
        .where(
            Attempt.window_id == Window.id,
            Attempt.status.notin_(["SUCCESS", "RUNNING"]),
        )
        .correlate(Window)
    )


def window_query(filters, include_status=True):
    query = (
        select(Window)
        .join(Host, Host.id == Window.host_id)
        .join(Collector, Collector.id == Window.collector_id)
    )
    if filters.get("date"):
        start, end = bounds(filters["date"])
        query = query.where(
            Window.period_start >= iso(start), Window.period_start < iso(end)
        )
    if filters.get("start"):
        query = query.where(Window.period_start >= filters["start"])
    if filters.get("end"):
        query = query.where(Window.period_start < filters["end"])
    for key, column in (
        ("collector_id", Window.collector_id),
        ("host", Window.host_id),
    ):
        if filters.get(key):
            query = query.where(column == filters[key])
    for key, fallback in (("zone", Host.zone), ("scope", Collector.scope)):
        if filters.get(key):
            query = query.where(
                func.coalesce(Window.snapshot[key].as_string(), fallback)
                == filters[key]
            )
    if filters.get("host_group_id"):
        query = query.where(group_membership(filters["host_group_id"]))
    if filters.get("schedule_id"):
        query = query.where(
            Window.snapshot["schedule_id"].as_string() == filters["schedule_id"]
        )
    if filters.get("worker"):
        query = query.where(latest_attempt(Attempt.worker_id) == filters["worker"])
    status = filters.get("status") if include_status else None
    if status == "RECOVERED":
        query = query.where(Window.status == "SUCCESS", failed_before())
    elif status == "ATTENTION":
        query = query.where(
            Window.status.in_(["FAILED", "TIMEOUT", "MISSING", "UNKNOWN"])
        )
    elif status:
        query = query.where(Window.status == status)
    return query


@dataclass(frozen=True)
class HistoryPage:
    rows: list[dict]
    total: int
    page: int
    pages: int


class HistoryStore:
    """Read history and summaries using one caller-owned database session."""

    def __init__(self, session):
        self.session = session

    def _grouped_health(self, filters):
        day = func.substr(Window.period_start, 1, 10)
        recovered = and_(Window.status == "SUCCESS", failed_before())
        return self.session.execute(
            window_query(filters, include_status=False)
            .with_only_columns(
                day, Window.collector_id, Window.status, recovered, func.count()
            )
            .group_by(day, Window.collector_id, Window.status, recovered)
        )

    def summary(self, filters):
        counts = Counter()
        daily = defaultdict(Counter)
        for day, collector, status, recovered, count in self._grouped_health(filters):
            counts[status] += count
            daily[day][status] += count
            if recovered:
                counts["RECOVERED"] += count
        return counts, daily

    def collector_health(self, filters, start, end):
        selected = {
            k: v
            for k, v in filters.items()
            if k not in ("date", "start", "end", "status")
        }
        selected.update(start=start, end=end)
        history = defaultdict(Counter)
        for day, collector, status, recovered, count in self._grouped_health(selected):
            history[(collector, day)][status] += count
            if recovered:
                history[(collector, day)]["RECOVERED"] += count
        return history

    def page(self, filters, page, page_size):
        query = window_query(filters)
        total = self.session.scalar(query.with_only_columns(func.count(Window.id)))
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        attempt_count = (
            select(func.count(Attempt.id))
            .where(Attempt.window_id == Window.id)
            .correlate(Window)
            .scalar_subquery()
        )
        records = self.session.execute(
            query.with_only_columns(
                Window,
                Collector.name,
                func.coalesce(Window.snapshot["scope"].as_string(), Collector.scope),
                latest_attempt(Attempt.worker_id),
                attempt_count,
                latest_attempt(Attempt.started_at),
                latest_attempt(Attempt.context["worker_timezone"].as_string()),
                failed_before(),
            )
            .order_by(
                Window.period_start.desc(), Window.host_id.desc(), Window.id.desc()
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        visible = []
        clocks = {}
        for (
            window,
            name,
            scope,
            worker,
            attempts,
            started,
            actual_zone,
            recovered,
        ) in records:
            row = dump(window)
            row.update(
                host=window.host_id,
                collector=name,
                worker=worker,
                attempt_count=attempts,
                scope=scope,
                recovered=window.status == "SUCCESS" and recovered,
                **execution_labels(
                    self.session, window, attempts, started, actual_zone, clocks
                ),
            )
            visible.append(row)
        return HistoryPage(visible, total, page, pages)


def execution_labels(session, window, has_attempts, started, actual_zone, clocks):
    planned_zone = window.snapshot.get("execution_timezone", "")
    if not planned_zone and not has_attempts and window.snapshot.get("schedule_id"):
        if window.host_id not in clocks:
            worker = clock_worker(session, session.get(Host, window.host_id))
            clocks[window.host_id] = worker.timezone if worker else ""
        planned_zone = clocks[window.host_id]
    scheduled = window.snapshot.get("scheduled_for", window.period_end)
    return {
        "execution_timezone": planned_zone,
        "scheduled_utc": scheduled,
        "scheduled_local": local_label(scheduled, planned_zone) if planned_zone else "",
        "actual_timezone": actual_zone,
        "started_local": local_label(started, actual_zone)
        if started is not None and actual_zone
        else "",
    }
