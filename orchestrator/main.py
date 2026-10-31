"""Application assembly, scheduler lifecycle and health endpoint."""

import asyncio
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import auth_pages, catalog_api, db, execution_api, pages, schedules_api
from . import service as svc
from .config import ROOT, settings
from .inventory_api import router as inventory_router
from .middleware import require_login
from .models import Zone


async def scheduler():
    global last_scheduler_tick
    while True:
        try:
            await asyncio.to_thread(svc.tick)
            last_scheduler_tick = time.monotonic()
        except Exception:
            import logging

            logging.getLogger("scheduler").exception(
                "Scheduler tick failed; retrying in 2 seconds"
            )
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    db.init()
    svc.bootstrap()
    task = asyncio.create_task(scheduler()) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


last_scheduler_tick = time.monotonic()
app = FastAPI(
    title="Backup Orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)
app.middleware("http")(require_login)


@app.exception_handler(IntegrityError)
async def conflict(request, error):
    return JSONResponse(
        {
            "detail": "ID already exists or an object is still referenced. Choose another ID."
        },
        409,
    )


for router in (
    inventory_router,
    catalog_api.router,
    schedules_api.router,
    execution_api.router,
    auth_pages.router,
    pages.router,
):
    app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "orchestrator/static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    try:
        with db.Session() as session:
            session.execute(select(Zone.id).limit(1)).first()
    except Exception:
        return JSONResponse({"status": "error", "database": "unavailable"}, 503)
    scheduler_ok = (
        not settings.scheduler_enabled or time.monotonic() - last_scheduler_tick < 30
    )
    data = {
        "status": "ok" if scheduler_ok else "error",
        "product": "Backup Orchestrator",
        "version": "0.1.0",
        "database": "ok",
        "scheduler": settings.scheduler_enabled,
        "scheduler_healthy": scheduler_ok,
    }
    return data if scheduler_ok else JSONResponse(data, 503)
