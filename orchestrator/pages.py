"""HTML routes dispatch to focused page renderers."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from .execution_api import collection
from .filters import filters
from .history_view import render_history
from .management_views import (
    render_administration,
    render_collector_settings,
    render_configuration,
    render_documentation,
    render_inventory,
    render_overview,
)
from .security import web_token
from .web import templates

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    selected_filters: dict = Depends(filters),
    view: str = "collectors",
    period: str | None = Query(None, pattern="^(day|week|month)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=10, le=100),
    section: str = Query("collectors", pattern="^(collectors|inventory)$"),
):
    period = period or (
        "week"
        if view == "collectors" and not selected_filters.get("collector_id")
        else "day"
    )
    if view in ("technical-architecture", "documentation"):
        return render_documentation(request, view)
    if not request.state.workspace_zones:
        return templates.TemplateResponse(
            request=request,
            name="administration.html",
            context={
                "request": request,
                "view": "zones",
                "catalog": {"zones": [], "scopes": []},
                "csrf": web_token(request),
            },
        )

    if view == "overview":
        return render_overview(request)
    if view == "settings":
        if section == "inventory":
            return RedirectResponse(
                str(request.url.include_query_params(view="hosts")), 303
            )
        return render_collector_settings(request, selected_filters)
    if view in ("workspace", "scopes"):
        return render_configuration(request, view)
    if view in ("zones", "sources", "workers"):
        return render_administration(request, view, page, page_size)
    if view in ("hosts", "groups", "schedules"):
        return render_inventory(request, view)
    if view == "collectors" and selected_filters.get("collector_id"):
        return RedirectResponse(str(request.url.include_query_params(view="runs")), 303)
    return render_history(request, selected_filters, view, period, page, page_size)


@router.get("/collections/{collection_id}")
def detail(request: Request, collection_id: str, page: int = Query(1, ge=1)):
    run = collection(collection_id)
    attempts = list(reversed(run["attempts"]))
    pages = max(1, (len(attempts) + 24) // 25)
    page = min(page, pages)
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "request": request,
            "run": run,
            "attempts": attempts[(page - 1) * 25 : page * 25],
            "page": page,
            "pages": pages,
            "csrf": web_token(request),
        },
    )
