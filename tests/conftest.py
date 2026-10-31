import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator import db
from orchestrator.config import settings
from orchestrator.models import Base


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "business.db"),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "Session", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "auth_username", "test-admin")
    monkeypatch.setattr(settings, "auth_password", "test-password")
    from tests.support import populate

    populate()
    yield engine
    engine.dispose()
