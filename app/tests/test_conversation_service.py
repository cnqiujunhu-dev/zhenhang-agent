"""Isolated CPU chat tests: no live model and never consume bank confirmation."""
import pytest
from sqlalchemy import func, select

from zhenxing.database import create_database
from zhenxing.schema import ensure_schema
from zhenxing.services import Principal, create_session
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.conversation_service import create_conversation, submit_turn, process_chat_job, conversation_view, close_conversation
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import Account, Confirmation, Task, Transfer
from zhenxing.runtime import run_one_job, task_trace
from zhenxing.contracts import DomainError


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("ZX_LAYA_MODE", "OFF")
    engine, factory = create_database(f"sqlite:///{(tmp_path / 'chat.db').as_posix()}")
    ensure_schema(engine)
    with factory.begin() as db:
        actor = create_session(db, "baseline")
        principal = Principal(actor["user_id"], actor["sandbox_id"])
        thread = create_conversation(db, principal)
        identifier = thread.id
    yield factory, ModelGateway(RuleIntentModel()), principal, identifier
    engine.dispose()


def turn(chat, message, **kwargs):
    factory, gateway, principal, identifier = chat
    with factory.begin() as db:
        row = submit_turn(db, principal, identifier, message=message, client_request_id=kwargs.pop("client_request_id", "request-" + str(db.scalar(select(func.count()).select_from(ChatTurn)))), **kwargs)
        turn_id = row.id
    assert process_chat_job(factory, gateway)
    with factory() as db:
        row = db.get(ChatTurn, turn_id)
        return row


def plan(chat, message):
    row = turn(chat, message)
    assert row.mode == "NEW_TASK" and row.status == "ANSWERED", row.answer
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        return task_trace(db, chat[2], row.task_id)


@pytest.mark.parametrize("message", ["怎么取消视频会员？", "美元可以吗", "对方可以直接收美元吗", "为什么目标预留不减少总余额？"])
def test_consultations_answer_sources_without_creating_task_or_bank_write(chat, message):
    row = turn(chat, message)
    assert row.status == "ANSWERED" and row.mode == "CONSULT", (row.status, row.error_code, row.answer)
    assert row.sources_json and row.task_id is None
    if message == "对方可以直接收美元吗":
        assert "人民币和美元分别记账" in row.answer and "当次报价为准" in row.answer
    elif message == "为什么目标预留不减少总余额？":
        assert "现金总余额不变" in row.answer and "能花的钱会减少" in row.answer
    else:
        assert "未执行银行操作" in row.answer
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
        assert db.scalar(select(func.count()).select_from(Confirmation)) == 0
        from zhenxing.models import ToolCall
        assert db.scalar(select(func.count()).select_from(ToolCall)) == 0


def test_new_request_original_text_and_money_continuation_preserve_scope_no_confirm(chat):
    old = plan(chat, "给房东转账")
    assert old["state"] == "NEED_CLARIFICATION"
    row = turn(chat, "200", current_task_id=old["task_id"], expected_task_version=old["version"])
    assert row.mode == "REVISE" and row.task_id == old["task_id"], row.answer
    assert row.field_sources_json[0]["sources"][0]["source"] == "USER_CONTEXT"
    assert row.field_sources_json[1]["value"] == 20000
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        trace = task_trace(db, chat[2], old["task_id"])
        assert trace["state"] == "AWAITING_CONFIRMATION", trace
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_amount_revision_replaces_compound_fraction_not_old_fraction_and_invalidates_confirmation(chat):
    old = plan(chat, "给房东转十二元五角六分")
    assert old["state"] == "AWAITING_CONFIRMATION", old
    old_id = old["pending_confirmation"]["confirmation_id"]
    row = turn(chat, "改成300", current_task_id=old["task_id"], expected_task_version=old["version"])
    assert row.mode == "REVISE", row.answer
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        trace = task_trace(db, chat[2], old["task_id"])
        assert "300人民币" in db.get(Task, old["task_id"]).message
        assert "五角" not in db.get(Task, old["task_id"]).message
        assert db.get(Confirmation, old_id).status == "CANCELLED"
        assert trace["pending_confirmation"]["confirmation_id"] != old_id
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


