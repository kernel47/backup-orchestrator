import asyncio
import sys

import pytest
import yaml

from worker.commands import build_command
from worker.executor import execute

DEFINITION = {
    "command": ["backup-collector", "collect"],
    "context_args": {
        "asset": "host",
        "start-date": "period_start",
        "end-date": "period_end",
    },
    "parameter_args": {
        "scope": "scope",
        "type": "type",
        "debug": "debug",
        "dry-run": "dry_run",
    },
    "required_parameters": ["scope", "type"],
}


@pytest.mark.parametrize(
    "catalog",
    [
        None,
        [],
        {"external": 42},
        {"external": {"command": [sys.executable], "parameter_args": []}},
        {"external": {"command": [sys.executable], "defaults": None}},
        {"external": {"command": [sys.executable], "context_args": {12: "host"}}},
    ],
)
def test_invalid_worker_catalog_returns_a_configuration_report(monkeypatch, catalog):
    monkeypatch.setattr("worker.executor.load", lambda name: catalog)
    report, _ = asyncio.run(
        execute({"script": "external", "timeout_seconds": 5, "parameters": {}})
    )
    assert report["status"] == "FAILURE"
    assert report["details"]["reason"] == "COMMAND_CONFIG"


def test_invalid_yaml_returns_a_report_without_disclosing_configuration(monkeypatch):
    def invalid_catalog(name):
        return yaml.safe_load("private-secret: [broken")

    monkeypatch.setattr("worker.executor.load", invalid_catalog)
    report, _ = asyncio.run(execute({"script": "external", "timeout_seconds": 5}))
    assert report["details"]["reason"] == "COMMAND_CONFIG"
    assert "private-secret" not in str(report)


@pytest.mark.parametrize("directory_exists", [True, False])
def test_external_command_loads_dotenv_from_its_working_directory(
    tmp_path, monkeypatch, directory_exists
):
    monkeypatch.delenv("COLLECTOR_TEST_REFERENCE", raising=False)
    script = tmp_path / "cli.py"
    script.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv(Path.cwd() / '.env')\n"
        "assert os.environ['COLLECTOR_TEST_REFERENCE'] == 'available'\n"
        "print(json.dumps({'status': 'SUCCESS', 'summary': 'Configuration loaded'}))\n"
    )
    (tmp_path / ".env").write_text("COLLECTOR_TEST_REFERENCE=available\n")
    monkeypatch.setattr(
        "worker.executor.load",
        lambda name: {
            "external": {
                "command": [sys.executable, str(script)],
                "working_directory": str(
                    tmp_path if directory_exists else tmp_path / "missing"
                ),
            }
        },
    )
    report, _ = asyncio.run(
        execute(
            {
                "script": "external",
                "timeout_seconds": 5,
                "parameters": {"working_directory": "/ignored-task-override"},
            }
        )
    )
    if directory_exists:
        assert report["status"] == "SUCCESS"
        assert report["summary"] == "Configuration loaded"
    else:
        assert report["status"] == "FAILURE"
        assert report["details"]["reason"] == "COMMAND_CONFIG"
        assert "working_directory" in report["error"]


def test_command_arguments_preserve_target_and_do_not_execute_parameter_text():
    task = {
        "host": "host-01",
        "period_start": "2026-10-29T06:00:00Z",
        "period_end": "2026-10-29T07:00:00Z",
        "parameters": {
            "scope": "netbackup",
            "type": "jobs; $(touch /tmp/no)",
            "debug": True,
            "dry_run": False,
            "asset": "other-host",
            "command": "evil",
        },
    }
    argv = build_command(DEFINITION, task)
    assert argv == [
        "backup-collector",
        "collect",
        "--asset=host-01",
        "--start-date=2026-10-29T06:00:00Z",
        "--end-date=2026-10-29T07:00:00Z",
        "--scope=netbackup",
        "--type=jobs; $(touch /tmp/no)",
        "--debug",
    ]
    task["parameters"].pop("type")
    with pytest.raises(ValueError, match="Missing command parameter: type"):
        build_command(DEFINITION, task)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("success", "SUCCESS"),
        ("failure", "FAILURE"),
        ("contradiction", "FAILURE"),
        ("invalid", "UNKNOWN"),
        ("timeout", "TIMEOUT"),
    ],
)
def test_command_execution_reports_logs_and_task_metadata(
    tmp_path, monkeypatch, mode, expected
):
    script = tmp_path / "external_collector.py"
    script.write_text("""import json,sys,time
print("Collection progress",file=sys.stderr)
mode=sys.argv[1]
if mode=="timeout":time.sleep(10)
if mode=="invalid":print("not json");sys.exit(0)
print(json.dumps({"status":"FAILURE" if mode=="failure" else "SUCCESS","summary":"Collected", "records_collected":12,"records_sent":12}))
sys.exit(2 if mode in ("failure","contradiction") else 0)
""")
    monkeypatch.setattr(
        "worker.executor.load",
        lambda name: {"external": {"command": [sys.executable, str(script), mode]}},
    )
    task = {
        "script": "external",
        "timeout_seconds": 1,
        "parameters": {},
        "host": "host-01",
        "period_start": "2026-10-29T06:00:00Z",
        "period_end": "2026-10-29T07:00:00Z",
        "attempt_number": 2,
        "task_id": "attempt-02",
    }
    report, logs = asyncio.run(execute(task))
    assert report["status"] == expected
    assert report["host"] == "host-01" and report["attempt_number"] == 2
    assert (
        report["period_start"] == task["period_start"]
        and report["period_end"] == task["period_end"]
    )
    assert report["started_at"] and report["finished_at"] and report["collected_at"]
    assert "Collection progress" in logs
    if mode == "failure":
        assert (
            report["summary"] == "Collected"
        )  # Keep valid failure reports on nonzero exit.


