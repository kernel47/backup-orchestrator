from datetime import date

from sqlalchemy import select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import Collector, Schedule, Window, Worker
from orchestrator.scheduling import boundary, daily_period
from scripts.migrate_schedules import migrate
from tests.client import DashboardClient as TestClient
from tests.test_host_groups import AUTH, freeze, group, schedule


def test_legacy_conversion_is_explicit_idempotent_and_preserves_history(database):
    with db.transaction() as s:
        svc.expected(s, "2026-10-15", "netbackup_jobs", host="nbu-emea-01")
    with db.Session() as s:
        before = [
            (w.id, w.period_start, w.period_end, w.status)
            for w in s.scalars(select(Window))
        ]
    assert len(migrate()) == 5
    assert migrate() == []
    with db.Session() as s:
        assert len(list(s.scalars(select(Schedule)))) == 5
        assert not any(c.legacy_schedule for c in s.scalars(select(Collector)))
        assert before == [
            (w.id, w.period_start, w.period_end, w.status)
            for w in s.scalars(select(Window))
        ]


def test_worker_timezone_heartbeat_validation_and_display(database):
    from orchestrator.config import settings

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + settings.worker_token}
        assert (
            client.post(
                "/api/workers/heartbeat",
                headers=headers,
                json={"worker_id": "EMEA-SAT-1", "timezone": "Europe/Paris"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/workers/heartbeat",
                headers=headers,
                json={"worker_id": "EMEA-SAT-1", "timezone": "bad/timezone"},
            ).status_code
            == 422
        )
        worker = next(
            w for w in client.get("/api/workers").json() if w["id"] == "EMEA-SAT-1"
        )
        assert worker["timezone"] == "Europe/Paris" and (
            "CEST" in worker["local_time"] or "CET" in worker["local_time"]
        )
        assert "Europe/Paris" in client.get("/?view=workers").text


def test_same_0700_runs_at_different_utc_times(database, monkeypatch):
    freeze(monkeypatch, "2026-10-22T00:00:00+00:00")
    with db.transaction() as s:
        for c in s.scalars(select(Collector)):
            c.enabled = False
        c = s.get(Collector, "netbackup_jobs")
        c.enabled = True
        s.get(Worker, "EMEA-SAT-1").timezone = "Europe/Paris"
        s.get(Worker, "APAC-SAT-1").timezone = "Asia/Singapore"
        s.get(Worker, "AMER-SAT-1").timezone = "America/New_York"
    with TestClient(app) as client:
        # Same script and local time, configured independently in each workspace.
        for zone, hid in [
            ("EMEA", "nbu-emea-01"),
            ("APAC", "nbu-apac-01"),
            ("AMER", "nbu-amer-01"),
        ]:
            headers = {**AUTH, "X-Tasker-Zone": zone}
            collector = next(
                c
                for c in client.get("/api/collectors", headers=headers).json()
                if c["name"] == "Jobs"
            )
            g = client.post(
                "/api/host-groups",
                headers=headers,
                json={"name": "Masters", "host_ids": [hid]},
            ).json()
            response = client.post(
                "/api/schedules",
                headers=headers,
                json={
                    "name": "Policies at 07:00",
                    "collector_id": collector["id"],
                    "host_group_ids": [g["id"]],
                    "kind": "daily_local",
                    "local_time": "07:00",
                    "interval_seconds": 86400,
                },
            )
            assert response.status_code == 200, response.text
        timing = [
            t
            for r in client.get("/api/schedules", headers=AUTH).json()
            for t in r["timing"]
        ]
        assert {t["timezone"] for t in timing} == {
            "Europe/Paris",
            "Asia/Singapore",
            "America/New_York",
        }
        freeze(monkeypatch, "2026-10-22T04:59:59+00:00")
        svc.tick()
        with db.Session() as s:
            assert not list(s.scalars(select(Window)))
        freeze(monkeypatch, "2026-10-22T05:00:00+00:00")
        svc.tick()
        svc.tick()
        with db.Session() as s:
            windows = list(s.scalars(select(Window)))
            assert len(windows) == 1 and windows[0].host_id == "nbu-emea-01"
            assert windows[0].snapshot["execution_timezone"] == "Europe/Paris"
        freeze(monkeypatch, "2026-10-22T11:00:00+00:00")
        svc.tick()
        freeze(monkeypatch, "2026-10-22T23:00:00+00:00")
        svc.tick()
        with db.Session() as s:
            windows = list(s.scalars(select(Window)))
            assert len(windows) == 3
            assert {w.host_id: w.period_end for w in windows} == {
                "nbu-emea-01": "2026-10-22T05:00:00Z",
                "nbu-amer-01": "2026-10-22T11:00:00Z",
                "nbu-apac-01": "2026-10-22T23:00:00Z",
            }
        assert "07:00" in client.get("/?view=schedules").text


def test_daily_calendar_handles_dst_without_duplicate_occurrences():
    for day, hours in [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)]:
        start, end = daily_period(day, "07:00", "Europe/Paris")
        assert (end - start).total_seconds() == hours * 3600
        assert end.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris")).hour == 7
    # Missing 02:30 moves to 03:30; repeated 02:30 runs at the first occurrence.
    assert (
        svc.iso(boundary(date(2026, 3, 29), "02:30", "Europe/Paris"))
        == "2026-03-29T01:30:00Z"
    )
    assert (
        svc.iso(boundary(date(2026, 10, 25), "02:30", "Europe/Paris"))
        == "2026-10-25T00:30:00Z"
    )


