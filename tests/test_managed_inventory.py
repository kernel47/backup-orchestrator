import json

import pytest
from sqlalchemy import select

from orchestrator import db
from orchestrator import inventory_service as inv
from orchestrator import service as svc
from orchestrator.main import app
from orchestrator.models import (
    Host,
    HostGroup,
    InventorySource,
    InventorySync,
    Schedule,
    Window,
)
from tests.client import DashboardClient as TestClient
from tests.test_host_groups import AUTH, freeze, group


def test_delete_source_preserves_inventory_and_history_and_expires_previews(
    database, tmp_path
):
    from tests.test_core import one_window

    path = tmp_path / "hosts.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "hosts": [{"id": "nbu-emea-01", "zone": "EMEA", "scope": "NetBackup"}],
            }
        )
    )
    body = {"name": "Reference inventory", "config": {"path": str(path)}}
    with TestClient(app) as client:
        source = client.post("/api/inventory/sources", headers=AUTH, json=body).json()
        url = f"/api/inventory/sources/{source['id']}"
        applied = client.post(url + "/discover", headers=AUTH).json()
        assert (
            client.post(
                f"/api/inventory/syncs/{applied['id']}/apply", headers=AUTH
            ).status_code
            == 200
        )
        window_id = one_window("SUCCESS")
        pending = client.post(url + "/discover", headers=AUTH).json()
        with db.Session() as session:
            before = svc.dump(session.get(Host, "nbu-emea-01"))
            groups = {
                g.id: list(g.host_ids) for g in session.scalars(select(HostGroup))
            }
        page = client.get("/?view=sources").text
        assert f'data-delete-source="{source["id"]}"' in page
        assert 'id="delete-source-dialog"' in page
        response = client.delete(url, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["history_preserved"]
        assert path.exists()
        with db.Session() as session:
            assert session.get(InventorySource, source["id"]) is None
            after = svc.dump(session.get(Host, "nbu-emea-01"))
            assert after.pop("source_id") == ""
            before.pop("source_id")
            assert after == before
            assert session.get(Window, window_id).status == "SUCCESS"
            assert {
                g.id: list(g.host_ids) for g in session.scalars(select(HostGroup))
            } == groups
            assert session.get(InventorySync, applied["id"]).status == "APPLIED"
            assert session.get(InventorySync, pending["id"]).status == "EXPIRED"
        assert (
            client.post(
                f"/api/inventory/syncs/{pending['id']}/apply", headers=AUTH
            ).status_code
            == 409
        )
        replacement = client.post(
            "/api/inventory/sources", headers=AUTH, json=body
        ).json()
        assert (
            client.post(
                f"/api/inventory/sources/{replacement['id']}/discover", headers=AUTH
            ).status_code
            == 200
        )


def test_delete_source_requires_admin_and_owning_workspace(database):
    with TestClient(app) as client:
        emea = {**AUTH, "X-Orchestrator-Zone": "EMEA"}
        source = client.post(
            "/api/inventory/sources",
            headers=emea,
            json={"name": "EMEA source", "config": {"path": "inventory/hosts.json"}},
        ).json()
        url = f"/api/inventory/sources/{source['id']}"
        assert client.delete(url).status_code == 401
        assert (
            client.delete(
                url, headers={**AUTH, "X-Orchestrator-Zone": "APAC"}
            ).status_code
            == 404
        )
        assert client.delete(url, headers=emea).status_code == 200
        assert client.delete(url, headers=emea).status_code == 404


@pytest.mark.parametrize(
    "content, expected",
    [
        (None, "Inventory file not found:"),
        ('{"version":', "Invalid inventory JSON at line 1, column"),
        ('{"hosts": []}', 'Expected {"version":1,"hosts":[...]}'),
        (
            json.dumps(
                {
                    "version": 1,
                    "hosts": [
                        {"id": "host-01", "parameters": {"token": "private-test-value"}}
                    ],
                }
            ),
            "Invalid inventory host field 'zone': Field required",
        ),
        (
            json.dumps({"version": 1, "hosts": [{"id": "host-01", "zone": "APAC"}]}),
            "Inventory hosts must belong to the current zone workspace",
        ),
        (
            json.dumps({"version": 1, "hosts": [{"id": "host-01", "zone": "EMEA"}]}),
            None,
        ),
    ],
)
def test_relative_inventory_path_and_discovery_errors(
    database, tmp_path, monkeypatch, content, expected
):
    master = tmp_path / "master"
    directory = master / "inventory"
    directory.mkdir(parents=True)
    monkeypatch.setattr(inv, "ROOT", master)
    if content is not None:
        (directory / "hosts.json").write_text(content)
    headers = {**AUTH, "X-Orchestrator-Zone": "EMEA"}
    with TestClient(app) as client:
        source = client.post(
            "/api/inventory/sources",
            headers=headers,
            json={"name": "Relative file", "config": {"path": "inventory/hosts.json"}},
        )
        assert source.status_code == 200
        response = client.post(
            f"/api/inventory/sources/{source.json()['id']}/discover", headers=headers
        )
        if expected is None:
            assert response.status_code == 200, response.text
            assert response.json()["changes"][0]["id"] == "host-01"
        else:
            assert response.status_code == 422
            assert expected in response.json()["detail"]
            assert "private-test-value" not in response.text


@pytest.mark.parametrize(
    "error, status, expected",
    [
        (
            PermissionError(13, "Permission denied", "inventory/hosts.json"),
            422,
            "Check permissions for the master service account",
        ),
        (RuntimeError("private-test-value"), 500, "failed unexpectedly"),
    ],
)
def test_discovery_reports_permission_and_internal_errors(
    database, monkeypatch, error, status, expected
):
    def fail(source):
        raise error

    monkeypatch.setattr(inv, "read_source", fail)
    with TestClient(app) as client:
        source = client.post(
            "/api/inventory/sources",
            headers=AUTH,
            json={
                "name": "Unreadable file",
                "config": {"path": "inventory/hosts.json"},
            },
        ).json()
        response = client.post(
            f"/api/inventory/sources/{source['id']}/discover", headers=AUTH
        )
        assert response.status_code == status
        assert expected in response.json()["detail"]
        assert "private-test-value" not in response.text


def payload(**overrides):
    return {
        "version": 1,
        "source_id": "test-cmdb",
        "hosts": [
            {"id": "new-host", "zone": "EMEA", "scope": "NetBackup", **overrides}
        ],
    }


def test_inventory_discover_apply_activate_archive_and_restart(database):
    with TestClient(app) as client:
        assert client.post("/api/inventory/discover", json=payload()).status_code == 401
        response = client.post("/api/inventory/discover", headers=AUTH, json=payload())
        assert response.status_code == 200, response.text
        sync = response.json()
        assert sync["changes"][0]["state"] == "new"
        assert not any(h["id"] == "new-host" for h in client.get("/api/hosts").json())
        url = "/api/inventory/syncs/" + sync["id"] + "/apply"
        assert client.post(url, headers=AUTH).status_code == 200
        assert client.post(url, headers=AUTH).status_code == 200
        with db.transaction() as session:
            host = session.get(Host, "new-host")
            assert (
                host.discovered and not host.enabled and host.source_id == "test-cmdb"
            )
        assert (
            client.put(
                "/api/hosts/new-host/state", headers=AUTH, json={"enabled": True}
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/hosts/new-host/state", headers=AUTH, json={"enabled": False}
            ).status_code
            == 200
        )
        svc.bootstrap()
        with db.transaction() as session:
            assert not session.get(Host, "new-host").enabled
        assert client.delete("/api/hosts/new-host", headers=AUTH).status_code == 200
        assert not any(h["id"] == "new-host" for h in client.get("/api/hosts").json())
        again = client.post(
            "/api/inventory/discover", headers=AUTH, json=payload()
        ).json()
        assert again["changes"][0]["state"] == "archived"
        assert (
            client.post(
                "/api/inventory/syncs/" + again["id"] + "/apply", headers=AUTH
            ).status_code
            == 200
        )
        with db.transaction() as session:
            assert session.get(Host, "new-host").deleted
        assert (
            client.put(
                "/api/hosts/new-host/state", headers=AUTH, json={"enabled": True}
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/hosts/new-host/state",
                headers=AUTH,
                json={"enabled": False, "restore": True},
            ).status_code
            == 200
        )


def test_preview_rejects_duplicates_unknown_zone_and_stale_apply(database):
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/inventory/discover", headers=AUTH, json=payload(zone="unknown")
            ).status_code
            == 422
        )
        data = payload()
        data["hosts"] *= 2
        assert (
            client.post("/api/inventory/discover", headers=AUTH, json=data).status_code
            == 422
        )
        sync = client.post(
            "/api/inventory/discover", headers=AUTH, json=payload()
        ).json()
        client.put(
            "/api/hosts/nbu-emea-01/state", headers=AUTH, json={"enabled": False}
        )
        assert (
            client.post(
                "/api/inventory/syncs/" + sync["id"] + "/apply", headers=AUTH
            ).status_code
            == 409
        )
        with db.transaction() as session:
            assert session.get(Host, "new-host") is None


