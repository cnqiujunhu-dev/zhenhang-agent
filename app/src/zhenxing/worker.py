from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import create_database
from .models import OutboxMessage, Task
from .schema import ensure_schema
from .model_gateway import ModelGateway
from .runtime_health import Heartbeat


logger = logging.getLogger(__name__)


def positive_finite_interval(value: str) -> float:
    """A CLI idle delay, not a busy-loop or non-finite sleep instruction."""
    try:
        interval = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("interval must be a positive finite number") from None
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive finite number")
    return interval


def drain_outbox(session: Session, batch_size: int = 50) -> int:
    """Materialize committed task completions into the user's WEB inbox."""
    messages = list(
        session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.status == "PENDING")
            .order_by(OutboxMessage.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    delivered = 0
    for message in messages:
        task = session.scalar(select(Task).where(
            Task.id == message.task_id,
            Task.sandbox_id == message.sandbox_id,
        ))
        if task is None or task.state not in {"SUCCEEDED", "CLOSED"}:
            message.status = "FAILED"
            logger.error("outbox_failed id=%s task_id=%s", message.id, message.task_id)
            continue
        # The outbox row is the durable WEB inbox item once marked delivered.
        # If this transaction rolls back, it stays PENDING and another Worker can retry.
        message.status = "DELIVERED"
        delivered += 1
    return delivered


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="圳行Agent first-round outbox worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=positive_finite_interval, default=1.0)
    args = parser.parse_args()
    engine, factory = create_database()
    ensure_schema(engine)
    gateway = ModelGateway.from_environment()
    worker_id = f"worker-{os.getpid()}-{uuid4().hex}"
    heartbeat = Heartbeat(factory, worker_id)
    old_sigterm = signal.getsignal(signal.SIGTERM)

    def stop_on_signal(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_on_signal)
    shadow_worker = None
    try:
        heartbeat.start()
        if not args.once:
            try:
                from .semantic_shadow import start_worker
                shadow_worker = start_worker(factory, getattr(gateway, 'advisor', None))
            except Exception:
                logger.warning('semantic_shadow_worker_unavailable')
        next_queue = 0
        while True:
            from .runtime import run_one_job
            from .conversation_service import process_chat_job
            from .attachments import process_attachment_job
            # Rotate queues after each handled item, avoiding a batch of slow
            # vision calls before routine cleanup. Network stays outside DB tx.
            processors = [lambda: run_one_job(factory, gateway, worker_id=worker_id),
                lambda: process_chat_job(factory, gateway, worker_id=worker_id),
                lambda: process_attachment_job(factory, worker_id=worker_id)]
            handled = False
            for offset in range(len(processors)):
                index = (next_queue + offset) % len(processors)
                if processors[index]():
                    handled = True
                    next_queue = (index + 1) % len(processors)
                    break
            with factory.begin() as session:
                from .banking_extensions import execute_due_schedules
                from .runtime import expire_runtime_tasks, refresh_waiting_tasks
                expire_runtime_tasks(session)
                due = execute_due_schedules(session)
                from .life_plan_worker import run_due_life_plans
                from .merchant_runtime import run_due_merchant_orders
                due += run_due_life_plans(session)
                due += run_due_merchant_orders(session)
                from .credit_cycle_service import run_due_credit_cycles
                credit_maintenance = run_due_credit_cycles(session)
                due += credit_maintenance['closed_statements'] + credit_maintenance['expired_authorizations']
                refresh_waiting_tasks(session)
                from .attachment_retention import cleanup_attachment_retention
                cleanup_attachment_retention(session)
                count = drain_outbox(session)
            if count:
                logger.info("web_inbox_materialized count=%s", count)
            if args.once:
                print(f"handled={int(handled)} due={due} delivered={count}")
                return
            # A completed item may have queued its next durable stage. Drain
            # available work fairly without adding an artificial stage delay.
            # Every iteration still runs all financial/retention maintenance.
            if not handled and not due and not count:
                time.sleep(args.interval)
    finally:
        if shadow_worker is not None:
            try:
                shadow_worker.stop()
            except Exception:
                logger.warning('semantic_shadow_worker_stop_unavailable')
        heartbeat.stop()
        signal.signal(signal.SIGTERM, old_sigterm)
        engine.dispose()


if __name__ == "__main__":
    main()
