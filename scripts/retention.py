"""Clear old detailed logs only. Reports, periods and attempt metadata are retained."""

import argparse
import time

from sqlalchemy import func, select, update

from orchestrator import db
from orchestrator.models import Attempt


def prune(days=30, apply=False):
    if days < 1:
        raise ValueError("Keep at least one day of logs")
    conditions = (
        Attempt.finished_at < time.time() - days * 86400,
        Attempt.logs != "",
        Attempt.status != "RUNNING",
    )
    with db.transaction() as session:
        count = session.scalar(
            select(func.count()).select_from(Attempt).where(*conditions)
        )
        if apply:
            session.execute(update(Attempt).where(*conditions).values(logs=""))
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-days", type=int, default=30)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually clear old logs; the default only counts eligible attempts",
    )
    args = parser.parse_args()
    print("Logs eligible for cleanup:", prune(args.logs_days, args.apply))
    print("Applied" if args.apply else "Dry run. Reports and history are retained.")


if __name__ == "__main__":
    main()
