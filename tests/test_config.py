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
    values = {"PREFECT_API_HOST": "0.0.0.0", "PREFECT_API_PORT": "4200",
              "PREFECT_SERVER_URL": "https://prefect.test", "PREFECT_DB_URL": "postgresql+asyncpg://db/prefect"}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    server_environment()
    import os
    assert os.environ["PREFECT_API_URL"] == "https://prefect.test/api"
    assert os.environ["PREFECT_SERVER_DATABASE_CONNECTION_URL"] == values["PREFECT_DB_URL"]