def test_missing_hosts_are_not_deleted_and_import_keeps_activation(database):
    with TestClient(app) as client:
        sync = client.post(
            "/api/inventory/discover", headers=AUTH, json=payload()
        ).json()
        client.post("/api/inventory/syncs/" + sync["id"] + "/apply", headers=AUTH)
        client.put("/api/hosts/new-host/state", headers=AUTH, json={"enabled": True})
        changed = client.post(
            "/api/inventory/discover",
            headers=AUTH,
            json=payload(parameters={"changed": True}),
        ).json()
        assert changed["changes"][0]["state"] == "changed"
        client.post("/api/inventory/syncs/" + changed["id"] + "/apply", headers=AUTH)
        missing = client.post(
            "/api/inventory/discover",
            headers=AUTH,
            json={"version": 1, "source_id": "test-cmdb", "hosts": []},
        ).json()
        assert missing["changes"][0]["state"] == "missing"
        client.post("/api/inventory/syncs/" + missing["id"] + "/apply", headers=AUTH)
        with db.transaction() as session:
            h = session.get(Host, "new-host")
            assert h.enabled and not h.deleted and h.parameters == {"changed": True}


def test_multi_group_zone_targeting_deduplicates_and_group_lifecycle(
    database, monkeypatch
):
    freeze(monkeypatch)
    with TestClient(app) as client:
        a = group(client, ["nbu-emea-01"], "A")
        b = group(client, ["nbu-emea-01", "nbu-emea-02"], "B")
        spec = {
            "collector_id": "netbackup_jobs",
            "host_group_ids": [a["id"], b["id"]],
            "zones": ["EMEA"],
        }
        preview = client.get(
            "/api/targets",
            params={
                "collector_id": "netbackup_jobs",
                "host_group_ids": a["id"] + "," + b["id"],
                "zones": "EMEA",
            },
        ).json()
        assert {h["id"] for h in preview["hosts"]} == {"nbu-emea-01", "nbu-emea-02"}
        result = client.post("/api/group-runs", headers=AUTH, json=spec)
        assert result.status_code == 200, result.text
        assert len(result.json()["ids"]) == 2
        schedule = client.post(
            "/api/schedules",
            headers=AUTH,
            json={**spec, "name": "EMEA Jobs", "interval_seconds": 600},
        )
        assert schedule.status_code == 200, schedule.text
        rule = schedule.json()
        assert set(rule["host_group_ids"]) == {a["id"], b["id"]} and rule["zones"] == [
            "EMEA"
        ]
        assert (
            client.patch(
                "/api/host-groups/" + a["id"], headers=AUTH, json={"enabled": False}
            ).status_code
            == 200
        )
        assert (
            client.post("/api/group-runs", headers=AUTH, json=spec).status_code == 422
        )
        svc.tick()
        assert (
            client.delete("/api/host-groups/" + a["id"], headers=AUTH).status_code
            == 200
        )
        with db.transaction() as session:
            assert not session.get(Schedule, rule["id"]).enabled
        assert not any(
            g["id"] == a["id"] for g in client.get("/api/host-groups").json()
        )
        svc.bootstrap()
        with db.transaction() as session:
            assert session.get(HostGroup, a["id"]).deleted


