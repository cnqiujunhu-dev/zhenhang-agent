from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from zhenxing.app import create_app
from zhenxing.database import create_database
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import AuditEvent, Task, User
from zhenxing.runtime import accept_task
from zhenxing.runtime_models import RuntimeRecord, RuntimeTaskRequest, WorkflowJob
from zhenxing.schema import SCHEMA_VERSION, ensure_schema
from zhenxing.services import Principal


@pytest.fixture
def requests(tmp_path):
    application = create_app(f"sqlite:///{(tmp_path / 'requests.db').as_posix()}", ModelGateway(RuleIntentModel()))
    with TestClient(application) as http:
        demo = http.post("/api/v1/demo/sessions").json()
        headers = {"Authorization": f"Bearer {demo['token']}"}
        yield http, headers, application.state.session_factory, demo


def post(http, headers, key=None, message="给房东转100元"):
    return http.post("/api/v2/tasks", headers={**headers, **({"Idempotency-Key": key} if key is not None else {})},
                     json={"message": message})


def test_replay_survives_sessions_without_duplicate_audit_or_plan_job(requests):
    http, headers, factory, _demo = requests
    first = post(http, headers, "submit-1")
    replay = post(http, headers, "submit-1")
    assert first.status_code == replay.status_code == 202
    assert first.json()["task_id"] == replay.json()["task_id"]
    assert first.json()["request_id"] == replay.json()["request_id"]
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(Task)) == 1
        assert database.scalar(select(func.count()).select_from(RuntimeRecord)) == 1
        assert database.scalar(select(func.count()).select_from(RuntimeTaskRequest)) == 1
        assert database.scalar(select(func.count()).select_from(WorkflowJob)) == 1
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_key_conflict_is_409_and_original_task_is_unchanged(requests):
    http, headers, factory, _demo = requests
    original = post(http, headers, "immutable-payload")
    conflict = post(http, headers, "immutable-payload", message="给房东转200元")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    with factory() as database:
        assert database.get(Task, original.json()["task_id"]).message == "给房东转100元"
        assert database.scalar(select(func.count()).select_from(Task)) == 1
        assert database.scalar(select(func.count()).select_from(WorkflowJob)) == 1


def test_replay_uses_original_payload_after_task_revision(requests):
    http, headers, factory, _demo = requests
    original = post(http, headers, "original-input").json()
    with factory.begin() as database:
        task = database.get(Task, original["task_id"])
        task.message = "用户后续补充后的需求"
        task.state = "NEED_CLARIFICATION"
        task.version = 3
    replay = post(http, headers, "original-input")
    assert replay.status_code == 202
    assert replay.json()["task_id"] == original["task_id"]
    assert replay.json()["state"] == "NEED_CLARIFICATION"
    assert replay.json()["version"] == 3
    assert post(http, headers, "original-input", message="用户后续补充后的需求").status_code == 409


def test_key_scope_isolates_sandboxes_and_users(requests):
    http, headers, factory, first_demo = requests
    other_demo = http.post("/api/v1/demo/sessions").json()
    other_headers = {"Authorization": f"Bearer {other_demo['token']}"}
    first = post(http, headers, "shared-client-key").json()
    other = post(http, other_headers, "shared-client-key", message="只分析账单").json()
    assert first["task_id"] != other["task_id"]
    assert http.get(f"/api/v2/tasks/{other['task_id']}/trace", headers=headers).status_code == 404
    with factory() as database:
        rows = list(database.scalars(select(RuntimeTaskRequest)))
        assert {row.user_id for row in rows} == {first_demo["user_id"], other_demo["user_id"]}
        assert len({row.sandbox_id for row in rows}) == 2


