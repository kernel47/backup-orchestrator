"""Authenticate requests and establish the authorized workspace context."""

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from . import db, navigation, security
from . import service as svc
from .models import Zone
from .security import web_token
from .workspaces import allowed_zones, current_zone


async def require_login(request: Request, call_next):
    browser_navigation = navigation.prepare(request)
    path = request.url.path
    parts = path.split("/")
    worker_route = path in ("/api/workers/heartbeat", "/api/workers/claim") or (
        len(parts) == 5
        and parts[1:3] == ["api", "tasks"]
        and parts[4] in ("heartbeat", "result")
    )
    public = (
        path in ("/login", "/health", "/favicon.ico")
        or path.startswith("/static/")
        or worker_route
    )
    if not public and not (
        web_token(request)
        or security.api_admin(request.headers.get("authorization", ""))
    ):
        if path.startswith("/api/") or path == "/openapi.json":
            return JSONResponse(
                {"detail": "Sign in required"},
                401,
                headers={"Cache-Control": "no-store"},
            )
        from urllib.parse import urlencode

        destination = (
            (
                browser_navigation.source_path
                + (
                    "?" + browser_navigation.source_query
                    if browser_navigation.source_query
                    else ""
                )
            )
            if browser_navigation
            else path + ("?" + request.url.query if request.url.query else "")
        )
        return RedirectResponse(
            "/login?" + urlencode({"next": destination}),
            303,
            headers={"Cache-Control": "no-store"},
        )
    token = None
    allowed_token = None
    request.state.user = (
        security.principal(request)
        if not worker_route and path != "/health" and not path.startswith("/static/")
        else None
    )
    request.state.workspace_zones = []
    request.state.zone = ""
    if not public:
        with db.Session() as session:
            available = [svc.dump(z) for z in session.scalars(select(Zone))]
        user = request.state.user
        if user and user["zones"] is not None:
            available = [z for z in available if z["id"] in user["zones"]]
        request.state.workspace_zones = available
        if user and user["zones"] is not None and not available:
            return JSONResponse(
                {
                    "detail": "No authorized workspace. Contact the global administrator."
                },
                403,
            )
        machine = security.api_admin(request.headers.get("authorization", ""))
        selected = (
            (browser_navigation.zone if browser_navigation else "")
            or request.headers.get("x-tasker-zone")
            or request.query_params.get("workspace")
            or (
                request.query_params.get("zone")
                if path in ("/", "/api/collections", "/api/collections/summary")
                else ""
            )
            or ("" if machine else request.cookies.get("workspace", ""))
        )
        if not selected and not machine and available:
            selected = available[0]["id"]
        if selected and selected not in {z["id"] for z in available}:
            return JSONResponse(
                {"detail": "Unknown or unauthorized zone workspace"}, 404
            )
        if (
            not selected
            and not machine
            and path
            not in (
                "/",
                "/api/zones",
                "/logout",
                "/workspace",
                "/docs",
                "/openapi.json",
            )
        ):
            return JSONResponse({"detail": "Create your first zone workspace"}, 409)
        request.state.zone = selected
        token = current_zone.set(selected or None)
        if user and user["zones"] is not None:
            allowed_token = allowed_zones.set(user["zones"])
    try:
        redirect = (
            browser_navigation.apply(request, request.state.zone)
            if browser_navigation
            else None
        )
        response = redirect if redirect is not None else await call_next(request)
    finally:
        if token is not None:
            current_zone.reset(token)
        if allowed_token is not None:
            allowed_zones.reset(allowed_token)
    if browser_navigation:
        browser_navigation.persist(request, response)
    if getattr(request.state, "zone", "") and path == "/":
        response.set_cookie(
            "workspace",
            request.state.zone,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=31536000,
        )
    if not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response
