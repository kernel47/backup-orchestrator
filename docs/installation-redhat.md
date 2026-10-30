# Install Tasker on Red Hat

The Mac checkout is for development. Install the master and workers from this repository on Red Hat machines. A fresh master starts empty; the Mac's configured database is not deployment input.

This procedure follows the current launch scripts. The application and subprocess flows have been tested locally; actual Red Hat deployment and the external backup-collector still require a pilot validation.

## Requirements and layout

- Python 3.11 or 3.12 with pip and venv, Git, and access to the Python packages or an internal package mirror. Use the interpreter/package source approved for your Red Hat release. Do not replace the system Python.
- A dedicated service account, shown below as `tasker`, with read/write access to its Tasker checkout and `var/` directory. Run installation and launch commands as that account; root is only needed for machine provisioning and systemd units.
- `/data/tasker` on each machine for Tasker; `/data/tasker/tools/backup-collectors` on eligible satellites for the external tool. Paths can be changed consistently.
- Workers connect outbound to the master. They need no incoming worker port. They also need access to the collection targets and destinations required by backup-collector.
- For remote deployments, expose the dashboard/API through HTTPS with a valid certificate and restrict network access. The uvicorn listener itself is plain HTTP; the examples below assume a controlled pilot network or an HTTPS reverse proxy configured separately. Do not disable certificate checks or SELinux to make the deployment work.

Clone the repository into `/data/tasker` on the master and each satellite using the same tested commit:

```bash
git clone git@github.com:kernel47/backup-orchestrator.git /data/tasker
cd /data/tasker
git rev-parse HEAD
python3.12 --version
```

The destination must be writable by the service account. SSH clone requires repository access; an approved source archive is also usable. If copying a Mac checkout instead, exclude `.venv/`, `.env`, `.env.worker`, `var/`, caches and the local `inventory/hosts.json`. Build each virtual environment on its own Red Hat machine.

## Keep application files under /data/tasker

`var/` is a directory inside the checkout, not the system `/var` directory. With the supplied launchers and working directory, `DATABASE_URL=sqlite:///var/orchestrator.db` resolves to `/data/tasker/var/orchestrator.db`. SQLite WAL/SHM sidecars stay next to that file. Backups are created under `/data/tasker/var/backups/` by default.

```text
/data/tasker/
├── .env                         # Master runtime configuration
├── .env.worker                  # Satellite runtime configuration
├── .venv/                       # Tasker Python environment
├── config/worker_scripts.yaml   # Local executable mappings
├── inventory/hosts.json         # Local host export
├── var/
│   ├── orchestrator.db          # Master configuration and execution history
│   ├── backups/
│   └── logs/                    # Local launcher logs, when used
└── tools/backup-collectors/
    └── .venv/                   # External collector environment
```

Each machine keeps its own files in this layout. Workers do not need a copy of the master's database or `.env`. The local `tools/` tree is ignored by Tasker's Git repository; install and maintain the external tool separately inside that directory. If changing the checkout location, update the command's absolute path and any service WorkingDirectory/ExecStart paths.

For an explicitly absolute SQLite path, the equivalent master setting is `DATABASE_URL=sqlite:////data/tasker/var/orchestrator.db` (four slashes after `sqlite:`). Do not use `sqlite:////var/orchestrator.db`, which would refer to the system `/var` directory.

## 1. Install the master

```bash
cd /data/tasker
PYTHON=python3.12 ./scripts/install_master.sh
```

Use `PYTHON=python3.11` if that is your installed supported interpreter. Installation creates `.venv`, installs dependencies and initializes an empty database. It creates `.env` from `.env.master.example` with random dashboard/API/session secrets when the file does not exist. Existing credentials and data are preserved.

Keep the master local to its Red Hat host on port 8000. Check `/data/tasker/.env`:

```dotenv
MASTER_HOST=127.0.0.1
MASTER_PORT=8000
DATABASE_URL=sqlite:///var/orchestrator.db
SCHEDULER_ENABLED=true
```

The master binds only to localhost, like a local application backend. Use a reverse proxy on the same machine for remote dashboard and worker access; keep the backend on `127.0.0.1:8000`. Keep the generated `AUTH_PASSWORD`, `ADMIN_TOKEN` and `SESSION_SECRET` private. `AUTH_USERNAME` defaults to `admin`. `MASTER_URL` in the master environment is used by the inventory synchronization CLI: set it to a reachable master URL when needed.

Start in the foreground for the first check:

```bash
./scripts/start_master.sh
```

