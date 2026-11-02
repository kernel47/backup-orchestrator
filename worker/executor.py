import asyncio
import json
import os
import signal
import tempfile
import time
from datetime import datetime, timezone

import yaml

from orchestrator.config import load
from orchestrator.schemas import CollectorResult
from worker.launch import prepare_launch
from worker.reports import backup_collector_report

REPORT_LIMIT = 65536
LOG_LIMIT = 16000


def log_tail(stream):
    stream.seek(0, 2)
    stream.seek(max(0, stream.tell() - LOG_LIMIT))
    return stream.read().decode(errors="replace")


def failure(reason, summary):
    return {
        "status": {"TIMEOUT": "TIMEOUT", "INVALID_REPORT": "UNKNOWN"}.get(
            reason, "FAILURE"
        ),
        "summary": summary,
        "error": summary,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "details": {"reason": reason},
    }


async def _execute(task):
    try:
        catalog = load("worker_scripts.yaml")
    except (OSError, ValueError, yaml.YAMLError):
        return failure(
            "COMMAND_CONFIG",
            "Unable to read worker_scripts.yaml; check YAML and permissions",
        ), ""
    try:
        launch = prepare_launch(catalog, task)
    except (ValueError, TypeError, KeyError) as error:
        return failure("COMMAND_CONFIG", str(error)), ""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = await asyncio.create_subprocess_exec(
                *launch.command,
                cwd=launch.working_directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=output,
                stderr=errors,
                start_new_session=True,
            )
        except OSError:
            return failure(
                "COMMAND_START",
                "Unable to start collector; check executable, working directory and permissions",
            ), ""
        try:
            await asyncio.wait_for(
                process.communicate(json.dumps(task).encode()),
                timeout=max(0.1, task["timeout_seconds"] - 0.5),
            )
            output.seek(0)
            raw = output.read(REPORT_LIMIT + 1)
            logs = log_tail(errors)
            if len(raw) > REPORT_LIMIT:
                return failure("INVALID_REPORT", "Execution report exceeds 64 KB"), logs
            try:
                payload = json.loads(raw)
                if launch.report_format == "backup_collector":
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
            return failure("TIMEOUT", "Collector execution timed out"), log_tail(errors)


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
