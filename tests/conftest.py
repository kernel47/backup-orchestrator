import pytest


@pytest.fixture
def configured(monkeypatch, tmp_path):
    values = {"ENVIRONMENT": "test", "REGION": "EMEA", "SATELLITE_NAME": "emea-sat01",
              "PREFECT_API_URL": "http://localhost:4200/api", "PREFECT_WORK_POOL": "backup-emea-01",
              "NETBACKUP_MASTER": "master.test", "NETBACKUP_USERNAME": "user",
              "NETBACKUP_PASSWORD": "secret-password", "NETBACKUP_VERIFY_SSL": "true",
              "BACKUP_HUB_URL": "https://hub.test/ingest", "BACKUP_HUB_TOKEN": "secret-token",
              "COLLECT_TIMEOUT": "60", "BATCH_SIZE": "2"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values