@pytest.mark.parametrize(
    "status,expected",
    [
        ("success", "SUCCESS"),
        ("failure", "FAILURE"),
        ("unknown", "UNKNOWN"),
        ("timeout", "TIMEOUT"),
    ],
)
def test_backup_collector_native_report(tmp_path, monkeypatch, status, expected):
    import json

    payload = {
        "schema_version": "1.0",
        "collection_id": "external-01",
        "status": status,
        "asset_id": "external-host",
        "region": "EMEA",
        "technology": "netbackup",
        "resource": "jobs",
        "collection": {
            "status": "failure",
            "collected": 2906,
            "invalid": 0,
            "excluded": 0,
        },
        "storage": {
            "status": "failure",
            "destination": "console",
            "created": 0,
            "updated": 0,
            "displayed": 2906,
        },
        "performance": {"cpu_user_seconds": 3.1},
        "duration_seconds": 17.454,
        "errors": [{"code": "EXAMPLE", "message": "Diagnostic"}],
    }
    script = tmp_path / "native.py"
    script.write_text(
        "import sys\nprint("
        + repr(json.dumps(payload))
        + ')\nprint("native log",file=sys.stderr)\n'
    )
    monkeypatch.setattr(
        "worker.executor.load",
        lambda name: {
            "external": {
                "command": [sys.executable, str(script)],
                "report_format": "backup_collector",
            }
        },
    )
    result, logs = asyncio.run(
        execute(
            {
                "script": "external",
                "timeout_seconds": 3,
                "host": "assigned-host",
                "task_id": "assigned-task",
                "attempt_number": 2,
                "period_start": "2026-10-29T06:00:00Z",
                "period_end": "2026-10-29T07:00:00Z",
            }
        )
    )
    # Only the external final status decides the business outcome, never nested fields.
    assert result["status"] == expected
    assert result["details"]["backup_collector"] == payload
    assert result["host"] == "assigned-host" and result["task_id"] == "assigned-task"
    assert result["records_collected"] == 2906 and result["records_sent"] == 0
    assert result["duration_seconds"] != 17.454
    assert "native log" in logs


def test_backup_collector_invalid_status_is_rejected():
    from worker.reports import backup_collector_report

    for payload in ({}, {"status": "partial"}, [], {"status": True}):
        with pytest.raises(ValueError):
            backup_collector_report(payload)


@pytest.mark.parametrize("include_period", [True, False])
def test_shared_alias_can_include_or_omit_dates_without_overriding_task_context(
    include_period,
):
    from orchestrator.config import load

    definition = load("worker_scripts.yaml")["backup_collector"]
    task = {
        "host": "host-01",
        "period_start": "2026-10-29T06:00:00Z",
        "period_end": "2026-10-29T07:00:00Z",
        "parameters": {
            "scope": "netbackup",
            "type": "jobs" if include_period else "policies",
            "include_period": include_period,
            "period_start": "ignored",
            "asset": "ignored",
        },
    }
    argv = build_command(definition, task)
    assert "--asset=host-01" in argv and "ignored" not in " ".join(argv)
    dates = [arg for arg in argv if arg.startswith(("--start-date=", "--end-date="))]
    assert dates == (
        ["--start-date=2026-10-29T06:00:00Z", "--end-date=2026-10-29T07:00:00Z"]
        if include_period
        else []
    )
    assert not any("include_period" in arg for arg in argv)
    task["parameters"].pop("include_period")
    assert "--start-date=2026-10-29T06:00:00Z" in build_command(definition, task)
    task.pop("period_end")
    with pytest.raises(ValueError, match="Missing task field: period_end"):
        build_command(definition, task)


@pytest.mark.parametrize("condition", ["false", 0, None, [], {}])
def test_period_condition_requires_a_json_boolean(condition):
    from orchestrator.config import load

    definition = load("worker_scripts.yaml")["backup_collector"]
    with pytest.raises(ValueError, match="include_period must be true or false"):
        build_command(
            definition,
            {
                "host": "host-01",
                "parameters": {
                    "scope": "netbackup",
                    "type": "policies",
                    "include_period": condition,
                },
            },
        )


@pytest.mark.parametrize("kind", ["jobs", "policies"])
def test_shared_command_executes_periodic_and_snapshot_collectors(
    tmp_path, monkeypatch, kind
):
    from orchestrator.config import load

    script = tmp_path / "collector.py"
    script.write_text("""import argparse, json
parser = argparse.ArgumentParser()
for field in ("asset", "scope", "type", "start-date", "end-date"):
    parser.add_argument("--" + field)
args = parser.parse_args()
assert args.asset == "host-01"
if args.type == "policies":
    assert args.start_date is None and args.end_date is None
else:
    assert args.start_date == "2026-10-29T06:00:00Z"
    assert args.end_date == "2026-10-29T07:00:00Z"
print(json.dumps({"status":"SUCCESS", "summary":args.type + " collected"}))
""")
    definition = load("worker_scripts.yaml")["backup_collector"]
    definition.update(command=[sys.executable, str(script)], report_format="standard")
    monkeypatch.setattr(
        "worker.executor.load", lambda _: {"backup_collector": definition}
    )
    report, _ = asyncio.run(
        execute(
            {
                "script": "backup_collector",
                "timeout_seconds": 5,
                "host": "host-01",
                "period_start": "2026-10-29T06:00:00Z",
                "period_end": "2026-10-29T07:00:00Z",
                "parameters": {
                    "scope": "netbackup",
                    "type": kind,
                    "include_period": kind == "jobs",
                },
            }
        )
    )
    assert report["status"] == "SUCCESS" and report["summary"] == kind + " collected"
    assert report["period_start"] == "2026-10-29T06:00:00Z"
