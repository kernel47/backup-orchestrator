from sqlalchemy import select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import Window, Worker
from tests.client import DashboardClient as TestClient
from tests.test_host_groups import AUTH, freeze, group, schedule


def test_satellite_has_one_zone_and_referenced_zone_cannot_change(database):
    with TestClient(app) as client:
        spec = {
            "id": "NEW-SAT",
            "zone": "EMEA",
            "scopes": ["NetBackup"],
            "concurrency": 2,
        }
        response = client.post("/api/satellites", headers=AUTH, json=spec)
        assert response.status_code == 200 and response.json()["zone"] == "EMEA"
        assert (
            client.post(
                "/api/satellites",
                headers=AUTH,
                json={**spec, "id": "BAD-SAT", "zones": ["EMEA", "APAC"]},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/satellites",
                headers=AUTH,
                json={"id": "EMPTY-SAT", "zones": [], "scopes": ["NetBackup"]},
            ).status_code
            == 422
        )
        move = {
            "id": "EMEA-SAT-1",
            "zone": "APAC",
            "scopes": ["NetBackup", "DataDomain", "TapeLibrary"],
            "concurrency": 2,
        }
        assert (
            client.put(
                "/api/satellites/EMEA-SAT-1", headers=AUTH, json=move
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/satellites/NEW-SAT", headers=AUTH, json={**spec, "zone": "APAC"}
            ).status_code
            == 200
        )
        with db.Session() as session:
            assert session.get(Worker, "EMEA-SAT-1").zones == ["EMEA"]


def test_cross_zone_inventory_routing_is_rejected(database):
    with TestClient(app) as client:
        payload = {
            "version": 1,
            "source_id": "routing-test",
            "hosts": [
                {
                    "id": "new-nbu",
                    "zone": "EMEA",
                    "scope": "NetBackup",
                    "preferred_worker": "APAC-SAT-1",
                }
            ],
        }
        assert (
            client.post(
                "/api/inventory/discover", headers=AUTH, json=payload
            ).status_code
            == 422
        )
        payload["hosts"][0]["preferred_worker"] = "EMEA-SAT-1"
        payload["hosts"][0]["fallback_worker"] = "AMER-SAT-1"
        assert (
            client.post(
                "/api/inventory/discover", headers=AUTH, json=payload
            ).status_code
            == 422
        )
        payload["hosts"][0]["fallback_worker"] = "EMEA-SAT-2"
        assert (
            client.post(
                "/api/inventory/discover", headers=AUTH, json=payload
            ).status_code
            == 200
        )


def test_collector_execution_stays_in_its_workspace(database, monkeypatch):
    freeze(monkeypatch)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/host-groups",
                headers=AUTH,
                json={"name": "Invalid", "host_ids": ["nbu-emea-01", "nbu-apac-01"]},
            ).status_code
            == 422
        )
        g = group(client, ["nbu-emea-01"])
        assert schedule(client, g).status_code == 200
        with db.transaction() as session:
            svc.expected(session, "2026-10-15", "netbackup_jobs")
            for row in session.scalars(
                select(Window).where(Window.collector_id == "netbackup_jobs")
            ):
                if row.period_start == "2026-10-15T10:00:00Z":
                    row.status = "PENDING"
        page = client.get(
            "/?view=runs&collector_id=netbackup_jobs&date=2026-10-15&workspace=EMEA"
        ).text
        assert "nbu-apac-01" not in page and "nbu-amer-01" not in page
        assert svc.claim("APAC-SAT-1") is None
        assert svc.claim("AMER-SAT-1") is None
        task = svc.claim("EMEA-SAT-1")
        assert task and task["host"] == "nbu-emea-01"
