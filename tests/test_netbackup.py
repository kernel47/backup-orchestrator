from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from workers.collectors.netbackup import NetBackupCollector
from workers.common.config import worker_config
from workers.common.parameters import ASSETS, validate_parameters
from workers.outputs.backup_hub import TemporaryUploadError, upload_batch
from workers.parsers.netbackup import batches, parse_record


@pytest.mark.parametrize("asset", sorted(ASSETS))
def test_selection(asset):
    collector = NetBackupCollector(None)
    with pytest.raises(NotImplementedError):
        collector.collect(asset, image_id="test-id")


@pytest.mark.parametrize("args", [("unknown",), ("jobs", "2026-01-01"),
    ("jobs", "2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ("policies", "2026-01-01T00:00:00Z"), ("image_contents",)])
def test_invalid_parameters(args):
    with pytest.raises(ValueError): validate_parameters(*args)


def test_dates():
    start, _ = validate_parameters("jobs", "2026-01-01T02:00:00+02:00")
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_json_and_identity():
    raw = {"when": datetime(2026, 1, 1, tzinfo=timezone.utc), "count": 3}
    result = parse_record(raw, "master", "jobs")
    assert result["data"]["when"] == "2026-01-01T00:00:00+00:00"
    assert result == parse_record(dict(reversed(list(raw.items()))), "master", "jobs")
    assert result["id"] != parse_record(raw, "other-master", "jobs")["id"]
    assert list(batches(iter(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    with pytest.raises(ValueError): parse_record({"nan": float("nan")}, "m", "jobs")


def test_http_and_dedup(configured, monkeypatch):
    config = worker_config()
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(204)
    monkeypatch.setattr(httpx, "post", post)
    records = [parse_record({"sample": 1}, config.master, "jobs")]
    assert upload_batch(config, "jobs", records) == 1
    other = SimpleNamespace(**{**config.__dict__, "satellite": "emea-sat02"})
    upload_batch(other, "jobs", records)
    assert calls[0]["headers"]["Idempotency-Key"] == calls[1]["headers"]["Idempotency-Key"]
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("status,exception", [(500, TemporaryUploadError), (429, TemporaryUploadError), (400, RuntimeError), (302, RuntimeError)])
def test_http_failure(configured, monkeypatch, status, exception):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(status, text="secret-token"))
    with pytest.raises(exception) as error: upload_batch(worker_config(), "jobs", [])
    assert "secret-token" not in str(error.value)


def test_pipeline_success(configured, monkeypatch):
    import workers.flows.netbackup as module
    from unittest.mock import Mock
    monkeypatch.setattr(module, "get_run_logger", lambda: Mock())
    monkeypatch.setattr(NetBackupCollector, "collect_jobs", lambda *a: iter([{"sample": i} for i in range(5)]))
    calls = []
    monkeypatch.setattr(module, "upload_batch", lambda c, a, records: calls.append(records) or len(records))
    result = module.collect_and_deliver.fn("jobs", None, None, None)
    assert result["collected"] == result["transmitted"] == 5
    assert [len(batch) for batch in calls] == [2, 2, 1]


def test_collection_failure(configured, monkeypatch):
    import workers.flows.netbackup as module
    from unittest.mock import Mock
    logger = Mock()
    monkeypatch.setattr(module, "get_run_logger", lambda: logger)
    with pytest.raises(RuntimeError, match="NotImplementedError"):
        module.collect_and_deliver.fn("jobs", None, None, None)
    assert logger.error.called


def test_retry_policy():
    from prefect.states import Failed
    from workers.collectors.netbackup import TemporaryCollectionError
    from workers.flows.netbackup import collect_and_deliver, retry_temporary
    assert collect_and_deliver.retries == 2
    assert retry_temporary(None, None, Failed(data=TemporaryCollectionError("temporary")))
    assert not retry_temporary(None, None, Failed(data=ValueError("permanent")))
