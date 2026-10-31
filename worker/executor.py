import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone

from orchestrator.config import ROOT, load
from orchestrator.schemas import CollectorResult
from worker.commands import build_command
from worker.reports import backup_collector_report


def failure(reason, summary):
    return {
        "status": "TIMEOUT"
        if reason == "TIMEOUT"
        else "UNKNOWN"
        if reason == "INVALID_REPORT"
        else "FAILURE",
        "summary": summary,
        "error": summary,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "details": {"reason": reason},
    }


async def _execute(task):
    try:
        catalog = load("worker_scripts.yaml")
        definition = catalog.get(task["script"])
        if definition is None:
            raise ValueError("Script is not allowed on this satellite")
        report_format = (
            definition.get("report_format", "standard")
            if isinstance(definition, dict)
            else "standard"
        )
        if report_format not in ("standard", "backup_collector"):
            raise ValueError("Unknown command report format")
        command = (
            build_command(definition, task)
            if isinstance(definition, dict)
            else [sys.executable, "-m", "worker.runner", task["script"]]
        )
    except (ValueError, TypeError, KeyError) as error:
        return failure("COMMAND_CONFIG", str(error)), ""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=ROOT,
                stdin=asyncio.subprocess.PIPE,
                stdout=output,
                stderr=errors,
                start_new_session=True,
            )
        except OSError:
            return failure(
                "COMMAND_START",
                "Unable to start collector; check executable installation and permissions",
            ), ""
        try:
            await asyncio.wait_for(
                process.communicate(json.dumps(task).encode()),
                timeout=max(0.1, task["timeout_seconds"] - 0.5),
            )
            output.seek(0)
            raw = output.read(65537)
            errors.seek(0, 2)
            size = errors.tell()
            errors.seek(max(0, size - 16000))
            logs = errors.read().decode(errors="replace")
            if len(raw) > 65536:
                return failure("INVALID_REPORT", "Execution report exceeds 64 KB"), logs
            try:
                payload = json.loads(raw)
                if report_format == "backup_collector":
                    payload = backup_collector_report(payload)
                report = CollectorResult.model_validate(payload).model_dump(mode="json")
            except ValueError:
                if process.returncode != 0:
                    return failure(
                        "SCRIPT_ERROR",
                        f"Collector exited with code {process.returncode}",
                    ), logs
                return failure("INVALID_REPORT", "Invalid execution report"), logs
            if process.returncode != 0 and report["status"] == "SUCCESS":
                report.update(
                    status="FAILURE",
                    summary=f"Collector reported success but exited with code {process.returncode}",
                    error=f"Exit code {process.returncode}",
                )
            return report, logs
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            errors.seek(0, 2)
            size = errors.tell()
            errors.seek(max(0, size - 16000))
            return failure(
                "TIMEOUT", "Collector execution timed out"
            ), errors.read().decode(errors="replace")


async def execute(task):
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    result, logs = await _execute(task)
    result.update(
        started_at=started.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=round(time.monotonic() - clock, 3),
    )
    for field in ("attempt_number", "task_id", "collector_id", "host", "worker_id"):
        if task.get(field) is not None:
            result[field] = task[field]
    if not result.get("period_start"):
        result["period_start"] = task.get("period_start")
    if not result.get("period_end"):
        result["period_end"] = task.get("period_end")
    return result, logs
