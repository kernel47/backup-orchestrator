from contextlib import contextmanager
import json
from pathlib import Path
from sqlalchemy import create_engine,event,inspect,text
from sqlalchemy.orm import sessionmaker
from .config import settings
from .models import Base
Path("var").mkdir(exist_ok=True)
engine=create_engine(settings.database_url,connect_args={"check_same_thread":False,"timeout":30} if settings.database_url.startswith("sqlite") else {})
if engine.dialect.name=="sqlite":
    @event.listens_for(engine,"connect")
    def setup(conn,_):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
Session=sessionmaker(engine,expire_on_commit=False)
def init():
    Base.metadata.create_all(engine)
    if engine.dialect.name=="sqlite":
        # Rename the live classification columns without rewriting historical JSON.
        with engine.begin() as conn:
            for table,old,new in [("hosts","technology","scope"),("collectors","technology","scope"),("workers","technologies","scopes")]:
                columns={c["name"] for c in inspect(conn).get_columns(table)}
                if old in columns and new not in columns:
                    conn.exec_driver_sql(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        # Additive, idempotent upgrades preserve all periods/results/attempt IDs.
        additions = {
            "collectors": {"zone":"VARCHAR NOT NULL DEFAULT ''", "legacy_schedule": "BOOLEAN NOT NULL DEFAULT 1"},
            "attempts": {"context": "JSON NOT NULL DEFAULT '{}'"},
            "hosts": {"zone": "VARCHAR NOT NULL DEFAULT ''", "discovered":"BOOLEAN NOT NULL DEFAULT 0", "deleted":"BOOLEAN NOT NULL DEFAULT 0", "source_id":"VARCHAR NOT NULL DEFAULT ''"},
            "workers": {"timezone":"VARCHAR NOT NULL DEFAULT ''", "zones": "JSON NOT NULL DEFAULT '[]'", "token_hash":"VARCHAR NOT NULL DEFAULT ''", "token_env":"VARCHAR NOT NULL DEFAULT ''"},
            "host_groups":{"zone":"VARCHAR NOT NULL DEFAULT ''","enabled":"BOOLEAN NOT NULL DEFAULT 1","deleted":"BOOLEAN NOT NULL DEFAULT 0"},
            "schedules":{"zone":"VARCHAR NOT NULL DEFAULT ''","kind":"VARCHAR NOT NULL DEFAULT 'interval'", "local_time":"VARCHAR NOT NULL DEFAULT '07:00'", "created_at":"FLOAT NOT NULL DEFAULT 0", "host_group_ids":"JSON NOT NULL DEFAULT '[]'","zones":"JSON NOT NULL DEFAULT '[]'"},
        }
        for table in ("windows","inventory_sources","inventory_syncs"):additions.setdefault(table,{})["zone"]="VARCHAR NOT NULL DEFAULT ''"
        for table, columns in additions.items():
            present = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, declaration in columns.items():
                if name not in present:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"))
        # Collapse the previous region/zone hierarchy to a single zone dimension.
        # Historical window/attempt payloads are intentionally not rewritten.
        with engine.begin() as conn:
            if "region" in {c["name"] for c in inspect(conn).get_columns("hosts")}:
                conn.exec_driver_sql("UPDATE hosts SET zone=region WHERE zone='' OR zone=region || '-CORE'")
                conn.exec_driver_sql("ALTER TABLE hosts DROP COLUMN region")
            if "region" in {c["name"] for c in inspect(conn).get_columns("workers")}:
                for wid,region,raw in conn.exec_driver_sql("SELECT id,region,zones FROM workers").fetchall():
                    zones=json.loads(raw) if isinstance(raw,str) else raw
                    zones=[region if z==region+"-CORE" else z for z in (zones or [region])]
                    conn.execute(text("UPDATE workers SET zones=:zones WHERE id=:id"),{"zones":json.dumps(zones),"id":wid})
                conn.exec_driver_sql("ALTER TABLE workers DROP COLUMN region")
    from .migration_workspaces import migrate
    migrate()
    with engine.begin() as conn:
        for table in ("hosts","collectors","host_groups","schedules","windows","inventory_sources","inventory_syncs"):
            conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_zone ON {table}(zone)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_windows_zone_period ON windows(zone,period_start,collector_id,status)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_attempts_window_status ON attempts(window_id,status)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_windows_queue ON windows(status,available_at,period_start)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_attempts_running ON attempts(status,worker_id)")

@contextmanager
def transaction():
    with Session() as session:
        if engine.dialect.name=="sqlite":session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:raise RuntimeError("V0 : SQLite uniquement")
        try:yield session;session.commit()
        except Exception:session.rollback();raise
