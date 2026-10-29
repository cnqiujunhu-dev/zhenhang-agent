"""Disposable SQLite fixtures prove runner lifecycle; never production readiness."""
import json

import pytest
from sqlalchemy import func, select

from zhenxing.embedding_gateway import EmbeddingResult, EmbeddingVector
from zhenxing.knowledge_admin_api import probe_local_embedding_health
from zhenxing.knowledge_index_runner import (KnowledgeIndexAttempt, RunnerLeaseLost, acquire_runner_lease,
    ensure_runner_schema, process_queued_once, recover_interrupted, run_forever, runner_status)
from zhenxing.knowledge_ingest import create_index_job, retry_index_job
from zhenxing.knowledge_models import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexJob, KnowledgeRevision
from zhenxing.knowledge_store import bootstrap_public_corpus, init_knowledge_store, retire_document


@pytest.fixture
def store(tmp_path):
    engine, factory = init_knowledge_store(f"sqlite:///{(tmp_path / 'runner-public.db').as_posix()}")
    ensure_runner_schema(engine)
    bootstrap_public_corpus(factory, [{"document_id": "runner:guide", "version": "v1", "title": "公开指南",
        "body": "查询与比较不会付款，资金操作需要另行确认。", "source": "knowledge:runner-guide", "lang": "zh-CN"}])
    with factory.begin() as session:
        job = create_index_job(session); job_id = job.id
    yield factory, job_id
    engine.dispose()


def embed(texts):
    return EmbeddingResult("READY", vectors=tuple(EmbeddingVector((1., 2., 3.), "fixture-public-model", "a" * 64) for _ in texts),
                           model="fixture-public-model", dimension=3, model_revision="a" * 64, max_tokens=128)


def test_production_runner_does_not_accept_sqlite_or_a_second_fixture_owner(store):
    factory, _ = store
    with pytest.raises(ValueError, match="REQUIRES_POSTGRESQL"):
        with acquire_runner_lease(factory): pass
    with acquire_runner_lease(factory, allow_sqlite_fixture=True) as first:
        assert first is not None
        with acquire_runner_lease(factory, allow_sqlite_fixture=True) as second:
            assert second is None


def test_queued_job_really_executes_and_records_attempt_without_claiming_fixture_online(store):
    factory, job_id = store
    result = run_forever(factory, once=True, allow_sqlite_fixture=True, bootstrap=False, embedder=embed)
    assert result == {"state": "STOPPED", "processed": 1}
    with factory() as session:
        assert session.get(KnowledgeIndexJob, job_id).state == "SUCCEEDED"
        attempt = session.scalar(select(KnowledgeIndexAttempt))
        assert attempt.state == "SUCCEEDED" and attempt.finished_at
        assert session.scalar(select(KnowledgeChunk)).embedding == [1., 2., 3.]
        status = runner_status(session)
        assert status["fixture_only"] and not status["online"] and not status["production"]


def test_failure_is_terminal_and_not_automatically_retried(store):
    factory, job_id = store
    failure = lambda _texts: EmbeddingResult("ERROR", error_code="EMBEDDING_TIMEOUT")
    assert run_forever(factory, once=True, allow_sqlite_fixture=True, bootstrap=False, embedder=failure)["processed"] == 1
    assert run_forever(factory, once=True, allow_sqlite_fixture=True, bootstrap=False, embedder=embed)["processed"] == 0
    with factory.begin() as session:
        original = session.get(KnowledgeIndexJob, job_id)
        assert original.state == "FAILED" and original.error_code == "EMBEDDING_TIMEOUT"
        assert session.scalar(select(func.count()).select_from(KnowledgeIndexAttempt)) == 1
        retry = retry_index_job(session, job_id)
        assert retry.id != original.id and retry.state == "QUEUED"


def test_lock_loss_during_inference_never_activates_and_restart_creates_new_job(store):
    factory, job_id = store
    with acquire_runner_lease(factory, allow_sqlite_fixture=True) as lease:
        def interrupted(texts):
            result = embed(texts); lease.valid = False; return result
        with pytest.raises(RunnerLeaseLost):
            process_queued_once(factory, lease, "fixture-instance-a", embedder=interrupted)
    with factory() as session:
        assert session.get(KnowledgeIndexJob, job_id).state == "RUNNING"
        assert session.scalar(select(KnowledgeChunk)).embedding is None
    with acquire_runner_lease(factory, allow_sqlite_fixture=True) as lease:
        assert recover_interrupted(factory, lease) == 1
        result = process_queued_once(factory, lease, "fixture-instance-b", embedder=embed)
        assert result["state"] == "SUCCEEDED" and result["id"] != job_id
    with factory() as session:
        assert session.get(KnowledgeIndexJob, job_id).state == "INTERRUPTED"
        assert {attempt.state for attempt in session.scalars(select(KnowledgeIndexAttempt))} == {"INTERRUPTED", "SUCCEEDED"}