def test_daily_needs_worker_clock_and_valid_time(database):
    with TestClient(app) as client:
        g = group(client, ["nbu-emea-01"])
        assert (
            schedule(client, g, kind="daily_local", local_time="07:00").status_code
            == 422
        )
        assert (
            schedule(client, g, kind="daily_local", local_time="25:00").status_code
            == 422
        )
        assert (
            schedule(client, g, kind="daily_local", local_time="7h00").status_code
            == 422
        )


def test_daily_backfill_retry_preserves_local_clock_and_utc_period(
    database, monkeypatch
):
    freeze(monkeypatch, "2026-10-22T00:00:00+00:00")
    with db.transaction() as s:
        for c in s.scalars(select(Collector)):
            c.enabled = False
        s.get(Collector, "netbackup_jobs").enabled = True
        s.get(Worker, "EMEA-SAT-1").timezone = "Europe/Paris"
        s.get(Worker, "EMEA-SAT-2").timezone = "America/New_York"
    with TestClient(app) as client:
        g = group(client, ["nbu-emea-01"])
        assert (
            schedule(client, g, kind="daily_local", local_time="07:00").status_code
            == 200
        )
        freeze(monkeypatch, "2026-10-22T05:00:00+00:00")
        svc.tick()
        # A fallback in a different timezone must not execute the automatic 07:00 slot.
        assert svc.claim("EMEA-SAT-2") is None
        task = svc.claim("EMEA-SAT-1")
        assert (
            task["scheduled_for"] == "2026-10-22T05:00:00Z"
            and task["worker_timezone"] == "Europe/Paris"
        )
        with db.transaction() as s:
            from orchestrator.models import Attempt

            window = s.get(Window, task["collection_id"])
            window.status = "FAILED"
            attempt = s.get(Attempt, task["task_id"])
            attempt.status = "FAILED"
            attempt.finished_at = attempt.started_at + 1
        assert svc.retry({}, [task["collection_id"]]) == [task["collection_id"]]
        with db.Session() as s:
            window = s.get(Window, task["collection_id"])
            assert (
                window.period_start == task["period_start"]
                and window.period_end == task["period_end"]
            )
            assert window.snapshot["scheduled_for"] == "2026-10-22T05:00:00Z"
            assert window.snapshot["execution_timezone"] == "Europe/Paris"
        details = client.get("/collections/" + task["collection_id"]).text
        assert (
            "Scheduled execution: 2026-10-22 07:00:00 CEST" in details
            and "Actual start:" in details
        )
        with db.transaction() as s:
            ids = svc.expected(
                s, "2026-10-20", "netbackup_jobs", host="nbu-emea-01", enqueue=True
            )
            windows = [s.get(Window, wid) for wid in ids]
            assert len(windows) == 1 and windows[0].period_end == "2026-10-21T05:00:00Z"
            assert windows[0].status == "PENDING"


def test_worker_timezone_detection_uses_explicit_iana_name(monkeypatch):
    from worker.timezone import worker_timezone

    monkeypatch.setenv("TZ", "America/New_York")
    assert worker_timezone() == "America/New_York"
    assert worker_timezone("Asia/Singapore") == "Asia/Singapore"
