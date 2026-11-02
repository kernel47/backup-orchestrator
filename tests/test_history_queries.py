"""Pagination must bound memory and leave SQLite's writer available."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session

from orchestrator import db
from orchestrator import service as svc
from orchestrator.history_queries import HistoryStore
from orchestrator.main import app
from orchestrator.models import Attempt, Window
from tests.client import DashboardClient
from tests.test_core import one_window


def test_history_filters_apply_to_lists_reports_and_counts(database):
    wid = one_window("SUCCESS")
    with db.transaction() as session:
        window = session.get(Window, wid)
        window.snapshot = dict(
            window.snapshot,
            host_group_ids=["group-a", "group-b"],
            schedule_id="schedule-a",
        )
        for number, status in ((1, "FAILED"), (2, "SUCCESS")):
            session.add(
                Attempt(
                    id=f"filtered-attempt-{number}",
                    window_id=wid,
                    worker_id=f"EMEA-SAT-{number}",
                    number=number,
                    status=status,
                    started_at=1,
                    finished_at=2,
                    lease_until=3,
                    context={"worker_timezone": "Europe/Paris"},
                    logs="private log",
                    result={"summary": "private report"},
                )
            )
    with db.Session() as session:
        store = HistoryStore(session)
        all_ids = set(session.scalars(select(Window.id)))
        for extra, expected_ids in (
            ({}, all_ids),
            ({"status": "RECOVERED"}, {wid}),
            ({"status": "ATTENTION"}, all_ids - {wid}),
            ({"host_group_id": "group-b"}, {wid}),
            ({"host_group_id": "other"}, set()),
            ({"schedule_id": "schedule-a"}, {wid}),
            ({"worker": "EMEA-SAT-2"}, {wid}),
            ({"worker": "EMEA-SAT-1"}, set()),
            ({"scope": "Other"}, set()),
            ({"zone": "APAC"}, set()),
        ):
            filters = {"date": "2026-10-15", "collector_id": "netbackup_jobs", **extra}
            reports = svc.rows(session, filters)
            page = store.page(filters, 1, 1000)
            assert {row["id"] for row in page.rows} == expected_ids, extra
            assert {row["id"] for row in reports} == expected_ids, extra
            assert page.total == len(expected_ids)
            counts, _ = store.summary(filters)
            summary_ids = all_ids if "status" in extra else expected_ids
            expected_counts = Counter(
                "SUCCESS" if key == wid else "MISSING" for key in summary_ids
            )
            if wid in summary_ids:
                expected_counts["RECOVERED"] = 1
            assert counts == expected_counts
        recovered = store.page({"status": "RECOVERED"}, 1, 25).rows
        assert recovered[0]["attempt_count"] == 2
        assert recovered[0]["worker"] == "EMEA-SAT-2"
        assert recovered[0]["started_local"]


def test_dashboard_large_history_reads_only_visible_windows_and_no_report_bodies(
    database, monkeypatch
):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    windows = [
        dict(
            id=f"volume-{i:05}",
            zone="EMEA",
            host_id="nbu-emea-01",
            collector_id="netbackup_jobs",
            period_start=svc.iso(start + timedelta(minutes=4 * i)),
            period_end=svc.iso(start + timedelta(minutes=4 * (i + 1))),
            status="SUCCESS",
            created_at=1,
            available_at=0,
            retry_base=0,
            snapshot={
                "scope": "NetBackup",
                "zone": "EMEA",
                "interval_seconds": 3600,
                "host_group_ids": ["demo_netbackup_emea"],
            },
        )
        for i in range(10000)
    ]
    with db.transaction() as session:
        session.execute(insert(Window), windows)
        # Neighboring months must not leak into the monthly aggregates or list.
        session.execute(
            insert(Window),
            [
                dict(
                    windows[0],
                    id="outside-september",
                    period_start="2026-09-30T23:00:00Z",
                    period_end="2026-10-01T00:00:00Z",
                ),
                dict(
                    windows[0],
                    id="outside-november",
                    period_start="2026-11-01T00:00:00Z",
                    period_end="2026-11-01T01:00:00Z",
                ),
            ],
        )
        session.execute(
            insert(Attempt),
            [
                dict(
                    id=f"volume-attempt-{i}",
                    window_id=w["id"],
                    worker_id="EMEA-SAT-1",
                    number=1,
                    status="SUCCESS",
                    started_at=1,
                    finished_at=2,
                    lease_until=3,
                    result={
                        "summary": "REPORT_BODY_MUST_NOT_LOAD",
                        "details": {"text": "x" * 2000},
                    },
                    logs="LOG_BODY_MUST_NOT_LOAD" + "x" * 2000,
                    context={},
                )
                for i, w in enumerate(windows)
            ],
        )

    def cannot_write(*args, **kwargs):
        raise AssertionError("Reading dashboard history must not plan or recover tasks")

    monkeypatch.setattr(svc, "expected", cannot_write)
    monkeypatch.setattr(svc, "recover", cannot_write)
    statements = []
    loaded = Counter()

    def capture_sql(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    def capture_objects(session, obj):
        loaded[type(obj)] += 1

    with DashboardClient(app) as client:
        event.listen(database, "before_cursor_execute", capture_sql)
        event.listen(Session, "loaded_as_persistent", capture_objects)
        try:
            response = client.get(
                "/?view=runs&workspace=EMEA&date=2026-10-15&period=month&page=2&page_size=25&scope=NetBackup&collector_id=netbackup_jobs&host_group_id=demo_netbackup_emea&host=nbu-emea-01"
            )
            assert response.status_code == 200
            assert "<strong>26–50</strong>" in response.text
            assert loaded[Window] == 25 and loaded[Attempt] == 0
            assert "REPORT_BODY_MUST_NOT_LOAD" not in response.text
            loaded.clear()
            response = client.get(
                "/?view=collectors&workspace=EMEA&scope=NetBackup&date=2026-10-15&period=month"
            )
            assert response.status_code == 200
            assert loaded[Window] == loaded[Attempt] == 0
            assert response.context["counts"]["SUCCESS"] == 10000
            assert response.context["total"] == 10000
            assert response.context["range_start"] == "2026-10-01"
            assert response.context["range_end"] == "2026-10-31"
            loaded.clear()
            response = client.get("/?view=overview&workspace=EMEA")
            assert response.status_code == 200
            assert loaded[Window] == 1 and loaded[Attempt] == 0
            assert not any(
                "row_number" in sql.lower()
                or "max(attempts.finished_at)" in sql.lower()
                for sql in statements
            )
            assert not any(
                sql.lstrip()
                .upper()
                .startswith(("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE"))
                for sql in statements
            )
            assert not any(
                "attempts.logs" in sql or "attempts.result" in sql for sql in statements
            )
            statements.clear()
            loaded.clear()
            selected = windows[-1]["id"]
            assert svc.retry({}, [selected], mode="rerun") == [selected]
            assert loaded[Window] == 1 and loaded[Attempt] == 0
            assert not any(
                "attempts.logs" in sql or "attempts.result" in sql for sql in statements
            )
        finally:
            event.remove(database, "before_cursor_execute", capture_sql)
            event.remove(Session, "loaded_as_persistent", capture_objects)


def test_reading_an_empty_day_does_not_create_periods(database):
    with DashboardClient(app) as client:
        assert client.get("/?view=runs&date=2026-09-01").status_code == 200
        assert (
            client.get("/?view=collectors&date=2026-09-01&period=day").status_code
            == 200
        )
    with db.Session() as session:
        assert session.scalar(select(Window.id).limit(1)) is None


def test_api_history_is_bounded_and_summary_never_loads_reports(database):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    windows = [
        dict(
            id=f"api-period-{number:04}",
            zone="EMEA",
            host_id="nbu-emea-01",
            collector_id="netbackup_jobs",
            period_start=svc.iso(start + timedelta(minutes=number)),
            period_end=svc.iso(start + timedelta(minutes=number + 1)),
            status="SUCCESS",
            created_at=1,
            snapshot={"scope": "NetBackup", "zone": "EMEA", "interval_seconds": 60},
        )
        for number in range(1505)
    ]
    with db.transaction() as session:
        session.execute(insert(Window), windows)
        session.execute(
            insert(Attempt),
            [
                dict(
                    id=f"api-attempt-{number}",
                    window_id=windows[number]["id"],
                    worker_id="EMEA-SAT-1",
                    number=1,
                    status="SUCCESS",
                    started_at=1,
                    finished_at=2,
                    lease_until=3,
                    context={},
                    result={"summary": f"Report {number}"},
                    logs="Sensitive report body",
                )
                for number in (0, 25)
            ],
        )
    loaded = Counter()

    def capture_objects(session, obj):
        loaded[type(obj)] += 1

    with DashboardClient(app) as client:
        event.listen(Session, "loaded_as_persistent", capture_objects)
        try:
            default = client.get("/api/collections")
            assert default.status_code == 200 and len(default.json()) == 1000
            assert default.headers["X-Total-Count"] == "1505"
            assert default.headers["X-Has-More"] == "true"
            assert loaded[Window] == 1000
            loaded.clear()
            page = client.get("/api/collections?limit=25&offset=25")
            assert page.status_code == 200 and len(page.json()) == 25
            assert page.json()[0]["id"] == "api-period-0025"
            assert page.json()[0]["attempts"][0]["result"]["summary"] == "Report 25"
            assert loaded[Window] == 25 and loaded[Attempt] == 1
            loaded.clear()
            summary = client.get("/api/collections/summary")
            assert summary.status_code == 200
            assert summary.json() == {"expected": 1505, "counts": {"SUCCESS": 1505}}
            assert loaded[Window] == loaded[Attempt] == 0
            assert client.get("/api/collections?limit=1001").status_code == 422
            assert client.get("/api/collections?limit=0").status_code == 422
            assert client.get("/api/collections?offset=-1").status_code == 422
        finally:
            event.remove(Session, "loaded_as_persistent", capture_objects)
