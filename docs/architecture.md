# Backup Orchestrator — managed architecture

The master runs FastAPI, the Jinja dashboard, managed domain services and one embedded scheduler. Workers use outbound HTTP and execute trusted Python collectors or allowlisted commands in subprocesses. Inventory discovery reads JSON/YAML exports; scripts/sync_inventory.py optionally refreshes exports through the adaptable per-zone integration in services/referential.py.

Configuration has two layers:

- `.env` on the master, `.env.worker` on satellites: service runtime, database URL/bind address or worker identity/master URL, credentials and timeouts.
- SQLite or PostgreSQL, selected by `DATABASE_URL`: zones, provisioned satellites, inventory sources/previews, hosts, host groups, collector definitions, targeting schedules, windows and attempts.

Normal startup migrates storage and preserves managed state. It does not load object configuration from YAML. Existing installations derive missing zone records from existing assignments; fresh installations remain empty. Managed objects are configured through the dashboard/API.

## Modules

- `run.py`: shared foreground/systemd entry point; `install.sh` installs the chosen unit and runtime requirements.
- `orchestrator/main.py`: application assembly, health endpoint and scheduler lifecycle.
- `orchestrator/catalog_api.py`: collector definitions, host/group listings and worker status.
- `orchestrator/schedules_api.py`: recurring schedules, target resolution and group-run requests.
- `orchestrator/execution_api.py`: collection history, retries, worker leases and reports.
- `orchestrator/pages.py`: HTML route dispatch; `management_views.py` renders configuration/inventory/overview; `history_view.py` builds collection health and run history.
- `orchestrator/auth_pages.py`, `security.py`, `middleware.py`: sign-in, authorization and workspace isolation.
- `orchestrator/navigation.py`, `filters.py`, `web.py`: saved browser state, validated query filters and shared templates.
- `orchestrator/inventory_api.py`: zone/satellite administration, source discovery, preview application and host/group lifecycle endpoints.
- `orchestrator/inventory_service.py`: provider normalization, diff generation, ownership, optimistic concurrency and atomic apply.
- `orchestrator/service.py`: collection windows, scheduler ticks, claims, retries and immutable execution contexts. Existing callers keep the same entry points.
- `orchestrator/targeting.py`: zone/scope eligibility, host-group target resolution and worker-clock selection.
- `orchestrator/collection_history.py`: detailed execution reports; loads only hosts, collectors and attempts matching the requested collections.
- `orchestrator/history_queries.py`: shared SQL filters, a session-scoped `HistoryStore`, the `HistoryPage` result model and shared execution labels. Lists and summaries exclude attempt report/log bodies; retries reuse the same filters without loading reports.
- `orchestrator/records.py`: shared UTC timestamps and database-record serialization.
- `orchestrator/models.py` and `db.py`: persisted domain and idempotent upgrades.
- `worker/agent.py`: heartbeat/claim loop, lease renewal and result acknowledgement by the master.
- `worker/launch.py`: validate a local script definition and prepare its command, working directory and report format.
- `worker/commands.py`: map authorized task parameters into command arguments, without a shell.
- `worker/executor.py`: subprocess lifecycle, timeout, logs and execution metadata; `worker/reports.py` translates native collector reports.
- `inventory/hosts.json`: optional static inventory. Referential refresh generates `inventory/hosts-<zone>.json`, one complete file per enabled zone, then discovers and optionally applies each source using `scripts/sync_inventory.py`.

## Inventory lifecycle

Provider → normalize version-1 payload → validate IDs/zones/assignments → store preview with baseline fingerprint → operator applies → new hosts enter Discovered/disabled → operator activates → hosts become execution targets.

Apply is transactional and idempotent. A changed baseline rejects stale previews. A source cannot overwrite a host owned by another source. Activation and soft-deletion state are master-owned. Missing hosts are reported without deletion. Deleted records remain available to historical windows and cannot be restored by a sync.

## Execution domain

A collector is a reusable script, scope and defaults. A schedule selects the union of one or more groups within its zone, an interval or a daily worker-local time, and parameter overrides. A host may belong to many groups, but a collector/host pair may have only one enabled recurring schedule. Group overlap within one schedule is deduplicated. Scope and zone/worker authorization are always enforced.

Workers authenticate using unique provisioned tokens; hashes are stored on the master and raw values are returned only once. Legacy shared-token compatibility remains available. Worker IDs and concurrency come from their `.env`; no inventory YAML is required on a satellite.

Assignment is bounded by the satellite's provisioned capacity, its local `WORKER_CONCURRENCY`, and the master's global per-host `HOST_CONCURRENCY` (default 1). The host bound applies across collectors and satellites and is checked inside the atomic claim transaction. Queue reads are zone/scope-filtered and streamed in batches of 32. Each worker fills its free slots before waiting for its next poll. Recovery releases expired/offline attempts for retry, subject to the retry budget and backoff; eligible fallbacks preserve zone, scope and daily-local clock constraints.

Overview uses indexed latest-period lookups per host/collector and seven-day SQL aggregates by collector and day. `Collector.last_success_at` is updated transactionally when a success is accepted, including historical reruns. Adding this field to an existing database triggers a one-time backfill after workspace migration; subsequent page requests do not aggregate the full attempt history.

Windows remain unique by host/collector/UTC start. Attempts carry their own UUID, requested period, execution context and result. Result delivery validates ownership, lease, timeout and period; identical repeated delivery is idempotent. Retries preserve requested periods and use current execution parameters with a new attempt snapshot.

Collectors and Runs page requests use read sessions instead of `BEGIN IMMEDIATE`, and never call scheduling or lease recovery. Runs applies filters, count and pagination in SQL before building visible rows. Daily health groups persisted periods in SQL. Opening a collection restricts report loading to that collection ID. The scheduler owns periodic recovery; explicit planning owns historical backfills. An unrecorded day is not materialized merely by opening it.

The worker distinguishes the collector outcome from the master's acknowledgement. It retries transport failures and HTTP 5xx responses up to five sends, and logs completion only after an accepted response. Permanent HTTP errors are not retried. Delivery is not a durable queue: worker shutdown or exhausted retries can still leave an unacknowledged attempt for the master's lease recovery. Invalid local YAML, command mappings and working directories produce configuration-failure reports instead of unhandled launch errors.

SQLite uses WAL and `BEGIN IMMEDIATE`; PostgreSQL uses a transaction-scoped advisory writer lock. Both preserve atomic claims, retry budgets and worker capacity with the same uniqueness constraints. PostgreSQL stores JSON columns as JSONB, including array equality for workspace isolation and membership filters for host groups. Historical SQLite schema upgrades remain SQLite-specific. New databases initialize from the current models; the last-success column and index upgrade supports both backends. Selecting another database does not migrate existing records.

This remains a single-master, single-uvicorn-process V0 with either backend. Named users have zone-specific admin/operator/viewer roles. This V0 does not implement distributed scheduler coordination or external alert delivery. Remote deployment needs HTTPS and network access control. Collector subprocesses are timeout isolation, not a hostile-code sandbox.
