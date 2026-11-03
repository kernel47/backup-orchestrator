# Backup Orchestrator

A generic collector orchestrator: FastAPI master, HTTP workers, host inventory, schedules and execution history. A fresh installation contains no configured objects or synthetic history. Only the `fake` collector returns simulated results when explicitly configured and executed.

## Install and start

Python 3.11 or 3.12 is required. The standard Red Hat deployment consists of two supplied units, `systemd/backup-orchestrator-master.service` and `systemd/backup-orchestrator-worker.service`, and one installer: `install.sh`.

The standard checkout paths are `/data/backup-orchestrator/master` on the master and `/data/backup-orchestrator/worker` on each satellite. The installer renders the selected service using its actual checkout path.

Configure the machine before starting it:

```bash
cp .env.master.example .env        # Master machine: edit settings and replace placeholder secrets.
# On a satellite instead: cp .env.worker.example .env.worker
```

Using an existing service account with access to the checkout:

```bash
sudo ./install.sh master --user backup-orchestrator
# On each satellite: sudo ./install.sh worker --user backup-orchestrator
```

The installer reads `requirements.txt` and prepares runtime dependencies in `venv`, installs the selected unit and enables/restarts it. It never generates or overwrites your configuration. Set `PYTHON=python3.12` when needed, for example `sudo PYTHON=python3.12 ./install.sh master --user backup-orchestrator`.

Use normal service commands: `systemctl status backup-orchestrator-master`, `systemctl restart backup-orchestrator-master` and `journalctl -u backup-orchestrator-master`. The default master bind is `127.0.0.1:8000`. Workers use outbound HTTP and a provisioned individual token; each belongs to one zone. Copy neither the master database nor its credentials to a satellite. Use one master process with either database backend.

Services also write `var/logs/master.log` or `var/logs/worker.log` inside their checkout, while keeping console/systemd output. Files rotate at 10 MiB with five backups. Optional `LOG_DIRECTORY` (relative to the checkout or absolute) and `LOG_LEVEL=INFO` configure logging in `.env` / `.env.worker`; defaults work without editing existing configuration. Master HTTP logs include status and duration; requests taking at least one second are warnings. Successful fast health checks, static requests and worker polls are omitted. Query strings and request bodies are not logged by the HTTP logger. Worker logs include collector summaries and the captured stderr tail, while complete execution reports remain in the dashboard. Collectors must avoid printing credentials in their own logs.

For Mac development or foreground troubleshooting, use the same entry point as systemd:

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python run.py master
# In another terminal: venv/bin/python run.py worker
# Alternate configuration: venv/bin/python run.py worker --config /path/to/worker.env
```

Stop foreground processes with Ctrl+C. Do not launch a second master while the service is running. The master reads `.env`; the worker reads `.env.worker`. Existing machine secrets and databases are preserved. See [the Red Hat installation guide](docs/installation-redhat.md) for network access, backup-collector and pilot checks.

## Select the database

Set one `DATABASE_URL` in the master's `.env`, then restart the master:

```dotenv
# SQLite (default, local file)
DATABASE_URL=sqlite:///var/orchestrator.db
# Or PostgreSQL (database and login provisioned separately)
# DATABASE_URL=postgresql://orchestrator:password@db.example.internal:5432/backup_orchestrator
```

`postgresql://` automatically uses the Psycopg 3 driver included in `requirements.txt`; `postgresql+psycopg://` is also supported. Use URL-encoded credentials when they contain reserved characters. The master creates tables and indexes inside the selected database; it does not install PostgreSQL or create database users. Workers continue using HTTP and need no database credentials. Changing the URL does not transfer existing configuration/history: a new database starts empty, while the original SQLite file remains untouched. See [PostgreSQL setup and backup](docs/installation-redhat.md#postgresql-option).

## Set up an empty workspace

