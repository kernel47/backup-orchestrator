# Red Hat installation

Backup Orchestrator runs as a standard systemd service. Each machine has one checkout under `/data/backup-orchestrator/app`, one manually configured role-specific environment file, and its own Python environment. The Mac is used for development.

## 1. Prepare the machine and configuration

Requirements: Linux/systemd, Python 3.11 or 3.12 with pip/venv, Git, an approved Python package source, and an existing non-root service account. Do not replace the operating system Python.

Clone the same tested commit on the master and each satellite:

```bash
git clone git@github.com:kernel47/backup-orchestrator.git /data/backup-orchestrator/app
cd /data/backup-orchestrator/app
```

Use an approved source archive if SSH access is unavailable. When copying a Mac checkout, exclude `.venv`, `.env`, `.env.worker`, `var/` and the local inventory export.

On a new machine, an administrator can create the service account if needed:

```bash
sudo useradd --system --no-create-home --home-dir /data/backup-orchestrator/app --shell /sbin/nologin backup-orchestrator
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

Worker `.env.worker`: use the ID and one-time token provisioned in **Configuration → Satellites** on the master:

```dotenv
MASTER_URL=https://backup-orchestrator.example.internal
WORKER_ID=EMEA-SAT-1
WORKER_TOKEN=REPLACE_WITH_THE_PROVISIONED_TOKEN
WORKER_CONCURRENCY=2
WORKER_TIMEZONE=Europe/Paris
```

A satellite belongs to one zone; the master controls its allowed scopes and capacity. Do not copy master secrets or its SQLite database to satellites. `WORKER_TIMEZONE` is an IANA timezone, not a broad region name.

Grant the service account access to its application files:

```bash
sudo chown -R backup-orchestrator:backup-orchestrator /data/backup-orchestrator/app
# Restrict the applicable configuration file:
sudo chmod 600 .env
# On a satellite instead: sudo chmod 600 .env.worker
```

## 2. Install and start the service

Only one installation command is needed on each machine:

```bash
cd /data/backup-orchestrator/app
sudo PYTHON=python3.12 ./install.sh master --user backup-orchestrator
# On each satellite instead:
# sudo PYTHON=python3.12 ./install.sh worker --user backup-orchestrator
```

The installer creates `.venv` if needed, installs `requirements.txt` as the service account, installs the selected unit from `systemd/`, reloads systemd, and enables/restarts the service. It checks for the manually configured file and leaves it untouched. Python 3.11 can be selected instead.

If replacing a service installed under another name, stop and disable that service before installing this one to avoid running two masters. Keep the existing environment file and database. You can retain an existing service account by passing its name with `--user`.

The unit runs `.venv/bin/python run.py master` or `.venv/bin/python run.py worker`, with the project as its working directory. The master reads `.env`; the worker reads `.env.worker`. There are no separate configure/run/start wrapper scripts.

```bash
sudo systemctl status backup-orchestrator-master
sudo systemctl restart backup-orchestrator-master
sudo systemctl stop backup-orchestrator-master
sudo journalctl -u backup-orchestrator-master -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health
```

Use `backup-orchestrator-worker` on satellites. Use one master process with the SQLite scheduler. Stop a foreground instance before starting the corresponding service.

## 3. Local storage and network access

```text
/data/backup-orchestrator/app/
├── .env                         # Master configuration
├── .env.worker                  # Satellite configuration, on worker machines
├── .venv/
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

`var/` is inside the project, not the system `/var`. With the supplied working directory, `sqlite:///var/orchestrator.db` resolves to `/data/backup-orchestrator/app/var/orchestrator.db`. The equivalent absolute setting is `sqlite:////data/backup-orchestrator/app/var/orchestrator.db`.

Only the installed service definition is stored under `/etc/systemd/system`. Service output is captured in the normal journal. The checkout, Python environments, configuration and database stay under `/data/backup-orchestrator/app`.

`127.0.0.1:8000` is local to the master host. A reverse proxy or Avi endpoint can expose it if the backend is reachable from that proxy. A remote Avi appliance cannot reach the master's loopback interface directly: bind to an appropriate private interface in `.env` and restrict access when using that deployment. Workers initiate outbound HTTP(S); they need no incoming worker port. For remote access use HTTPS and a valid certificate; keep SELinux and certificate validation enabled.

## 4. Add backup-collector

Install your approved external collector under `/data/backup-orchestrator/app/tools/backup-collectors`, using its own environment. Backup Orchestrator does not distribute that application. If it is pip-installable:

```bash
cd /data/backup-orchestrator/app/tools/backup-collectors
python3.12 -m venv .venv
.venv/bin/pip install .
```

Configure the external tool's NetBackup/API credentials and destinations using its own mechanism. It must run as the worker account from `/data/backup-orchestrator/app`, without relying on a manually activated shell. Arbitrary keys in `.env.worker` are not automatically exported into external command environments.

On each eligible satellite, configure the already supplied alias in `config/worker_scripts.yaml`:

```yaml
backup_collector:
  command: [/data/backup-orchestrator/app/tools/backup-collectors/.venv/bin/backup-collector, collect]
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
cd /data/backup-orchestrator/app
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run.py master
# In another terminal: .venv/bin/python run.py worker
# Another configuration: .venv/bin/python run.py worker --config /path/to/worker.env
```

Configure `.env` / `.env.worker` first. Ctrl+C stops the foreground process. This is the same application entry point used by the two services.

Before updates, take a verified master backup with `.venv/bin/python -m scripts.backup --check-restore`. Use a maintenance window, preserve all private configuration and `var/`, deploy the tested commit and rerun the single installer for the role. Red Hat startup and the external collector must be validated on the actual deployment machine; local Mac tests do not replace that pilot.