@pytest.mark.parametrize("message,mode", [("确认", "CONFIRM_HINT"), ("好的", "CONFIRM_HINT"), ("还是人民币", "NEED_CONTEXT")])
def test_confirmation_and_currency_fragments_never_consume_confirmation_or_cancel(chat, message, mode):
    old = plan(chat, "给房东转100元")
    row = turn(chat, message, current_task_id=old["task_id"])
    assert row.mode == mode and row.status == "ANSWERED", (row.mode, row.answer)
    with chat[0]() as db:
        assert db.get(Task, old["task_id"]).state == "AWAITING_CONFIRMATION"
        assert db.get(Confirmation, old["pending_confirmation"]["confirmation_id"]).status == "PENDING"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_resource_status_query_not_misrouted_to_last_task_status(chat):
    old = plan(chat, "分析本月账单")
    row = turn(chat, "查看银行卡状态")
    assert row.mode == "NEW_TASK" and row.task_id != old["task_id"], row.answer
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        assert task_trace(db, chat[2], row.task_id)["nodes"][0]["action"] == "CARD_STATUS"


def test_unknown_guards_new_and_short_writes_without_repeating_payment(chat):
    old = plan(chat, "给房东转100元")
    with chat[0].begin() as db:
        db.get(Task, old["task_id"]).state = "UNKNOWN"
    assert turn(chat, "200").mode == "NEED_CONTEXT"
    assert turn(chat, "给房东转100元", context_policy="INDEPENDENT").mode == "NEED_CONTEXT"
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Task).where(Task.intent_type == "UNSUPPORTED")) == 0
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_independent_short_does_not_inherit_context_and_multiple_active_tasks_need_selection(chat):
    first = plan(chat, "给房东转账")
    assert turn(chat, "200", context_policy="INDEPENDENT").mode == "NEED_CONTEXT"
    plan(chat, "给妈妈转账")
    assert turn(chat, "200").mode == "NEED_CONTEXT"
    row = turn(chat, "200", current_task_id=first["task_id"])
    assert row.mode == "REVISE" and row.task_id == first["task_id"]


def test_idempotent_client_request_and_changed_payload_conflict_restore_history(chat):
    factory, gateway, principal, identifier = chat
    with factory.begin() as db:
        one = submit_turn(db, principal, identifier, message="分析账单", client_request_id="stable-request")
        again = submit_turn(db, principal, identifier, message="分析账单", client_request_id="stable-request")
        assert one.id == again.id
        with pytest.raises(DomainError, match="原请求标识"):
            submit_turn(db, principal, identifier, message="锁卡", client_request_id="stable-request")
    assert process_chat_job(factory, gateway)
    assert not process_chat_job(factory, gateway)
    with factory() as db:
        result = conversation_view(db, principal, identifier)
        assert len(result["turns"]) == 1 and result["turns"][0]["task_link"]


def test_other_actor_cannot_read_bind_or_close_thread(chat):
    with chat[0].begin() as db:
        actor = create_session(db, "baseline")
        other = Principal(actor["user_id"], actor["sandbox_id"])
        with pytest.raises(DomainError):
            conversation_view(db, other, chat[3])
        with pytest.raises(DomainError):
            close_conversation(db, other, chat[3])


def test_close_keeps_linked_bank_task_and_blocks_pending_chat(chat):
    old = plan(chat, "给房东转100元")
    with chat[0].begin() as db:
        close_conversation(db, chat[2], chat[3])
        assert db.get(Task, old["task_id"]).state == "AWAITING_CONFIRMATION"
        with pytest.raises(DomainError):
            submit_turn(db, chat[2], chat[3], message="确认", client_request_id="closed-request")


def test_otp_is_not_context_money_or_cloud_material(chat):
    row = turn(chat, "验证码：123456，确认")
    assert row.mode == "CONFIRM_HINT" and "123456" not in row.message


