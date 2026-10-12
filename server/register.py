import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from workers.common.config import server_environment


async def create_pools():
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.actions import WorkPoolCreate
    from prefect.exceptions import ObjectNotFound
    from prefect.workers.process import ProcessWorker
    from server.deployments import POOLS

    async with get_client() as client:
        for pool in POOLS:
            try:
                existing = await client.read_work_pool(pool)
            except ObjectNotFound:
                await client.create_work_pool(WorkPoolCreate(
                    name=pool, type="process", base_job_template=ProcessWorker.get_default_base_job_template()))
            else:
                if existing.type != "process":
                    raise ValueError(f"Le pool {pool} existe avec un type incompatible")


def main():
    server_environment()
    import yaml
    from server.deployments import deployment_config
    definitions = deployment_config()
    asyncio.run(create_pools())
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "prefect.yaml"
        path.write_text(yaml.safe_dump(definitions, sort_keys=False))
        subprocess.run([str(Path(sys.executable).with_name("prefect")),
                        "deploy", "--all", "--prefect-file", str(path), "--no-prompt"], check=True)


if __name__ == "__main__":
    main()
