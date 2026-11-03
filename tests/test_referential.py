"""Referential filtering and refresh failures must preserve managed inventory inputs."""

import json

import httpx
import pytest

from scripts import sync_inventory
from services.referential import ReferentialService, ReferentialSettings


def use_api(monkeypatch, response):
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        return response(request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs)
    )
    monkeypatch.setenv("REFERENTIAL_URL", "https://referential.example/hosts")
    monkeypatch.setenv("REFERENTIAL_TOKEN", "test-token")
    return requests


def test_region_export_keeps_all_types_and_maps_each_scope(monkeypatch, tmp_path):
    requests = use_api(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json=[
                {
                    "hostname": "nbu-01",
                    "region": "emea",
                    "type": "Master server",
                    "address": "nbu-01.example.net",
                },
                {"hostname": "dd-01", "region": "EMEA", "type": "Data Domain"},
                {"hostname": "nbu-02", "region": "APAC", "type": "Master server"},
            ],
        ),
    )
    path = tmp_path / "hosts.json"
    sync_inventory.refresh(path, region="EMEA")
    hosts = json.loads(path.read_text())["hosts"]
    assert len(hosts) == 2 and hosts[0]["id"] == "nbu-01"
    assert hosts[0]["zone"] == "EMEA" and hosts[0]["scope"] == "NetBackup"
    assert hosts[0]["parameters"] == {
        "asset_type": "Master server",
        "address": "nbu-01.example.net",
    }
    assert hosts[1]["id"] == "dd-01" and hosts[1]["scope"] == "DataDomain"
    assert dict(requests[0].url.params) == {"region": "EMEA"}
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert "test-token" not in path.read_text()


@pytest.mark.parametrize(
    "status,payload",
    [
        (503, {}),
        (200, {"hosts": [], "next_page": 2}),
        (200, [{"hostname": "nbu-01", "region": "EMEA"}]),
        (200, [{"hostname": "bad host", "region": "EMEA", "type": "Master server"}]),
    ],
)
def test_failed_or_incomplete_refresh_preserves_existing_file(
    monkeypatch, tmp_path, status, payload
):
    use_api(monkeypatch, lambda _: httpx.Response(status, json=payload))
    path = tmp_path / "hosts.json"
    original = '{"version":1,"hosts":[{"id":"existing","zone":"EMEA"}]}'
    path.write_text(original)
    with pytest.raises((ValueError, httpx.HTTPError)):
        sync_inventory.refresh(path, region="EMEA")
    assert path.read_text() == original


def test_referential_config_reads_selected_env_file_and_static_files_need_no_api(
    monkeypatch, tmp_path
):
    env_file = tmp_path / "master.env"
    env_file.write_text(
        "REFERENTIAL_URL=https://referential.example/assets\nREFERENTIAL_TOKEN=example\nREFERENTIAL_VERIFY_SSL=false\n"
    )
    config = ReferentialSettings(_env_file=env_file)
    assert config.url.endswith("/assets") and config.token == "example"
    assert not config.verify_ssl and "token=" not in repr(config)
    service = ReferentialService(ReferentialSettings(_env_file=None))
    with pytest.raises(ValueError, match="Region"):
        service.collect_hosts(" ")
    monkeypatch.setattr(
        "sys.argv", ["sync_inventory", "--refresh", "--file", "inventory/hosts.json"]
    )
    with pytest.raises(SystemExit) as error:
        sync_inventory.main()
    assert error.value.code == 2


