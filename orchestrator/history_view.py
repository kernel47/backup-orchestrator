"""Build daily collector health and paginated execution history."""

import calendar
from datetime import timedelta

from fastapi import HTTPException

from . import db
from . import service as svc
from .catalog_api import catalog, collectors, host_groups, hosts
from .history_queries import HistoryStore
from .records import dt, iso
from .security import web_token
from .web import templates


def render_history(request, selected_filters, view, period, page, page_size):
    collector_rows = collectors()
    catalog_data = catalog()
    scope_tabs = view == "collectors"
    scope_names = list(
        dict.fromkeys(catalog_data["scopes"] + [c["scope"] for c in collector_rows])
    )
    if scope_tabs:
        selected_id = selected_filters.get("collector_id")
        selected = next((c for c in collector_rows if c["id"] == selected_id), None)
        if selected_id and not selected:
            raise HTTPException(404, "Collector not found in this workspace")
        if selected:
            selected_filters.setdefault("scope", selected["scope"])
            if selected_filters["scope"] != selected["scope"]:
                raise HTTPException(
                    422, "Collector does not belong to the selected scope"
                )
        if scope_names:
            selected_filters.setdefault("scope", scope_names[0])
            if selected_filters["scope"] not in scope_names:
                raise HTTPException(422, "Unknown scope")
    groups = host_groups()
    memberships = {}
    for group in groups:
        for host_id in group["host_ids"]:
            memberships.setdefault(host_id, []).append(group["id"])
    if view == "runs":
        # HTML history follows day/week/month; old hourly URLs must not leave
        # an invisible range active after the hour selector has been removed.
        selected_filters.pop("start", None)
        selected_filters.pop("end", None)
        selected = next(
            (
                c
                for c in collector_rows
                if c["id"] == selected_filters.get("collector_id")
            ),
            None,
        )
        if (
            selected
            and selected_filters.get("scope")
            and selected["scope"] != selected_filters["scope"]
        ):
            selected_filters.pop("collector_id", None)
        if (
            selected_filters.get("host_group_id")
            and selected_filters.get("host")
            and selected_filters["host_group_id"]
            not in memberships.get(selected_filters["host"], [])
        ):
            selected_filters.pop("host", None)
    selected_filters.setdefault("date", svc.now().strftime("%Y-%m-%d"))
    anchor = dt(selected_filters["date"] + "T00:00:00Z")
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
        selected_filters["start"] = iso(begin)
        selected_filters["end"] = iso(finish)
    with db.Session() as session:
        store = HistoryStore(session)
        counts, daily = store.summary(selected_filters)
        if view == "runs":
            result = store.page(selected_filters, page, page_size)
            visible, total = result.rows, result.total
            page, pages = result.page, result.pages
        else:
            visible, total, pages = (
                [],
                sum(daily_count.total() for daily_count in daily.values()),
                1,
            )
            page = 1
    selected_hour = next(
        (
            hour
            for hour in range(24)
            if period == "day"
            and selected_filters.get("start") == iso(anchor + timedelta(hours=hour))
            and selected_filters.get("end") == iso(anchor + timedelta(hours=hour + 1))
        ),
        None,
    )
    expected = sum(
        v for k, v in counts.items() if k not in ("SCHEDULED", "DISABLED", "RECOVERED")
    )
    # Seven-day overview uses persisted expectations, never hides MISSING windows.
    days = (
        [(begin + timedelta(days=n)).strftime("%Y-%m-%d") for n in range(length)]
        if period != "day"
        else [
            (anchor - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(6, -1, -1)
        ]
    )
    history = {}
    if view == "collectors":
        with db.Session() as session:
            history = HistoryStore(session).collector_health(
                selected_filters, days[0] + "T00:00:00Z", iso(finish)
            )
    selected_filters["date"] = anchor.strftime("%Y-%m-%d")
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
    context = {
        "selected_hour": selected_hour,
        "scope_tabs": scope_tabs,
        "scope_names": scope_names,
        "host_groups": groups,
        "host_memberships": memberships,
        "card_collectors": card_collectors,
        "period": period,
        "page": page,
        "pages": pages,
        "page_size": page_size,
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
        "total": total,
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
