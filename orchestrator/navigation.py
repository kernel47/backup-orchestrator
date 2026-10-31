"""Clean browser routes and per-workspace/page filter preferences.

The query-based HTML interface remains supported for integrations and old links.
API routes and authorization are unchanged.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, quote, unquote, urlencode

from fastapi.responses import RedirectResponse

PAGES = {
    "overview": "/overview",
    "collectors": "/collectors",
    "runs": "/runs",
    "schedules": "/schedules",
    "hosts": "/inventory/hosts",
    "groups": "/inventory/groups",
    "sources": "/inventory/sources",
    "workspace": "/configuration/dashboard",
    "workers": "/configuration/satellites",
    "zones": "/configuration/zones",
    "scopes": "/configuration/scopes",
    "settings": "/configuration/collectors",
    "documentation": "/documentation/live",
    "technical-architecture": "/documentation/technical",
}
FILTERS = {
    "date",
    "period",
    "scope",
    "status",
    "host",
    "host_group_id",
    "collector_id",
    "schedule_id",
    "worker",
    "start",
    "end",
    "hour",
    "page",
    "page_size",
    "group_id",
    "sync_id",
}
MAX_COOKIE = 2400


def set_query(request, params):
    request.scope["query_string"] = urlencode(params).encode()
    request.__dict__.pop("_query_params", None)
    request.__dict__.pop("_url", None)


def route_for(view, params):
    if view == "collection-detail":
        return "/collections/" + quote(params["collection_id"], safe="")
    path = PAGES[view]
    if view == "settings":
        if params.get("collector_id"):
            path += "/" + quote(params["collector_id"], safe="")
            if params.get("mode") == "edit":
                path += "/edit"
        elif params.get("new") == "1":
            path += "/new"
    elif view == "schedules":
        if params.get("schedule_id"):
            path += "/" + quote(params["schedule_id"], safe="")
        elif (
            params.get("new") == "1"
            or params.get("collector_id")
            or params.get("group_id")
        ):
            path += "/new"
    elif view == "sources" and params.get("sync_id"):
        path += "/discoveries/" + quote(params["sync_id"], safe="")
    return path


def decode_route(path):
    if re.fullmatch(r"/collections/[^/]+", path):
        return "collection-detail", {"collection_id": unquote(path.split("/")[-1])}
    for view, base in sorted(PAGES.items(), key=lambda item: -len(item[1])):
        if path == base:
            return view, {}
        if view in ("settings", "schedules") and path.startswith(base + "/"):
            tail = path[len(base) + 1 :]
            if tail == "new":
                return view, {"new": "1"}
            edit = tail.endswith("/edit") and view == "settings"
            identifier = tail[:-5] if edit else tail
            if identifier and "/" not in identifier:
                return view, {
                    ("collector_id" if view == "settings" else "schedule_id"): unquote(
                        identifier
                    ),
                    **({"mode": "edit"} if edit else {}),
                }
        if view == "sources" and path.startswith(base + "/discoveries/"):
            identifier = path[len(base + "/discoveries/") :]
            if identifier and "/" not in identifier:
                return view, {"sync_id": unquote(identifier)}
    return None


def read_cookie(request, key):
    try:
        value = request.cookies.get(key, "")
        if len(value) > MAX_COOKIE:
            return {}
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def normalize(params, view):
    data = {
        k: v
        for k, v in params.items()
        if k in FILTERS and isinstance(v, str) and 0 < len(v) <= 160
    }
    for key in ("page", "page_size", "hour"):
        if key in data:
            low, high = (
                (1, 100000)
                if key == "page"
                else (10, 100)
                if key == "page_size"
                else (0, 23)
            )
            if not data[key].isdigit() or not low <= int(data[key]) <= high:
                data.pop(key)
    if data.get("period") not in (None, "day", "week", "month"):
        data.pop("period")
    if "date" in data:
        try:
            data["date"] = datetime.strptime(data["date"], "%Y-%m-%d").strftime(
                "%Y-%m-%d"
            )
        except ValueError:
            data.pop("date")
    for key in ("start", "end"):
        if key in data:
            try:
                if (
                    datetime.fromisoformat(data[key].replace("Z", "+00:00")).tzinfo
                    is None
                ):
                    data.pop(key)
            except ValueError:
                data.pop(key)
    if data.get("status") not in (
        None,
        "ATTENTION",
        "RECOVERED",
        "SUCCESS",
        "FAILED",
        "TIMEOUT",
        "MISSING",
        "UNKNOWN",
        "RUNNING",
        "PENDING",
        "SCHEDULED",
        "DISABLED",
        "WORKER_OFFLINE",
    ):
        data.pop("status")
    # Prevent stale saved filters from leaving a page stuck after configuration changes.
    from sqlalchemy import select

    from . import db
    from .models import Collector, Host, HostGroup, Schedule, Scope, Worker

    with db.Session() as session:
        for key, model in (
            ("collector_id", Collector),
            ("host", Host),
            ("host_group_id", HostGroup),
            ("schedule_id", Schedule),
            ("worker", Worker),
            ("group_id", HostGroup),
        ):
            if key not in data:
                continue
            obj = session.get(model, data[key])
            if not obj or getattr(obj, "deleted", False):
                data.pop(key)
            elif (
                key == "collector_id"
                and data.get("scope")
                and obj.scope != data["scope"]
            ):
                data.pop("scope")
        if "scope" in data:
            known = (
                set(session.scalars(select(Scope.id)))
                | set(session.scalars(select(Collector.scope)))
                | set(session.scalars(select(Host.scope)))
            )
            if data["scope"] not in known:
                data.pop("scope")
    if view != "runs":
        data.pop("status", None)
    return data


@dataclass
class BrowserNavigation:
    source_path: str
    source_query: str
    view: str
    params: dict
    route_params: dict
    zone: str = ""
    header_state: bool = False
    cookie_key: str = ""
    cookie_value: str = ""

    def apply(self, request, zone):
        self.zone = zone
        if self.view == "collectors" and self.params.get("collector_id"):
            from . import db
            from .models import Collector

            with db.Session() as session:
                exists = session.get(Collector, self.params["collector_id"]) is not None
            if exists:
                self.view = "runs"
            else:
                self.params.pop("collector_id")
        if self.view == "settings" and self.params.get("section") == "inventory":
            self.view = "hosts"
            self.params = {}
            self.route_params = {}
        if not zone and self.view not in (
            "documentation",
            "technical-architecture",
            "zones",
        ):
            self.view = "zones"
            self.params = {}
            self.route_params = {}
        variant = ""
        if (
            self.params.get("new") == "1"
            or self.view == "schedules"
            and not self.params.get("schedule_id")
            and (self.params.get("collector_id") or self.params.get("group_id"))
        ):
            variant = "_new"
        elif (
            self.view == "settings"
            and self.params.get("collector_id")
            or self.view == "schedules"
            and self.params.get("schedule_id")
            or self.view == "sources"
            and self.params.get("sync_id")
        ):
            variant = "_detail"
        self.cookie_key = (
            "tasker_nav_"
            + self.view.replace("-", "_")
            + variant
            + "_"
            + hashlib.sha256(zone.encode()).hexdigest()[:8]
        )
        explicit = any(
            k not in ("view", "workspace", "section", "zone")
            and k not in self.route_params
            for k in self.params
        )
        raw = (
            self.params
            if explicit or self.header_state
            else read_cookie(request, self.cookie_key)
        )
        preferences = normalize(raw, self.view)
        # Object identity belongs to its path, not the preference cookie.
        params = {**preferences, **self.route_params}
        for key in ("new", "mode"):
            if self.params.get(key):
                params[key] = self.params[key]
        canonical = route_for(self.view, params)
        if zone:
            canonical = "/w/" + quote(zone, safe="") + canonical
        saved = {k: v for k, v in preferences.items() if k not in self.route_params}
        # Opening a detail page does not overwrite the list's saved filters.
        self.cookie_value = (
            base64.urlsafe_b64encode(json.dumps(saved, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        query = {**params, "view": self.view}
        if zone:
            query["workspace"] = zone
        if self.view == "settings":
            query["section"] = "collectors"
        set_query(request, query)
        if self.source_path != canonical or self.source_query:
            return RedirectResponse(canonical, 303)
        return None

    def persist(self, request, response):
        if (
            self.cookie_key
            and len(self.cookie_value) <= MAX_COOKIE
            and request.cookies.get(self.cookie_key) != self.cookie_value
        ):
            response.set_cookie(
                self.cookie_key,
                self.cookie_value,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                max_age=2592000,
            )


def prepare(request):
    path = request.scope["path"]
    query = request.scope.get("query_string", b"").decode(errors="replace")
    params = dict(parse_qsl(query, keep_blank_values=True))
    zone = ""
    route_path = path
    match = re.match(r"^/w/([A-Za-z0-9_-]{1,64})(/.*)$", path)
    if match:
        zone, route_path = match.groups()
    route = decode_route(route_path)
    browser = (
        "text/html" in request.headers.get("accept", "")
        or request.headers.get("x-tasker-view") == "1"
    )
    if request.method != "GET" or (not route and not (path == "/" and browser)):
        return None
    if route:
        view, route_params = route
    else:
        view, route_params = params.get("view", "collectors"), {}
    if view not in PAGES and view != "collection-detail":
        view = "collectors"
    if view == "collection-detail" and not match and not browser:
        return None
    header_state = False
    state = request.headers.get("x-tasker-page-state", "")
    if state and len(state) < 4096:
        try:
            data = json.loads(state)
            if isinstance(data, dict):
                params = {
                    **{k: v for k, v in data.items() if isinstance(v, str)},
                    **params,
                }
                header_state = True
        except ValueError:
            pass
    params.update(route_params)
    params["view"] = view
    if zone:
        params["workspace"] = zone
    internal_path = route_path if view == "collection-detail" else "/"
    request.scope["path"] = internal_path
    request.scope["raw_path"] = internal_path.encode()
    set_query(request, params)
    return BrowserNavigation(
        path, query, view, params, route_params, zone, header_state
    )