def test_zones_workers_and_management_pages(database):
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/zones", headers=AUTH, json={"id": "LAB", "name": "Lab"}
            ).status_code
            == 200
        )
        provision = client.post(
            "/api/satellites",
            headers=AUTH,
            json={
                "id": "LAB-SAT-1",
                "zones": ["LAB"],
                "scopes": ["General"],
                "concurrency": 1,
            },
        )
        assert provision.status_code == 200, provision.text
        token = provision.json()["worker_token"]
        body = {"worker_id": "LAB-SAT-1"}
        assert (
            client.post(
                "/api/workers/heartbeat",
                headers={"Authorization": "Bearer " + token},
                json=body,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/workers/heartbeat",
                headers={"Authorization": "Bearer bad"},
                json=body,
            ).status_code
            == 401
        )
        assert (
            "token_hash" not in client.get("/api/workers").text
            and token not in client.get("/api/workers").text
        )
        assert client.delete("/api/zones/LAB", headers=AUTH).status_code == 409
        for view in [
            "zones",
            "sources",
            "workers",
            "hosts",
            "groups",
            "schedules",
            "overview",
        ]:
            response = client.get("/?view=" + view)
            assert response.status_code == 200, (view, response.text)


def test_file_source_discovery_contract(database, tmp_path):
    import json

    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps({"version": 1, "hosts": [{"id": "file-host", "zone": "EMEA"}]})
    )
    with TestClient(app) as client:
        source = client.post(
            "/api/inventory/sources",
            headers=AUTH,
            json={
                "name": "Local export",
                "kind": "file",
                "config": {"path": str(path)},
            },
        ).json()
        sync = client.post(
            "/api/inventory/sources/" + source["id"] + "/discover", headers=AUTH
        )
        assert sync.status_code == 200, sync.text
        assert sync.json()["changes"][0]["id"] == "file-host"


