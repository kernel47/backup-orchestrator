# Validation

Install `requirements-dev.txt`, then run `venv/bin/ruff check .`, `venv/bin/ruff format --check .` and `venv/bin/python -m pytest -q` for isolated database and HTTP worker tests. Test configuration and synthetic historical records exist only under `tests/`.

Managed objects live only in the database; config/ contains only the executable collector allowlist. Tests populate isolated databases using tests/support.py and test-only fixtures.

Run `venv/bin/python -m scripts.audit_state` for a read-only database integrity and routing audit. Runtime data and logs are under the ignored `var/` directory.