def test_same_sandbox_users_have_separate_request_keys(requests):
    http, headers, factory, demo = requests
    first = post(http, headers, "same-sandbox-key").json()
    with factory.begin() as database:
        another_user = User(sandbox_id=demo["sandbox_id"], display_name="另一个合成用户")
        database.add(another_user)
        database.flush()
        other = accept_task(database, Principal(another_user.id, demo["sandbox_id"]),
                            "只分析账单", "same-sandbox-key")
        assert other.id != first["task_id"]
    with factory() as database:
        rows = list(database.scalars(select(RuntimeTaskRequest)))
        assert len({row.sandbox_id for row in rows}) == 1
        assert len({row.user_id for row in rows}) == 2


@pytest.mark.parametrize("key", ["", "has spaces", "x" * 121])
def test_invalid_key_does_not_persist_a_task(requests, key):
    http, headers, factory, _demo = requests
    result = post(http, headers, key)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(Task)) == 0


def test_no_key_and_old_three_argument_call_remain_independent(requests):
    http, headers, factory, demo = requests
    first = post(http, headers).json()
    second = post(http, headers).json()
    assert first["task_id"] != second["task_id"]
    with factory.begin() as database:
        third = accept_task(database, Principal(demo["user_id"], demo["sandbox_id"]), "只分析账单")
        assert third.id not in {first["task_id"], second["task_id"]}
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(Task)) == 3
        assert database.scalar(select(func.count()).select_from(RuntimeTaskRequest)) == 0


def test_caller_rollback_removes_all_acceptance_effects(requests):
    _http, _headers, factory, demo = requests
    with factory() as database:
        task = accept_task(database, Principal(demo["user_id"], demo["sandbox_id"]), "只分析账单", "rolled-back")
        task_id = task.id
        database.rollback()
    with factory() as database:
        assert database.get(Task, task_id) is None
        assert database.scalar(select(func.count()).select_from(RuntimeTaskRequest)) == 0
        assert database.scalar(select(func.count()).select_from(RuntimeRecord)) == 0
        assert database.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert database.scalar(select(func.count()).select_from(WorkflowJob)) == 0


def test_parallel_replay_creates_one_task_and_one_planning_job(requests):
    http, headers, factory, _demo = requests
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(http, headers, "parallel-submit"), range(8)))
    assert all(result.status_code == 202 for result in results)
    assert len({result.json()["task_id"] for result in results}) == 1
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(Task)) == 1
        assert database.scalar(select(func.count()).select_from(RuntimeTaskRequest)) == 1
        assert database.scalar(select(func.count()).select_from(WorkflowJob)) == 1


def test_persistent_unique_constraint_rejects_bypassing_request_service(requests):
    http, headers, factory, demo = requests
    original = post(http, headers, "database-owned-key").json()
    another = post(http, headers).json()
    with factory() as database:
        database.add(RuntimeTaskRequest(sandbox_id=demo["sandbox_id"], user_id=demo["user_id"],
                                       idempotency_key="database-owned-key", request_hash="0" * 64,
                                       task_id=another["task_id"]))
        with pytest.raises(IntegrityError):
            database.commit()
        database.rollback()
        assert database.scalar(select(RuntimeTaskRequest.task_id)) == original["task_id"]


def test_existing_v7_schema_adds_request_table_without_resetting_tasks(tmp_path):
    path = tmp_path / "v7.db"
    engine, factory = create_database(f"sqlite:///{path.as_posix()}")
    ensure_schema(engine)
    from zhenxing.services import create_session
    with factory.begin() as database:
        demo = create_session(database)
        task_id = accept_task(database, Principal(demo["user_id"], demo["sandbox_id"]), "只分析账单").id
    with engine.begin() as connection:
        RuntimeTaskRequest.__table__.drop(connection)
        connection.execute(text("DELETE FROM schema_migrations WHERE version > 7"))
        connection.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES(7, 'v7-fixture')"))
    ensure_schema(engine)
    assert "runtime_task_requests" in inspect(engine).get_table_names()
    with factory() as database:
        assert database.get(Task, task_id).message == "只分析账单"
        assert database.scalar(text("SELECT MAX(version) FROM schema_migrations")) == SCHEMA_VERSION
    engine.dispose()
