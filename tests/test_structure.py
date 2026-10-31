import pytest

from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import Collector, Window, Worker, Zone
from orchestrator.schemas import CollectorUpdate
from tests.client import DashboardClient as TestClient
from tests.test_core import one_window


def test_invalid_cadence():
    with pytest.raises(ValueError):
        CollectorUpdate(
            interval_seconds=77, timeout_seconds=10, max_retries=1, enabled=True
        )


@pytest.mark.parametrize(
    "seconds,count", [(60, 1440), (300, 288), (900, 96), (7200, 12)]
)
def test_generation_and_idempotence_for_new_cadences(database, seconds, count):
    with db.transaction() as s:
        s.get(Collector, "netbackup_jobs").interval_seconds = seconds
        ids = svc.expected(s, "2026-10-15", "netbackup_jobs", host="nbu-emea-01")
        assert len(ids) == count
        assert ids == svc.expected(
            s, "2026-10-15", "netbackup_jobs", host="nbu-emea-01"
        )
        windows = [s.get(Window, cid) for cid in ids]
        assert all(
            (svc.dt(w.period_end) - svc.dt(w.period_start)).total_seconds() == seconds
            for w in windows
        )
        assert len({(w.period_start, w.period_end) for w in windows}) == count


def test_zone_filters_and_worker_authorization(database):
    cid = one_window()
    with db.transaction() as s:
        s.add(Zone(id="EMEA-EDGE", name="EMEA edge", enabled=True))
        # Hosts retain immutable workspace ownership; change worker authorization
        # to exercise routing without moving an existing host between workspaces.
        s.get(Worker, "EMEA-SAT-1").zones = ["EMEA-EDGE"]
        s.get(Worker, "EMEA-SAT-2").zones = ["EMEA"]
        assert len(svc.rows(s, {"zone": "EMEA", "status": "PENDING"})) == 1
        assert not svc.rows(s, {"zone": "APAC"})
    assert svc.claim("EMEA-SAT-1") is None
    assert svc.claim("APAC-SAT-1") is None
    task = svc.claim("EMEA-SAT-2")
    assert task and task["collection_id"] == cid and task["zone"] == "EMEA"


def test_only_zones_in_public_model_and_old_filters_refused(database):
    from orchestrator.config import settings

    with TestClient(app) as client:
        assert "regions" not in client.get("/api/catalog").json()
        assert all("region" not in x for x in client.get("/api/hosts").json())
        assert all("region" not in x for x in client.get("/api/workers").json())
        html = client.get("/").text
        assert 'name="region"' not in html and 'name="zone"' in html
        assert (
            client.get("/api/collections", params={"region": "EMEA"}).status_code == 422
        )
        headers = {"Authorization": "Bearer " + settings.admin_token}
        assert (
            client.post(
                "/api/collections/plan",
                headers=headers,
                json={
                    "collector_id": "netbackup_jobs",
                    "date": "2026-10-15",
                    "region": "EMEA",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/collections/retry",
                headers=headers,
                json={"filters": {"region": "EMEA"}},
            ).status_code
            == 422
        )


def test_worker_without_authorized_zones_cannot_claim(database):
    one_window()
    with db.transaction() as s:
        s.get(Worker, "EMEA-SAT-1").zones = []
        s.get(Worker, "EMEA-SAT-2").zones = []
    assert svc.claim("EMEA-SAT-1") is None
    assert svc.claim("EMEA-SAT-2") is None
