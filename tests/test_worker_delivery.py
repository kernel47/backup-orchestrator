"""A collector outcome is not an acknowledgement from the master."""

import asyncio
import logging

import httpx
import pytest

from worker.agent import work


@pytest.mark.parametrize(
    "responses,acknowledged,requests",
    [
        ([200], True, 1),
        ([503, 200], True, 2),
        ([503] * 5, False, 5),
        ([None] * 5, False, 5),
        ([409], False, 1),
    ],
)
def test_result_delivery_only_logs_completion_after_master_acknowledges(
    monkeypatch, caplog, responses, acknowledged, requests
):
    calls = []

    async def respond(request):
        assert request.url.path == "/api/tasks/attempt-1/result"
        calls.append(request)
        status = responses[len(calls) - 1]
        if status is None:
            raise httpx.ConnectError("Master unavailable", request=request)
        return httpx.Response(status, json={"accepted": status == 200})

    async def execute(task):
        return {"status": "SUCCESS", "summary": "Collected"}, "collector logs"

    async def no_wait(seconds):
        pass

    async def run():
        async with httpx.AsyncClient(
            base_url="http://master", transport=httpx.MockTransport(respond)
        ) as http:
            await work(http, "worker-1", {"task_id": "attempt-1"})

    monkeypatch.setattr("worker.agent.execute", execute)
    monkeypatch.setattr("worker.agent.asyncio.sleep", no_wait)
    with caplog.at_level(logging.INFO, logger="worker"):
        asyncio.run(run())
    assert len(calls) == requests
    assert (
        any("worker-1 attempt-1 SUCCESS" in record.message for record in caplog.records)
        == acknowledged
    )
    assert (
        any(record.message == "Attempt failed" for record in caplog.records)
        != acknowledged
    )
