# Tasker

A generic collector orchestrator: FastAPI master, HTTP workers, host inventory, schedules and execution history. A fresh installation contains no configured objects or synthetic history. Only the `fake` collector returns simulated results when explicitly configured and executed.

## Install and start

The Red Hat layout keeps the project and runtime data under `/data/tasker`; the SQLite database is `/data/tasker/var/orchestrator.db`, not a file under the system `/var`. For Red Hat master/worker installation, the backup-collector integration and systemd examples, see [the Red Hat installation guide](docs/installation-redhat.md).

Python 3.11 or 3.12 is required.

```bash
PYTHON=python3.12 ./scripts/install_master.sh
./scripts/start_master.sh
```

For local macOS development:

```bash
PYTHON=python3.12 ./scripts/install_mac.sh
./scripts/start_local.sh
./scripts/stop_local.sh
```

Local startup launches only the master. Neither installation nor startup imports configuration or creates workers. The master uses one uvicorn process and SQLite; do not run multiple master processes against the same database.

Sign in using `AUTH_USERNAME` and `AUTH_PASSWORD` in the master `.env`. Installation generates random secrets. Restart after changing credentials; existing browser sessions become invalid. `ADMIN_TOKEN` is the administrator API bearer token. Keep `.env` private and use HTTPS for remote access.

On each satellite:

```bash
PYTHON=python3.12 ./scripts/install_worker.sh
# Configure MASTER_URL, WORKER_ID and WORKER_TOKEN in .env.worker.
./scripts/start_worker.sh
```

Create its zone and scope, then register the satellite in Configuration → Satellites. Copy the one-time token into the worker's `.env.worker`. A satellite belongs to exactly one zone, uses outbound HTTP and needs no inventory file or master credentials.

The master reads `.env`; `start_worker.sh` reads `.env.worker` independently, including when both run on the same Mac. Pass another file as the first argument to start another worker: `./scripts/start_worker.sh /path/to/another-worker.env`. Worker secret files must not be committed.

## Set up an empty workspace

1. **Configuration → Zones / Scopes:** create your locations and collector categories.
2. **Configuration → Satellites:** register workers, scopes, concurrency and zone. Start the workers.
3. **Inventory → Sources & discovery:** read your host file, review differences and apply them.
4. **Inventory → Hosts:** activate discovered hosts. Missing hosts are reported; they are never deleted automatically.
5. **Inventory → Host groups:** organize hosts into reusable target sets. Groups select members but never modify their activation state. Select one or more rows in Inventory → Hosts and use Activate selected / Disable selected. The header checkbox selects visible, non-deleted hosts; changing filters clears hidden selections. Activation validates routing and schedule compatibility atomically.
6. **Configuration → Collector settings:** create a collector; select `fake` to test without contacting equipment.
7. **Schedules:** use Add schedule, choose the collector, groups, parameters and frequency in the selected zone. Daily `07:00` uses each target worker's local timezone.
8. **Collectors / Runs & history:** inspect daily health in Collectors, then open History for host/hour/status details. Retry failures or rerun successful periods without losing attempts.

Overview summarizes collectors, workers and inventory in the current zone workspace. Scope tabs are generated from configured objects.

## Inventory

Copy `inventory/hosts.example.json` to the ignored local file `inventory/hosts.json`, then populate it. The expected format is:

```json
{"version": 1, "hosts": []}
```

Add hosts with this format (the referenced zone must already exist):

```json
{"id":"host-01","zone":"EMEA","scope":"NetBackup","parameters":{}}
```

Optional `preferred_worker` and `fallback_worker` must match the host's zone and scope. Without a preferred worker, any eligible worker in the zone can execute its task. JSON and YAML use the same version-1 envelope. Files are limited to 1 MB and 10,000 unique hosts.

```bash
# Discover changes from a static file, then review in the dashboard.
.venv/bin/python -m scripts.sync_inventory --zone EMEA
# Discover and apply; new hosts remain disabled until activated.
.venv/bin/python -m scripts.sync_inventory --apply
# Use YAML instead.
.venv/bin/python -m scripts.sync_inventory --file inventory/hosts.yaml
# After implementing collect_hosts(): refresh from your API, then discover.
.venv/bin/python -m scripts.sync_inventory --refresh
```