def test_create_conversation_uniqueness_race_recovers_same_row_without_rolling_back_caller(chat, monkeypatch):
    from zhenxing.models import User
    with chat[0].begin() as db:
        existing = create_conversation(db, chat[2], "race", "same-create")
        identifier = existing.id
    with chat[0].begin() as db:
        db.get(User, chat[2].user_id).display_name = "caller-change-preserved"
        scalar = db.scalar
        missed = False
        def racing(query, *args, **kwargs):
            nonlocal missed
            if not missed and "chat_threads" in str(query):
                missed = True
                return None  # Concurrent winner was absent in the first snapshot.
            return scalar(query, *args, **kwargs)
        monkeypatch.setattr(db, "scalar", racing)
        recovered = create_conversation(db, chat[2], "race", "same-create")
        assert recovered.id == identifier and db.is_active
    with chat[0]() as db:
        assert db.get(User, chat[2].user_id).display_name == "caller-change-preserved"
        assert db.scalar(select(func.count()).select_from(ChatThread).where(ChatThread.client_request_id == "same-create")) == 1


@pytest.mark.parametrize("message", ["比较现有理财产品", "比较适合我的理财产品"])
def test_explicit_product_comparison_keeps_original_business_read_workflow(chat, message):
    trace = plan(chat, message)
    assert trace["state"] == "SUCCEEDED", trace
    assert [node["action"] for node in trace["nodes"]] == ["WEALTH_COMPARE"]
    assert trace["nodes"][0]["result"]["products"]
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


@pytest.mark.parametrize("message", ["理财产品的区别是什么", "怎么比较理财产品"])
def test_product_comparison_tutorial_keeps_consultation_not_bank_task(chat, message):
    row = turn(chat, message)
    assert row.mode == "CONSULT" and row.task_id is None


def test_again_amount_change_continues_only_unfinished_task_never_completed_payment(chat):
    trace = plan(chat, "给房东转100元")
    row = turn(chat, "再改成300", current_task_id=trace["task_id"], expected_task_version=trace["version"])
    assert row.mode == "REVISE" and row.task_id == trace["task_id"]
    assert run_one_job(chat[0], chat[1])
    with chat[0].begin() as db:
        db.get(Task, trace["task_id"]).state = "SUCCEEDED"  # Isolated controller terminal-state fixture.
        count = db.scalar(select(func.count()).select_from(Task))
    ended = turn(chat, "再改成500", current_task_id=trace["task_id"])
    assert ended.mode == "NEED_CONTEXT", ended.answer
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == count
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


@pytest.mark.parametrize("message", [
    "什么情况下转账需要再次确认？仅咨询解释，不转账，不办理任何银行业务。",
    "什么条件下转账需要再次确认？仅咨询，不执行任何操作。",
    "什么时候转账需要再次确认？只咨询解释，不办理任何银行业务。",
])
def test_procedural_confirmation_questions_and_explicit_consult_only_never_create_bank_task(chat, message):
    row = turn(chat, message)
    assert row.mode == "CONSULT" and row.task_id is None, row.answer
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_general_consult_only_followup_does_not_inherit_existing_task_link(chat):
    old = plan(chat, "给房东转账")
    row = turn(chat, "再解释一下确认版本为什么不能复用。仅咨询，不执行任何操作。", current_task_id=old["task_id"])
    assert row.mode == "CONSULT" and row.task_id is None
    assert all(source["source_id"] != "facts:current-task" for source in row.sources_json)
    with chat[0]() as db:
        assert db.get(ChatThread, chat[3]).current_task_id == old["task_id"]
        assert db.get(Task, old["task_id"]).state == "NEED_CLARIFICATION"


def test_positive_independent_bank_step_followed_by_explanation_stays_request(chat):
    row = turn(chat, "先给房东转100元，再解释为什么确认后才能执行")
    assert row.mode == "NEW_TASK" and row.task_id
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
