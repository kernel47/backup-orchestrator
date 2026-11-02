"""Synthetic master load: five collectors, bounded concurrency and worker loss."""

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from orchestrator import db
from orchestrator import service as svc
from orchestrator.config import settings
from orchestrator.models import Attempt, Collector, Host, Window, Worker


def seed_capacity(hosts):
    with db.transaction() as session:
        collectors = []
        for number in range(5):
            collector = Collector(
                id=f"capacity_{number}",
                name=f"Capacity {number}",
                scope="Capacity",
                zone="EMEA",
                script="fake",
                interval_seconds=3600 if number == 0 else 86400,
                timeout_seconds=300,
                max_retries=2,
                enabled=True,
                parameters={},
                legacy_schedule=False,
            )
            session.add(collector)
            collectors.append(collector)
        for worker_id in ("EMEA-SAT-1", "EMEA-SAT-2"):
            worker = session.get(Worker, worker_id)
            worker.scopes = ["Capacity"]
            worker.concurrency = 2
            worker.heartbeat = time.time()
        end = svc.now().replace(minute=0, second=0, microsecond=0)
        for number in range(hosts):
            preferred = "EMEA-SAT-1" if number % 2 == 0 else "EMEA-SAT-2"
            fallback = "EMEA-SAT-2" if number % 2 == 0 else "EMEA-SAT-1"
            host = Host(
                id=f"capacity-host-{number:03}",
                zone="EMEA",
                scope="Capacity",
                enabled=True,
                preferred_worker=preferred,
                fallback_worker=fallback,
                parameters={},
            )
            session.add(host)
            session.flush()
            for collector in collectors:
                session.add(
                    Window(
                        id=f"capacity-{number:03}-{collector.id}",
                        zone="EMEA",
                        host_id=host.id,
                        collector_id=collector.id,
                        period_start=svc.iso(
                            end - svc.timedelta(seconds=collector.interval_seconds)
                        ),
                        period_end=svc.iso(end),
                        status="PENDING",
                        available_at=0,
                        retry_base=0,
                        created_at=time.time(),
                        snapshot=svc.snapshot(collector, host),
                    )
                )


@pytest.mark.parametrize("limit", [1, 2])
def test_host_capacity_is_atomic_across_workers_and_collectors(
    database, monkeypatch, limit
):
    seed_capacity(1)
    monkeypatch.setattr(settings, "host_concurrency", limit)
    with db.transaction() as session:
        session.get(Host, "capacity-host-000").preferred_worker = ""
    with ThreadPoolExecutor(6) as pool:
        tasks = list(pool.map(svc.claim, ["EMEA-SAT-1", "EMEA-SAT-2"] * 3))
    running = [task for task in tasks if task]
    assert len(running) == limit
    assert len({task["collector_id"] for task in running}) == limit
    svc.result(
        running[0]["task_id"],
        running[0]["worker_id"],
        {"status": "SUCCESS", "summary": "Collected"},
    )
    assert svc.claim(running[0]["worker_id"]) is not None


@pytest.mark.parametrize("hosts", [10, 50, 100])
def test_five_collectors_drain_without_host_overlap_after_worker_loss(database, hosts):
    seed_capacity(hosts)
    workers = ["EMEA-SAT-1", "EMEA-SAT-2"]
    done = set()
    lost_tasks = []
    while len(done) < hosts * 5:
        tasks = [
            task for worker in workers for _ in range(2) if (task := svc.claim(worker))
        ]
        assert tasks, (
            "Pending work must not be starved by unavailable preferred workers"
        )
        assert max(Counter(t["host"] for t in tasks).values()) == 1
        assert max(Counter(t["worker_id"] for t in tasks).values()) <= 2
        if not lost_tasks:
            lost_tasks = [task for task in tasks if task["worker_id"] == "EMEA-SAT-1"]
            with db.transaction() as session:
                session.get(Worker, "EMEA-SAT-1").heartbeat = 0
                svc.recover(session)
                for task in lost_tasks:
                    session.get(Window, task["collection_id"]).available_at = 0
            workers = ["EMEA-SAT-2"]
            tasks = [task for task in tasks if task["worker_id"] != "EMEA-SAT-1"]
        for task in tasks:
            svc.result(
                task["task_id"],
                task["worker_id"],
                {"status": "SUCCESS", "summary": "Collected"},
            )
            assert task["collection_id"] not in done
            done.add(task["collection_id"])
    with db.Session() as session:
        assert (
            session.scalar(
                select(func.count(Window.id)).where(Window.status == "SUCCESS")
            )
            == hosts * 5
        )
        for task in lost_tasks:
            assert session.get(Attempt, task["task_id"]).status == "WORKER_OFFLINE"
            attempts = list(
                session.scalars(
                    select(Attempt)
                    .where(Attempt.window_id == task["collection_id"])
                    .order_by(Attempt.number)
                )
            )
            assert len(attempts) == 2 and attempts[-1].worker_id == "EMEA-SAT-2"
        assert all(
            c.last_success_at > 0
            for c in session.scalars(
                select(Collector).where(Collector.scope == "Capacity")
            )
        )
