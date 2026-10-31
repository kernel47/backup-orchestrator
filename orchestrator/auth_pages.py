"""Sign-in, workspace switching and sign-out forms."""

import secrets

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import security
from .config import settings
from .security import issue_session, web_token
from .web import templates

router = APIRouter()


def login_page(
    request, error="", status_code=200, username="", next_url="/?view=overview"
):
    challenge = issue_session("login")
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "error": error,
            "username": username,
            "next_url": security.safe_next(next_url),
            "challenge": challenge,
            "configured": bool(settings.auth_password),
        },
        status_code=status_code,
    )
    response.set_cookie(
        "login_csrf",
        challenge,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=1800,
        path="/login",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/login")
def login_form(request: Request, next: str = "/?view=overview"):
    if web_token(request):
        return RedirectResponse(security.safe_next(next), 303)
    return login_page(request, next_url=next)


@router.post("/login")
def login(
    request: Request,
    username: str = Form(default="", max_length=80),
    password: str = Form(default="", max_length=1024),
    csrf: str = Form(default="", max_length=256),
    next: str = Form(default="/?view=overview", max_length=4096),
):
    challenge = request.cookies.get("login_csrf", "")
    if not security.valid(challenge, "login", 1800) or not secrets.compare_digest(
        csrf.encode(), challenge.encode()
    ):
        return login_page(
            request, "This sign-in form expired. Please try again.", 403, username, next
        )
    key = request.client.host if request.client else "unknown"
    if security.login_limited(key):
        response = login_page(
            request,
            "Too many sign-in attempts. Please try again in a few minutes.",
            429,
            username,
            next,
        )
        response.headers["Retry-After"] = "300"
        return response
    if not security.credentials_match(username, password):
        security.login_failure(key)
        return login_page(request, "Invalid username or password.", 401, username, next)
    security.login_success(key)
    response = RedirectResponse(security.safe_next(next), 303)
    response.set_cookie(
        "session",
        issue_session(username=username),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=43200,
        path="/",
    )
    response.delete_cookie("login_csrf", path="/login")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/workspace")
def switch_workspace(
    request: Request, zone_id: str = Form(...), csrf: str = Form(default="")
):
    token = web_token(request)
    if not token or not secrets.compare_digest(csrf.encode(), token.encode()):
        raise HTTPException(403, "Invalid workspace request")
    if zone_id not in {z["id"] for z in request.state.workspace_zones}:
        raise HTTPException(404, "Unknown workspace")
    response = RedirectResponse("/?view=overview&workspace=" + zone_id, 303)
    response.set_cookie(
        "workspace",
        zone_id,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=31536000,
    )
    return response


@router.post("/logout")
def logout(request: Request, csrf: str = Form(default="")):
    token = web_token(request)
    if not token or not secrets.compare_digest(csrf.encode(), token.encode()):
        raise HTTPException(403, "Invalid sign-out request")
    response = RedirectResponse("/login", 303)
    response.delete_cookie("session", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
