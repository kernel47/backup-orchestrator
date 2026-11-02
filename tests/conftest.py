import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from orchestrator import db
from orchestrator.config import settings
from orchestrator.models import Base


@pytest.fixture
def database(tmp_path, monkeypatch):
    postgres_url = os.environ.get("TEST_POSTGRES_URL")
    owner = None
    schema = None
    if postgres_url:
        owner = db.make_engine(postgres_url)
        schema = "orchestrator_test_" + uuid4().hex
        with owner.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = db.make_engine(postgres_url)

        @event.listens_for(engine, "connect")
        def search_path(connection, _):
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO "' + schema + '"')
            connection.commit()
    else:
        engine = create_engine(
            "sqlite:///" + str(tmp_path / "business.db"),
            connect_args={"check_same_thread": False, "timeout": 30},
        )
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setattr(db, "engine", engine)
        monkeypatch.setattr(db, "Session", sessionmaker(engine, expire_on_commit=False))
        monkeypatch.setattr(settings, "scheduler_enabled", False)
        monkeypatch.setattr(settings, "auth_username", "test-admin")
        monkeypatch.setattr(settings, "auth_password", "test-password")
        from tests.support import populate

        populate()
        yield engine
    finally:
        engine.dispose()
        if owner:
            with owner.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            owner.dispose()
