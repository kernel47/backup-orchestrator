# Red Hat installation

Backup Orchestrator runs as a standard systemd service. The master checkout is under `/data/backup-orchestrator/master`; each satellite checkout is under `/data/backup-orchestrator/worker`. Each checkout has a manually configured environment file and its own Python environment. The Mac is used for development.

## 1. Prepare the machine and configuration

Requirements: Linux/systemd, Python 3.11 or 3.12 with pip/venv, Git, an approved Python package source, and an existing non-root service account. Do not replace the operating system Python.

Clone the same tested commit on the master and each satellite:

```bash
# On the master:
git clone git@github.com:kernel47/backup-orchestrator.git /data/backup-orchestrator/master
cd /data/backup-orchestrator/master
# On a satellite instead:
# git clone git@github.com:kernel47/backup-orchestrator.git /data/backup-orchestrator/worker
# cd /data/backup-orchestrator/worker
```

Use an approved source archive if SSH access is unavailable. When copying a Mac checkout, exclude `venv`, `.env`, `.env.worker`, `var/` and the local inventory export.

On a new machine, an administrator can create the service account if needed:

```bash
sudo useradd --system --no-create-home --home-dir /data/backup-orchestrator --shell /sbin/nologin backup-orchestrator
```

Copy **only the configuration for that machine's role**, then edit it:

```bash
cp .env.master.example .env         # Master
# OR, on a satellite:
cp .env.worker.example .env.worker  # Worker
```

Master `.env`: replace all `change-me` credentials/secrets before use. Retain the local listener by default:

```dotenv
MASTER_HOST=127.0.0.1
MASTER_PORT=8000
DATABASE_URL=sqlite:///var/orchestrator.db
```

Configure `AUTH_USERNAME`, `AUTH_PASSWORD`, `ADMIN_TOKEN` and `SESSION_SECRET` with your chosen credentials and long random secrets. There is no automatic credential generation or configuration rewrite.

### PostgreSQL option

SQLite remains the default. To use PostgreSQL, provision a database and login first (for example through your database administrator):

```sql
CREATE ROLE orchestrator LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_PASSWORD';
CREATE DATABASE backup_orchestrator OWNER orchestrator;
```

Set the master's `.env` to:

```dotenv
DATABASE_URL=postgresql://orchestrator:URL_ENCODED_PASSWORD@db.example.internal:5432/backup_orchestrator
```

The driver is included in `requirements.txt`; rerunning the installer updates dependencies for an existing deployment. `postgresql+psycopg://` is accepted as well. The configured login must be able to create tables/indexes in the application's schema. Restart the master after changing the URL. Workers keep using `MASTER_URL` and do not connect directly to PostgreSQL. For remote PostgreSQL use the server's required SSL settings, for example `?sslmode=verify-full&sslrootcert=/path/to/ca.pem`.

Selecting an empty PostgreSQL database creates an empty dashboard. It does not import configuration or history from SQLite, and it leaves the SQLite file unchanged. Preserve your existing database and plan a separate data migration if you need its contents. Continue running one master process: PostgreSQL support does not enable active-active schedulers.

For PostgreSQL backups use your DBA's backup process or the native tools. An example using libpq environment settings and a protected `.pgpass` file:

```bash
PGHOST=db.example.internal PGPORT=5432 PGUSER=orchestrator \
  pg_dump --format=custom --file=var/backups/backup-orchestrator.dump backup_orchestrator
# Inspect the archive; perform restore drills in a separate database.
pg_restore --list var/backups/backup-orchestrator.dump
```

Provision `var/backups` with restricted permissions before the dump. Stop the master before restoring its database. `scripts.backup` and its `--check-restore` option are SQLite-specific; the audit and retention commands support both backends.

Worker `.env.worker`: use the ID and one-time token provisioned in **Configuration → Satellites** on the master:

```dotenv
MASTER_URL=https://backup-orchestrator.example.internal
WORKER_VERIFY_SSL=true
WORKER_ID=EMEA-SAT-1
WORKER_TOKEN=REPLACE_WITH_THE_PROVISIONED_TOKEN
WORKER_CONCURRENCY=2
WORKER_TIMEZONE=Europe/Paris
```

A satellite belongs to one zone; the master controls its allowed scopes and capacity. Do not copy master secrets or its SQLite database to satellites. `WORKER_TIMEZONE` is an IANA timezone, not a broad region name.