1. **Configuration → Zones / Scopes:** create your locations and collector categories.
2. **Configuration → Satellites:** register workers, scopes, concurrency and zone. Start the workers.
3. **Inventory → Sources & discovery:** read your host file, review differences and apply them.
4. **Inventory → Hosts:** activate discovered hosts. Missing hosts are reported; they are never deleted automatically.
5. **Inventory → Host groups:** organize hosts into reusable target sets. Groups select members but never modify their activation state. Select one or more rows in Inventory → Hosts and use Activate selected / Disable selected. The header checkbox selects visible, non-deleted hosts; changing filters clears hidden selections. Activation validates routing and schedule compatibility atomically.
6. **Configuration → Collector settings:** create a collector; select `fake` to test without contacting equipment.
7. **Schedules:** use Add schedule, choose the collector, groups, parameters and frequency in the selected zone. Daily `07:00` uses each target worker's local timezone.
8. **Collectors / Runs & history:** inspect daily health in Collectors, then open History for host/hour/status details. Retry failures or rerun successful periods without losing attempts.

Overview shows four cards: Satellites (online/offline), Collectors (enabled/paused), Scopes (with host/group counts), and the success rate for regular collections completed in the last 24 hours. Each collection period counts once, even after retries; the final state determines its outcome. Empty periods display no percentage. Needs attention appears only for offline satellites or collectors with failed, unknown, missing or overdue regular periods. New schedules do not imply missed collections before their creation.

Current activity shows all running/pending tasks, the oldest pending task's waiting age, and a separate running/pending count for historical backfills. Existing tasks without a queue timestamp use their creation time for that age. Collector health lists the latest regular period per target host, prioritizing collectors needing attention. Last success is the most recent successful execution, including manual runs. The two activity panels summarize regular collection periods by collector/day over the last seven days. Explicit backfills launched through Run a past collection are tagged and excluded from regular health and success/activity statistics; their retries keep the tag, and their full results remain in History. Older untagged history is treated as regular collection history. Scope tabs are generated from configured objects.

## Capacity and host protection

The master defaults to `HOST_CONCURRENCY=1`: across collectors and workers, at most one active leased attempt is assigned per host. This can be increased in the master's `.env` if the target supports parallel collectors. Each satellite also has its server-side capacity and `WORKER_CONCURRENCY` in `.env.worker`; the effective parallelism is bounded by both. Workers fill free slots within one polling cycle. Queue reads filter zone/scope in SQL and stream candidates in bounded batches.

For 100 hosts, hourly Jobs and four daily collectors represent 2,800 collection periods per day before retries. Start with PostgreSQL, two satellites per zone, two local slots per satellite and preferred/fallback hosts distributed between them. Size for the busiest zone and for the loss of one satellite, using measured collector durations. Keep daily-local fallback satellites on the required execution timezone. Stagger the daily collectors, for example 07:15, 07:30, 07:45 and 08:15 in Schedules. Run a real pilot on 10, then 50, then 100 approved hosts before expanding.

