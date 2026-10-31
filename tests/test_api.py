from orchestrator import service as svc
from orchestrator.config import settings
from orchestrator.main import app
from tests.client import DashboardClient as TestClient
from tests.test_core import one_window


def test_pages_filters_and_login(database):
    one_window("FAILED")
    with TestClient(app) as c:
        for url in [
            "/",
            "/?view=workers",
            "/?view=settings",
            "/?view=runs",
            "/?collector_id=netbackup_jobs&date=2026-10-15",
            "/docs",
        ]:
            r = c.get(url)
            assert r.status_code == 200, (url, r.text)
        assert (
            c.get(
                "/api/collections", params={"date": "2026-10-15", "status": "FAILED"}
            ).json()[0]["zone"]
            == "EMEA"
        )
        assert c.post("/api/collections/retry", json={"ids": []}).status_code == 401
        c.cookies.clear()
        import re

        page = c.get("/login")
        challenge = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        assert (
            c.post(
                "/login",
                data={
                    "username": settings.auth_username,
                    "password": "invalid",
                    "csrf": challenge,
                },
            ).status_code
            == 401
        )
        page = c.get("/login")
        challenge = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        assert (
            c.post(
                "/login",
                data={
                    "username": settings.auth_username,
                    "password": settings.auth_password,
                    "csrf": challenge,
                },
            ).status_code
            == 200
        )
        session = c.cookies.get("session")
        assert session
        assert c.post("/api/collections/retry", json={"ids": []}).status_code == 401
        assert (
            c.post(
                "/api/collections/retry",
                headers={"X-CSRF-Token": session},
                json={"ids": []},
            ).status_code
            == 200
        )


def test_collector_update_create_and_heartbeat(database):
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + settings.admin_token}
        worker = {"Authorization": "Bearer " + settings.worker_token}
        fields = {
            "interval_seconds": 3600,
            "timeout_seconds": 10,
            "max_retries": 1,
            "enabled": True,
            "parameters": {"x": 1},
        }
        assert (
            c.put(
                "/api/collectors/netbackup_jobs", headers=admin, json=fields
            ).status_code
            == 200
        )
        assert c.get("/api/collectors").json()[0]["interval_seconds"] == 3600
        body = {
            **fields,
            "id": "nbu_clients",
            "name": "Clients",
            "scope": "NetBackup",
            "script": "fake",
        }
        assert c.post("/api/collectors", headers=admin, json=body).status_code == 200
        assert c.post("/api/collectors", headers=admin, json=body).status_code == 409
        assert (
            c.post(
                "/api/workers/heartbeat",
                headers=worker,
                json={"worker_id": "EMEA-SAT-1"},
            ).status_code
            == 200
        )
        assert (
            c.post("/api/workers/claim", json={"worker_id": "EMEA-SAT-1"}).status_code
            == 401
        )
        assert c.get("/api/collections", params={"date": "invalid"}).status_code == 422


def test_hour_view_and_pause_resume_future_windows(database):
    from datetime import timedelta

    with TestClient(app) as c:
        response = c.get(
            "/",
            params={
                "collector_id": "netbackup_jobs",
                "date": "2026-10-15",
                "zone": "EMEA",
                "start": "2026-10-15T10:00:00Z",
                "end": "2026-10-15T11:00:00Z",
            },
        )
        assert response.status_code == 200 and "24 of 24" in response.text
        assert "start=2026-10-15T10" in response.text
        future = (svc.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        c.get(
            "/api/collections",
            params={"collector_id": "netbackup_jobs", "date": future},
        )
        admin = {"Authorization": "Bearer " + settings.admin_token}
        fields = {
            "interval_seconds": 3600,
            "timeout_seconds": 10,
            "max_retries": 1,
            "enabled": False,
            "parameters": {},
        }
        assert (
            c.put(
                "/api/collectors/netbackup_jobs", headers=admin, json=fields
            ).status_code
            == 200
        )
        assert all(
            r["status"] == "DISABLED"
            for r in c.get(
                "/api/collections",
                params={"collector_id": "netbackup_jobs", "date": future},
            ).json()
        )
        fields["enabled"] = True
        assert (
            c.put(
                "/api/collectors/netbackup_jobs", headers=admin, json=fields
            ).status_code
            == 200
        )
        rows = c.get(
            "/api/collections",
            params={"collector_id": "netbackup_jobs", "date": future},
        ).json()
        assert len(rows) == 4 * 24 and all(r["status"] == "SCHEDULED" for r in rows)
