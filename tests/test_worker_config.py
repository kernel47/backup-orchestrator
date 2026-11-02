import asyncio

import pytest
from pydantic import ValidationError

from orchestrator.config import Settings
from worker import agent


@pytest.mark.parametrize(
    "value, expected", [(None, True), ("true", True), ("false", False)]
)
def test_worker_reads_ssl_verification_from_env_file(
    tmp_path, monkeypatch, value, expected
):
    monkeypatch.delenv("WORKER_VERIFY_SSL", raising=False)
    config = tmp_path / "worker.env"
    config.write_text(f"WORKER_VERIFY_SSL={value}\n" if value is not None else "")
    configured = Settings(_env_file=config)
    monkeypatch.setattr(agent, "settings", configured)
    monkeypatch.setattr(agent, "worker_timezone", lambda value: "UTC")
    monkeypatch.setattr(agent, "runtime_info", lambda: {})
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(agent.time, "tzset"):
        monkeypatch.setattr(agent.time, "tzset", lambda: None)
    received = {}

    class StopBeforeConnecting(Exception):
        pass

    def client(**kwargs):
        received.update(kwargs)
        raise StopBeforeConnecting

    monkeypatch.setattr(agent.httpx, "AsyncClient", client)

    async def start_worker():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda *args: None)
        with pytest.raises(StopBeforeConnecting):
            await agent.run("test-worker")

    asyncio.run(start_worker())
    assert configured.worker_verify_ssl is expected
    assert received["verify"] is expected


def test_invalid_ssl_verification_setting_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKER_VERIFY_SSL", raising=False)
    config = tmp_path / "worker.env"
    config.write_text("WORKER_VERIFY_SSL=invalid\n")
    with pytest.raises(ValidationError, match="worker_verify_ssl"):
        Settings(_env_file=config)


def test_worker_fills_available_slots_before_the_next_poll(monkeypatch):
    monkeypatch.setattr(agent.settings, "worker_concurrency", 2)
    monkeypatch.setattr(agent.settings, "poll_seconds", 10)
    monkeypatch.setattr(agent, "worker_timezone", lambda value: "UTC")
    monkeypatch.setattr(agent, "runtime_info", lambda: {})
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(agent.time, "tzset"):
        monkeypatch.setattr(agent.time, "tzset", lambda: None)
    callbacks = []
    claimed = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def post(http, path, body):
        if path.endswith("/claim"):
            claimed.append({"task_id": str(len(claimed))})
            if len(claimed) == 2:
                asyncio.get_running_loop().call_soon(callbacks[0])
            return claimed[-1]

    async def work(*args):
        await asyncio.Event().wait()

    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop, "add_signal_handler", lambda sig, callback: callbacks.append(callback)
        )
        await asyncio.wait_for(agent.run("test-worker"), timeout=1)

    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(agent, "post", post)
    monkeypatch.setattr(agent, "work", work)
    asyncio.run(run())
    assert len(claimed) == 2


def test_worker_capacity_matches_the_master_heartbeat_contract(monkeypatch):
    from orchestrator.schemas import WorkerRuntime
    from worker import runtime

    configured = Settings(_env_file=None, worker_concurrency=32)
    monkeypatch.setattr(runtime, "settings", configured)
    metadata = WorkerRuntime.model_validate(runtime.runtime_info()["runtime"])
    assert metadata.concurrency == 32
    with pytest.raises(ValidationError, match="worker_concurrency"):
        Settings(_env_file=None, worker_concurrency=33)
