from datetime import datetime

from sqlalchemy import select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.config import settings
from orchestrator.main import app
from orchestrator.models import Collector, Host, HostGroup, Window
from tests.client import DashboardClient as TestClient

AUTH = {"Authorization": "Bearer " + settings.admin_token}


def group(client, hosts, name="Test group"):
    response = client.post(
        "/api/host-groups", headers=AUTH, json={"name": name, "host_ids": hosts}
    )
    assert response.status_code == 200, response.text
    return response.json()


def schedule(client, g, interval=600, **overrides):
    return client.post(
        "/api/schedules",
        headers=AUTH,
        json={
            "name": "Test schedule",
            "collector_id": "netbackup_jobs",
            "host_group_id": g["id"],
            "interval_seconds": interval,
            **overrides,
        },
    )


def freeze(monkeypatch, value="2026-10-15T10:12:00+00:00"):
    monkeypatch.setattr(svc, "now", lambda: datetime.fromisoformat(value))


def test_group_preview_manual_run_and_idempotent_targeting(database, monkeypatch):
    freeze(monkeypatch)
    with db.transaction() as session:
        session.get(Host, "nbu-emea-02").enabled = False
    # Lifespan sync runs first; disable the host after entering the client.
    with TestClient(app) as client:
        with db.transaction() as session:
            session.get(Host, "nbu-emea-02").enabled = False
        g = group(client, ["nbu-emea-01", "nbu-emea-02", "dd-emea-01"])
        target = client.get(
            "/api/targets",
            params={"collector_id": "netbackup_jobs", "host_group_id": g["id"]},
        ).json()
        assert [h["id"] for h in target["hosts"]] == ["nbu-emea-01"]
        assert {h["reason"] for h in target["skipped"]} == {
            "Host disabled",
            "Scope mismatch",
        }
        request = {
            "collector_id": "netbackup_jobs",
            "host_group_id": g["id"],
            "parameters": {"simulation": "SUCCESS", "group_test": 42},
        }
        assert client.post("/api/group-runs", json=request).status_code == 401
        first = client.post("/api/group-runs", headers=AUTH, json=request).json()
        second = client.post("/api/group-runs", headers=AUTH, json=request).json()
        assert first["ids"] == second["ids"] and len(first["ids"]) == 1
        with db.transaction() as session:
            windows = list(session.scalars(select(Window)))
            assert len(windows) == 1 and windows[0].host_id == "nbu-emea-01"
            assert (
                windows[0].period_start == "2026-10-15T10:00:00Z"
                and windows[0].period_end == "2026-10-15T10:10:00Z"
            )
            assert (
                windows[0].snapshot["host_group_id"] == g["id"]
                and windows[0].snapshot["parameters"]["group_test"] == 42
            )
        task = svc.claim("EMEA-SAT-1")
        assert task and task["host"] == "nbu-emea-01" and task["scope"] == "NetBackup"
        assert svc.claim("APAC-SAT-1") is None


def test_group_schedules_have_independent_intervals_and_conflicts(
    database, monkeypatch
):
    freeze(monkeypatch)
    with TestClient(app) as client:
        a = group(client, ["nbu-emea-01"], "Production")
        b = group(client, ["nbu-emea-03"], "Lab")
        first = schedule(client, a, 600, parameters={"test": "prod"})
        second = schedule(client, b, 3600, parameters={"test": "lab"})
        assert first.status_code == second.status_code == 200
        rule = first.json()
        conflict = schedule(client, a, 300)
        assert conflict.status_code == 422 and "already has" in conflict.text
        # Moving a host into two groups cannot bypass conflict validation.
        changed = client.put(
            "/api/host-groups/" + b["id"],
            headers=AUTH,
            json={"name": "Lab", "host_ids": ["nbu-emea-03", "nbu-emea-01"]},
        )
        assert changed.status_code == 422
        with db.transaction() as session:
            assert session.get(HostGroup, b["id"]).host_ids == ["nbu-emea-03"]
            assert not session.get(Collector, "netbackup_jobs").legacy_schedule
            svc.expected(session, "2026-10-15", "netbackup_jobs")
            windows = list(
                session.scalars(
                    select(Window).where(Window.collector_id == "netbackup_jobs")
                )
            )
            assert len([w for w in windows if w.host_id == "nbu-emea-01"]) == 144
            assert len([w for w in windows if w.host_id == "nbu-emea-03"]) == 24
            assert {w.host_id for w in windows} == {"nbu-emea-01", "nbu-emea-03"}
        svc.tick()
        svc.tick()
        with db.transaction() as session:
            pending = list(
                session.scalars(
                    select(Window).where(
                        Window.collector_id == "netbackup_jobs",
                        Window.status == "PENDING",
                    )
                )
            )
            assert len(pending) == 2
            assert {w.snapshot["parameters"]["test"] for w in pending} == {
                "prod",
                "lab",
            }
        paused = {k: v for k, v in rule.items() if k != "id"}
        paused["enabled"] = False
        assert (
            client.put(
                "/api/schedules/" + rule["id"], headers=AUTH, json=paused
            ).status_code
            == 200
        )
        freeze(monkeypatch, "2026-10-15T10:22:00+00:00")
        svc.tick()
        with db.transaction() as session:
            latest = session.scalar(
                select(Window).where(
                    Window.collector_id == "netbackup_jobs",
                    Window.host_id == "nbu-emea-01",
                    Window.period_start == "2026-10-15T10:10:00Z",
                )
            )
            assert latest.status == "DISABLED"
        paused["enabled"] = True
        assert (
            client.put(
                "/api/schedules/" + rule["id"], headers=AUTH, json=paused
            ).status_code
            == 200
        )
        svc.tick()
        with db.transaction() as session:
            latest = session.scalar(
                select(Window).where(
                    Window.collector_id == "netbackup_jobs",
                    Window.host_id == "nbu-emea-01",
                    Window.period_start == "2026-10-15T10:10:00Z",
                )
            )
            assert latest.status == "PENDING"


