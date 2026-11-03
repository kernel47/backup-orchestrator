"""Historical collection starts are explicit, bounded and preserve prior results."""

from sqlalchemy import func, select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import Collector, Window, Worker
from tests.client import DashboardClient as TestClient
from tests.test_host_groups import AUTH, freeze, group, schedule


def setup_schedule(client, **options):
    g = group(client, ["nbu-emea-01"])
    with db.transaction() as session:
        collector = session.get(Collector, "netbackup_jobs")
        collector.legacy_schedule = False
        collector.parameters = {"scope": "netbackup", "type": "jobs"}
    response = schedule(client, g, interval=3600, **options)
    assert response.status_code == 200, response.text


def launch(client, **options):
    return client.post(
        "/api/collections/backfill",
        headers={**AUTH, "X-Orchestrator-Zone": "EMEA"},
        json={
            "collector_id": "netbackup_jobs",
            "host": "nbu-emea-01",
            "date": "2026-10-08",
            **options,
        },
    )


def test_past_day_before_installation_queues_exact_periods_and_preserves_results(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T12:00:00+00:00")
    with TestClient(app) as client:
        setup_schedule(client)
        for view in ("collectors", "runs"):
            html = client.get("/", params={"view": view, "date": "2026-10-08"}).text
            assert "Run a past collection" in html and "data-backfill" in html
        first = launch(client)
        assert first.status_code == 200, first.text
        assert first.json()["queued"] == 24
        assert launch(client).json()["queued"] == 0
        with db.transaction() as s:
            windows = list(s.scalars(select(Window).order_by(Window.period_start)))
            assert len(windows) == 24
            assert windows[0].period_start == "2026-10-08T00:00:00Z"
            assert windows[-1].period_end == "2026-10-09T00:00:00Z"
            for w, status in zip(windows, ["SUCCESS", "FAILED", "RUNNING", "MISSING"]):
                w.status = status
            preserved = [(w.id, w.status) for w in windows[:3]]
        assert launch(client).json()["queued"] == 1
        with db.Session() as s:
            assert all(s.get(Window, wid).status == status for wid, status in preserved)
        task = svc.claim("EMEA-SAT-1")
        assert task["period_start"].startswith("2026-10-08T")
        assert task["period_end"].startswith("2026-10-08T")
        assert task["parameters"]["type"] == "jobs"


def test_daily_backfill_before_schedule_creation_preserves_worker_clock(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T12:00:00+00:00")
    with TestClient(app) as client:
        with db.transaction() as s:
            s.get(Worker, "EMEA-SAT-1").timezone = "Europe/Paris"
        setup_schedule(client, kind="daily_local", local_time="07:00")
        response = launch(client)
        assert response.status_code == 200, response.text
        assert response.json()["queued"] == 1
        with db.Session() as s:
            w = s.get(Window, response.json()["ids"][0])
            assert w.period_start == "2026-10-08T05:00:00Z"
            assert w.period_end == "2026-10-09T05:00:00Z"
            assert w.snapshot["local_time"] == "07:00"
            assert w.snapshot["manual_trigger"]


def test_backfill_rejects_invalid_targets_dates_and_unauthenticated_writes(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T12:00:00+00:00")
    with TestClient(app) as client:
        setup_schedule(client)
        for options in (
            {"date": "2026-10-22"},
            {"date": "bad"},
            {"host": "nbu-emea-02"},
            {"host": "dd-emea-01"},
        ):
            assert launch(client, **options).status_code == 422
        assert launch(client, host="nbu-apac-01").status_code == 404
        assert (
            client.post(
                "/api/collections/backfill",
                json={
                    "collector_id": "netbackup_jobs",
                    "host": "nbu-emea-01",
                    "date": "2026-10-08",
                },
            ).status_code
            == 401
        )
        with db.Session() as s:
            assert s.scalar(select(func.count(Window.id))) == 0


def test_oversized_backfill_rolls_back_entire_request(database, monkeypatch):
    freeze(monkeypatch, "2026-10-22T12:00:00+00:00")
    with TestClient(app) as client:
        setup_schedule(client)
        from orchestrator.models import Schedule

        with db.transaction() as s:
            s.scalar(select(Schedule)).interval_seconds = 60
        response = launch(client)
        assert response.status_code == 422 and "500" in response.text
        with db.Session() as s:
            assert s.scalar(select(func.count(Window.id))) == 0