Only adapt `collect_hosts()` in `scripts/sync_inventory.py` for your API. It intentionally contains no provider implementation. Return a list of host dictionaries; store API credentials in `.env`. A failed refresh preserves the previous file. Static lists need no Python changes. The dashboard reads files and never executes provider code.

Discover creates a reviewable preview of new, changed, unchanged, missing and archived hosts. Apply is atomic and rejects stale previews. Existing activation states are preserved; archived hosts are not automatically restored. The script reuses or creates a file source and prints a change summary. The dashboard also offers Discover and Apply. External tools can push the same envelope to `POST /api/inventory/discover` using the admin bearer token and an `X-Tasker-Zone` header.

## Configuration and storage

The database is the only source of truth for zones, scopes, satellites, hosts, host groups, collectors, schedules and inventory sources. Manage these objects through the dashboard or authenticated API. There are no declarative object YAML files or configuration import command.

Only three external inputs remain:

- `.env`: master/worker runtime settings and credentials.
- `inventory/hosts.json` (or YAML): host discovery input; preview/apply synchronizes it into the database while preserving managed activation and history.
- `config/worker_scripts.yaml`: allowed collector aliases mapped to installed Python files or fixed command definitions. It includes the `fake` Python script and a `backup_collector` CLI definition; install that CLI separately on eligible workers. Install the same script aliases/code on the workers that execute them. This file authorizes executable code; the dashboard cannot change it.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `orchestrator/` | FastAPI routes, authentication, domain services, persistence and scheduling |
| `orchestrator/inventory_service.py` | Read host files, preview differences and apply changes |
| `orchestrator/templates/`, `orchestrator/static/` | Dashboard interface |
| `worker/` | Poll tasks, execute allowlisted scripts and report results |
| `collectors/fake.py` | Only retained fake implementation: success, failure, timeout and sample data |
| `inventory/` | Static or externally regenerated host export |
| `config/` | Collector script allowlist only |
| `scripts/` | Installation, lifecycle, inventory sync and audits |
| `tests/fixtures/` | Isolated test data; never imported by normal installation/startup |
| `var/` | Local database, logs and backups; ignored by Git |

Collectors either implement `collect(context) -> dict` or run as an allowlisted command that writes its report JSON to stdout and logs to stderr. The `backup_collector` command accepts its native JSON: Tasker trusts its final status and keeps collection/storage/destination diagnostics in Details without interpreting them. They send their business data directly to their destination. Tasker receives only a small execution report and captured logs:

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

