"""Shared template setup and dashboard context."""

from urllib.parse import urlsplit, urlunsplit

from fastapi.templating import Jinja2Templates

from .cadences import cadence_label
from .config import ROOT, settings
from .scheduling import local_label


def local_url(value):
    """Keep navigation on the browser's origin behind TLS-terminating proxies."""
    parts = urlsplit(str(value))
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


templates = Jinja2Templates(directory=ROOT / "orchestrator/templates")
templates.env.filters["local_url"] = local_url
templates.context_processors.append(
    lambda request: {
        "active_zone": getattr(request.state, "zone", ""),
        "workspace_zones": getattr(request.state, "workspace_zones", []),
        "auth_username": request.state.user["username"]
        if getattr(request.state, "user", None)
        else settings.auth_username,
        "user_role": request.state.user["role"]
        if getattr(request.state, "user", None)
        else "admin",
    }
)
templates.env.globals.update(
    cadence_label=cadence_label,
    workspace_name=settings.workspace_name,
    local_label=local_label,
    auth_username=settings.auth_username,
)
