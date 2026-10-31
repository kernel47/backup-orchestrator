"""Page rendering for configuration, inventory and the global overview."""

from collections import Counter
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import db, inventory_api
from . import service as svc
from .cadences import CADENCES, SCHEDULE_CADENCES
from .catalog_api import catalog, collectors, host_groups, hosts, workers
from .config import settings
from .models import Window
from .schedules_api import schedules
from .security import web_token
from .views import latest_collector_health, overview_activity
from .web import templates


def render_overview(request):
    today = svc.now().strftime("%Y-%m-%d")
    with db.Session() as session:
        totals = Counter(
            session.scalars(
                select(Window.status).where(
                    Window.period_start >= today + "T00:00:00Z",
                    Window.period_start
                    < svc.iso(svc.dt(today + "T00:00:00Z") + timedelta(days=1)),
                )
            )
        )
        health = latest_collector_health(session)
        activity = overview_activity(session)
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "request": request,
            "view": "overview",
            "overview": True,
            "collectors": collectors(),
            "hosts": hosts(),
            "groups": host_groups(),
            "catalog": catalog(),
            "today": today,
            "counts": totals,
            "health": health,
            **activity,
            "csrf": web_token(request),
        },
    )


def render_collector_settings(request, selected_filters):
    collector_rows = collectors()
    catalog_data = catalog()
    selected_id = request.query_params.get("collector_id")
    selected = next((c for c in collector_rows if c["id"] == selected_id), None)
    if selected_id and not selected:
        raise HTTPException(404, "Collector not found in this workspace")
    creating = request.query_params.get("new") == "1" and not selected
    names = list(
        dict.fromkeys(catalog_data["scopes"] + [c["scope"] for c in collector_rows])
    )
    if not selected and not creating and names:
        selected_filters.setdefault("scope", names[0])
        if selected_filters["scope"] not in names:
            raise HTTPException(422, "Unknown scope")
    return templates.TemplateResponse(
        request=request,
        name="collector_settings.html",
        context={
            "request": request,
            "view": "settings",
            "filters": selected_filters,
            "collectors": collector_rows,
            "catalog": catalog_data,
            "scope_names": names,
            "selected": selected,
            "creating": creating,
            "editing": bool(selected and request.query_params.get("mode") == "edit"),
            "cadences": CADENCES,
            "csrf": web_token(request),
        },
    )


def render_configuration(request, view):
    return templates.TemplateResponse(
        request=request,
        name="configuration.html",
        context={
            "request": request,
            "view": view,
            "catalog": catalog(),
            "csrf": web_token(request),
        },
    )


def render_administration(request, view, page, page_size):
    history = inventory_api.syncs() if view == "sources" else []
    selected = request.query_params.get("sync_id") or (
        history[0]["id"] if history else ""
    )
    sync = inventory_api.sync_detail(selected) if selected else None
    changes_total = len(sync["changes"]) if sync else 0
    pages = max(1, (changes_total + page_size - 1) // page_size)
    page = min(page, pages)
    if sync:
        sync["changes"] = sync["changes"][(page - 1) * page_size : page * page_size]
    return templates.TemplateResponse(
        request=request,
        name="administration.html",
        context={
            "request": request,
            "view": view,
            "catalog": catalog(),
            "workers": workers(),
            "sources": inventory_api.sources(),
            "syncs": [sync] if sync else [],
            "sync_history": history,
            "changes_total": changes_total,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "csrf": web_token(request),
        },
    )


def render_inventory(request, view):
    host_rows = hosts(include_archived=True)
    groups = host_groups()
    rules = schedules()
    collector_rows = collectors()
    if (
        view == "schedules"
        and request.query_params.get("schedule_id")
        and not any(r["id"] == request.query_params["schedule_id"] for r in rules)
    ):
        raise HTTPException(404, "Schedule not found in this workspace")
    return templates.TemplateResponse(
        request=request,
        name="schedules.html" if view == "schedules" else "inventory.html",
        context={
            "request": request,
            "view": view,
            "hosts": host_rows,
            "groups": groups,
            "schedules": rules,
            "collectors": collector_rows,
            "catalog": catalog(),
            "cadences": SCHEDULE_CADENCES,
            "csrf": web_token(request),
        },
    )


def render_documentation(request, view):
    if view == "technical-architecture":
        return templates.TemplateResponse(
            request=request,
            name="technical_architecture.html",
            context={
                "request": request,
                "view": "technical-architecture",
                "csrf": web_token(request),
            },
        )

    if view == "documentation":
        return templates.TemplateResponse(
            request=request,
            name="live_architecture.html",
            context={
                "request": request,
                "view": "documentation",
                "workers": workers(),
                "hosts": hosts(),
                "collectors": collectors(),
                "catalog": catalog(),
                "master_host": settings.master_host,
                "master_port": settings.master_port,
                "offline_seconds": settings.offline_seconds,
                "csrf": web_token(request),
            },
        )