Open `http://127.0.0.1:8000` on the Red Hat host, or your configured HTTPS proxy URL. From a remote workstation without a proxy, an SSH tunnel such as `ssh -L 18000:127.0.0.1:8000 USER@MASTER` makes the dashboard available at `http://127.0.0.1:18000` on that workstation. A tunnel used for your browser does not automatically provide remote workers with access. Sign in with `AUTH_USERNAME` / `AUTH_PASSWORD` from `.env`. `/health` must report healthy. Ctrl+C stops the foreground process.

Run one master process against the SQLite database. Do not run multiple uvicorn workers or multiple scheduler instances against it.

## 2. Provision each satellite in the dashboard

1. Create the required zones, for example EMEA, APAC and AMER, and the NetBackup scope.
2. Select the satellite's zone workspace.
3. Open **Configuration → Satellites** and add its ID, scope and concurrency.
4. Copy the one-time worker token. The master retains its hash; do not copy the master `.env` to a worker.

Each satellite belongs to one zone. For example, provision `EMEA-SAT-1` in EMEA with the NetBackup scope and capacity 2.

## 3. Install and start the worker

On each satellite:

```bash
cd /data/tasker
PYTHON=python3.12 ./scripts/install_worker.sh
```

Edit `/data/tasker/.env.worker` using the provisioned ID and token:

```dotenv
MASTER_URL=https://tasker.example.internal
WORKER_ID=EMEA-SAT-1
WORKER_TOKEN=REPLACE_WITH_THE_PROVISIONED_TOKEN
WORKER_CONCURRENCY=2
POLL_SECONDS=1
LEASE_SECONDS=30
WORKER_TIMEZONE=Europe/Paris
```

Replace the example master URL with yours. A worker on the same machine can use `http://127.0.0.1:8000`. Remote workers need a reachable master endpoint, normally the HTTPS reverse proxy; they cannot connect to the master machine's loopback listener directly. The concurrency should match the provisioned capacity. Use the satellite's actual IANA timezone, for example `Europe/Paris`, `Asia/Singapore` or `America/New_York`; a broad zone such as EMEA is not a timezone.

```bash
chmod 600 .env.worker
./scripts/start_worker.sh
```

The worker runs in the foreground and should become **Up** in Configuration → Satellites. It polls for tasks and reports heartbeat, capacity and timezone. The launcher reads `.env.worker` independently from the master's `.env`.

## 4. Install backup-collector on eligible satellites

Tasker does not distribute or install the external backup-collectors project. Place your approved checkout under `/data/tasker/tools/backup-collectors` on every satellite that will execute it. A separate virtual environment avoids dependency conflicts.

If your project provides a pip-installable package, from its checkout:

```bash
cd /data/tasker/tools/backup-collectors
python3.12 -m venv .venv
.venv/bin/pip install .
```

Otherwise use that project's documented installation method. The following integration assumes it exposes the `backup-collector` executable shown in your CLI.

Configure NetBackup credentials, API/SSH access and output destinations through backup-collector's own configuration. Tasker does not decide whether output goes to console, OpenSearch, Backup Hub or another destination. Ensure the tool can find its configuration when executed as the worker account from `/data/tasker`; do not rely on an activated shell or a different current directory. If it needs exported environment variables, provide those through the worker service environment: merely adding arbitrary keys to Tasker's `.env.worker` does not export them to child commands.

On each eligible satellite, edit `/data/tasker/config/worker_scripts.yaml`. Keep the same `backup_collector` alias on the master so the dashboard offers it. The executable path may differ per satellite:

