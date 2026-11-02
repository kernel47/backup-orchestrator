# Validation

Install `requirements-dev.txt`, then run `venv/bin/ruff check .`, `venv/bin/ruff format --check .` and `venv/bin/python -m pytest -q` for isolated database and HTTP worker tests. Test configuration and synthetic historical records exist only under `tests/`.

Managed objects live only in the database; config/ contains only the executable collector allowlist. Tests populate isolated databases using tests/support.py and test-only fixtures.

The default suite uses SQLite. To exercise PostgreSQL, install the standard requirements and set `TEST_POSTGRES_URL` to a dedicated test database whose login can create schemas. Each database fixture creates a random `orchestrator_test_*` schema, sets its connection search path and drops that schema at teardown. Do not point this at a production database. For example:

```bash
TEST_POSTGRES_URL=postgresql://test_user:password@127.0.0.1:5432/orchestrator_test \
  venv/bin/python -m pytest -q tests/test_core.py tests/test_api.py \
  tests/test_history_queries.py tests/test_dashboard_views.py \
  tests/test_zone_structure.py tests/test_managed_inventory.py tests/test_local_schedules.py \
  tests/test_database_backends.py
```

SQLite-only historical-schema upgrades and the SQLite snapshot command are validated by the default suite. PostgreSQL integration tests cover actual SQL execution, JSONB equality and group filters, concurrent claims, retries, daily-local schedules, workspace-aware page reads and inventory operations.

Run `venv/bin/python -m scripts.audit_state` for a read-only database integrity and routing audit. Runtime data and logs are under the ignored `var/` directory.

`tests/test_capacity.py` exercises five collectors (one hourly, four daily) on 10, 50 and 100 synthetic hosts, with two worker identities at capacity two. It simulates a satellite becoming unavailable during assigned work, verifies recovery/fallback attempts and drains every collection without overlapping active attempts for a host. A separate concurrent-claim test checks configurable host limits across worker identities. Worker tests verify that two free slots are filled before the next polling interval. These are synthetic master-domain tests; the HTTP end-to-end tests separately execute real worker processes and fake collector subprocesses. Production collector duration, failover behavior and dashboard latency still require a deployment pilot.

`tests/test_history_queries.py` checks expected IDs and counters independently for list/detail filters, latest-worker selection, and a 10,000-period/10,000-report dataset. A combined scope/collector/group/host search followed by page 2 loads only the requested page of window objects in Runs, Collectors loads no window objects, Overview loads only the latest window for the recorded host/collector, and page requests issue no writes or `BEGIN IMMEDIATE`. None of these pages loads attempt report/log bodies. Retrying one selected period must not load unrelated periods or report bodies. Reading an empty day must not create historical periods. This regression test checks bounded loading, not Red Hat production capacity or Avi timeouts.
