# Collector reports and execution

The master schedules and decides retries. The worker launches the script, enforces its timeout, captures logs and reports the attempt. The collector retrieves, transforms and delivers business data to Backup-Hub or another destination. Backup Orchestrator stores the execution report, not those datasets.

## Standard collector report

All collectors use this base object:

```json
{
  "status": "SUCCESS",
  "summary": "125 jobs collected and delivered",
  "collected_at": "2026-10-22T07:02:15Z",
  "records_collected": 125,
  "records_sent": 125,
  "details": {
    "collection": "Completed",
    "delivery": "Completed",
    "reference": "optional-external-reference"
  },
  "error": null
}
```

Return this object as the collector report. Python collectors implement `collect(context)` and return a dictionary (`None` for JSON null). Command collectors write exactly one JSON object to stdout and progress logs to stderr. Do not print debug messages or business datasets to stdout.

Only `status` and `summary` are required by validation. The timestamp, counters, details and error above are the recommended contract. Do not return `period_start`, `period_end`, task/worker/host identifiers, attempt number, execution start/end or duration: Backup Orchestrator already has or measures them. `collected_at` describes when data was collected, rather than which period it covers; if omitted it defaults to report validation time.

## Command collectors

`config/worker_scripts.yaml` supports both Python paths and worker-owned command definitions. The shipped `backup_collector` alias runs:

```yaml
backup_collector:
  command: [backup-collector, collect]
  report_format: backup_collector
  context_args:
    asset: host
    start-date: period_start
    end-date: period_end
  parameter_args:
    scope: scope
    type: type
    dry-run: dry_run
    debug: debug
  required_parameters: [scope, type]
```

Install the CLI in each eligible worker's environment or replace the executable with its absolute path. Register the same alias on the master so it appears in the collector script selector. Choose `backup_collector` in the dashboard and set collector or schedule parameters, for example:

```json
{"scope": "netbackup", "type": "jobs"}
```

An external collector can keep its own virtual environment and configuration. Set the optional `working_directory` on its command definition when it reads files relative to its project directory:

```yaml
backup_collector:
  command:
    - /data/backup-collectors-cli/venv/bin/python
    - /data/backup-collectors-cli/cli.py
    - collect
  working_directory: /data/backup-collectors-cli
  report_format: backup_collector
  # Keep the context_args, parameter_args and required_parameters above.
```

Without this setting, commands run from the worker project root. Relative working directories are resolved from that root. This setting is controlled only by the worker's local allowlist; task parameters cannot override it. The collector must still load its own `.env` (for example with `load_dotenv`); selecting a Python executable does not load it automatically. The service account needs access to the directory and its configuration. Interactive shell exports are not automatically available to a systemd service. Never print secret values when diagnosing missing configuration.

For a task on `nbu-emea-01`, the worker constructs argv equivalent to:

```bash
backup-collector collect --asset=nbu-emea-01 --start-date=2026-10-29T06:00:00Z --end-date=2026-10-29T07:00:00Z --scope=netbackup --type=jobs
```

The period comes from the assigned task, including historical retries. No manual date parameters are required. `scope` is explicitly configured for the external tool (for example lowercase `netbackup`), independently of the dashboard scope's display name.

`command` is a fixed executable/argument list. `context_args` maps option names to required task fields; `parameter_args` maps option names to merged parameter keys. Only locally mapped parameters become options. Optional `defaults` supplies parameter defaults, and `required_parameters` rejects missing values before launch. True adds a boolean flag, false/null omits it; strings/numbers become `--option=value`. Nested objects/lists are rejected. Values are passed directly as argv without a shell. Dashboard parameters cannot replace the executable or the task's host/period mappings. Keep credentials in the worker environment or the collector's own configuration, not command arguments.

By default, commands output the standard Backup Orchestrator report above (`report_format: standard`). The shipped `backup_collector` alias instead accepts the native backup-collector JSON (`report_format: backup_collector`), for example:

```json
{
  "schema_version": "1.0",
  "collection_id": "external-reference",
  "status": "success",
  "asset_id": "nbu-emea-01",
  "region": "EMEA",
  "technology": "netbackup",
  "resource": "jobs",
  "collection": {"status": "success", "collected": 2906, "invalid": 0, "excluded": 0},
  "storage": {"status": "success", "destination": "console", "created": 0, "updated": 0, "noop": 0, "displayed": 2906},
  "duration_seconds": 17.454,
  "performance": {"cpu_user_seconds": 3.1},
  "errors": []
}
```

Backup Orchestrator uses **only the top-level final status** for the business outcome. It accepts case-insensitive `success`, `failure` (also `failed`), `unknown` and `timeout`. The collector owns collection, delivery, create/update decisions and destination selection, including console/OpenSearch/other outputs. Backup Orchestrator neither checks nested statuses nor requires a delivery destination. It does not construct or configure the collector's output pipeline.

The original object is preserved under `details.backup_collector`, including extra diagnostics and future fields within the existing size limit. `summary` is optional; otherwise the worker generates a neutral summary from the declared status. `collection.collected` is copied into the generic collected counter (or explicit top-level `records_collected`). `records_sent` is copied only when explicitly reported; otherwise its generic display defaults to 0, which does not prove failed delivery. Backup Orchestrator never computes it from created/updated/noop/displayed. Native errors remain in the original report, visible in Details. Optional top-level `error` is a concise string; optional `collected_at` must include a timezone. If absent, the report timestamp is generated locally.