```yaml
backup_collector:
  command:
    - /data/tasker/tools/backup-collectors/.venv/bin/backup-collector
    - collect
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

Only mappings authorized in this worker-owned file become CLI options. If your tool uses different option names, adapt this mapping. Tasker passes argument values directly, without a shell. The assigned host and UTC period supply `--asset`, `--start-date` and `--end-date`; the collector/schedule parameters supply `--scope` and `--type`.

The command must write exactly one JSON report to stdout and progress logs to stderr. The native top-level final `status` determines the business outcome. Tasker preserves collection/storage/create/update/performance/error details without interpreting delivery or destination decisions. It supplies task identity, attempt number, period and execution timings itself. See [the report contract and command behavior](scripts.md).

Restart the worker after installing updated Tasker Python code. Changes to the command allowlist are read for each new task; running attempts retain their launched command.

## 5. Configure inventory and collectors

The database is the source of truth for the master configuration. A local host export is only an input for inventory synchronization. Start with the empty template if needed:

```bash
cd /data/tasker
cp inventory/hosts.example.json inventory/hosts.json
```

Populate the file with the host IDs accepted by `backup-collector --asset`, their zones and scopes. Add it as an Inventory source, Discover changes, review/apply, activate the intended hosts and create a host group. See the [inventory workflow](../README.md#inventory).

In **Configuration → Collector settings**, create **NetBackup Jobs** in the selected zone:

- Script: `backup_collector`.
- Parameters: `{"scope":"netbackup","type":"jobs"}`.
- Timeout: begin with 300 seconds and adjust after measuring the real workload.
- Automatic retries: begin with 0 while checking repeat-delivery behavior.

Policies and Images use their own collectors and the actual `type` values supported by your CLI. Confirm those names with your tool; Tasker does not implement NetBackup API calls itself.

In **Schedules**, bind the collector to the relevant group. Start with schedules paused. Jobs can run hourly; Policies and Images can run daily. Daily-local 07:00 is resolved using the satellite timezone, not the master's clock.

## 6. Validate the pilot

1. Use one approved host and a short historical period. If necessary, use a temporary one-host group rather than launching against a large group.
2. Launch that task from Tasker and verify the generated CLI options, report status and logs in Runs & history.
3. Independently verify the result at the destination configured by backup-collector.
4. Test a controlled failure and a retry of the same period. Confirm destination-side deduplication/create/update behavior before allowing automatic retries.
5. Enable the schedule only after the pilot succeeds, then expand to more hosts.

A valid business failure report is preserved even when the command exits nonzero. A reported success with nonzero exit code is a technical failure. Invalid JSON is not treated as success. Timeout kills the collector process group; it does not prove that no data was delivered.

## 7. Install the persistent systemd service

`start_master.sh` and `start_worker.sh` are foreground launchers. To keep the master running after closing the terminal and start it at boot, use the service installer on Red Hat. The application must first be installed and `.env` configured. Stop any foreground master first to free port 8000.

The service account must exist and have access to `/data/tasker`, including write access to `var/`. For a new deployment, an administrator can provision it before installing Tasker:

```bash
sudo useradd --system --no-create-home --home-dir /data/tasker --shell /sbin/nologin tasker
sudo chown -R tasker:tasker /data/tasker
```

Skip account creation if it already exists. Application/dependency installation should run as that account, while the following service-registration command uses sudo:

```bash
cd /data/tasker
sudo ./scripts/install_service.sh master --user tasker --start
sudo systemctl status tasker-master
curl --fail http://127.0.0.1:8000/health
```

The installer derives paths from the checkout, writes `/etc/systemd/system/tasker-master.service`, reloads systemd, and with `--start` enables and starts the service. It runs the master as `tasker`, not root, with `/data/tasker` as its working directory. The master reads `/data/tasker/.env`; its bind address remains `MASTER_HOST=127.0.0.1` and port `MASTER_PORT=8000`.

For a satellite after installing the worker and configuring `.env.worker`:

```bash
cd /data/tasker
sudo ./scripts/install_service.sh worker --user tasker --start
sudo systemctl status tasker-worker
```

Inspect the generated unit without making changes (also works on the development Mac):

```bash
./scripts/install_service.sh master --user tasker --print
```

Operating commands:

```bash
sudo systemctl restart tasker-master
sudo systemctl stop tasker-master
sudo journalctl -u tasker-master -n 100 --no-pager
```

Use `tasker-worker` on satellites. Do not run a second foreground process while its service is running. An already-running service needs `systemctl restart` after code/config changes; `enable --now` does not restart an active process. If startup fails, check the journal for a port conflict, missing environment, filesystem permissions, or an environment copied from another machine.

The code, configuration, Python environment and SQLite database remain under `/data/tasker`. The systemd registration lives in `/etc/systemd/system`, and service output goes to the system journal. Actual installation/startup on Red Hat still requires validation there; the development Mac cannot run systemd.

## Backups and updates

On the master, take and verify a backup before an update:

```bash
cd /data/tasker
.venv/bin/python -m scripts.backup --check-restore
```

Use a maintenance window for updates. Stop the relevant service, deploy the tested commit, rerun its installation script to update dependencies and initialize/upgrade storage where applicable, and restart it. Preserve `.env`, `.env.worker`, worker command mappings, host exports and `var/`. Do not reset or overwrite those files with the Mac development environment. Keep verified backups outside the master machine according to your retention policy. Never commit runtime secrets, live inventory or databases.
