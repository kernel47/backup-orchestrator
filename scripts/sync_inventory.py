"""Refresh the referential into one inventory file/source per configured zone."""

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

import httpx

from orchestrator.config import settings
from orchestrator.inventory_service import HostRecord, inventory_path, read_source
from services.referential import collect_hosts


def export_content(path, records):
    records = [HostRecord.model_validate(row).model_dump() for row in records]
    if len(records) > 10000 or len({r["id"] for r in records}) != len(records):
        raise ValueError("Maximum 10000 hosts with unique IDs")
    payload = {"version": 1, "hosts": records}
    if path.suffix.lower() == ".json":
        content = json.dumps(payload, indent=2) + "\n"
    else:
        import yaml

        content = yaml.safe_dump(payload, sort_keys=False)
    if len(content.encode()) > 1048576:
        raise ValueError("Inventory payload exceeds 1 MB")
    return content


def write_export(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    # A failed API call/validation never replaces the previous export.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Exported inventory to {path}")


def refresh(path, region=None):
    write_export(path, export_content(path, collect_hosts(region=region)))


def master_client(zone=None):
    headers = {"Authorization": "Bearer " + settings.admin_token}
    if zone:
        headers["X-Orchestrator-Zone"] = zone
    return httpx.Client(base_url=settings.master_url, headers=headers, timeout=30)


def request(client, method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def sync_file(path, zone=None, apply=False):
    payload = read_source(
        {"enabled": True, "kind": "file", "config": {"path": str(path)}}
    )
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("hosts"), list)
    ):
        raise ValueError('Expected {"version":1,"hosts":[...]}')
    regions = {h["zone"] for h in payload["hosts"]}
    if len(regions) > 1 or zone and regions - {zone}:
        raise ValueError("All inventory hosts must belong to one zone / --zone")
    zone = zone or next(iter(regions), None)
    if not zone:
        raise ValueError("Pass --zone for an empty inventory file")
    with master_client(zone) as client:
        sources = request(client, "GET", "/api/inventory/sources")
        source = next(
            (
                s
                for s in sources
                if s["kind"] == "file"
                and inventory_path(s["config"].get("path")).resolve() == path.resolve()
            ),
            None,
        )
        if source is None:
            source = request(
                client,
                "POST",
                "/api/inventory/sources",
                json={
                    "name": f"{zone} inventory",
                    "kind": "file",
                    "config": {"path": str(path)},
                },
            )
        preview = request(
            client, "POST", f"/api/inventory/sources/{source['id']}/discover"
        )
        print(
            f"{zone} changes:",
            dict(Counter(row["state"] for row in preview["changes"])),
        )
        print("Preview:", preview["id"])
        if apply:
            request(client, "POST", f"/api/inventory/syncs/{preview['id']}/apply")
            print("Applied. Activate new hosts in Inventory → Hosts.")
        else:
            print(
                "Review in Inventory → Sources & discovery. Use --apply to apply changes."
            )


def refresh_zones(region=None, filename=None, apply=False):
    if filename and not region:
        raise ValueError("A custom export path requires one explicit region")
    with master_client() as client:
        zones = request(client, "GET", "/api/zones")
    canonical = {z["id"].casefold(): z["id"] for z in zones}
    if len(canonical) != len(zones):
        raise ValueError(
            "Zone IDs must be distinct ignoring case for referential synchronization"
        )
    selected = [
        z["id"]
        for z in zones
        if z["enabled"] and (region is None or z["id"].casefold() == region.casefold())
    ]
    if not selected:
        raise ValueError(
            "Create/enable the requested zones before refreshing the referential."
        )
    records = [
        HostRecord.model_validate(row).model_dump()
        for row in collect_hosts(region=selected[0] if region else None)
    ]
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("Referential host IDs must be unique across zones")
    grouped = {zone: [] for zone in selected}
    for row in records:
        zone = canonical.get(row["zone"].casefold())
        if zone is None:
            raise ValueError(
                f"Unknown referential zone: {row['zone']}. Create the zone or adapt map_host()."
            )
        if zone in grouped:
            grouped[zone].append(dict(row, zone=zone))
    # Validate every zone before writing any file, including empty-zone exports.
    exports = []
    for zone, hosts in grouped.items():
        path = inventory_path(filename or f"inventory/hosts-{zone.casefold()}.json")
        exports.append((zone, path, export_content(path, hosts)))
    for zone, path, content in exports:
        write_export(path, content)
    for zone, path, _ in exports:
        sync_file(path, zone=zone, apply=apply)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        help="Static inventory path, or custom export path with --refresh --zone",
    )
    parser.add_argument(
        "--zone",
        "--region",
        dest="zone",
        help="One zone; refresh defaults to all enabled zones",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch all host types and generate one file per zone",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply discovery; new hosts remain disabled",
    )
    args = parser.parse_args()
    if args.refresh:
        if args.file and not args.zone:
            parser.error(
                "--file with --refresh requires --zone; otherwise one file per zone is generated"
            )
        refresh_zones(args.zone, args.file, args.apply)
    else:
        sync_file(
            inventory_path(args.file or "inventory/hosts.json"), args.zone, args.apply
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, httpx.HTTPError) as error:
        raise SystemExit("Inventory sync failed: " + str(error))