Final report statuses are **SUCCESS**, **FAILURE**, **TIMEOUT** and **UNKNOWN**. Reports include the summary, collection timestamp, start/end dates, duration, attempt number, host/worker/collector identity, requested period, collected/sent counters, error and optional details. The worker/master supplies execution metadata; the collector handles data retrieval and delivery. Business datasets never belong in the report. See [the complete report format, statuses and logs](docs/scripts.md).

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.audit_state
```

Tests use isolated databases and test-only configuration. No seeding command is shipped in `scripts/`. This V0 runs one SQLite master scheduler. Named users have zone/role permissions; no external notification delivery is configured. Trusted collectors execute with the worker service account's permissions.

## Live dashboard

Overview, Collectors, Runs & history and attempt details use in-place navigation and background HTML updates. Live can be paused; Refresh now requests an update. Filters, hour/status selections and pagination keep browser history and scroll position. Updates pause while the tab is hidden, during input/selection, open logs/details or recent interactions. Filter controls survive background updates. Management pages do not poll; local satellite clocks continue ticking without network requests. Running tasks use a subtle CSS pulse, disabled when reduced motion is requested.

## Zone workspaces and daily monitoring

Select the zone using the workspace selector at the bottom of the sidebar. Overview, collectors, hosts, groups, schedules, sources, satellites and history are scoped to it. New objects inherit that zone. No zone means the first-zone setup screen. The last selected zone is remembered; each tab also carries its own workspace in links and fetch headers. Switching zones opens Overview and drops object/filter IDs from the previous zone.

Collectors, groups, schedules and sources have persisted zone ownership. A group cannot span zones; a schedule cannot target a collector/group in another zone. Scripts and scope categories are shared, while collector configuration is per zone. System bearer-token integrations may enumerate all zones; specify `X-Tasker-Zone: EMEA` for scoped reads/writes. Normal browser users cannot bypass zone filtering with an object ID. IDs for hosts/satellites remain globally unique; use qualified IDs. Collector names may repeat, and conflicting new collector IDs get a zone prefix.

Startup migrates old shared definitions into zone-owned copies when needed. Existing period and attempt IDs and report/log contents are preserved. A multi-zone inventory preview expires and must be discovered again; use one source/file per zone. Make a backup before upgrading.

Collectors owns the period summary counters (expected, successful, failed and running/pending), filtered by workspace, scope and selected date/period. History keeps the execution list and status filters without duplicating these summary cards. Collectors opens in Week by default. Week and Month include the same period overview and Previous/Next controls as History; Day has neither overview nor period navigation. Collectors shows one cell per collector/day (current week by default; Day and Month also available). Failure takes priority over missing/unknown, then active execution, then success. Future periods are planned, never missing. Recovered means a successful period had an earlier failed/unknown/timed-out attempt; the original attempts remain in History. Click a day to open History with the collector/date/workspace selected. History has an optional hour selector in Advanced filters, no 24-hour grid. Retry applies to failed, unknown, missing or timed-out periods; Run again applies to successful periods. Running/pending periods cannot be queued twice. Replays can repeat destination delivery: collectors must handle deduplication/idempotency.

Overview shows a live seven-day activity chart, the six latest execution attempts, and collector health based on the latest period per host. Click a chart day or execution to inspect history. Satellite management stays under Configuration. Collector settings uses a scope-based table with separate View/Edit detail pages and a dedicated Add collector form. Health cards show the last success and overdue hosts for enabled schedules. Last success is the collector's most recent successful attempt, not a guarantee that every host succeeded. Paused schedules are labelled separately. Tasker monitors script execution/delivery; a successful script can collect failed business/backup jobs. Those job results are analyzed at the collector's destination.

Schedules is a table with Add schedule and individual detail/edit pages. New intervals are 1, 2, 4, 6, 12 or 24 hours, or daily at a satellite-local time (including DST). Older intervals remain readable and unchanged.

## Accounts and zone permissions

The `.env` administrator and system API token retain global administration. Add named users in the database with a prompted password (scrypt hash, never plaintext):

```bash
.venv/bin/python -m scripts.user alice --role operator --zones EMEA
.venv/bin/python -m scripts.user bob --role viewer --zones APAC,AMER
# Disable an account and invalidate its sessions.
.venv/bin/python -m scripts.user alice --zones EMEA --disable
```

Viewer: read allowed zones. Operator: read plus plan, retry and run collectors in allowed zones. Admin: configure objects in allowed zones. Only the global administrator manages the shared zone/scope registry. Sessions expire after 12 hours; disabling a user or changing its credentials/permissions invalidates its sessions. Configure HTTPS at your reverse proxy before exposing the master; session cookies become Secure over HTTPS. Keep the master listener private and store `.env` and backups with restricted filesystem permissions.

## Backup, history and operational checks

SQLite reports and attempt metadata are retained; no automatic history purge is enabled. Zone/period and attempt indexes support date-based lookup. Detailed logs can be cleared separately; take a backup first. History queries are date-bounded and full attempt reports/logs are loaded only for visible rows. Very large month views still aggregate period metadata in the single master process: validate expected production volume before rollout.

```bash
# Online snapshot plus restore into a temporary database and integrity check.
.venv/bin/python -m scripts.backup --check-restore
# Read-only consistency audit.
.venv/bin/python -m scripts.audit_state
# Preview old log cleanup; reports and period/attempt metadata are preserved.
.venv/bin/python -m scripts.retention --logs-days 30
# Explicitly apply the same cleanup after reviewing its count and backup.
.venv/bin/python -m scripts.retention --logs-days 30 --apply
```

Backups are mode 0600 and include configuration, account hashes and history. Restore requires stopping the master and restoring a verified snapshot to DATABASE_URL; do not replace a running SQLite database. Restore drills never overwrite the master. Set up recurring backups and external monitoring of `/health` in your deployment environment. Satellite availability, failed/missing collections and overdue hosts are visible in Overview; external email/chat notifications need a separately configured destination.
