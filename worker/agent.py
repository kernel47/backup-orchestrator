import asyncio
import logging
import os
import signal
import time

import httpx

from orchestrator.config import settings

from .executor import execute
from .runtime import runtime_info
from .timezone import worker_timezone

log = logging.getLogger("worker")


async def post(http, path, data):
    response = await http.post(path, json=data)
    response.raise_for_status()
    return response.json()


async def send_result(http, task_id, payload):
    for attempt in range(5):
        try:
            return await post(http, f"/api/tasks/{task_id}/result", payload)
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if (
                isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code < 500
            ):
                raise
            if attempt == 4:
                raise
            await asyncio.sleep(1)


async def work(http, worker_id, task):
    body = {"worker_id": worker_id}
    execution = asyncio.create_task(execute(task))
    try:
        while not execution.done():
            done, _ = await asyncio.wait(
                [execution],
                timeout=max(0.2, task.get("lease_seconds", settings.lease_seconds) / 3),
            )
            if not done:
                await post(http, f"/api/tasks/{task['task_id']}/heartbeat", body)
        result, logs = await execution
        log.info(
            "Collector finished worker=%s task=%s host=%s status=%s summary=%s",
            worker_id,
            task["task_id"],
            task.get("host", ""),
            result["status"],
            result.get("summary", ""),
        )
        if logs:
            logging.getLogger("collector").info(
                "task=%s output:\n%s", task["task_id"], logs
            )
        payload = dict(body, result=result, logs=logs)
        await send_result(http, task["task_id"], payload)
        log.info("%s %s %s", worker_id, task["task_id"], result["status"])
    except Exception:
        log.exception("Attempt failed")
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


async def run(worker_id):
    token = settings.worker_token
    local_zone = worker_timezone(settings.worker_timezone)
    os.environ["TZ"] = local_zone
    if hasattr(time, "tzset"):
        time.tzset()
    metadata = runtime_info()
    metadata_at = time.monotonic()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    active = set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(
        base_url=settings.master_url,
        headers={"Authorization": "Bearer " + token},
        verify=settings.worker_verify_ssl,
        timeout=15,
    ) as http:
        body = {"worker_id": worker_id}

        try:
            while not stop.is_set():
                try:
                    if time.monotonic() - metadata_at >= 60:
                        metadata = runtime_info()
                        metadata_at = time.monotonic()
                    await post(
                        http,
                        "/api/workers/heartbeat",
                        dict(body, timezone=local_zone, **metadata),
                    )
                    active = {t for t in active if not t.done()}
                    while (
                        len(active) < settings.worker_concurrency and not stop.is_set()
                    ):
                        task = await post(http, "/api/workers/claim", body)
                        if not task:
                            break
                        active.add(asyncio.create_task(work(http, worker_id, task)))
                except httpx.HTTPError:
                    log.warning("Master unavailable", exc_info=True)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
