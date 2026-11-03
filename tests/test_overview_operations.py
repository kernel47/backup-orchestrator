"""The workspace overview separates current operations from historical backfills."""

from sqlalchemy import select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import Attempt, Collector, Schedule, Window, Worker
from orchestrator.views import (
    latest_collector_health,
    overview_activity,
    overview_operations,
)
from orchestrator.workspaces import current_zone
from tests.client import DashboardClient as TestClient
from tests.test_backfill import launch, setup_schedule
from tests.test_host_groups import freeze


def test_recent_results_are_deduplicated_and_backfills_do_not_change_regular_health(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T12:00:00+00:00")
    current = svc.now().timestamp()
    with TestClient(app) as client:
        setup_schedule(client)
        response = launch(client)
        assert response.json()["queued"] == 24
        historical_id = response.json()["ids"][0]
        with db.transaction() as session:
            historical = session.get(Window, historical_id)
            historical.status = "FAILED"
            session.add(
                Attempt(
                    id="historical-attempt",
                    window_id=historical.id,
                    worker_id="EMEA-SAT-1",
                    number=1,
                    status="FAILED",
                    started_at=current - 10,
                    finished_at=current - 1,
                    lease_until=current,
                    result=None,
                    logs="",
                    context={},
                )
            )
            for number, (status, finished, zone) in enumerate(
                [
                    ("SUCCESS", current - 20, "EMEA"),
                    ("TIMEOUT", current - 20, "EMEA"),
                    ("SUCCESS", current - 90000, "EMEA"),
                    ("SUCCESS", current - 20, "APAC"),
                ]
            ):
                collector = session.scalar(
                    select(Collector).where(
                        Collector.zone == zone, Collector.name == "Jobs"
                    )
                )
                window = Window(
                    id=f"regular-{number}",
                    zone=zone,
                    collector_id=collector.id,
                    host_id="nbu-emea-01" if zone == "EMEA" else "nbu-apac-01",
                    period_start=f"2026-10-{20 if number == 2 else 21}T{number:02}:00:00Z",
                    period_end=f"2026-10-{20 if number == 2 else 21}T{number + 1:02}:00:00Z",
                    status=status,
                    created_at=current - 100,
                    snapshot={"interval_seconds": 3600},
                )
                session.add(window)
                session.flush()
                for attempt in (1, 2):
                    session.add(
                        Attempt(
                            id=f"regular-attempt-{number}-{attempt}",
                            window_id=window.id,
                            worker_id="EMEA-SAT-1",
                            number=attempt,
                            status=status,
                            started_at=finished - 10,
                            finished_at=finished,
                            lease_until=current,
                            result=None,
                            logs="",
                            context={},
                        )
                    )
        token = current_zone.set("EMEA")
        try:
            with db.Session() as session:
                operations = overview_operations(session)
                assert operations["completed"] == 2
                assert operations["successful"] == 1
                assert operations["success_rate"] == 50
                assert operations["pending"] == operations["backfills_pending"] == 23
                assert (
                    latest_collector_health(session)["netbackup_jobs"]["counts"][
                        "TIMEOUT"
                    ]
                    == 1
                )
                assert (
                    sum(day["total"] for day in overview_activity(session)["trend"])
                    == 3
                )
        finally:
            current_zone.reset(token)
        assert svc.retry({}, [historical_id]) == [historical_id]
        with db.Session() as session:
            snapshot = session.get(Window, historical_id).snapshot
            assert snapshot["historical_backfill"] and snapshot["queued_at"] > 0


def test_fresh_install_has_no_synthetic_alerts_then_reports_real_missed_schedule(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T12:10:00+00:00")
    with TestClient(app) as client:
        setup_schedule(client)
        with db.transaction() as session:
            for c in session.scalars(select(Collector)):
                c.legacy_schedule = False
            for worker in session.scalars(select(Worker)):
                worker.heartbeat = svc.now().timestamp()
            rule = session.scalar(select(Schedule))
            rule.created_at = svc.now().timestamp()
        page = client.get("/?view=overview&workspace=EMEA").text
        assert 'id="attention-title"' not in page
        assert "awaiting first run" in page and "0 successful · 0 completed" in page
        with db.transaction() as session:
            session.scalar(select(Schedule)).created_at -= 7200
        page = client.get("/?view=overview&workspace=EMEA").text
        assert 'id="attention-title"' in page and "1 overdue" in page
        assert page.index('id="attention-title"') < page.index(
            'id="current-activity-title"'
        )
        assert page.index('id="overview-collectors"') < page.index(
            'id="collector-activity-title"'
        )


def test_daily_overdue_uses_satellite_clock_and_schedule_creation(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-26T07:00:00+00:00")
    with TestClient(app) as client:
        with db.transaction() as session:
            session.get(Worker, "EMEA-SAT-1").timezone = "Europe/Paris"
        setup_schedule(client, kind="daily_local", local_time="07:00")
        with db.transaction() as session:
            rule = session.scalar(select(Schedule))
            rule.created_at = svc.now().timestamp() - 7200
        token = current_zone.set("EMEA")
        try:
            with db.Session() as session:
                assert latest_collector_health(session)["netbackup_jobs"]["stale"] == 1
            with db.transaction() as session:
                session.scalar(select(Schedule)).created_at = svc.now().timestamp() - 60
            with db.Session() as session:
                assert latest_collector_health(session)["netbackup_jobs"]["stale"] == 0
        finally:
            current_zone.reset(token)