The master defaults to `HOST_CONCURRENCY=1` (one active collection per target host across all satellites/collectors). The worker defaults to `WORKER_CONCURRENCY=2`; set its provisioned dashboard capacity consistently. These settings take effect after restarting the relevant service. For 100+ targets use PostgreSQL, distribute preferred/fallback assignments between satellites in each zone, and measure throughput with one satellite unavailable. Set daily-local collectors to staggered times to avoid a single daily burst. Do not enable multiple master processes to increase throughput.

Grant the service account access to its application files:

```bash
sudo chown -R backup-orchestrator:backup-orchestrator /data/backup-orchestrator/master
# On a satellite instead:
# sudo chown -R backup-orchestrator:backup-orchestrator /data/backup-orchestrator/worker
# Restrict the applicable configuration file:
sudo chmod 600 .env
# On a satellite instead: sudo chmod 600 .env.worker
```

## 2. Install and start the service

Only one installation command is needed on each machine:

```bash
cd /data/backup-orchestrator/master
sudo PYTHON=python3.12 ./install.sh master --user backup-orchestrator
# On each satellite instead:
# cd /data/backup-orchestrator/worker
# sudo PYTHON=python3.12 ./install.sh worker --user backup-orchestrator
```

The installer creates `venv` if needed, installs `requirements.txt` as the service account, installs the selected unit from `systemd/`, reloads systemd, and enables/restarts the service. It checks for the manually configured file and leaves it untouched. Python 3.11 can be selected instead.

If replacing a service installed under another name, stop and disable that service before installing this one to avoid running two masters. Keep the existing environment file and database. You can retain an existing service account by passing its name with `--user`.

The unit runs `venv/bin/python run.py master` or `venv/bin/python run.py worker`, with the project as its working directory. The master reads `.env`; the worker reads `.env.worker`. There are no separate configure/run/start wrapper scripts.

```bash
sudo systemctl status backup-orchestrator-master
sudo systemctl restart backup-orchestrator-master
sudo systemctl stop backup-orchestrator-master
sudo journalctl -u backup-orchestrator-master -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health
```

Use `backup-orchestrator-worker` on satellites. Use one master process with either database backend. Stop a foreground instance before starting the corresponding service.

### Local log files

The launcher writes logs and console output locally as well as to the systemd journal:

```text
/data/backup-orchestrator/master/var/logs/master.log
/data/backup-orchestrator/worker/var/logs/worker.log
```

No service unit changes are needed. The service account must be able to create/write the log directory. Optional `LOG_DIRECTORY=var/logs` and `LOG_LEVEL=INFO` can be added to the role's environment file, followed by a service restart. Each file rotates at 10 MiB and keeps five backups (`.1` through `.5`). Python logs, warnings, stdout/stderr and exception tracebacks are captured after runtime configuration is loaded. Master request logs include method, path, status and duration in milliseconds; requests taking at least one second are warnings. Successful fast worker polls and health checks are omitted to reduce noise. Worker logs also contain collector summaries and the captured collector stderr tail (up to 16 KB); report JSON remains in execution history. The collector must redact any secrets in its own output.

```bash
tail -f /data/backup-orchestrator/master/var/logs/master.log
# On a satellite:
tail -f /data/backup-orchestrator/worker/var/logs/worker.log
```

If a process is killed by the OS (for example an out-of-memory kill), it cannot write its own final traceback: check `journalctl -u backup-orchestrator-master` and OS logs as well.

### Update an existing service or move its checkout

The service names do not change. There is no need to uninstall or delete a service when changing its checkout path.

If the files already exist at the new path, update `WorkingDirectory` and both paths in `ExecStart` in `/etc/systemd/system/backup-orchestrator-master.service` or `backup-orchestrator-worker.service`, keeping the existing `User`. Then run `systemctl daemon-reload` and restart that service. Alternatively, rerun `install.sh` from the new checkout with the existing service account passed to `--user`; it replaces the unit and restarts the same service.

If the files still need moving, stop the corresponding service first and make a verified backup of the master database. Move the whole checkout, including `.env` or `.env.worker`, `var/`, local inventory exports and collector configuration. Recreate the checkout's `venv` at its new location rather than relying on a moved Python environment, then rerun the installer. Reinstall or reconfigure external collector environments if their paths also changed.

