"""Durable public-knowledge index worker; no bank connection or chat API.

Production uses a PostgreSQL session advisory singleton lock. Inference runs
without a DB transaction; activation is on the same lock-owning connection.
SQLite is accepted only by explicit test fixtures, never the production CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import signal
import threading
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from .embedding_gateway import EmbeddingResult, embed_texts
from .knowledge_ingest import (apply_index_batch, create_index_job, ensure_vector_index,
                               heartbeat_index_job, prepare_index_batch, retry_index_job)
from .knowledge_models import KnowledgeBase, KnowledgeIndexJob, knowledge_now
from .knowledge_store import KnowledgeError, aware, bootstrap_public_corpus, init_knowledge_store, job_public, record_knowledge_audit

LOCK_NAMESPACE, LOCK_ID = 100004221, 20261007
RUNNER_KEY = "public-knowledge-index-worker"
_fixture_locks = {}
_fixture_guard = threading.Lock()


class KnowledgeRunnerState(KnowledgeBase):
    __tablename__ = "knowledge_runner_state"
    runner_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(24))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class KnowledgeIndexAttempt(KnowledgeBase):
    __tablename__ = "knowledge_index_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_jobs.id"), index=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(24), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RunnerLeaseLost(RuntimeError):
    pass


def ensure_runner_schema(engine):
    KnowledgeRunnerState.__table__.create(engine, checkfirst=True)
    KnowledgeIndexAttempt.__table__.create(engine, checkfirst=True)


class RunnerLease:
    def __init__(self, factory, connection=None, fixture_lock=None, backend_pid=None):
        self.factory, self.connection, self.fixture_lock = factory, connection, fixture_lock
        self.backend_pid = backend_pid
        self.valid = True

    def require(self, session=None):
        if not self.valid:
            raise RunnerLeaseLost("KNOWLEDGE_RUNNER_LOCK_LOST")
        if self.connection is None:
            return  # Only an explicitly admitted SQLite fixture reaches here.
        query = text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=:namespace AND objid=:lock AND granted)")
        try:
            if session is not None:
                owned = session.scalar(query, {"namespace": LOCK_NAMESPACE, "lock": LOCK_ID})
            else:
                with self.connection.begin():
                    owned = self.connection.scalar(query, {"namespace": LOCK_NAMESPACE, "lock": LOCK_ID})
            if not owned:
                raise RunnerLeaseLost("KNOWLEDGE_RUNNER_LOCK_LOST")
        except Exception:
            self.valid = False
            raise RunnerLeaseLost("KNOWLEDGE_RUNNER_LOCK_LOST") from None

    def observed_on(self, session):
        if not self.valid: return False
        if self.connection is None: return True
        return bool(session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=:pid AND classid=:namespace AND objid=:lock AND granted)"),
                                   {"pid": self.backend_pid, "namespace": LOCK_NAMESPACE, "lock": LOCK_ID}))

    @contextmanager
    def activation(self):
        # PostgreSQL activation shares the live advisory-lock connection: a
        # broken connection cannot silently reconnect and commit unleased work.
        session = Session(bind=self.connection, expire_on_commit=False) if self.connection is not None else self.factory()
        try:
            with session.begin():
                self.require(session)
                yield session
        finally:
            session.close()


@contextmanager
def acquire_runner_lease(factory, *, allow_sqlite_fixture=False):
    engine = factory.kw["bind"]
    if engine.dialect.name == "sqlite" and allow_sqlite_fixture:
        with _fixture_guard:
            lock = _fixture_locks.setdefault(str(engine.url), threading.Lock())
        acquired = lock.acquire(blocking=False)
        try:
            yield RunnerLease(factory, fixture_lock=lock) if acquired else None
        finally:
            if acquired:
                lock.release()
        return
    if engine.dialect.name != "postgresql":
        raise ValueError("KNOWLEDGE_RUNNER_REQUIRES_POSTGRESQL")
    connection = engine.connect()
    lease = None
    try:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:namespace,:lock)"), {"namespace": LOCK_NAMESPACE, "lock": LOCK_ID})
        backend_pid = connection.scalar(text("SELECT pg_backend_pid()"))
        connection.commit()  # Session lock survives; no transaction during inference.
        if acquired:
            lease = RunnerLease(factory, connection=connection, backend_pid=backend_pid)
        yield lease
    finally:
        if lease:
            lease.valid = False
            try:
                if connection.in_transaction(): connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:namespace,:lock)"), {"namespace": LOCK_NAMESPACE, "lock": LOCK_ID})
                connection.commit()
            except Exception:
                pass
        connection.close()


def heartbeat(factory, instance_id, *, state="RUNNING", job_id=None, error_code=None):
    with factory.begin() as session:
        row = session.get(KnowledgeRunnerState, RUNNER_KEY)
        if row is not None and row.instance_id != instance_id and state != "STARTING":
            return  # Never let an old worker's finalizer overwrite its successor.
        if row is None:
            row = KnowledgeRunnerState(runner_key=RUNNER_KEY, instance_id=instance_id,
                started_at=knowledge_now(), heartbeat_at=knowledge_now(), state=state)
            session.add(row)
        elif state == "STARTING":
            row.instance_id, row.started_at = instance_id, knowledge_now()
        row.state, row.current_job_id, row.error_code, row.heartbeat_at = state, job_id, error_code, knowledge_now()


def runner_status(session):
    row = session.get(KnowledgeRunnerState, RUNNER_KEY)
    if row is None:
        return {"state": "NOT_OBSERVED", "online": False, "production": session.bind.dialect.name == "postgresql"}
    age = (knowledge_now() - aware(row.heartbeat_at)).total_seconds()
    production = session.bind.dialect.name == "postgresql"
    lock_held = production and bool(session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=:namespace AND objid=:lock AND granted)"),
                                                  {"namespace": LOCK_NAMESPACE, "lock": LOCK_ID}))
    online = production and lock_held and row.state in {"STARTING", "RUNNING"} and 0 <= age <= 20
    return {"state": row.state if age <= 20 else "STALE", "online": online, "production": production,
            "heartbeat_at": aware(row.heartbeat_at).isoformat(), "current_job_id": row.current_job_id,
            "error_code": row.error_code, "fixture_only": not production, "lock_held": bool(lock_held)}


def recover_interrupted(factory, lease):
    lease.require()
    recovered = 0
    with lease.activation() as session:
        for job in session.scalars(select(KnowledgeIndexJob).where(KnowledgeIndexJob.state == "RUNNING").with_for_update()):
            for attempt in session.scalars(select(KnowledgeIndexAttempt).where(KnowledgeIndexAttempt.job_id == job.id, KnowledgeIndexAttempt.state == "RUNNING")):
                attempt.state, attempt.finished_at, attempt.error_code = "INTERRUPTED", knowledge_now(), "KNOWLEDGE_RUNNER_INTERRUPTED"
            # Public vectors activate atomically; an uncommitted attempt can be
            # recomputed. Failed/stale/retired jobs are not automatically retried.
            job.state, job.finished_at, job.error_code, job.lease_token = "INTERRUPTED", knowledge_now(), "KNOWLEDGE_RUNNER_INTERRUPTED", None
            session.flush()
            try:
                replacement = retry_index_job(session, job.id)
                record_knowledge_audit(session, actor_account_id=RUNNER_KEY, action="REINDEX", reason="New recovery job " + replacement.id + " for interrupted job " + job.id)
            except KnowledgeError:
                # A source retired while the worker was offline remains a
                # terminal interrupted record, not an endless retry loop.
                record_knowledge_audit(session, actor_account_id=RUNNER_KEY, action="REINDEX", reason="Interrupted source no longer retryable " + job.id)
            recovered += 1
    return recovered


def process_queued_once(factory, lease, instance_id, *, embedder=None):
    lease.require()
    with factory.begin() as session:
        job = session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.state == "QUEUED").order_by(KnowledgeIndexJob.created_at, KnowledgeIndexJob.id).limit(1))
        if job is None:
            return None
        job_id = job.id
        job, items = prepare_index_batch(session, job_id)
        if job.state != "RUNNING" or items is None:
            return job_public(job)
        attempt = KnowledgeIndexAttempt(job_id=job.id, instance_id=instance_id, state="RUNNING")
        session.add(attempt); session.flush(); attempt_id = attempt.id
        lease_token = job.lease_token
    heartbeat(factory, instance_id, job_id=job_id)
    inference = embedder or embed_texts
    result, vectors = EmbeddingResult("ERROR", error_code="KNOWLEDGE_INDEX_EMPTY"), []
    if items:
        result = None
        for offset in range(0, len(items), 32):
            lease.require()
            with factory.begin() as session:
                heartbeat_index_job(session, job_id, lease_token)
            try:
                batch = inference([item.content for item in items[offset:offset + 32]])
            except Exception:
                batch = EmbeddingResult("ERROR", error_code="EMBEDDING_JOB_UNEXPECTED_ERROR")
            lease.require()  # No result can activate after ownership is lost.
            if not isinstance(batch, EmbeddingResult):
                batch = EmbeddingResult("ERROR", error_code="EMBEDDING_JOB_RESULT_INVALID")
            if batch.status != "READY":
                result = batch; break
            if result and (batch.model, batch.dimension, batch.model_revision, batch.max_tokens) != (result.model, result.dimension, result.model_revision, result.max_tokens):
                result = EmbeddingResult("ERROR", error_code="EMBEDDING_BATCH_MODEL_MISMATCH"); break
            result = batch; vectors.extend(batch.vectors)
        else:
            result = EmbeddingResult("READY", vectors=tuple(vectors), model=result.model,
                                     dimension=result.dimension, model_revision=result.model_revision, max_tokens=result.max_tokens)
    try:
        with lease.activation() as session:
            job = apply_index_batch(session, job_id, items, result, lease_token=lease_token)
            attempt = session.get(KnowledgeIndexAttempt, attempt_id)
            attempt.state, attempt.finished_at, attempt.error_code = job.state, knowledge_now(), job.error_code
            public = job_public(job)
    except KnowledgeError as error:
        with lease.activation() as session:
            job = session.get(KnowledgeIndexJob, job_id)
            if not job or job.state != "RUNNING" or job.lease_token != lease_token:
                raise RunnerLeaseLost("KNOWLEDGE_RUNNER_JOB_LEASE_LOST") from None
            job.state, job.finished_at, job.error_code, job.lease_token = "FAILED", knowledge_now(), error.code, None
            attempt = session.get(KnowledgeIndexAttempt, attempt_id)
            attempt.state, attempt.finished_at, attempt.error_code = "FAILED", knowledge_now(), error.code
            public = job_public(job)
    if public["state"] == "SUCCEEDED":
        lease.require()
        try:
            public["vector_index"] = ensure_vector_index(factory.kw["bind"], model=public["model"],
                dimension=public["dimension"], model_revision=public["model_revision"])
        except Exception:
            public["vector_index"] = {"status": "EXACT_ONLY_INDEX_ERROR", "error_code": "VECTOR_INDEX_CREATE_FAILED"}
        lease.require()
    heartbeat(factory, instance_id)
    return public


def run_forever(factory, *, stop_event=None, poll_seconds=2, once=False, allow_sqlite_fixture=False, bootstrap=True, embedder=None):
    stop = stop_event or threading.Event()
    ensure_runner_schema(factory.kw["bind"])
    instance_id = str(uuid4())
    with acquire_runner_lease(factory, allow_sqlite_fixture=allow_sqlite_fixture) as lease:
        if lease is None:
            return {"state": "LOCK_BUSY", "processed": 0}
        heartbeat(factory, instance_id, state="STARTING")
        recover_interrupted(factory, lease)
        if bootstrap:
            bootstrap_public_corpus(factory)
            with factory.begin() as session:
                if session.scalar(select(KnowledgeIndexJob.id).limit(1)) is None:
                    create_index_job(session)
        heartbeat(factory, instance_id)
        pulse_stop = threading.Event()
        def pulse():
            while not pulse_stop.wait(5):
                try:
                    with factory() as session:
                        if not lease.observed_on(session):
                            lease.valid = False
                            pulse_stop.set()
                            return
                        row = session.get(KnowledgeRunnerState, RUNNER_KEY)
                        job_id = row.current_job_id if row and row.instance_id == instance_id else None
                    heartbeat(factory, instance_id, job_id=job_id)
                except Exception:
                    pulse_stop.set()
        thread = threading.Thread(target=pulse, name="knowledge-index-heartbeat", daemon=True); thread.start()
        processed, final_state = 0, "STOPPED"
        try:
            while not stop.is_set():
                if pulse_stop.is_set(): raise RunnerLeaseLost("KNOWLEDGE_RUNNER_HEARTBEAT_FAILED")
                result = process_queued_once(factory, lease, instance_id, embedder=embedder)
                if result is not None: processed += 1
                if once: break
                if result is None: stop.wait(max(.1, min(poll_seconds, 30)))
        except RunnerLeaseLost:
            final_state = "LOCK_LOST"
        except Exception:
            final_state = "ERROR"
        finally:
            pulse_stop.set(); thread.join(timeout=6)
            try: heartbeat(factory, instance_id, state=final_state, error_code=None if final_state == "STOPPED" else "KNOWLEDGE_RUNNER_STOPPED_UNEXPECTEDLY")
            except Exception: pass
        return {"state": final_state, "processed": processed}


def main():
    parser = argparse.ArgumentParser(description="Run the separate public-knowledge index worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set()); signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        _engine, factory = init_knowledge_store()
        result = run_forever(factory, stop_event=stop, once=args.once)
        print("knowledge-index-worker " + result["state"])
        return 0 if result["state"] in {"STOPPED", "LOCK_BUSY"} else 1
    except Exception:
        print("knowledge-index-worker ERROR")  # Never print connection URLs or keys.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
