"""Build daily collector health and paginated execution history."""

import calendar
from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import db
from . import service as svc
from .catalog_api import catalog, collectors, host_groups, hosts
from .models import Attempt, Host, Window
from .security import web_token
from .web import templates


def collection_rows(filters):
    with db.transaction() as session:
        svc.recover(session)
        if filters.get("date"):
            svc.expected(
                session,
                filters["date"],
                filters.get("collector_id"),
                filters.get("host", ""),
                zone=filters.get("zone", ""),
            )
        matching_rows = svc.rows(session, filters, include_attempts=False)
        unfiltered_rows = (
            svc.rows(
                session,
                {k: v for k, v in filters.items() if k != "status"},
                include_attempts=False,
            )
            if filters.get("status")
            else matching_rows
        )
    return matching_rows, unfiltered_rows


def daily_health(filters, days, finish):
    history = defaultdict(Counter)
    recovered = set()
    with db.transaction() as session:
        recovered = {
            wid
            for wid in session.scalars(
                select(Attempt.window_id)
                .join(Window, Window.id == Attempt.window_id)
                .where(
                    Window.period_start >= days[0] + "T00:00:00Z",
                    Window.period_start < svc.iso(finish),
                    Attempt.status.in_(
                        ["FAILED", "TIMEOUT", "WORKER_OFFLINE", "UNKNOWN"]
                    ),
                )
                .distinct()
            )
        }
        history_hosts = {host.id: host for host in session.scalars(select(Host))}
        for window in session.scalars(
            select(Window).where(
                Window.period_start >= days[0] + "T00:00:00Z",
                Window.period_start
                < svc.iso(svc.dt(days[-1] + "T00:00:00Z") + timedelta(days=1)),
            )
        ):
            host = history_hosts[window.host_id]
            if filters.get("scope") and host.scope != filters["scope"]:
                continue
            if (
                filters.get("host")
                and host.id != filters["host"]
                or filters.get("zone")
                and host.zone != filters["zone"]
            ):
                continue
            if filters.get("host_group_id") and filters[
                "host_group_id"
            ] not in window.snapshot.get(
                "host_group_ids", [window.snapshot.get("host_group_id")]
            ):
                continue
            if (
                filters.get("schedule_id")
                and window.snapshot.get("schedule_id") != filters["schedule_id"]
            ):
                continue
            history[(window.collector_id, window.period_start[:10])][window.status] += 1
            if window.status == "SUCCESS" and window.id in recovered:
                history[(window.collector_id, window.period_start[:10])][
                    "RECOVERED"
                ] += 1
    return history


def load_attempt_details(visible):
    with db.transaction() as session:
        details = {
            r["id"]: r
            for r in svc.rows(session, {}, window_ids=[r["id"] for r in visible])
        }
        visible = [details[r["id"]] for r in visible]
    return visible


def render_history(request, selected_filters, view, period, page, page_size):
    collector_rows = collectors()
    catalog_data = catalog()
    scope_tabs = view == "collectors" and not selected_filters.get("collector_id")
    scope_names = list(
        dict.fromkeys(catalog_data["scopes"] + [c["scope"] for c in collector_rows])
    )
    if scope_tabs and scope_names:
        selected_filters.setdefault("scope", scope_names[0])
        if selected_filters["scope"] not in scope_names:
            raise HTTPException(422, "Unknown scope")
    selected_filters.setdefault("date", svc.now().strftime("%Y-%m-%d"))
    anchor = svc.dt(selected_filters["date"] + "T00:00:00Z")
    begin = (
        anchor
        if period == "day"
        else anchor - timedelta(days=anchor.weekday())
        if period == "week"
        else anchor.replace(day=1)
    )
    length = (
        1
        if period == "day"
        else 7
        if period == "week"
        else calendar.monthrange(anchor.year, anchor.month)[1]
    )
    finish = begin + timedelta(days=length)
    if period != "day":
        selected_filters.pop("date", None)
        selected_filters["start"] = svc.iso(begin)
        selected_filters["end"] = svc.iso(finish)
    matching_rows, unfiltered_rows = collection_rows(selected_filters)
    selected_hour = next(
        (
            hour
            for hour in range(24)
            if period == "day"
            and selected_filters.get("start") == svc.iso(anchor + timedelta(hours=hour))
            and selected_filters.get("end")
            == svc.iso(anchor + timedelta(hours=hour + 1))
        ),
        None,
    )
    counts = Counter(r["status"] for r in unfiltered_rows)
    expected = sum(v for k, v in counts.items() if k not in ("SCHEDULED", "DISABLED"))
    counts["RECOVERED"] = sum(bool(r.get("recovered")) for r in unfiltered_rows)
    # Seven-day overview uses persisted expectations, never hides MISSING windows.
    days = (
        [(begin + timedelta(days=n)).strftime("%Y-%m-%d") for n in range(length)]
        if period != "day"
        else [
            (anchor - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(6, -1, -1)
        ]
    )
    history = (
        daily_health(selected_filters, days, finish) if view == "collectors" else {}
    )
    selected_filters["date"] = anchor.strftime("%Y-%m-%d")
    daily = defaultdict(Counter)
    for row in unfiltered_rows:
        daily[row["period_start"][:10]][row["status"]] += 1
    listing = (
        collector_rows
        if view == "collectors"
        else sorted(
            matching_rows, key=lambda r: (r["period_start"], r["host"]), reverse=True
        )
    )
    if scope_tabs and selected_filters.get("scope"):
        listing = [c for c in listing if c["scope"] == selected_filters["scope"]]
    paginated = view in ("collectors", "runs")
    pages = max(1, (len(listing) + page_size - 1) // page_size) if paginated else 1
    page = min(page, pages)
    visible = (
        listing[(page - 1) * page_size : page * page_size] if paginated else listing
    )
    card_collectors = [
        c
        for c in collector_rows
        if (
            not selected_filters.get("scope") or c["scope"] == selected_filters["scope"]
        )
        and (
            not selected_filters.get("collector_id")
            or c["id"] == selected_filters["collector_id"]
        )
    ]
    if view == "runs" and visible:
        visible = load_attempt_details(visible)
    context = {
        "selected_hour": selected_hour,
        "scope_tabs": scope_tabs,
        "scope_names": scope_names,
        "host_groups": host_groups(),
        "card_collectors": card_collectors,
        "period": period,
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "visible": visible,
        "daily": daily,
        "range_start": begin.strftime("%Y-%m-%d"),
        "range_end": (finish - timedelta(days=1)).strftime("%Y-%m-%d"),
        "previous_date": (begin - timedelta(days=1)).strftime("%Y-%m-%d"),
        "following_date": finish.strftime("%Y-%m-%d"),
        "request": request,
        "filters": selected_filters,
        "view": view,
        "collectors": collector_rows,
        "rows": visible,
        "total": len(matching_rows),
        "counts": counts,
        "expected": expected,
        "success_rate": round(counts["SUCCESS"] / expected * 100, 1) if expected else 0,
        "days": days,
        "history": history,
        "catalog": catalog_data,
        "hosts": hosts(),
        "csrf": web_token(request),
        "today": svc.now().strftime("%Y-%m-%d"),
    }
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context=context
    )