def test_refresh_generates_one_complete_export_and_source_per_zone(
    monkeypatch, tmp_path
):
    from pathlib import Path

    calls = []

    def response(request):
        if request.url.host == "referential.example":
            assert not request.url.params
            return httpx.Response(
                200,
                json=[
                    {"hostname": "nbu-01", "region": "EMEA", "type": "Master server"},
                    {"hostname": "dd-01", "region": "EMEA", "type": "Data Domain"},
                    {"hostname": "tape-01", "region": "APAC", "type": "Tape library"},
                ],
            )
        assert request.headers["Authorization"] == "Bearer master-test-token"
        if request.url.path == "/api/zones":
            return httpx.Response(
                200,
                json=[
                    {"id": zone, "enabled": True} for zone in ("EMEA", "APAC", "AMER")
                ],
            )
        zone = request.headers["X-Orchestrator-Zone"]
        calls.append((zone, request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/inventory/sources":
            data = json.loads(request.content)
            assert data["config"]["path"].endswith(f"hosts-{zone.lower()}.json")
            return httpx.Response(200, json={"id": zone})
        if request.url.path.endswith("/discover"):
            return httpx.Response(200, json={"id": zone, "changes": [{"state": "new"}]})
        return httpx.Response(200, json={"status": "APPLIED"})

    requests = use_api(monkeypatch, response)
    monkeypatch.setattr(sync_inventory.settings, "master_url", "http://master.example")
    monkeypatch.setattr(sync_inventory.settings, "admin_token", "master-test-token")
    monkeypatch.setattr(
        sync_inventory, "inventory_path", lambda value: tmp_path / Path(value)
    )
    monkeypatch.setattr("sys.argv", ["sync_inventory", "--refresh", "--apply"])
    sync_inventory.main()
    assert len([r for r in requests if r.url.host == "referential.example"]) == 1
    for zone in ("EMEA", "APAC", "AMER"):
        assert [method for z, method, path in calls if z == zone] == [
            "GET",
            "POST",
            "POST",
            "POST",
        ]
    emea = json.loads((tmp_path / "inventory/hosts-emea.json").read_text())["hosts"]
    assert {h["scope"] for h in emea} == {"NetBackup", "DataDomain"}
    assert (
        json.loads((tmp_path / "inventory/hosts-apac.json").read_text())["hosts"][0][
            "scope"
        ]
        == "TapeLibrary"
    )
    assert (
        json.loads((tmp_path / "inventory/hosts-amer.json").read_text())["hosts"] == []
    )


def test_invalid_batch_leaves_all_zone_exports_untouched(monkeypatch, tmp_path):
    from pathlib import Path

    def response(request):
        if request.url.path == "/api/zones":
            return httpx.Response(
                200,
                json=[{"id": "EMEA", "enabled": True}, {"id": "APAC", "enabled": True}],
            )
        return httpx.Response(
            200,
            json=[
                {"hostname": "nbu-01", "region": "EMEA", "type": "Master server"},
                {"hostname": "nbu-02", "region": "UNKNOWN", "type": "Master server"},
            ],
        )

    use_api(monkeypatch, response)
    monkeypatch.setattr(
        sync_inventory, "inventory_path", lambda value: tmp_path / Path(value)
    )
    directory = tmp_path / "inventory"
    directory.mkdir()
    for zone in ("emea", "apac"):
        (directory / f"hosts-{zone}.json").write_text("previous export")
    with pytest.raises(ValueError, match="Unknown referential zone"):
        sync_inventory.refresh_zones()
    assert all(path.read_text() == "previous export" for path in directory.iterdir())


def test_multi_zone_apply_preserves_host_activation_and_groups(
    database, monkeypatch, tmp_path
):
    from pathlib import Path

    from sqlalchemy import select

    from orchestrator import db
    from orchestrator.main import app
    from orchestrator.models import Host, HostGroup, InventorySource
    from tests.client import DashboardClient

    with DashboardClient(app) as client:
        with db.Session() as session:
            before = {
                h.id: (h.enabled, h.discovered) for h in session.scalars(select(Host))
            }
            groups = {
                g.id: list(g.host_ids) for g in session.scalars(select(HostGroup))
            }

        def response(request):
            if request.url.host == "referential.example":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "hostname": "ref-emea-nbu",
                            "region": "EMEA",
                            "type": "Master server",
                        },
                        {
                            "hostname": "ref-emea-dd",
                            "region": "EMEA",
                            "type": "Data Domain",
                        },
                        {
                            "hostname": "ref-apac-tape",
                            "region": "APAC",
                            "type": "Tape library",
                        },
                    ],
                )
            reply = client.request(
                request.method,
                request.url.raw_path.decode(),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(reply.status_code, json=reply.json())

        use_api(monkeypatch, response)
        monkeypatch.setattr(
            sync_inventory, "inventory_path", lambda value: tmp_path / Path(value)
        )
        sync_inventory.refresh_zones(apply=True)
        with db.Session() as session:
            for hid, state in before.items():
                host = session.get(Host, hid)
                assert (host.enabled, host.discovered) == state
            for hid in ("ref-emea-nbu", "ref-emea-dd", "ref-apac-tape"):
                host = session.get(Host, hid)
                assert host and not host.enabled and host.discovered
                source = session.get(InventorySource, host.source_id)
                assert source.zone == host.zone
                assert source.config["path"].endswith(f"hosts-{host.zone.lower()}.json")
            assert {
                g.id: g.host_ids for g in session.scalars(select(HostGroup))
            } == groups


@pytest.mark.parametrize(
    "location", ["master_servers/?region=EMEA", "/assets/master_servers/?region=EMEA"]
)
def test_referential_follows_trailing_slash_redirect_with_region_and_auth(
    monkeypatch, location
):
    def response(request):
        assert request.url.params["region"] == "EMEA"
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == "/assets/master_servers":
            return httpx.Response(301, headers={"Location": location})
        assert request.url.path == "/assets/master_servers/"
        return httpx.Response(
            200,
            json=[{"hostname": "nbu-01", "region": "EMEA", "type": "Master server"}],
        )

    requests = use_api(monkeypatch, response)
    monkeypatch.setenv(
        "REFERENTIAL_URL", "https://referential.example/assets/master_servers"
    )
    hosts = ReferentialService().collect_hosts("EMEA")
    assert len(requests) == 2
    assert hosts[0]["id"] == "nbu-01"


def test_referential_redirect_loop_preserves_existing_export(monkeypatch, tmp_path):
    requests = use_api(
        monkeypatch,
        lambda _: httpx.Response(301, headers={"Location": "/hosts?region=EMEA"}),
    )
    path = tmp_path / "hosts-emea.json"
    path.write_text("previous export")
    with pytest.raises(httpx.TooManyRedirects):
        sync_inventory.refresh(path, region="EMEA")
    assert path.read_text() == "previous export"
    assert len(requests) == 11
