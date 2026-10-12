"""Charge le .env, valide puis remplace le processus par la CLI Prefect."""
import os
import sys
from pathlib import Path

from workers.common.config import server_environment, worker_config


def main():
    executable = str(Path(sys.executable).with_name("prefect"))
    match sys.argv[1]:
        case "server":
            server_environment()
            command = [executable, "server", "start"]
        case "worker":
            config = worker_config()
            command = [executable, "worker", "start", "--pool", config.pool,
                       "--type", "process", "--name", config.satellite, "--limit", "1"]
        case _:
            raise ValueError("Mode attendu : server ou worker")
    os.execv(executable, command)


if __name__ == "__main__":
    main()
