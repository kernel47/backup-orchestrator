from time import monotonic

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

from workers.collectors.netbackup import NetBackupCollector, TemporaryCollectionError
from workers.common.config import worker_config
from workers.common.parameters import validate_parameters
from workers.outputs.backup_hub import TemporaryUploadError, upload_batch
from workers.parsers.netbackup import batches, parse_record


def retry_temporary(task, task_run, state):
    try:
        state.result()
    except (TemporaryCollectionError, TemporaryUploadError):
        return True
    except Exception:
        return False
    return False


@task(retries=2, retry_delay_seconds=60, retry_condition_fn=retry_temporary,
      persist_result=False, cache_policy=NO_CACHE)
def collect_and_deliver(asset, start_date, end_date, image_id):
    config = worker_config()
    logger = get_run_logger()
    started = monotonic()
    collected = transmitted = 0
    stage = "collection"
    try:
        logger.info("Collection started master=%s asset=%s region=%s satellite=%s",
                    config.master, asset, config.region, config.satellite)
        records = NetBackupCollector(config).collect(asset, start_date, end_date, image_id)
        for raw in batches(records, config.batch_size):
            collected += len(raw)
            stage = "parsing"
            logger.info("Parsing started")
            parsed = [parse_record(r, config.master, asset) for r in raw]
            logger.info("Parsing completed count=%s", len(parsed))
            stage = "upload"
            logger.info("Backup Hub upload started")
            transmitted += upload_batch(config, asset, parsed)
            logger.info("Backup Hub upload completed count=%s", len(parsed))
            stage = "collection"
        logger.info("Collection completed count=%s", collected)
    except Exception as exc:
        logger.error("Collection failed stage=%s error_type=%s master=%s asset=%s region=%s satellite=%s collected=%s transmitted=%s errors=1 duration=%.2f",
                     stage, type(exc).__name__, config.master, asset, config.region, config.satellite,
                     collected, transmitted, monotonic() - started)
        # Ne pas propager les messages arbitraires des clients externes vers Prefect.
        if isinstance(exc, (TemporaryCollectionError, TemporaryUploadError)):
            raise type(exc)(f"Erreur temporaire à l'étape {stage}; consulter les logs de contexte") from None
        raise RuntimeError(f"Échec à l'étape {stage} ({type(exc).__name__}); vérifier le raccordement et la configuration") from None
    return {"collected": collected, "transmitted": transmitted, "errors": 0,
            "duration_seconds": round(monotonic() - started, 3), "region": config.region,
            "satellite": config.satellite, "master": config.master, "asset": asset}


@flow(name="netbackup", persist_result=False)
def netbackup_flow(asset: str = "jobs", start_date: str | None = None,
                   end_date: str | None = None, image_id: str | None = None):
    start, end = validate_parameters(asset, start_date, end_date, image_id)
    config = worker_config()
    result = collect_and_deliver.with_options(timeout_seconds=config.timeout)(asset, start, end, image_id)
    get_run_logger().info("Flow completed %s", result)
    return result
