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
        assert (
            "Infrastructure &amp; inventory" in page
            or "Infrastructure & inventory" in page
        )
        assert "datadomain_capacity" in page and 'class="filters"' not in page
        assert "Collection history" not in page and "Latest executions" in page
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


def test_overview_activity_uses_persisted_periods_and_bounded_recent_attempts(database):
    from orchestrator import service as svc
    from orchestrator.models import Attempt
    from orchestrator.views import overview_activity
    from tests.test_core import one_window

    cid = one_window("SUCCESS")
    with db.transaction() as session:
        for n in range(8):
            session.add(
                Attempt(
                    id=f"feed-{n}",
                    window_id=cid,
                    worker_id="EMEA-SAT-1",
                    number=n + 1,
                    status="SUCCESS",
                    started_at=svc.now().timestamp() + n,
                    finished_at=svc.now().timestamp() + n + 1,
                    lease_until=0,
                    result=None,
                    logs="",
                    context={},
                )
            )
    with db.Session() as session:
        activity = overview_activity(session)
        assert len(activity["trend"]) == 7 and len(activity["recent"]) == 6
        assert activity["recent"][0]["id"] == "feed-7"
    with TestClient(app) as client:
        page = client.get("/?view=overview&workspace=EMEA").text
        assert "Collection activity" in page and "Latest executions" in page
        assert "overview-satellites" not in page and "satellite-summary" not in page
        assert (
            "feed-7" not in page
        )  # Links reference the collection, not attempt internals.
