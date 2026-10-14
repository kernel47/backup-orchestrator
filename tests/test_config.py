import pytest
from workers.common.config import worker_config, server_environment


def test_config(configured):
    config = worker_config()
    assert config.region == "EMEA" and config.batch_size == 2
    assert "secret-password" not in repr(config)
    assert "secret-token" not in repr(config)


def test_dotenv(configured, monkeypatch, tmp_path):
    monkeypatch.delenv("NETBACKUP_MASTER")
    path = tmp_path / "worker.env"
    path.write_text("NETBACKUP_MASTER=dotenv-master\n")
    assert worker_config(path).master == "dotenv-master"


@pytest.mark.parametrize("name,value", [("REGION", "unknown"), ("NETBACKUP_PASSWORD", ""),
    ("NETBACKUP_VERIFY_SSL", "maybe"), ("BATCH_SIZE", "0"),
    ("PREFECT_API_URL", "ftp://server"), ("LOG_LEVEL", "bad")])
def test_invalid_config(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError): worker_config()


def test_server_mapping(monkeypatch):
    from workers.common.config import ROOT
    values = {"PREFECT_API_HOST": "0.0.0.0", "PREFECT_API_PORT": "4200",
              "PREFECT_SERVER_URL": "https://prefect.test", "PREFECT_DB_URL": "sqlite+aiosqlite:///data/prefect.db"}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv("PREFECT_DB_URL", "sqlite+aiosqlite:///" + str(ROOT / "database.db"))
    server_environment()
    import os
    assert os.environ["PREFECT_API_URL"] == "https://prefect.test/api"
    assert os.environ["PREFECT_SERVER_DATABASE_CONNECTION_URL"] == "sqlite+aiosqlite:///" + str(ROOT / "database.db")


def test_runtime_paths_stay_in_project(configured):
    import os
    import tempfile
    from pathlib import Path
    from workers.common.config import ROOT
    worker_config()
    for name in ("HOME", "PREFECT_HOME", "PREFECT_PROFILES_PATH", "PREFECT_LOCAL_STORAGE_PATH",
                 "PREFECT_RESULTS_LOCAL_STORAGE_PATH", "TMPDIR", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
        assert Path(os.environ[name]).is_relative_to(ROOT)
    assert tempfile.gettempdir() == str(ROOT / ".runtime" / "tmp")


def test_default_database_is_local(monkeypatch):
    import os
    from workers.common.config import ROOT
    for key, value in {"PREFECT_API_HOST": "127.0.0.1", "PREFECT_API_PORT": "4200",
                       "PREFECT_SERVER_URL": "http://localhost:4200", "PREFECT_DB_URL": ""}.items():
        monkeypatch.setenv(key, value)
    server_environment()
    assert os.environ["PREFECT_SERVER_DATABASE_CONNECTION_URL"] == "sqlite+aiosqlite:///" + str(ROOT / ".runtime/prefect/prefect.db")


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:////outside/prefect.db", "postgresql+asyncpg://db/prefect"])
def test_reject_external_database(monkeypatch, url):
    for key, value in {"PREFECT_API_HOST": "127.0.0.1", "PREFECT_API_PORT": "4200",
                       "PREFECT_SERVER_URL": "http://localhost:4200", "PREFECT_DB_URL": url}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError): server_environment()
