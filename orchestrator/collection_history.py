"""Detailed execution reports using the same SQL filters as dashboard summaries."""

from collections import defaultdict

from sqlalchemy import select

from .history_queries import execution_labels, window_query
from .models import Attempt, Collector, Host, Window
from .records import dump


def rows(session, filters=None, window_ids=None, limit=None, offset=0):
    query = window_query(filters or {})
    if window_ids is not None:
        query = query.where(Window.id.in_(window_ids))
    query = query.order_by(Window.period_start, Window.host_id, Window.id)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    records = list(session.execute(query.add_columns(Host, Collector)))
    if not records:
        return []
    attempts = defaultdict(list)
    for attempt in session.scalars(
        select(Attempt)
        .where(Attempt.window_id.in_(query.with_only_columns(Window.id)))
        .order_by(Attempt.number)
    ):
        attempts[attempt.window_id].append(dump(attempt))
    result = []
    clocks = {}
    for window, host, collector in records:
        history = attempts[window.id]
        last = history[-1] if history else {}
        result.append(
            dict(
                dump(window),
                host=host.id,
                zone=window.snapshot.get("zone", host.zone),
                scope=window.snapshot.get("scope", collector.scope),
                collector=collector.name,
                attempts=history,
                worker=last.get("worker_id"),
                summary=(last.get("result") or {}).get("summary", ""),
                recovered=window.status == "SUCCESS"
                and any(a["status"] not in ("SUCCESS", "RUNNING") for a in history),
                **execution_labels(
                    session,
                    window,
                    bool(history),
                    last.get("started_at"),
                    last.get("context", {}).get("worker_timezone", ""),
                    clocks,
                ),
            )
        )
    return result