def test_group_filtered_planning_never_queues_outside_group(database, monkeypatch):
    freeze(monkeypatch)
    with TestClient(app) as client:
        g = group(client, ["nbu-emea-01"])
        response = client.post(
            "/api/collections/plan",
            headers=AUTH,
            json={
                "collector_id": "netbackup_jobs",
                "date": "2026-10-15",
                "host_group_id": g["id"],
            },
        )
        assert response.status_code == 200, response.text
        with db.transaction() as session:
            pending = list(
                session.scalars(select(Window).where(Window.status == "PENDING"))
            )
            assert pending and {w.host_id for w in pending} == {"nbu-emea-01"}
            assert all(w.snapshot["host_group_id"] == g["id"] for w in pending)
        rows = client.get(
            "/api/collections",
            params={
                "date": "2026-10-15",
                "collector_id": "netbackup_jobs",
                "host_group_id": g["id"],
            },
        ).json()
        assert rows and all(r["host"] == "nbu-emea-01" for r in rows)
        page = client.get("/?collector_id=netbackup_jobs&host_group_id=" + g["id"]).text
        assert 'name="host_group_id"' in page and g["id"] in page


def test_schedule_changes_preserve_history_and_future_windows(database, monkeypatch):
    freeze(monkeypatch)
    with TestClient(app) as client:
        g = group(client, ["nbu-emea-01"])
        rule = schedule(client, g).json()
        with db.transaction() as session:
            svc.expected(session, "2026-10-15", "netbackup_jobs")
            old = list(
                session.scalars(
                    select(Window).where(Window.collector_id == "netbackup_jobs")
                )
            )
            historical = {
                w.id: (w.period_start, w.period_end)
                for w in old
                if w.status != "SCHEDULED"
            }
        body = {k: v for k, v in rule.items() if k != "id"}
        body["interval_seconds"] = 3600
        assert (
            client.put(
                "/api/schedules/" + rule["id"], headers=AUTH, json=body
            ).status_code
            == 200
        )
        with db.transaction() as session:
            for cid, period in historical.items():
                w = session.get(Window, cid)
                assert (w.period_start, w.period_end) == period
            assert not list(
                session.scalars(
                    select(Window).where(
                        Window.collector_id == "netbackup_jobs",
                        Window.status == "SCHEDULED",
                    )
                )
            )
            svc.expected(session, "2026-10-16", "netbackup_jobs")
            future = list(
                session.scalars(
                    select(Window).where(
                        Window.collector_id == "netbackup_jobs",
                        Window.period_start >= "2026-10-16",
                    )
                )
            )
            assert len(future) == 24 and all(
                w.snapshot["interval_seconds"] == 3600 for w in future
            )


def test_disabled_members_cannot_bypass_schedule_overlap_checks(database):
    with TestClient(app) as client:
        with db.transaction() as session:
            session.get(Host, "nbu-emea-02").enabled = False
        a = group(client, ["nbu-emea-01", "nbu-emea-02"], "A")
        b = group(client, ["nbu-emea-03", "nbu-emea-02"], "B")
        assert schedule(client, a).status_code == 200
        assert schedule(client, b, 3600).status_code == 422


def test_database_upgrade_preserves_existing_history(database):
    from sqlalchemy import inspect

    from tests.test_core import one_window

    cid = one_window("FAILED")
    with db.transaction() as session:
        original = svc.dump(session.get(Window, cid))
    with db.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE collectors DROP COLUMN legacy_schedule")
    db.init()
    db.init()
    assert {"host_groups", "schedules"}.issubset(inspect(db.engine).get_table_names())
    with db.transaction() as session:
        assert session.get(Collector, "netbackup_jobs").legacy_schedule
        assert svc.dump(session.get(Window, cid)) == original