def test_source_ownership_overview_summary_and_token_rotation(database):
    with TestClient(app) as client:
        first = client.post(
            "/api/inventory/discover", headers=AUTH, json=payload()
        ).json()
        client.post("/api/inventory/syncs/" + first["id"] + "/apply", headers=AUTH)
        other = payload()
        other["source_id"] = "other-source"
        assert (
            client.post("/api/inventory/discover", headers=AUTH, json=other).status_code
            == 422
        )
        page = client.get("/?view=overview&collector_id=netbackup_jobs&date=2026-10-15")
        assert (
            page.status_code == 200
            and "collector_id=datadomain_capacity" in page.text
            and 'class="filters"' not in page.text
        )
        worker = client.post(
            "/api/satellites",
            headers=AUTH,
            json={"id": "ROTATE-SAT", "zones": ["EMEA"], "scopes": ["NetBackup"]},
        ).json()
        new = client.post(
            "/api/satellites/ROTATE-SAT/rotate-token", headers=AUTH
        ).json()
        body = {"worker_id": "ROTATE-SAT"}
        assert (
            client.post(
                "/api/workers/heartbeat",
                json=body,
                headers={"Authorization": "Bearer " + worker["worker_token"]},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/workers/heartbeat",
                json=body,
                headers={"Authorization": "Bearer " + new["worker_token"]},
            ).status_code
            == 200
        )
        for config in [{"path": 123}, {"path": ""}, {"path": "inventory.py"}]:
            assert (
                client.post(
                    "/api/inventory/sources",
                    headers=AUTH,
                    json={"name": "bad", "kind": "file", "config": config},
                ).status_code
                == 422
            )


