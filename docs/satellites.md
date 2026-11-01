# Master and satellites

Install the same checkout at `/data/backup-orchestrator/master` on the master and `/data/backup-orchestrator/worker` on each satellite. The master uses `.env`; each satellite uses `.env.worker`. Configure those files from the supplied examples before installation, using the machine's own settings and credentials.

Two standard units are provided in `systemd/`. The only installer is `install.sh`:

```bash
sudo ./install.sh master --user backup-orchestrator
sudo ./install.sh worker --user backup-orchestrator
```

Run the first on the master, the second on each satellite. The account must exist and have read/write access to the checkout. Configuration is never generated or overwritten. Use `systemctl` and `journalctl` to operate the selected service. Detailed instructions, network access and external command integration are in [the Red Hat guide](installation-redhat.md).

For foreground troubleshooting use `venv/bin/python run.py master` or `venv/bin/python run.py worker`. Stop with Ctrl+C; do not run a duplicate process alongside systemd. The single entry point accepts `--config /path/to/file.env` if a different configuration is needed.

## HTTPS certificate verification

Set `WORKER_VERIFY_SSL=true` in `.env.worker` (the default) to verify the master's HTTPS certificate for all worker requests, including heartbeat, task claims and result submission. For temporary testing with an untrusted certificate, `WORKER_VERIFY_SSL=false` disables certificate and hostname verification; the connection is then vulnerable to interception. Prefer configuring a trusted certificate or CA for production. This setting applies only to the worker's connection to the master, not to collector connections.

After changing `.env.worker`, run `sudo systemctl restart backup-orchestrator-worker`.

## Worker-local clocks and daily execution

Workers report an IANA timezone with each heartbeat. `WORKER_TIMEZONE=Europe/Paris` can override detection; otherwise `TZ`, `/etc/localtime` and `/etc/timezone` are checked on the worker. If none provides a valid zone, startup fails with an explicit configuration message. The worker also sets `TZ` for its Python subprocesses. Never derive timezone from a broad zone such as EMEA or AMER.

Schedules support two timing modes:

- `interval`: an elapsed-time cadence (existing schedules retain their previous UTC boundaries).
- `daily_local`: `local_time=07:00` means one slot per day at 07:00 on each host's preferred authorized satellite. The master converts each local occurrence to UTC internally; its own local timezone is not used.

A preferred satellite's reported clock remains the reference while it is offline. Automatic fallback execution requires the same IANA timezone, so moving to a different clock cannot accidentally change the 07:00 slot. Manual retries retain the original period and planned timezone, and record the actual executing satellite's timezone separately. Configure workers and preferred routing before creating a daily-local schedule.

Spring-forward nonexistent times advance by the DST gap; fall-back ambiguous times use the first occurrence once. At 07:00, daily periods naturally contain 23 or 25 hours when the timezone changes offset. Retries reuse the exact UTC bounds instead of reconstructing a nominal 24-hour range. A newly created daily-local rule starts at its next occurrence; older missing periods require an explicit backfill. Offline workers or exhausted capacity may delay actual execution; planned and actual start times are shown separately.

Satellite cards show live local clocks and next planned execution. Overview focuses on collection activity and health. Schedule details show local/UTC next occurrences per assigned satellite. History/detail includes planned local time, actual local start and UTC collection periods. Date filters still refer to UTC collection-period start dates, not local execution dates.
