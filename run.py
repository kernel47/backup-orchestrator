"""Single entry point for systemd services and foreground troubleshooting."""

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("master", "worker"))
    parser.add_argument(
        "--config",
        help="Configuration file; defaults to .env for master or .env.worker for worker",
    )
    args = parser.parse_args()
    path = Path(args.config or (".env" if args.role == "master" else ".env.worker"))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        parser.error(
            f"Configuration missing: {path}. Copy the matching .env example and configure it first."
        )
    os.environ["ORCHESTRATOR_ENV_FILE"] = str(path.resolve())
    os.chdir(ROOT)

    from orchestrator.config import settings
    from orchestrator.logging_setup import configure_logging

    log_directory = settings.log_directory
    if not log_directory.is_absolute():
        log_directory = ROOT / log_directory
    configure_logging(args.role, log_directory, settings.log_level)
    if args.role == "master":
        import uvicorn

        uvicorn.run(
            "orchestrator.main:app",
            host=settings.master_host,
            port=settings.master_port,
            workers=1,
            log_config=None,
            access_log=False,
        )
    else:
        import asyncio

        from worker.agent import run

        if not settings.worker_id:
            parser.error("Set WORKER_ID in the worker configuration")
        asyncio.run(run(settings.worker_id))


if __name__ == "__main__":
    main()