def test_large_discovery_is_paginated_in_ui(database):
    with TestClient(app) as client:
        data = {
            "version": 1,
            "hosts": [{"id": f"new-{n}", "zone": "EMEA"} for n in range(30)],
        }
        preview = client.post("/api/inventory/discover", headers=AUTH, json=data).json()
        first = client.get("/?view=sources&sync_id=" + preview["id"]).text
        second = client.get("/?view=sources&sync_id=" + preview["id"] + "&page=2").text
        assert "<strong>1–25</strong>" in first and "<strong>26–30</strong>" in second
        assert "<td>new-29</td>" not in first and "<td>new-29</td>" in second


def test_export_validation_preserves_file_and_reads_yaml(tmp_path, monkeypatch):
    import pytest

    from scripts import sync_inventory as export

    path = tmp_path / "hosts.yaml"
    path.write_text("version: 1\nhosts: []\n")
    monkeypatch.setattr(
        export,
        "collect_hosts",
        lambda: [{"id": "api-host", "zone": "EMEA", "scope": "NetBackup"}],
    )
    export.refresh(path)
    assert (
        inv.read_source(
            {"enabled": True, "kind": "file", "config": {"path": str(path)}}
        )["hosts"][0]["id"]
        == "api-host"
    )
    original = path.read_bytes()
    monkeypatch.setattr(
        export, "collect_hosts", lambda: [{"id": "bad host", "zone": "EMEA"}]
    )
    with pytest.raises(ValueError):
        export.refresh(path)
    assert path.read_bytes() == original
    path.write_text("x" * 1048577)
    with pytest.raises(ValueError, match="1 MB"):
        inv.read_source(
            {"enabled": True, "kind": "file", "config": {"path": str(path)}}
        )


def test_selected_host_activation_is_atomic(database):
    with db.transaction() as s:
        s.get(Host, "nbu-emea-01").enabled = False
        s.get(Host, "nbu-emea-01").discovered = True
        s.get(Host, "nbu-emea-02").enabled = False
        s.get(Host, "nbu-emea-03").deleted = True
    with TestClient(app) as client:
        url = "/api/hosts/state"
        body = {"ids": ["nbu-emea-01", "nbu-emea-02"], "enabled": True}
        assert client.put(url, json=body).status_code == 401
        with db.transaction() as s:
            s.get(Host, "nbu-emea-02").preferred_worker = "missing-worker"
        assert client.put(url, headers=AUTH, json=body).status_code == 422
        with db.Session() as s:
            assert (
                not s.get(Host, "nbu-emea-01").enabled
                and s.get(Host, "nbu-emea-01").discovered
            )
        with db.transaction() as s:
            s.get(Host, "nbu-emea-02").preferred_worker = "EMEA-SAT-2"
        result = client.put(url, headers=AUTH, json=body)
        assert result.status_code == 200 and result.json()["changed"] == 2
        assert client.put(url, headers=AUTH, json=body).json()["changed"] == 0
        assert (
            client.put(url, headers=AUTH, json={**body, "enabled": False}).json()[
                "changed"
            ]
            == 2
        )
        assert (
            client.put(
                url,
                headers=AUTH,
                json={"ids": ["nbu-emea-01", "nbu-emea-03"], "enabled": True},
            ).status_code
            == 422
        )
        with db.Session() as s:
            assert not s.get(Host, "nbu-emea-01").enabled
        page = client.get("/?view=hosts").text
        assert (
            "Select all visible hosts" in page
            and "Activate selected" in page
            and "Disable selected" in page
        )
        assert "Delete / archive" not in page and ">Archived<" not in page
        assert "data-group-hosts-state" not in client.get("/?view=groups").text