Overview looks up latest host/collector periods through an index; successful result delivery updates the collector's last-success timestamp instead of rescanning all attempt history on each refresh. Existing databases receive an additive column/index upgrade and a one-time success backfill at startup. Synthetic regression scenarios cover 10/50/100 hosts × five collectors, host-capacity races and an unavailable satellite. These validate orchestration invariants, not the runtime or API capacity of external backup systems.

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
venv/bin/python -m scripts.sync_inventory --zone EMEA
# Discover and apply; new hosts remain disabled until activated.
venv/bin/python -m scripts.sync_inventory --apply
# Use YAML instead.
venv/bin/python -m scripts.sync_inventory --file inventory/hosts.yaml
# After implementing collect_hosts(): refresh from your API, then discover.
venv/bin/python -m scripts.sync_inventory --refresh
```

Only adapt `collect_hosts()` in `scripts/sync_inventory.py` for your API. It intentionally contains no provider implementation. Return a list of host dictionaries; store API credentials in `.env`. A failed refresh preserves the previous file. Static lists need no Python changes. The dashboard reads files and never executes provider code.

Discover creates a reviewable preview of new, changed, unchanged, missing and archived hosts. Apply is atomic and rejects stale previews. Existing activation states are preserved; archived hosts are not automatically restored. The script reuses or creates a file source and prints a change summary. The dashboard also offers Discover and Apply. External tools can push the same envelope to `POST /api/inventory/discover` using the admin bearer token and an `X-Orchestrator-Zone` header.

Use the delete icon in Sources & discovery to remove a configured source after confirmation. Deletion preserves the inventory file, imported hosts, groups and history; pending previews from that source expire. Hosts are detached from the removed source so a new source can manage them. The administrator API is `DELETE /api/inventory/sources/{source_id}`.

## Configuration and storage

The database is the only source of truth for zones, scopes, satellites, hosts, host groups, collectors, schedules and inventory sources. Manage these objects through the dashboard or authenticated API. There are no declarative object YAML files or configuration import command.

Only three external inputs remain:

- `.env`: master/worker runtime settings and credentials.
- `inventory/hosts.json` (or YAML): host discovery input; preview/apply synchronizes it into the database while preserving managed activation and history.
- `config/worker_scripts.yaml`: allowed collector aliases mapped to installed Python files or fixed command definitions. It includes the `fake` Python script and a `backup_collector` CLI definition; install that CLI separately on eligible workers. Install the same script aliases/code on the workers that execute them. This file authorizes executable code; the dashboard cannot change it.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `run.py`, `install.sh`, `systemd/` | Service/foreground entry point, installer and service units |
| `requirements.txt`, `requirements-dev.txt` | Tested runtime dependencies and optional development tools |
| `orchestrator/main.py` | Application assembly, health and scheduler lifecycle |
| `orchestrator/*_api.py` | Configuration, inventory, scheduling and execution APIs |
| `orchestrator/pages.py`, `management_views.py`, `history_view.py` | HTML routing and focused page rendering |
| `orchestrator/middleware.py`, `navigation.py`, `security.py` | Workspace isolation, saved filters and access checks |
| `orchestrator/service.py`, `scheduling.py` | Collection windows, attempts and worker-local schedule calculations |
| `orchestrator/targeting.py`, `collection_history.py`, `records.py` | Target eligibility, history queries and shared record serialization |
| `orchestrator/inventory_service.py` | Read host files, preview differences and apply changes |
| `orchestrator/templates/`, `orchestrator/static/` | Dashboard interface |
| `worker/` | Poll tasks, validate local launch configuration, execute collectors and send acknowledged reports |
| `collectors/fake.py` | Only retained fake implementation: success, failure, timeout and sample data |
| `inventory/` | Static or externally regenerated host export |
| `config/` | Collector script allowlist only |
| `scripts/` | Inventory sync, backups, retention, user administration, audits and legacy schedule migration |
| `tests/fixtures/` | Isolated test data; never imported by normal installation/startup |
| `var/` | Local database, logs and backups; ignored by Git |

Collectors either implement `collect(context) -> dict` or run as an allowlisted command that writes its report JSON to stdout and logs to stderr. The `backup_collector` command accepts its native JSON: Backup Orchestrator trusts its final status and keeps collection/storage/destination diagnostics in Details without interpreting them. They send their business data directly to their destination. Backup Orchestrator receives only a small execution report and captured logs:

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
venv/bin/python -m pytest -q
venv/bin/python -m scripts.audit_state
```

Tests use isolated databases and test-only configuration. No seeding command is shipped in `scripts/`. This V0 runs one master scheduler with SQLite or PostgreSQL. Named users have zone/role permissions; no external notification delivery is configured. Trusted collectors execute with the worker service account's permissions.

## Live dashboard

Overview, Collectors, Runs & history and attempt details use in-place navigation and background HTML updates. Live can be paused; Refresh now requests an update. Filters, period/status selections and pagination keep browser history and scroll position. Updates pause while the tab is hidden, during input/selection, open logs/details or recent interactions. Filter controls survive background updates. Management pages do not poll; local satellite clocks continue ticking without network requests. Running tasks use a subtle CSS pulse, disabled when reduced motion is requested.

## Zone workspaces and daily monitoring

Select the zone using the workspace selector at the bottom of the sidebar. Overview, collectors, hosts, groups, schedules, sources, satellites and history are scoped to it. New objects inherit that zone. No zone means the first-zone setup screen. The last selected zone is remembered; clean browser paths include the workspace, for example `/w/EMEA/collectors`. Each tab also sends its workspace header for API calls. Switching zones opens Overview and drops object/filter IDs from the previous zone.

Collectors, groups, schedules and sources have persisted zone ownership. A group cannot span zones; a schedule cannot target a collector/group in another zone. Scripts and scope categories are shared, while collector configuration is per zone. System bearer-token integrations may enumerate all zones; specify `X-Orchestrator-Zone: EMEA` for scoped reads/writes. Normal browser users cannot bypass zone filtering with an object ID. IDs for hosts/satellites remain globally unique; use qualified IDs. Collector names may repeat, and conflicting new collector IDs get a zone prefix.

Startup migrates old shared definitions into zone-owned copies when needed. Existing period and attempt IDs and report/log contents are preserved. A multi-zone inventory preview expires and must be discovered again; use one source/file per zone. Make a backup before upgrading.

Collectors owns the period summary counters (expected, successful, failed and running/pending), filtered by workspace, scope and selected date/period. History keeps the execution list and status filters without duplicating these summary cards. Collectors opens in Week by default. Its compact filter row contains View, Collection date, Collector, Host group and Host, followed by Apply and Reset. Selecting a collector narrows the daily health grid without leaving Collectors; Reset restores the current week and clears host/group/collector selections while preserving the workspace and scope. Week and Month include the same period overview and Previous/Next controls as History; Day has neither overview nor period navigation. Collectors shows one cell per collector/day (current week by default; Day and Month also available). Failure takes priority over missing/unknown, then active execution, then success. Future periods are planned, never missing. Recovered means a successful period had an earlier failed/unknown/timed-out attempt; the original attempts remain in History. Click a day to open History with the collector/date/workspace selected. History groups View, Collection date, Scope, Collector, Host group and Host in one filter section with Apply and Reset. Scope limits collector choices and host groups limit host choices locally; incompatible selections are cleared. There is no hourly or advanced filter panel. Retries are individual: use the retry icon on an eligible row or open its details; the history table has no bulk selection. Retry applies to failed, unknown, missing or timed-out periods; Run again applies to successful periods. Running/pending periods cannot be queued twice. Replays can repeat destination delivery: collectors must handle deduplication/idempotency.

Overview shows satellite and collector totals, the all-time collection task count and success rate, seven-day activity by collector and by day, and collector health based on the latest period per host. Click a collector activity row or chart day to inspect history. Satellite management stays under Configuration. Collector settings uses a scope-based table with separate View/Edit detail pages and a dedicated Add collector form. Health cards show the last success and overdue hosts for enabled schedules. Last success is the collector's most recent successful attempt, not a guarantee that every host succeeded. Paused schedules are labelled separately. Backup Orchestrator monitors script execution/delivery; a successful script can collect failed business/backup jobs. Those job results are analyzed at the collector's destination.

Schedules is a table with Add schedule and individual detail/edit pages. New intervals are 1, 2, 4, 6, 12 or 24 hours, or daily at a satellite-local time (including DST). Older intervals remain readable and unchanged.

## Accounts and zone permissions

The `.env` administrator and system API token retain global administration. Add named users in the database with a prompted password (scrypt hash, never plaintext):

```bash
venv/bin/python -m scripts.user alice --role operator --zones EMEA
venv/bin/python -m scripts.user bob --role viewer --zones APAC,AMER
# Disable an account and invalidate its sessions.
venv/bin/python -m scripts.user alice --zones EMEA --disable
```

Viewer: read allowed zones. Operator: read plus plan, retry and run collectors in allowed zones. Admin: configure objects in allowed zones. Only the global administrator manages the shared zone/scope registry. Sessions expire after 12 hours; disabling a user or changing its credentials/permissions invalidates its sessions. Configure HTTPS at your reverse proxy before exposing the master; session cookies become Secure over HTTPS. Keep the master listener private and store `.env` and backups with restricted filesystem permissions.

## Backup, history and operational checks

Reports and attempt metadata are retained in the selected database; no automatic history purge is enabled. Zone/period and attempt indexes support date-based lookup. Detailed logs can be cleared separately; take a backup first. Collectors and Runs pages are read-only: pagination uses SQL count/limit/offset, daily/status summaries are grouped in the database, and lists do not load attempt report or log bodies. Full reports are loaded only when opening a specific collection. Browsing a date does not create missing periods or recover leases; the scheduler handles recovery, and explicit planning handles backfills. Existing recorded MISSING periods remain visible and retryable. Month summaries still scan matching metadata: validate expected production volume before rollout.

```bash
# Online snapshot plus restore into a temporary database and integrity check.
venv/bin/python -m scripts.backup --check-restore
# Read-only consistency audit.
venv/bin/python -m scripts.audit_state
# Preview old log cleanup; reports and period/attempt metadata are preserved.
venv/bin/python -m scripts.retention --logs-days 30
# Explicitly apply the same cleanup after reviewing its count and backup.
venv/bin/python -m scripts.retention --logs-days 30 --apply
```

The SQLite backup command creates mode-0600 snapshots including configuration, account hashes and history. For PostgreSQL use `pg_dump`/`pg_restore` and your database backup policy; `scripts.backup` intentionally rejects PostgreSQL. Stop the master before a restore; SQLite restore drills never overwrite the master. Set up recurring backups and external monitoring of `/health` in your deployment environment. Satellite availability, failed/missing collections and overdue hosts are visible in Overview; external email/chat notifications need a separately configured destination.

## In-app technical documentation

Documentation has two pages. **Live architecture** shows the selected workspace's actual registrations, collector and asset counts, plus worker heartbeat metadata. Its HTML is in `orchestrator/templates/live_architecture.html` and it uses the operational live controls. **Technical architecture** explains a theoretical vertical deployment with a master, three example zones and two satellites per zone, then worker processes, generic collectors and asset targets. Its static HTML is in `orchestrator/templates/technical_architecture.html`; it never creates example infrastructure or polls runtime state. Both pages are scope-independent and available before initial zone setup. Edit their templates and CSS to evolve the diagrams.

The worker registry combines master-side registration with safe metadata sent in authenticated heartbeats: hostname, OS, Python version, master endpoint origin, local concurrency and availability of allowlisted collector executables. The master never reads remote `.env` files or receives their credentials. Metadata is refreshed locally every minute and sent with heartbeats. Up/Down is based on heartbeat age; executable detection does not validate API credentials or target reachability. Older workers remain compatible and show metadata as not reported until updated and restarted.

## Browser navigation and saved filters

HTML pages have clean routes such as `/w/EMEA/collectors`, `/w/EMEA/runs` and `/w/EMEA/configuration/collectors/<id>/edit`. Filters and pagination are saved in HttpOnly, SameSite cookies per workspace and page. Browser history keeps prior selections internally so Back/Forward and live refresh work without adding filters to the address bar. Invalid or deleted saved filter values fall back to valid defaults. Object IDs remain in detail paths, and authorization is always enforced on the server. Old query-based HTML links and API filter parameters remain supported.

## Development checks

Runtime dependencies are pinned in `requirements.txt`; packaging reads the same file. Development tools are separate in `requirements-dev.txt`. Both services run the checkout directly and do not require an editable package installation. Keep migrations: they upgrade existing databases without discarding configuration or history.

```bash
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/ruff check .
venv/bin/ruff format --check .
venv/bin/python -m pytest -q
```

Python uses four-space indentation and separate statements/imports. JavaScript uses Prettier formatting; if Node is available, run `npx --yes prettier@3.6.2 --check "orchestrator/static/*.js"`. Node and the development tools are not required to run either service.

### API history pagination

`GET /api/collections` keeps its JSON list format and is limited to 1,000 collection periods per response. Pass `limit` (1–1,000) and `offset` to read subsequent pages; prefer `limit=25` when inspecting reports. Response headers `X-Total-Count`, `X-Limit`, `X-Offset` and `X-Has-More` describe pagination. Clients that previously assumed an unlimited response must follow these headers. `GET /api/collections/summary` computes full filtered counts in SQL without loading report/log bodies. Both legacy API reads retain their existing expectation-planning behavior when a date is supplied; the dashboard uses separate read-only queries. Individual report details remain available through `GET /api/collections/{id}`.

### Collect a past day

In **Collectors** or **Runs & history**, expand **Run a past collection**. Choose the collector, one host and a past date, then **Queue missing periods**. This also works for dates before the master or schedule was installed. The host must be active and targeted by an enabled schedule for that collector. The current script, parameters and schedule cadence apply: an hourly schedule queues up to 24 periods for the chosen day. The worker receives each historical `period_start` / `period_end`, so the external collector must support querying that range and the source must still retain the data.

Each request covers one host and one day, with a maximum of 500 periods. The date selects period starts in UTC; daily schedules preserve the satellite's local clock and timezone, including DST. Only completed missing periods are queued. Existing successes, failures and active tasks remain unchanged, and repeating the same request does not duplicate tasks. Retry existing failures individually in History. Tasks wait in the normal worker queue if a satellite is busy or offline. This action is available to administrators and operators; browsing dates remains read-only.
