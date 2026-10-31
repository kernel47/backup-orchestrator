import json

from orchestrator.main import app
from tests.client import DashboardClient
from tests.test_core import one_window

BROWSER = {"Accept": "text/html"}


def test_clean_urls_remember_filters_and_clear_them_explicitly(database):
    one_window("SUCCESS")
    with DashboardClient(app, headers=BROWSER) as client:
        page = client.get(
            "/?view=runs&workspace=EMEA&date=2026-10-15&period=week&status=SUCCESS"
        )
        assert page.status_code == 200 and str(page.url).endswith("/w/EMEA/runs")
        assert "2026-10-12 — 2026-10-18" in page.text
        assert 'aria-current="true">Success' in page.text
        again = client.get("/w/EMEA/runs")
        assert "2026-10-12 — 2026-10-18" in again.text
        # A link which removes status replaces preferences instead of merging it back.
        reset = client.get("/?view=runs&workspace=EMEA&date=2026-10-15&period=day")
        assert 'aria-current="true">All' in reset.text
        assert "?" not in str(reset.url)
        other = client.get("/w/APAC/runs")
        assert "2026-10-15" not in other.text
        # Per-tab live state can restore a prior selection without putting it in the URL.
        back = client.get(
            "/w/EMEA/runs",
            headers={
                "X-Orchestrator-View": "1",
                "X-Orchestrator-Page-State": json.dumps(
                    {
                        "view": "runs",
                        "date": "2026-10-15",
                        "period": "week",
                        "status": "SUCCESS",
                        "nav_reset": "1",
                    }
                ),
            },
        )
        assert back.status_code == 200 and "2026-10-12 — 2026-10-18" in back.text


def test_clean_routes_cover_lists_details_and_creation(database):
    with DashboardClient(app, headers=BROWSER) as client:
        for source, target in [
            ("/?view=overview&workspace=EMEA", "/w/EMEA/overview"),
            ("/?view=settings&workspace=EMEA", "/w/EMEA/configuration/collectors"),
            (
                "/?view=settings&workspace=EMEA&collector_id=netbackup_jobs&mode=edit",
                "/w/EMEA/configuration/collectors/netbackup_jobs/edit",
            ),
            (
                "/?view=schedules&workspace=EMEA&collector_id=netbackup_jobs",
                "/w/EMEA/schedules/new",
            ),
            ("/?view=schedules&workspace=EMEA", "/w/EMEA/schedules"),
            (
                "/?view=settings&workspace=EMEA&new=1",
                "/w/EMEA/configuration/collectors/new",
            ),
            ("/?view=hosts&workspace=EMEA", "/w/EMEA/inventory/hosts"),
            ("/?view=documentation&workspace=EMEA", "/w/EMEA/documentation/live"),
            (
                "/?view=technical-architecture&workspace=EMEA",
                "/w/EMEA/documentation/technical",
            ),
        ]:
            page = client.get(source)
            assert page.status_code == 200, (source, page.text)
            assert str(page.url).endswith(target), str(page.url)
        assert (
            client.get("/w/APAC/configuration/collectors/netbackup_jobs").status_code
            == 404
        )


def test_invalid_preferences_recover_and_path_workspace_is_authoritative(database):
    with DashboardClient(app, headers=BROWSER) as client:
        page = client.get(
            "/?view=collectors&workspace=EMEA&date=bad&period=bad&page=bad&scope=removed&collector_id=removed"
        )
        assert page.status_code == 200
        assert (
            "No collections match" in page.text
            or "Daily collection health" in page.text
        )
        assert "?" not in str(page.url)
        assert client.get("/w/NOT_AUTHORIZED/collectors").status_code == 404
        page = client.get(
            "/w/APAC/inventory/hosts", headers={"X-Orchestrator-Zone": "EMEA"}
        )
        assert (
            page.status_code == 200
            and "nbu-apac-01" in page.text
            and "nbu-emea-01" not in page.text
        )
        # Historical/API callers retain the original query interface.
        legacy = client.get(
            "/?view=runs&period=bad", headers={"Accept": "application/json"}
        )
        assert legacy.status_code == 422


def test_collection_detail_path_keeps_zone_and_restores_pagination(database):
    from orchestrator import db
    from orchestrator.models import Attempt

    cid = one_window("FAILED")
    with db.transaction() as session:
        for number in range(1, 27):
            session.add(
                Attempt(
                    id=f"clean-attempt-{number}",
                    window_id=cid,
                    worker_id="EMEA-SAT-1",
                    number=number,
                    status="FAILED",
                    started_at=1,
                    finished_at=2,
                    lease_until=3,
                    result=None,
                    logs="",
                    context={},
                )
            )
    with DashboardClient(app, headers=BROWSER) as client:
        page = client.get(f"/collections/{cid}?workspace=EMEA&page=2")
        assert page.status_code == 200 and str(page.url).endswith(
            "/w/EMEA/collections/" + cid
        )
        assert "<strong>26–26</strong>" in page.text
        again = client.get("/w/EMEA/collections/" + cid)
        assert "<strong>26–26</strong>" in again.text
        assert client.get("/w/APAC/collections/" + cid).status_code == 404