No period or Backup Orchestrator identity fields are required in the native response. Backup Orchestrator attaches the assigned period and identity. The collector's own duration stays in the original object; the worker measures execution duration separately. Python and other command collectors keep their existing Backup Orchestrator format.

Process health is still checked independently of the declared business outcome. Return exit code 0 for success. A valid failure report is retained even with a nonzero exit code; `SUCCESS` with a nonzero exit becomes `FAILURE`. Missing/malformed JSON with exit code 0 becomes `UNKNOWN`; a nonzero exit without a valid report becomes `FAILURE`. Exceeding the timeout kills the process group and produces `TIMEOUT`. The existing report/log size limits apply. Installations without `backup-collector` can still run the `fake` Python alias. Restart already-running workers after updating their Python code.

Python scripts remain supported: install them under `collectors/` and map an alias to their path, such as `fake: collectors/fake.py`.

`collected_at` includes a timezone. `records_collected` and `records_sent` are nonnegative counters; `error` is null on success or an error message. `details` is a small JSON object; collection/delivery/reference are suggested keys and may vary by collector. No business dataset belongs in the report.

The worker measures execution duration and fills missing report timestamps. The schema supplies defaults for omitted counters/details/error, but the complete object above is the recommended collector contract. Attempt identity, start/end, host, worker and requested period are execution metadata added by Backup Orchestrator; they are not additional fields the collector must generate. Progress messages printed by the script appear in the attempt logs.

## Report stored by Backup Orchestrator

```json
{
  "status": "SUCCESS",
  "summary": "125 jobs collected and delivered",
  "collected_at": "2026-10-22T07:00:12Z",
  "started_at": "2026-10-22T07:00:00Z",
  "finished_at": "2026-10-22T07:00:12Z",
  "duration_seconds": 12.0,
  "attempt_number": 2,
  "task_id": "assigned-attempt-id",
  "collector_id": "netbackup_jobs",
  "host": "host-01",
  "worker_id": "EMEA-SAT-1",
  "period_start": "2026-10-22T06:50:00Z",
  "period_end": "2026-10-22T07:00:00Z",
  "records_collected": 125,
  "records_sent": 125,
  "error": null,
  "details": {"stage": "delivery"}
}
```

The worker records script start/end in UTC and elapsed duration using a monotonic clock. The master binds host, worker, collector, attempt ID and attempt number to the assigned task: collector-supplied identities cannot replace them. Attempt numbers start at 1 and continue across automatic/manual retries of the same collection period. Each attempt keeps its own report and logs.

For master-detected failures without a worker report, the master supplies the available attempt dates and duration. `collected_at` is the report timestamp, separate from the requested data period. Report dates must include a timezone; finish must not precede start. Collection periods supplied by the worker must match the assigned task.

## Final report statuses

| Status | Meaning | Backup Orchestrator behavior |
| --- | --- | --- |
| `SUCCESS` | All required work, including delivery, is confirmed complete | Attempt succeeds; no retry |
| `FAILURE` | Collection, delivery or script execution failed | Attempt fails; retries according to the configured budget |
| `TIMEOUT` | Execution exceeded its timeout or the task lease expired | Timeout; retries according to the configured budget |
| `UNKNOWN` | Final outcome cannot be determined, for example an invalid/missing report or disconnected worker | Requires attention; follows the failure/retry policy, never treated as success |

Use these exact uppercase values. `OK` and `KO` are not accepted. A successful fetch does not mean `SUCCESS` when required delivery failed. Use `error` for the error message and `details` for small diagnostic fields such as stage, reason or external reference.

Backup Orchestrator scheduling states are distinct from the report: `PENDING`, `RUNNING`, `SCHEDULED`, `DISABLED` and `MISSING` are not final collector report statuses. Internally, unsuccessful attempts use `FAILED` (or `TIMEOUT`/`WORKER_OFFLINE`); the original report retains `FAILURE` or `UNKNOWN`. A collection period may return to `PENDING` while retrying.

A timeout or disconnection cannot prove that no data was delivered. Collectors must make repeated delivery safe, for example using destination-side deduplication keyed by host, collector and requested period.

## Details and logs

The attempt page shows the report status, summary, collection timestamp, execution dates, attempt number, duration, counters, error, expandable details, full JSON report and latest script logs. Failed-attempt logs open automatically.

`details` is a JSON object limited to 16 KB; the entire report is limited to 64 KB. Summary and error messages are limited to 2,000 characters. Extra top-level fields such as `data` are rejected. Do not return jobs, images or other business datasets to Backup Orchestrator.

For command collectors, use stderr for progress and reserve stdout for the JSON report. For Python `collect(context)` collectors, use `print()` or Python logging for progress. The runner captures collector stdout/stderr while reserving its own stdout for the report. The worker retains the latest approximately 16 KB; the API accepts at most 20,000 characters. Never log credentials, tokens or business datasets. Logs are retained in the database and visible to administrators; there is no automatic secret redaction.

## Context and fake collector

Context includes host, zone, scope, collector, assigned attempt number, worker ID, identifiers, requested UTC period and merged parameters. Parameter precedence is host → collector → schedule → explicit launch overrides. Retries preserve the requested period and may use updated configuration. Scripts execute as trusted subprocesses under the worker's account.

`collectors/fake.py` simulates reports without contacting equipment or a destination. Its `simulation` parameter supports `SUCCESS`, `FAILURE` (`FAILED` is also recognized for this simulation), `UNKNOWN`, `ERROR` and `TIMEOUT`. An uncaught exception produces `FAILURE`; malformed reports produce `UNKNOWN`.