The relative database setting `sqlite:///var/orchestrator.db` follows the master checkout. Explicit absolute database URLs, inventory source paths stored in the dashboard, and absolute commands in `config/worker_scripts.yaml` must be updated if they point to the old location. Changing the checkout path does not require changing `MASTER_URL` when the master hostname and port stay the same.

## 3. Local storage and network access

```text
/data/backup-orchestrator/master/
├── .env                         # Master configuration
├── venv/
├── run.py                       # Common service/foreground entry point
├── install.sh                   # Common service installer
├── systemd/                     # Supplied master and worker units
├── config/worker_scripts.yaml
├── inventory/hosts.json          # Local inventory input
├── var/
│   ├── orchestrator.db          # Master SQLite configuration and history
│   └── backups/
└── tools/backup-collectors/      # Separately installed external tool
```

A satellite uses the same source layout under `/data/backup-orchestrator/worker`, with `.env.worker` in place of `.env` and no master database.

`var/` is inside the project, not the system `/var`. With the supplied working directory, `sqlite:///var/orchestrator.db` resolves to `/data/backup-orchestrator/master/var/orchestrator.db`. The equivalent absolute setting is `sqlite:////data/backup-orchestrator/master/var/orchestrator.db`.

Only the installed service definition is stored under `/etc/systemd/system`. Service output is captured in the normal journal and local log files. The checkout, Python environments, configuration and SQLite database stay under the corresponding `master/` or `worker/` checkout. With PostgreSQL, database storage is managed by the PostgreSQL server; the application directory does not control its data directory.

`127.0.0.1:8000` is local to the master host. A reverse proxy or Avi endpoint can expose it if the backend is reachable from that proxy. A remote Avi appliance cannot reach the master's loopback interface directly: bind to an appropriate private interface in `.env` and restrict access when using that deployment. Workers initiate outbound HTTP(S); they need no incoming worker port. For remote access use HTTPS and a valid certificate; keep SELinux and certificate validation enabled. Generated dashboard links use relative paths so filters, pagination and collector drilldowns keep the browser's public protocol and hostname when Avi terminates HTTPS and forwards HTTP to the master.

## 4. Add backup-collector

Install your approved external collector under `/data/backup-orchestrator/worker/tools/backup-collectors`, using its own environment. Backup Orchestrator does not distribute that application. If it is pip-installable:

```bash
cd /data/backup-orchestrator/worker/tools/backup-collectors
python3.12 -m venv venv
venv/bin/pip install .
```

Configure the external tool's NetBackup/API credentials and destinations using its own mechanism. It must run as the worker account from `/data/backup-orchestrator/worker`, without relying on a manually activated shell. Arbitrary keys in `.env.worker` are not automatically exported into external command environments.

On each eligible satellite, configure the already supplied alias in `config/worker_scripts.yaml`:

```yaml
backup_collector:
  command: [/data/backup-orchestrator/worker/tools/backup-collectors/venv/bin/backup-collector, collect]
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

Keep the same alias on the master for the collector selector. Adjust option names if your tool differs. The worker supplies host/period from the task and options from configured parameters, without a shell. Native JSON goes to stdout; logs go to stderr. The collector alone determines its final business status and delivery destination. See [the report contract](scripts.md).

Configure zones, satellites, inventory, host groups and collectors through the dashboard. The database owns these objects; configuration examples never create demo infrastructure. Copy `inventory/hosts.example.json` to the ignored `inventory/hosts.json` if a file source is needed.

For an initial Jobs pilot, select `backup_collector`, parameters `{"scope":"netbackup","type":"jobs"}`, timeout 300 seconds and no automatic retries. Test one approved host/period and inspect both the Backup Orchestrator report and destination data. Test a failure and repeat delivery before enabling schedules. Policies and Images use the types supported by your tool. Daily-local schedules use the satellite timezone.

## Foreground troubleshooting or Mac development

Prepare dependencies manually outside systemd:

```bash
cd /path/to/your/checkout
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python run.py master
# In another terminal: venv/bin/python run.py worker
# Another configuration: venv/bin/python run.py worker --config /path/to/worker.env
```

Configure `.env` / `.env.worker` first. Ctrl+C stops the foreground process. This is the same application entry point used by the two services.

Before updates, take a verified master backup with `venv/bin/python -m scripts.backup --check-restore`. Use a maintenance window, preserve all private configuration and `var/`, deploy the tested commit and rerun the single installer for the role. Red Hat startup and the external collector must be validated on the actual deployment machine; local Mac tests do not replace that pilot.
