import json
import time

from sqlalchemy import func, select

from orchestrator import db
from orchestrator.config import settings
from orchestrator.main import app
from orchestrator.models import Window, Worker
from tests.client import DashboardClient


def test_authenticated_metadata_heartbeat_and_documentation_are_scoped(database):
    runtime = {
        "platform": "Red Hat Enterprise Linux",
        "python_version": "3.12.9",
        "master_endpoint": "https://tasker.example.internal",
        "concurrency": 2,
        "scripts": [
            {"alias": "backup_collector", "kind": "command", "available": False}
        ],
    }
    payload = {
        "worker_id": "EMEA-SAT-1",
        "hostname": "sat-emea-01.example.internal",
        "timezone": "Europe/Paris",
        "runtime": runtime,
    }
    with DashboardClient(app) as client:
        assert client.post("/api/workers/heartbeat", json=payload).status_code == 401
        auth = {"Authorization": "Bearer " + settings.worker_token}
        assert (
            client.post(
                "/api/workers/heartbeat", headers=auth, json=payload
            ).status_code
            == 200
        )
        with db.Session() as s:
            worker = s.get(Worker, "EMEA-SAT-1")
            assert worker.hostname == payload["hostname"] and worker.runtime == runtime
            count = s.scalar(select(func.count()).select_from(Window))
        # Old workers remain compatible and cannot erase existing machine details.
        assert (
            client.post(
                "/api/workers/heartbeat", headers=auth, json={"worker_id": "EMEA-SAT-1"}
            ).status_code
            == 200
        )
        page = client.get("/?view=documentation&workspace=EMEA")
        assert page.status_code == 200
        assert "Workspace topology" in page.text and payload["hostname"] in page.text
        assert (
            "Not installed / unavailable" in page.text
            and "configured collectors" in page.text
        )
        assert 'data-live-view="true"' in page.text
        assert (
            "sat-emea-01.example.internal"
            not in client.get("/?view=documentation&workspace=APAC").text
        )
        with db.Session() as s:
            assert s.scalar(select(func.count()).select_from(Window)) == count
        with db.transaction() as s:
            s.get(Worker, "EMEA-SAT-1").heartbeat = (
                time.time() - settings.offline_seconds - 1
            )
        rows = client.get("/api/workers?workspace=EMEA").json()
        assert next(w for w in rows if w["id"] == "EMEA-SAT-1")["online"] is False
        bad = dict(
            payload,
            runtime={
                **runtime,
                "master_endpoint": "https://name:password@master.example/?token=secret",
            },
        )
        assert (
            client.post("/api/workers/heartbeat", headers=auth, json=bad).status_code
            == 422
        )


def test_worker_runtime_reports_capabilities_without_command_arguments_or_secrets(
    monkeypatch, tmp_path
):
    from worker.runtime import runtime_info

    monkeypatch.setattr(
        "worker.runtime.settings.master_url",
        "https://user:password@master.example:8443/api?token=secret",
    )
    monkeypatch.setattr(
        "worker.runtime.load",
        lambda name: {
            "external": {"command": ["missing-tool", "--token=private"]},
            "fake": "collectors/fake.py",
        },
    )
    monkeypatch.setattr("worker.runtime.shutil.which", lambda command: None)
    info = runtime_info()
    assert info["hostname"] and info["runtime"]["python_version"]
    assert info["runtime"]["master_endpoint"] == "https://master.example:8443"
    assert info["runtime"]["scripts"] == [
        {"alias": "external", "kind": "command", "available": False},
        {"alias": "fake", "kind": "python", "available": True},
    ]
    assert all(
        secret not in json.dumps(info) for secret in ("password", "private", "secret")
    )


def test_worker_metadata_columns_upgrade_idempotently(database):
    with db.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE workers DROP COLUMN hostname")
        connection.exec_driver_sql("ALTER TABLE workers DROP COLUMN runtime")
    db.init()
    db.init()
    with db.Session() as session:
        worker = session.get(Worker, "EMEA-SAT-1")
        assert worker.hostname == "" and worker.runtime == {}


def test_live_and_theoretical_pages_are_separate_and_generic(database):
    with DashboardClient(app) as client:
        live = client.get("/?view=documentation&workspace=EMEA").text
        theory = client.get("/?view=technical-architecture&workspace=EMEA").text
        assert "<h1>Live architecture</h1>" in live
        assert "<h1>Technical architecture</h1>" in theory
        for page in (live, theory):
            assert "Editable HTML" not in page and "Maintain this diagram" not in page
            assert (
                "NetBackup assets" not in page
                and "<h3>backup-collector</h3>" not in page
            )
        assert 'data-live-view="true"' in live and 'data-live-view="false"' in theory
        assert (
            "Connected workers &amp; configuration" in live
            or "Connected workers & configuration" in live
        )
        assert "theory-zones" in theory
        for zone in ("EMEA", "APAC", "AMER"):
            for number in (1, 2):
                assert f"{zone}-SAT-{number}" in theory
        assert (
            "Workers connect · tasks &amp; reports" in theory
            or "Workers connect · tasks & reports" in theory
        )
        assert "Queries assets and exports data." in theory
        assert "What each component does" in theory
        assert "Execution and recovery" in theory and "Capacity and fallback" in theory
        assert "model-execution" in theory