def test_retired_source_after_interruption_is_not_requeued(store):
    factory, job_id = store
    with acquire_runner_lease(factory, allow_sqlite_fixture=True) as lease:
        def interrupted(texts):
            lease.valid = False; return embed(texts)
        with pytest.raises(RunnerLeaseLost): process_queued_once(factory, lease, "fixture-instance-a", embedder=interrupted)
    with factory.begin() as session:
        document = session.scalar(select(KnowledgeDocument)); retire_document(session, document.id, "Public source withdrawn")
    with acquire_runner_lease(factory, allow_sqlite_fixture=True) as lease:
        assert recover_interrupted(factory, lease) == 1
        assert process_queued_once(factory, lease, "fixture-instance-b", embedder=embed) is None
    with factory() as session:
        assert session.get(KnowledgeIndexJob, job_id).state == "INTERRUPTED"
        assert session.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 1


def test_bootstrap_runner_creates_corpus_and_initial_job_once(tmp_path):
    from zhenxing.knowledge_corpus import corpus_seed
    from zhenxing.product_knowledge import KNOWLEDGE_VERSION, KNOWLEDGE_ENTRY_VERSIONS, DOMAIN_POLICY_IDS
    seeds = corpus_seed()
    assert len(seeds) == len({row['document_id'] for row in seeds}) == 54
    payment_ids = {key for group in DOMAIN_POLICY_IDS.values() for key in group}
    assert len(payment_ids) == 8
    expected = {row['document_id']:row for row in seeds}
    engine, factory = init_knowledge_store(f"sqlite:///{(tmp_path / 'bootstrap-public.db').as_posix()}")
    result = run_forever(factory, once=True, allow_sqlite_fixture=True, embedder=embed)
    assert result["processed"] == 1
    with factory() as session:
        documents = list(session.scalars(select(KnowledgeDocument)))
        assert {row.document_key for row in documents} == set(expected)
        assert len(documents) == 54
        before = {}
        for row in documents:
            revision = session.get(KnowledgeRevision, row.active_revision_id)
            seed = expected[row.document_key]
            assert revision.version_label == seed['version'] and revision.content == seed['body']
            if row.document_key.startswith('policy:'):
                identity = row.document_key.removeprefix('policy:').rsplit(':',1)[0]
                assert revision.version_label == KNOWLEDGE_ENTRY_VERSIONS.get(identity, KNOWLEDGE_VERSION)
            before[row.document_key] = (row.id, row.active_revision_id, revision.content_hash, revision.version_label)
        assert session.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 1
    assert run_forever(factory, once=True, allow_sqlite_fixture=True, embedder=embed)["processed"] == 0
    with factory() as session:
        after = {row.document_key:(row.id, row.active_revision_id,
            session.get(KnowledgeRevision,row.active_revision_id).content_hash,
            session.get(KnowledgeRevision,row.active_revision_id).version_label)
            for row in session.scalars(select(KnowledgeDocument))}
        assert after == before
        assert session.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 1
    engine.dispose()


@pytest.mark.parametrize("base", ["http://example.com/v1", "http://embedding:8001/v1", "http://127.0.0.1:80/private", "http://user:key@127.0.0.1/v1", "http://127.0.0.1/v1?api_key=private"])
def test_local_health_rejects_arbitrary_or_credential_urls_without_network(monkeypatch, base):
    monkeypatch.setenv("ZX_EMBEDDING_PROVIDER", "local_http"); monkeypatch.setenv("ZX_EMBEDDING_BASE_URL", base); monkeypatch.setenv("ZX_EMBEDDING_MODEL", "fixture-public-model")
    class Forbidden:
        def stream(self, *_args, **_kwargs): raise AssertionError("Must not fetch an arbitrary health URL")
    result = probe_local_embedding_health(client=Forbidden())
    assert result["status"] == "ERROR" and result["error_code"] == "EMBEDDING_ENDPOINT_INVALID"
    assert base not in json.dumps(result)


def test_real_health_projection_verifies_loaded_fingerprint_without_echoing_extra_fields(monkeypatch):
    import httpx
    monkeypatch.setenv("ZX_EMBEDDING_PROVIDER", "local_http"); monkeypatch.setenv("ZX_EMBEDDING_BASE_URL", "http://embedding:8000/v1")
    monkeypatch.setenv("ZX_EMBEDDING_MODEL", "fixture-public-model")
    monkeypatch.delenv("ZX_EMBEDDING_MODEL_REVISION", raising=False); monkeypatch.delenv("ZX_EMBEDDING_DIMENSION", raising=False)
    def transport(request):
        assert request.method == "GET" and str(request.url) == "http://embedding:8000/health" and "authorization" not in request.headers
        return httpx.Response(200, json={"status": "READY", "model": "fixture-public-model", "dimension": 3,
            "model_revision": "b" * 64, "chat_model": False, "generation_supported": False, "extra_secret": "sk-ARTIFICIAL_QA_ONLY"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = probe_local_embedding_health(client=client)
    assert result["status"] == "READY" and result["model_revision"] == "b" * 64
    assert "sk-ARTIFICIAL" not in json.dumps(result)
