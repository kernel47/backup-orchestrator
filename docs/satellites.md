# Master and satellite installation

For a complete Red Hat setup with backup-collector and systemd examples, see [the installation guide](installation-redhat.md).

Use Python 3.11/3.12 and a copy of the repository on each machine. `.env` belongs to that machine; do not share the master admin/session secrets with workers.

## Master

```bash
PYTHON=python3.12 ./scripts/install_master.sh
./scripts/start_master.sh
```

Installation creates a venv and random secrets if `.env` does not exist. Existing `.env` and database contents are preserved. See `.env.master.example`: DATABASE_URL, MASTER_HOST, MASTER_PORT, AUTH_USERNAME, AUTH_PASSWORD, ADMIN_TOKEN, SESSION_SECRET and scheduler/lease settings. A fresh master has no demo hosts or collectors.

The launcher runs one uvicorn process in the foreground on `127.0.0.1:8000` by default. For a persistent Red Hat service, after installation/configuration run `sudo ./scripts/install_service.sh master --user tasker --start`. The account must exist and have access to the project. Stop any foreground master first. Use `systemctl status tasker-master` and `journalctl -u tasker-master` for diagnostics. Do not use multiple uvicorn workers or multiple masters on the same SQLite database.

## Satellite

On the master, configure zones, then provision a satellite under Configuration → Satellites. Choose its ID, one zone, supported scopes and capacity. Copy its one-time token.

On the satellite:

```bash
PYTHON=python3.12 ./scripts/install_worker.sh
# Edit .env.worker using .env.worker.example.
./scripts/start_worker.sh
```

Required: MASTER_URL, WORKER_ID, WORKER_TOKEN. Optional: WORKER_CONCURRENCY, POLL_SECONDS and LEASE_SECONDS. The master enforces capacity and authorization independently from local settings. Workers use outbound HTTP and need no listening port or local inventory YAML. Install the Python scripts or command executables and the config/worker_scripts.yaml aliases used on that worker. See docs/scripts.md for command arguments and native backup-collector reports.

For remote deployment, use HTTPS and appropriate service-account/network controls. Linux service deployment has not been validated by this change. Provisioned worker tokens are stored as hashes on the master.

## Worker-local clocks and daily execution

Workers report an IANA timezone with each heartbeat. `WORKER_TIMEZONE=Europe/Paris` can override detection; otherwise `TZ`, `/etc/localtime` and `/etc/timezone` are checked on the worker. If none provides a valid zone, startup fails with an explicit configuration message. The worker also sets `TZ` for its Python subprocesses. Never derive timezone from a broad zone such as EMEA or AMER.

Schedules support two timing modes:

- `interval`: an elapsed-time cadence (existing schedules retain their previous UTC boundaries).
- `daily_local`: `local_time=07:00` means one slot per day at 07:00 on each host's preferred authorized satellite. The master converts each local occurrence to UTC internally; its own local timezone is not used.

A preferred satellite's reported clock remains the reference while it is offline. Automatic fallback execution requires the same IANA timezone, so moving to a different clock cannot accidentally change the 07:00 slot. Manual retries retain the original period and planned timezone, and record the actual executing satellite's timezone separately. Configure workers and preferred routing before creating a daily-local schedule.

Spring-forward nonexistent times advance by the DST gap; fall-back ambiguous times use the first occurrence once. At 07:00, daily periods naturally contain 23 or 25 hours when the timezone changes offset. Retries reuse the exact UTC bounds instead of reconstructing a nominal 24-hour range. A newly created daily-local rule starts at its next occurrence; older missing periods require an explicit backfill. Offline workers or exhausted capacity may delay actual execution; planned and actual start times are shown separately.

Satellite cards show live local clocks and next planned execution. Overview focuses on collection activity and health. Schedule details show local/UTC next occurrences per assigned satellite. History/detail includes planned local time, actual local start and UTC collection periods. Date filters still refer to UTC collection-period start dates, not local execution dates.
