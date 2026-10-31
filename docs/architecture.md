# Tasker — managed architecture

The master runs FastAPI, the Jinja dashboard, managed domain services and one embedded scheduler. Workers use outbound HTTP and execute trusted Python collectors or allowlisted commands in subprocesses. Inventory discovery reads JSON/YAML exports; scripts/sync_inventory.py optionally refreshes exports from an API.

Configuration has two layers:

- `.env` on the master, `.env.worker` on satellites: service runtime, database URL/bind address or worker identity/master URL, credentials and timeouts.
- SQLite: zones, provisioned satellites, inventory sources/previews, hosts, host groups, collector definitions, targeting schedules, windows and attempts.

Normal startup migrates storage and preserves managed state. It does not load object configuration from YAML. Existing installations derive missing zone records from existing assignments; fresh installations remain empty. Managed objects are configured through the dashboard/API.

## Modules

- `tasker.py`: shared foreground/systemd entry point; `install.sh` installs the chosen unit and runtime requirements.
- `orchestrator/main.py`: application assembly, health endpoint and scheduler lifecycle.
- `orchestrator/catalog_api.py`: collector definitions, host/group listings and worker status.
- `orchestrator/schedules_api.py`: recurring schedules, target resolution and group-run requests.
- `orchestrator/execution_api.py`: collection history, retries, worker leases and reports.
- `orchestrator/pages.py`: HTML route dispatch; `management_views.py` renders configuration/inventory/overview; `history_view.py` builds collection health and run history.
- `orchestrator/auth_pages.py`, `security.py`, `middleware.py`: sign-in, authorization and workspace isolation.
- `orchestrator/navigation.py`, `filters.py`, `web.py`: saved browser state, validated query filters and shared templates.
- `orchestrator/inventory_api.py`: zone/satellite administration, source discovery, preview application and host/group lifecycle endpoints.
- `orchestrator/inventory_service.py`: provider normalization, diff generation, ownership, optimistic concurrency and atomic apply.
- `orchestrator/service.py`: eligibility, windows, claims, retries, immutable execution contexts and group/zone targeting.
- `orchestrator/models.py` and `db.py`: persisted domain and idempotent upgrades.
- `worker/`: heartbeat/claim loop, lease renewal and script execution.
- `inventory/hosts.json`: static or API-generated hosts; `scripts/sync_inventory.py` refreshes, previews and optionally applies them.

## Inventory lifecycle

Provider → normalize version-1 payload → validate IDs/zones/assignments → store preview with baseline fingerprint → operator applies → new hosts enter Discovered/disabled → operator activates → hosts become execution targets.

Apply is transactional and idempotent. A changed baseline rejects stale previews. A source cannot overwrite a host owned by another source. Activation and soft-deletion state are master-owned. Missing hosts are reported without deletion. Deleted records remain available to historical windows and cannot be restored by a sync.

## Execution domain

A collector is a reusable script, scope and defaults. A schedule selects the union of one or more groups within its zone, an interval or a daily worker-local time, and parameter overrides. A host may belong to many groups, but a collector/host pair may have only one enabled recurring schedule. Group overlap within one schedule is deduplicated. Scope and zone/worker authorization are always enforced.

Workers authenticate using unique provisioned tokens; hashes are stored on the master and raw values are returned only once. Legacy shared-token compatibility remains available. Worker IDs and concurrency come from their `.env`; no inventory YAML is required on a satellite.

Windows remain unique by host/collector/UTC start. Attempts carry their own UUID, requested period, execution context and result. Result delivery validates ownership, lease, timeout and period; identical repeated delivery is idempotent. Retries preserve requested periods and use current execution parameters with a new attempt snapshot.

SQLite WAL, `BEGIN IMMEDIATE` and uniqueness constraints protect concurrent claims/retries. This remains a single-master, single-uvicorn-process V0. Named users have zone-specific admin/operator/viewer roles. This V0 does not implement distributed scheduler coordination or external alert delivery. Remote deployment needs HTTPS and network access control. Collector subprocesses are timeout isolation, not a hostile-code sandbox.
