# Production pilot check — 2026-11-02

The checked working tree is ready for a controlled Red Hat pilot. This is a development-side validation on macOS/Python 3.12 and a temporary PostgreSQL 16 instance; it does not certify the actual Red Hat service, Avi route, NetBackup endpoints or destination system.

## Verified

- Full SQLite suite: 157 tests passed. PostgreSQL suite: 152 passed, excluding SQLite-only migration/backup tests and the SQLite HTTP process tests. A further worker configuration contract check passed after aligning the maximum concurrency to 32 across configuration, heartbeat validation and the dashboard.
- Runtime dependencies are consistent (`pip check`); Python lint/format checks, changed JavaScript syntax checks and shell syntax checks pass.
- Systemd units use non-root accounts, `venv`, and `/data/backup-orchestrator/master` or `/data/backup-orchestrator/worker`. Installer/unit checks pass; systemd was not executed on Red Hat here.
- Local database integrity and routing audit: no issues. SQLite backup and a restore drill passed without replacing the live database.
- Isolated HTTP tests execute real worker processes and fake collector subprocesses. Synthetic 10/50/100-host scenarios cover five collectors, per-host capacity, satellite loss, fallback and retries.
- Dashboard monthly search/pagination is checked against 10,000 report-bearing periods plus out-of-month periods. It reads only the requested page or SQL aggregates, without loading report/log bodies or planning tasks during page reads.
- Runs & history has linked filters and individual retry actions. UI bulk selection is removed; the administrator API retains explicit batch retry support for automation.

## Corrections made during this check

- Legacy `GET /api/collections` now returns at most 1,000 periods per response. Its JSON list format is preserved. Clients must use `limit`/`offset` and inspect `X-Total-Count` and `X-Has-More`; the default is 1,000 and smaller limits are recommended for report inspection.
- `GET /api/collections/summary` now computes complete filtered counts in SQL without loading report/log bodies. Legacy API expectation planning remains unchanged; dashboard page queries are separate and read-only.
- `WORKER_CONCURRENCY` is capped at 32 consistently. The previous configuration accepted values up to 64 which the master's heartbeat schema rejected.

## Before the Red Hat pilot

1. Publish the tested revision; the checked changes are currently local. Deploy the same revision to master and workers and install its `requirements.txt` into each role's `venv`.
2. Back up the production database and retain `.env`, `.env.worker`, inventory exports and the collector allowlist. Restart both services after deployment. Database startup includes additive indexes/last-success backfill for older schemas.
3. Check `/health` through the actual Avi URL, the worker's online state and local timezone, certificate validation, service-account permissions, and both local service logs. The production configuration has not been inspected from this Mac.
4. Execute the external backup collector as the worker account using its own environment/working directory, then run one approved host/period through the orchestrator. Check its report and destination data, then an intentional failure, timeout and individual retry before enabling broad schedules.
5. Expand progressively (10, then 50, then 100 hosts), measuring duration and backlog during the night peak and with one satellite unavailable. PostgreSQL is recommended for this scale; selecting it does not migrate existing SQLite data.

## Operating limits

Use one master process. Keep `HOST_CONCURRENCY=1` and start workers at concurrency 2. The worker's result transport retries transient errors but has no durable local report outbox; a prolonged interruption may require recovery and another collection attempt. Collectors must tolerate repeated delivery. The tests do not establish an exactly-once guarantee or a capacity benchmark for real backup systems.
