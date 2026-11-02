"""Local files must capture diagnostics without losing console output."""

import asyncio
import logging
import subprocess
import sys

import pytest

from orchestrator.logging_setup import RequestLoggingMiddleware


@pytest.mark.parametrize("role", ["master", "worker"])
def test_service_log_captures_console_output_exceptions_and_rotates(tmp_path, role):
    code = """
import logging, sys
from logging.handlers import RotatingFileHandler
from orchestrator.logging_setup import configure_logging
configure_logging(sys.argv[1], sys.argv[2])
for handler in logging.getLogger().handlers:
    if isinstance(handler, RotatingFileHandler):
        handler.maxBytes = 1000
for number in range(100):
    logging.info('Rotation exercise %s', number)
print('Output marker')
print('Error output marker', file=sys.stderr)
try:
    raise RuntimeError('Exception marker')
except RuntimeError:
    logging.exception('Failure marker')
sys.stdout.write('Partial line marker')
sys.stdout.flush()
raise RuntimeError('Unhandled marker')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, role, str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    files = list(tmp_path.glob(f"{role}.log*"))
    saved = "\n".join(path.read_text() for path in files)
    assert len(files) == 6 and (tmp_path / f"{role}.log.5").exists()
    for marker in (
        "Output marker",
        "Error output marker",
        "Failure marker",
        "Exception marker",
        "Traceback",
        "Partial line marker",
        "RuntimeError: Unhandled marker",
    ):
        assert marker in saved
    assert result.stdout.count("Output marker") == 1
    assert result.stderr.count("Error output marker") == 1


@pytest.mark.parametrize(
    "path,status,delay,expected_level",
    [
        ("/w/EMEA/runs", 200, 0, logging.INFO),
        ("/api/workers/heartbeat", 200, 0, None),
        ("/api/workers/heartbeat", 503, 0, logging.ERROR),
        ("/health", 200, 1.01, logging.WARNING),
    ],
)
def test_http_logging_reports_slow_requests_and_errors_without_query_secrets(
    caplog, path, status, delay, expected_level
):
    async def app(scope, receive, send):
        if delay:
            await asyncio.sleep(delay)
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger="http"):
        asyncio.run(
            RequestLoggingMiddleware(app)(
                {
                    "type": "http",
                    "method": "GET",
                    "path": path,
                    "query_string": b"token=private-value",
                },
                None,
                send,
            )
        )
    records = [record for record in caplog.records if record.name == "http"]
    if expected_level is None:
        assert not records
    else:
        assert len(records) == 1
        assert records[0].levelno == expected_level
        assert f"GET {path} status={status} duration_ms=" in records[0].message
    assert "private-value" not in caplog.text
