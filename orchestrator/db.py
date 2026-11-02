import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import LATEST_WINDOW_INDEX, RECENT_ATTEMPT_INDEX, Base

WRITE_LOCK = 732614902  # One transaction-scoped writer lock per PostgreSQL database.


def make_engine(database_url):
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend not in ("sqlite", "postgresql"):
        raise ValueError("DATABASE_URL must use SQLite or PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if backend == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        result = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(result, "connect")
        def setup(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    else:
        result = create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
        )
    return result


engine = make_engine(settings.database_url)


Session = sessionmaker(engine, expire_on_commit=False)


def init():
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        # Rename the live classification columns without rewriting historical JSON.
        with engine.begin() as conn:
            for table, old, new in [
                ("hosts", "technology", "scope"),
                ("collectors", "technology", "scope"),
                ("workers", "technologies", "scopes"),
            ]:
                columns = {c["name"] for c in inspect(conn).get_columns(table)}
                if old in columns and new not in columns:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
                    )
        # Additive, idempotent upgrades preserve all periods/results/attempt IDs.
        additions = {
            "collectors": {
                "zone": "VARCHAR NOT NULL DEFAULT ''",
                "legacy_schedule": "BOOLEAN NOT NULL DEFAULT 1",
            },
            "attempts": {"context": "JSON NOT NULL DEFAULT '{}'"},
            "hosts": {
                "zone": "VARCHAR NOT NULL DEFAULT ''",
                "discovered": "BOOLEAN NOT NULL DEFAULT 0",
                "deleted": "BOOLEAN NOT NULL DEFAULT 0",
                "source_id": "VARCHAR NOT NULL DEFAULT ''",
            },
            "workers": {
                "hostname": "VARCHAR NOT NULL DEFAULT ''",
                "runtime": "JSON NOT NULL DEFAULT '{}'",
                "timezone": "VARCHAR NOT NULL DEFAULT ''",
                "zones": "JSON NOT NULL DEFAULT '[]'",
                "token_hash": "VARCHAR NOT NULL DEFAULT ''",
                "token_env": "VARCHAR NOT NULL DEFAULT ''",
            },
            "host_groups": {
                "zone": "VARCHAR NOT NULL DEFAULT ''",
                "enabled": "BOOLEAN NOT NULL DEFAULT 1",
                "deleted": "BOOLEAN NOT NULL DEFAULT 0",
            },
            "schedules": {
                "zone": "VARCHAR NOT NULL DEFAULT ''",
                "kind": "VARCHAR NOT NULL DEFAULT 'interval'",
                "local_time": "VARCHAR NOT NULL DEFAULT '07:00'",
                "created_at": "FLOAT NOT NULL DEFAULT 0",
                "host_group_ids": "JSON NOT NULL DEFAULT '[]'",
                "zones": "JSON NOT NULL DEFAULT '[]'",
            },
        }
        for table in ("windows", "inventory_sources", "inventory_syncs"):
            additions.setdefault(table, {})["zone"] = "VARCHAR NOT NULL DEFAULT ''"
        for table, columns in additions.items():
            present = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, declaration in columns.items():
                if name not in present:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                        )
        # Collapse the previous region/zone hierarchy to a single zone dimension.
        # Historical window/attempt payloads are intentionally not rewritten.
        with engine.begin() as conn:
            if "region" in {c["name"] for c in inspect(conn).get_columns("hosts")}:
                conn.exec_driver_sql(
                    "UPDATE hosts SET zone=region WHERE zone='' OR zone=region || '-CORE'"
                )
                conn.exec_driver_sql("ALTER TABLE hosts DROP COLUMN region")
            if "region" in {c["name"] for c in inspect(conn).get_columns("workers")}:
                for wid, region, raw in conn.exec_driver_sql(
                    "SELECT id,region,zones FROM workers"
                ).fetchall():
                    zones = json.loads(raw) if isinstance(raw, str) else raw
                    zones = [
                        region if z == region + "-CORE" else z
                        for z in (zones or [region])
                    ]
                    conn.execute(
                        text("UPDATE workers SET zones=:zones WHERE id=:id"),
                        {"zones": json.dumps(zones), "id": wid},
                    )
                conn.exec_driver_sql("ALTER TABLE workers DROP COLUMN region")
    health_added = False
    with engine.begin() as conn:
        if "last_success_at" not in {
            c["name"] for c in inspect(conn).get_columns("collectors")
        }:
            conn.exec_driver_sql(
                "ALTER TABLE collectors ADD COLUMN last_success_at FLOAT NOT NULL DEFAULT 0"
            )
            health_added = True
    from .migration_workspaces import migrate

    migrate()
    if health_added:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE collectors SET last_success_at = COALESCE("
                "(SELECT MAX(a.finished_at) FROM attempts a JOIN windows w ON w.id=a.window_id "
                "WHERE w.collector_id=collectors.id AND a.status='SUCCESS'), 0)"
            )
    LATEST_WINDOW_INDEX.create(engine, checkfirst=True)
    RECENT_ATTEMPT_INDEX.create(engine, checkfirst=True)
    with engine.begin() as conn:
        for table in (
            "hosts",
            "collectors",
            "host_groups",
            "schedules",
            "windows",
            "inventory_sources",
            "inventory_syncs",
        ):
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_zone ON {table}(zone)"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_windows_zone_period ON windows(zone,period_start,collector_id,status)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_attempts_window_status ON attempts(window_id,status)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_windows_queue ON windows(status,available_at,period_start)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_attempts_running ON attempts(status,worker_id)"
        )


@contextmanager
def transaction():
    with Session() as session:
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            # Preserve atomic claims, retry budgets and capacity across HTTP threads.
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_LOCK}
            )
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
