"""Rotating local service logs, console output and HTTP timings."""

import atexit
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LoggedOutput:
    """Keep stdout/stderr visible and copy complete lines into the service log."""

    def __init__(self, stream, logger, level):
        self.stream = stream
        self.logger = logger
        self.level = level
        self.local = threading.local()

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, text):
        self.stream.write(text)
        if getattr(self.local, "emitting", False):
            return len(text)
        pending = getattr(self.local, "pending", "") + text
        lines = pending.split("\n")
        self.local.pending = lines.pop()
        for line in lines:
            self.record(line)
        return len(text)

    def record(self, line):
        self.local.emitting = True
        try:
            if line.strip():
                self.logger.log(self.level, line.rstrip("\r"))
        finally:
            self.local.emitting = False

    def flush(self):
        self.stream.flush()
        if getattr(self.local, "emitting", False):
            return
        pending = getattr(self.local, "pending", "")
        self.local.pending = ""
        if pending:
            self.record(pending)


def configure_logging(role, directory, level="INFO"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    file = RotatingFileHandler(
        directory / f"{role}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file.setFormatter(formatter)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[console, file], force=True)
    # Successful worker polls would otherwise fill the file every second.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for name, stream, severity in (
        ("stdout", "stdout", logging.INFO),
        ("stderr", "stderr", logging.ERROR),
    ):
        logger = logging.getLogger(name)
        logger.handlers = [file]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        output = LoggedOutput(getattr(sys, stream), logger, severity)
        setattr(sys, stream, output)
        atexit.register(output.flush)
    logging.captureWarnings(True)
    logging.getLogger("service").info(
        "Starting %s; local log: %s", role, file.baseFilename
    )


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status = 500

        async def response(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, response)
        finally:
            elapsed = (time.monotonic() - started) * 1000
            path = scope.get("path", "").replace("\n", "\\n").replace("\r", "\\r")
            routine = (
                path.startswith("/static/")
                or path
                in (
                    "/health",
                    "/api/workers/claim",
                    "/api/workers/heartbeat",
                )
                or (path.startswith("/api/tasks/") and path.endswith("/heartbeat"))
            )
            if not routine or status >= 400 or elapsed >= 1000:
                level = (
                    logging.ERROR
                    if status >= 500
                    else logging.WARNING
                    if status >= 400 or elapsed >= 1000
                    else logging.INFO
                )
                logging.getLogger("http").log(
                    level,
                    "%s %s status=%s duration_ms=%.1f",
                    scope["method"],
                    path,
                    status,
                    elapsed,
                )
