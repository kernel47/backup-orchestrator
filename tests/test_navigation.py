from sqlalchemy import func, select

from orchestrator import db
from orchestrator.main import app
from orchestrator.models import Window
from tests.client import DashboardClient as TestClient
from tests.test_host_groups import AUTH


def test_overview_is_unfiltered_and_read_only(database):
    with TestClient(app) as client:
        with db.Session() as session:
            before = session.scalar(select(func.count()).select_from(Window))
        page = client.get(
            "/?view=overview&scope=unknown&collector_id=netbackup_jobs&date=2026-10-15&period=month"
        ).text
        assert "overview-infrastructure" not in page
        assert "SATELLITES" in page and "TOTAL JOBS" in page and "SUCCESS RATE" in page
        assert "datadomain_capacity" in page and 'class="filters"' not in page
        assert "Collection history" not in page and "Activity by collector" in page
        assert "overview-satellites" not in page
        with db.Session() as session:
            assert session.scalar(select(func.count()).select_from(Window)) == before


def test_registered_scope_creates_tab_and_cannot_remove_used_scope(database):
    with TestClient(app) as client:
        assert client.post("/api/scopes", json={"id": "Linux"}).status_code == 401
        assert (
            client.post("/api/scopes", headers=AUTH, json={"id": "Linux"}).status_code
            == 200
        )
        assert "Linux" in client.get("/api/catalog").json()["scopes"]
        assert (
            "No collectors in this workspace and scope."
            in client.get("/?scope=Linux").text
        )
        assert client.delete("/api/scopes/NetBackup", headers=AUTH).status_code == 409
        assert client.delete("/api/scopes/Linux", headers=AUTH).status_code == 200
        assert "Linux" not in client.get("/api/catalog").json()["scopes"]
        assert (
            client.post("/api/scopes", headers=AUTH, json={"id": "../bad"}).status_code
            == 422
        )


def test_sidebar_identifies_one_active_page_including_collection_details(database):
    import re

    from tests.test_core import one_window

    with TestClient(app) as client:
        for key in (
            "overview",
            "collectors",
            "runs",
            "schedules",
            "sources",
            "hosts",
            "groups",
            "workspace",
            "workers",
            "zones",
            "scopes",
            "settings",
            "documentation",
            "technical-architecture",
        ):
            response = client.get("/?view=" + key + "&workspace=EMEA")
            assert response.status_code == 200
            active = re.findall(
                r'<a data-nav-key="([^"]+)"\s+aria-current="page"', response.text
            )
            assert active == [key]
        cid = one_window("SUCCESS")
        response = client.get("/collections/" + cid + "?workspace=EMEA")
        assert response.status_code == 200
        assert re.findall(
            r'<a data-nav-key="([^"]+)"\s+aria-current="page"', response.text
        ) == ["runs"]


def test_collector_settings_table_view_edit_and_workspace_boundaries(database):
    with TestClient(app) as client:
        listing = client.get("/?view=settings&section=collectors&workspace=EMEA").text
        assert "collector-settings-list" in listing
        assert (
            "data-update-collector=" not in listing
            and 'id="create-collector"' not in listing
        )
        detail = client.get(
            "/?view=settings&collector_id=netbackup_jobs&workspace=EMEA"
        )
        assert detail.status_code == 200 and "Execution configuration" in detail.text
        assert (
            "data-update-collector=" not in detail.text and "mode=edit" in detail.text
        )
        edit = client.get(
            "/?view=settings&collector_id=netbackup_jobs&mode=edit&workspace=EMEA"
        ).text
        assert (
            'data-update-collector="netbackup_jobs"' in edit and "Save changes" in edit
        )
        create = client.get("/?view=settings&new=1&workspace=EMEA").text
        assert (
            'id="create-collector"' in create and "data-update-collector=" not in create
        )
        assert (
            client.get(
                "/?view=settings&collector_id=not-found&workspace=EMEA"
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/?view=settings&collector_id=netbackup_jobs&workspace=APAC"
            ).status_code
            == 404
        )


