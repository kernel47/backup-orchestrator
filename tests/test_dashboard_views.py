from orchestrator import db
from orchestrator import service as svc
from orchestrator.main import app
from tests.client import DashboardClient as TestClient


def test_calendar_periods_and_filtered_pagination(database):
    with TestClient(app) as client:
        for day in ["2026-10-14", "2026-10-15"]:
            client.get(
                "/api/collections",
                params={"date": day, "collector_id": "netbackup_jobs", "zone": "EMEA"},
            )
        url = "/?view=runs&collector_id=netbackup_jobs&zone=EMEA&date=2026-10-15"
        first = client.get(url + "&page_size=10").text
        second = client.get(url + "&page_size=10&page=2").text
        assert "<strong>1–10</strong>" in first and "<strong>11–20</strong>" in second
        assert 'aria-current="page">1' in first and 'aria-current="page">2' in second
        assert first != second and "page=2" in first and "zone=EMEA" in first
        week = client.get(url + "&period=week").text
        month = client.get(url + "&period=month").text
        assert "Collection period navigation" not in first
        assert (
            "Collection period navigation" in week
            and "Collection period navigation" in month
        )
        assert "2026-10-12 — 2026-10-18" in week
        assert "2026-10-01 — 2026-10-31" in month
        assert "period=day" in month and "Queue missing windows" not in month
        assert "2026-10-14" in month and "2026-10-15" in month
        assert client.get(url + "&period=year").status_code == 422
        assert client.get(url + "&page=0").status_code == 422
        assert client.get(url + "&page_size=1000").status_code == 422


def test_attempt_history_pagination(database):
    from orchestrator.models import Attempt
    from tests.test_core import one_window

    cid = one_window("FAILED")
    with db.transaction() as session:
        for number in range(1, 27):
            session.add(
                Attempt(
                    id=f"page-attempt-{number}",
                    window_id=cid,
                    worker_id="EMEA-SAT-1",
                    number=number,
                    status="FAILED",
                    started_at=1,
                    finished_at=2,
                    lease_until=3,
                    result=None,
                    logs="Demo log",
                    context={},
                )
            )
    with TestClient(app) as client:
        first = client.get(f"/collections/{cid}").text
        second = client.get(f"/collections/{cid}?page=2").text
        assert first.count('class="panel attempt"') == 25
        assert second.count('class="panel attempt"') == 1
        assert "Attempt #26" in first and "Attempt #1 " in second
        assert "<strong>1–25</strong>" in first and "<strong>26–26</strong>" in second
        assert "<nav" not in first.split("<title>")[1].split("</title>")[0]


def test_hour_filter_and_status_selection_survive_drilldown(database):
    from tests.test_core import one_window

    one_window("SUCCESS")
    base = "/?view=runs&collector_id=netbackup_jobs&host=nbu-emea-01&date=2026-10-15&hour=10"
    with TestClient(app) as client:
        for status in [
            "",
            "FAILED",
            "SUCCESS",
            "MISSING",
            "RUNNING",
            "TIMEOUT",
            "UNKNOWN",
        ]:
            page = client.get(base + "&status=" + status)
            assert page.status_code == 200
            assert "10:00–11:00 UTC" in page.text and "hour-grid" not in page.text
            assert 'aria-current="true"' in page.text and 'name="hour"' in page.text


def test_live_controls_only_on_monitoring_views(database):
    from tests.test_core import one_window

    cid = one_window("RUNNING")
    with TestClient(app) as client:
        for url in [
            "/?view=overview",
            "/?view=collectors",
            "/?view=runs",
            "/collections/" + cid,
        ]:
            page = client.get(url).text
            assert (
                'data-live-view="true"' in page
                and 'id="live-toggle"' in page
                and 'id="live-refresh"' in page
            )
        for view in [
            "settings",
            "workspace",
            "hosts",
            "groups",
            "schedules",
            "sources",
            "workers",
            "zones",
            "scopes",
        ]:
            page = client.get("/?view=" + view).text
            assert 'data-live-view="false"' in page and 'id="live-toggle"' not in page
        assert "badge RUNNING" in client.get("/collections/" + cid).text


def test_latest_host_health_and_attention_filter(database):
    from sqlalchemy import select

    from orchestrator.models import Window
    from orchestrator.views import latest_collector_health
    from tests.test_core import one_window

    one_window("FAILED")
    with db.transaction() as session:
        rows = list(
            session.scalars(
                select(Window)
                .where(
                    Window.collector_id == "netbackup_jobs",
                    Window.host_id == "nbu-emea-01",
                )
                .order_by(Window.period_start)
            )
        )
        for row in rows:
            row.status = "SUCCESS"
        rows[-1].status = "TIMEOUT"
        session.flush()
        health = latest_collector_health(session)["netbackup_jobs"]
        assert health["status"] == "FAILED"
        assert health["counts"]["TIMEOUT"] == 1
        assert sum(health["counts"].values()) == health["expected"]
        filtered = svc.rows(
            session,
            {
                "collector_id": "netbackup_jobs",
                "date": "2026-10-15",
                "host": "nbu-emea-01",
                "status": "ATTENTION",
            },
        )
        assert len(filtered) == 1 and filtered[0]["status"] == "TIMEOUT"
    with TestClient(app) as client:
        page = client.get("/?view=overview").text
        assert "Last success:" in page and "hosts successful" in page
        schedules = client.get("/?view=schedules&new=1").text
        assert 'value="3600" selected' in schedules
        assert 'value="600" ' not in schedules


def test_daily_workspace_catalogue_and_progressive_filters(database):
    with TestClient(app) as client:
        page = client.get(
            "/?view=collectors&scope=NetBackup&workspace=EMEA&date=2026-10-15"
        ).text
        assert (
            'aria-label="Zone workspace"' in page and "Daily collection health" in page
        )
        assert "Collection zones" not in page and "hour-grid" not in page
        assert 'class="advanced-filters" >' in page
        assert "collector_id=netbackup_jobs" in page and "view=runs" in page
        detail = client.get(
            "/?view=runs&collector_id=netbackup_jobs&workspace=EMEA&host=nbu-emea-01&date=2026-10-15"
        ).text
        assert 'class="advanced-filters" open' in detail and "filter-active" in detail
        assert "Back to collectors" in detail


def test_collectors_defaults_to_week_and_matches_history_period_overview(database):
    with TestClient(app) as client:
        default = client.get("/?view=collectors&date=2026-10-15").text
        assert "period-week" in default and "<h2>Week overview</h2>" in default
        assert default.count('aria-label="Collection period navigation"') == 1
        for period in ("day", "week", "month"):
            page = client.get("/?view=collectors&date=2026-10-15&period=" + period).text
            assert "<h2>Daily collection health</h2>" in page
            assert ('aria-label="Collection period navigation"' in page) == (
                period != "day"
            )
            assert ("<h2>" + period.title() + " overview</h2>" in page) == (
                period != "day"
            )
        assert (
            "Collection period navigation"
            not in client.get("/?view=runs&date=2026-10-15").text
        )
