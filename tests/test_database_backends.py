import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from orchestrator import db
from orchestrator.models import Worker


def test_audit_and_retention_support_the_selected_database(database):
    import time

    from sqlalchemy import select

    from orchestrator import service as svc
    from orchestrator.models import Attempt
    from scripts.audit_state import audit
    from scripts.retention import prune
    from tests.test_core import one_window

    assert audit()["ok"]
    one_window()
    task = svc.claim("EMEA-SAT-1")
    svc.result(
        task["task_id"],
        "EMEA-SAT-1",
        {"status": "SUCCESS", "summary": "Collected"},
        logs="Old execution log",
    )
    with db.transaction() as session:
        session.get(Attempt, task["task_id"]).finished_at = time.time() - 40 * 86400
    assert prune(days=30) == 1
    with db.Session() as session:
        assert session.get(Attempt, task["task_id"]).logs == "Old execution log"
    assert prune(days=30, apply=True) == 1
    with db.Session() as session:
        report = session.scalar(select(Attempt).where(Attempt.id == task["task_id"]))
        assert report.logs == "" and report.result["summary"] == "Collected"


def test_database_url_selects_backend_without_exposing_credentials(tmp_path):
    sqlite_engine = db.make_engine(
        "sqlite:///" + str(tmp_path / "nested" / "db.sqlite")
    )
    postgres_engine = db.make_engine(
        "postgresql://test:private-password@localhost/test"
    )
    try:
        assert sqlite_engine.dialect.name == "sqlite"
        assert (tmp_path / "nested").is_dir()
        assert postgres_engine.dialect.name == "postgresql"
        assert postgres_engine.url.drivername == "postgresql+psycopg"
        with pytest.raises(ValueError, match="SQLite or PostgreSQL") as error:
            db.make_engine("mysql://test:private-password@localhost/test")
        assert "private-password" not in str(error.value)
    finally:
        sqlite_engine.dispose()
        postgres_engine.dispose()


def test_json_storage_uses_jsonb_only_on_postgresql():
    assert "JSONB" in str(
        CreateTable(Worker.__table__).compile(dialect=postgresql.dialect())
    )
    assert "JSONB" not in str(
        CreateTable(Worker.__table__).compile(dialect=sqlite.dialect())
    )


def test_last_success_upgrade_keeps_reports_and_is_idempotent(database):
    from orchestrator import service as svc
    from orchestrator.models import Attempt, Collector, Window
    from tests.test_core import one_window

    wid = one_window()
    task = svc.claim("EMEA-SAT-1")
    svc.result(
        task["task_id"], "EMEA-SAT-1", {"status": "SUCCESS", "summary": "Preserved"}
    )
    with db.Session() as session:
        finished = session.get(Attempt, task["task_id"]).finished_at
    with database.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE collectors DROP COLUMN last_success_at")
    db.init()
    db.init()
    with db.Session() as session:
        window = session.get(Window, wid)
        collector = session.get(Collector, window.collector_id)
        assert collector.last_success_at == finished
        assert session.get(Attempt, task["task_id"]).result["summary"] == "Preserved"