def test_overview_activity_matches_days_collectors_and_workspace(database, monkeypatch):
    from datetime import datetime, timezone

    from orchestrator import service as svc
    from orchestrator.models import Collector, Worker
    from orchestrator.views import overview_activity
    from orchestrator.workspaces import current_zone

    monkeypatch.setattr(
        svc, "now", lambda: datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    )
    with TestClient(app) as client:
        with db.transaction() as session:
            for worker_id in ("EMEA-SAT-1", "APAC-SAT-1"):
                session.get(Worker, worker_id).heartbeat = svc.now().timestamp()
            jobs = session.scalar(
                select(Collector).where(
                    Collector.zone == "EMEA", Collector.name == "Jobs"
                )
            )
            policies = session.scalar(
                select(Collector).where(
                    Collector.zone == "EMEA", Collector.name == "Policies"
                )
            )
            apac = session.scalar(
                select(Collector).where(
                    Collector.zone == "APAC", Collector.name == "Jobs"
                )
            )
            for number, (collector, host, day, status) in enumerate(
                [
                    (jobs, "nbu-emea-01", "2026-10-15", "SUCCESS"),
                    (jobs, "nbu-emea-01", "2026-10-15", "SUCCESS"),
                    (jobs, "nbu-emea-01", "2026-10-15", "FAILED"),
                    (jobs, "nbu-emea-01", "2026-10-15", "PENDING"),
                    (jobs, "nbu-emea-01", "2026-10-15", "MISSING"),
                    (jobs, "nbu-emea-01", "2026-10-15", "SCHEDULED"),
                    (jobs, "nbu-emea-01", "2026-10-15", "DISABLED"),
                    (jobs, "nbu-emea-01", "2026-10-01", "SUCCESS"),
                    (policies, "nbu-emea-02", "2026-10-14", "SUCCESS"),
                    (apac, "nbu-apac-01", "2026-10-15", "FAILED"),
                ]
            ):
                session.add(
                    Window(
                        id=f"activity-{number}",
                        collector_id=collector.id,
                        host_id=host,
                        zone=collector.zone,
                        period_start=f"{day}T{number:02}:00:00Z",
                        period_end=f"{day}T{number + 1:02}:00:00Z",
                        status=status,
                        created_at=1,
                        snapshot={"interval_seconds": 3600},
                    )
                )
        token = current_zone.set("EMEA")
        try:
            with db.Session() as session:
                activity = overview_activity(session)
            assert len(activity["trend"]) == 7
            assert sum(d["total"] for d in activity["trend"]) == 6
            rows = {c["name"]: c for c in activity["collector_activity"]}
            assert rows["Jobs"]["total"] == 5
            assert rows["Jobs"]["counts"] == {
                "SUCCESS": 2,
                "FAILED": 1,
                "PENDING": 1,
                "MISSING": 1,
            }
            assert rows["Policies"]["total"] == 1
            assert rows["Images"]["total"] == 0
            assert sum(c["total"] for c in rows.values()) == 6
        finally:
            current_zone.reset(token)
        page = client.get("/?view=overview&workspace=EMEA").text
        assert "Activity by collector" in page and "Activity by day" in page
        assert '80.0<span class="overview-stat-unit">%</span>' in page
        assert "4 successful · 5 completed" in page
        satellite_card = page.split('stat-satellites"', 1)[1].split("</a>", 1)[0]
        assert "<strong>2</strong>" in satellite_card
        assert "1 online" in satellite_card and "1 offline" in satellite_card
        jobs_card = page.split('stat-jobs"', 1)[1].split("</a>", 1)[0]
        assert "<strong>7</strong>" in jobs_card
        assert "Latest executions" not in page and "overview-infrastructure" not in page
        activity_section = page.split('aria-labelledby="collector-activity-title"', 1)[
            1
        ].split("</section>", 1)[0]
        assert '<th scope="col">Collections</th>' in activity_section
        assert '<th scope="col">Success</th>' in activity_section
        assert '<th scope="col">Failed</th>' in activity_section
        assert "View history" not in activity_section
        assert "collector-activity-track" not in activity_section
        assert 'aria-label="Collector activity for the last 7 days"' in activity_section
        assert (
            client.get(
                f"/?view=runs&collector_id={rows['Jobs']['id']}&date=2026-10-15"
            ).status_code
            == 200
        )
