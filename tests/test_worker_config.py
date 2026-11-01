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
