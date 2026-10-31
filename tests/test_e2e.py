"""Real API + two independent HTTP workers + actual Python-script subprocesses."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_real_http_workers_and_historical_retry(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    master_config = tmp_path / "master.env"
    master_config.write_text(
        f"MASTER_HOST=127.0.0.1\nMASTER_PORT={port}\nAUTH_USERNAME=test-admin\nAUTH_PASSWORD=test-password\nSESSION_SECRET=integration-test-session-secret\n"
    )
    env = dict(
        os.environ,
        DATABASE_URL="sqlite:///" + str(tmp_path / "e2e.db"),
        MASTER_URL=f"http://127.0.0.1:{port}",
        SCHEDULER_ENABLED="false",
        POLL_SECONDS=".1",
        ADMIN_TOKEN="e2e-admin",
        WORKER_TOKEN="e2e-worker",
    )
    subprocess.run(
        [sys.executable, "-m", "tests.seed"],
        env=env,
        cwd=root,
        check=True,
        capture_output=True,
    )
    ps = []
    with open(tmp_path / "services.log", "w") as log:
        try:
            ps.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(root / "run.py"),
                        "master",
                        "--config",
                        str(master_config),
                    ],
                    env=env,
                    cwd=root,
                    stdout=log,
                    stderr=log,
                )
            )
            with httpx.Client(
                base_url=env["MASTER_URL"],
                timeout=20,
                headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
            ) as c:
                for _ in range(100):
                    try:
                        if c.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("API inaccessible")
                for wid in ["EMEA-SAT-1", "EMEA-SAT-2"]:
                    worker_config = tmp_path / (wid + ".env")
                    worker_config.write_text(
                        f"WORKER_ID={wid}\nWORKER_TIMEZONE=Europe/Paris\n"
                    )
                    ps.append(
                        subprocess.Popen(
                            [
                                sys.executable,
                                str(root / "run.py"),
                                "worker",
                                "--config",
                                str(worker_config),
                            ],
                            cwd=root,
                            env=env,
                            stdout=log,
                            stderr=log,
                        )
                    )
                filters = {
                    "date": "2026-10-15",
                    "collector_id": "netbackup_jobs",
                    "zone": "EMEA",
                    "status": "FAILED",
                }
                before = c.get("/api/collections", params=filters).json()
                assert len(before) == 3
                ids = [r["id"] for r in before]
                response = c.post(
                    "/api/collections/retry",
                    headers={"Authorization": "Bearer e2e-admin"},
                    json={"ids": ids},
                )
                assert response.status_code == 200 and len(response.json()["ids"]) == 3
                for _ in range(100):
                    rows = c.get(
                        "/api/collections", params={**filters, "status": "SUCCESS"}
                    ).json()
                    completed = [r for r in rows if r["id"] in ids]
                    if len(completed) == 3:
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError("Les trois retries ne sont pas terminés")
                assert all(
                    r["period_start"] == "2026-10-15T10:00:00Z"
                    and len(r["attempts"]) == 2
                    for r in completed
                )
                assert all("DEMO" in r["attempts"][-1]["logs"] for r in completed)
                assert all(
                    r["attempts"][-1]["result"]["details"]["simulated"] is True
                    for r in completed
                )
                assert c.get("/collections/" + ids[0]).status_code == 200
                assert sum(w["online"] for w in c.get("/api/workers").json()) == 2
                assert (
                    c.get(
                        "/",
                        params={
                            "view": "runs",
                            "collector_id": "netbackup_jobs",
                            "date": "2026-10-15",
                            "start": "2026-10-15T10:00:00Z",
                            "end": "2026-10-15T11:00:00Z",
                        },
                    ).status_code
                    == 200
                )
                assert (
                    c.post(
                        "/api/workers/claim", json={"worker_id": "EMEA-SAT-1"}
                    ).status_code
                    == 401
                )
        finally:
            for p in reversed(ps):
                p.terminate()
            for p in ps:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()


def test_clean_master_file_inventory_and_env_only_worker(tmp_path):
    import json

    export = tmp_path / "hosts.json"
    export.write_text(
        json.dumps(
            {
                "version": 1,
                "hosts": [
                    {
                        "id": "new-nbu-01",
                        "zone": "EMEA",
                        "scope": "NetBackup",
                        "preferred_worker": "NEW-SAT-1",
                        "parameters": {"simulation": "SUCCESS"},
                    }
                ],
            }
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    master_config = tmp_path / "master.env"
    master_config.write_text(
        f"MASTER_HOST=127.0.0.1\nMASTER_PORT={port}\nAUTH_USERNAME=test-admin\nAUTH_PASSWORD=test-password\nSESSION_SECRET=integration-test-session-secret\n"
    )
    env = dict(
        os.environ,
        DATABASE_URL="sqlite:///" + str(tmp_path / "fresh.db"),
        MASTER_URL=f"http://127.0.0.1:{port}",
        SCHEDULER_ENABLED="false",
        ADMIN_TOKEN="fresh-admin",
        WORKER_TOKEN="legacy-not-used",
        POLL_SECONDS=".1",
    )
    auth = {"Authorization": "Bearer fresh-admin"}
    processes = []
    with open(tmp_path / "fresh-services.log", "w") as log:
        try:
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(root / "run.py"),
                        "master",
                        "--config",
                        str(master_config),
                    ],
                    env=env,
                    cwd=root,
                    stdout=log,
                    stderr=log,
                )
            )
            with httpx.Client(
                base_url=env["MASTER_URL"],
                timeout=20,
                headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("Fresh master failed to start")
                assert (
                    client.get("/api/hosts").json() == []
                    and client.get("/api/collectors").json() == []
                    and client.get("/api/zones").json() == []
                )
                assert client.get("/api/catalog").json()["scopes"] == []
                assert client.get("/api/workers").json() == []
                overview = client.get("/?view=overview").text
                assert "DEMO" not in overview and "Demo workspace" not in overview
                assert "Create zone" in client.get("/").text
                assert 'class="demo-pill"' not in client.get("/").text
                assert (
                    client.post(
                        "/api/zones", headers=auth, json={"id": "EMEA", "name": "EMEA"}
                    ).status_code
                    == 200
                )
                provision = client.post(
                    "/api/satellites",
                    headers=auth,
                    json={
                        "id": "NEW-SAT-1",
                        "zones": ["EMEA"],
                        "scopes": ["NetBackup"],
                        "concurrency": 1,
                    },
                ).json()
                token = provision["worker_token"]
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.sync_inventory",
                        "--file",
                        str(export),
                        "--apply",
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert result.returncode == 0, result.stderr
                assert "'new': 1" in result.stdout
                sources = client.get("/api/inventory/sources").json()
                assert len(sources) == 1 and sources[0]["kind"] == "file"
                assert client.get("/api/hosts").json()[0]["discovered"]
                assert (
                    client.put(
                        "/api/hosts/new-nbu-01/state",
                        headers=auth,
                        json={"enabled": True},
                    ).status_code
                    == 200
                )
                group = client.post(
                    "/api/host-groups",
                    headers=auth,
                    json={"name": "Production NetBackup", "host_ids": ["new-nbu-01"]},
                ).json()
                collector = client.post(
                    "/api/collectors",
                    headers=auth,
                    json={
                        "id": "fresh_jobs",
                        "name": "NetBackup Jobs",
                        "scope": "NetBackup",
                        "script": "fake",
                        "interval_seconds": 600,
                        "timeout_seconds": 30,
                        "max_retries": 0,
                        "enabled": True,
                        "parameters": {"simulation": "SUCCESS"},
                    },
                )
                assert collector.status_code == 200, collector.text
                target = {
                    "collector_id": "fresh_jobs",
                    "host_group_ids": [group["id"]],
                    "zones": ["EMEA"],
                }
                run = client.post("/api/group-runs", headers=auth, json=target)
                assert run.status_code == 200, run.text
                cid = run.json()["ids"][0]
                worker_env = dict(
                    env,
                    WORKER_ID="NEW-SAT-1",
                    WORKER_TOKEN=token,
                    WORKER_CONCURRENCY="1",
                )
                worker_config = tmp_path / "worker.env"
                worker_config.write_text("WORKER_TIMEZONE=Europe/Paris\n")
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            str(root / "run.py"),
                            "worker",
                            "--config",
                            str(worker_config),
                        ],
                        env=worker_env,
                        cwd=root,
                        stdout=log,
                        stderr=log,
                    )
                )
                for _ in range(100):
                    row = client.get("/api/collections/" + cid).json()
                    if row["status"] == "SUCCESS":
                        break
                    time.sleep(0.15)
                else:
                    raise AssertionError(
                        "Env-only worker did not execute the discovered host collector"
                    )
                assert (
                    len(row["attempts"]) == 1
                    and row["attempts"][0]["worker_id"] == "NEW-SAT-1"
                )
                assert (
                    row["attempts"][0]["result"]["summary"]
                    and row["attempts"][0]["result"]["collected_at"]
                )
                assert row["attempts"][0]["context"]["host_group_ids"] == [group["id"]]
                assert client.get("/?view=overview").status_code == 200
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
