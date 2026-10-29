"""Bounded history reads; old stored JSON and source hashes are never rewritten.

The database measures protected columns before returning JSON to the application.
Only a verified, bounded row reaches the existing authority/source validators.
An unavailable row is not a successful source audit or permission to retry work.
"""
from base64 import b64encode
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import JSON, LargeBinary, Text, and_, or_, case, cast, func, literal, select

from .authentication import as_utc
from .contracts import DomainError
from .conversation_models import ChatTurn
from .models import Task
from .permissions import actor_role, require_sandbox_owner
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord


VERSION = "bounded-history-v2"
AUDIT_VERSION = "stored-turn-field-v1"
MAX_TURN_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 8192
TURN_SHELL_RESERVE_BYTES = MAX_MESSAGE_BYTES + 2048
PAGE_HEADER_RESERVE_BYTES = 4096
MAX_AUDIT_CHUNK_BYTES = 64 * 1024
MAX_AUDIT_FIELD_BYTES = 256 * 1024 * 1024
PRIVATE_DIAGNOSTIC_KEYS = frozenset({"semantic_source_bundle_v2"})
JSON_FIELDS = ("reviewed_evidence_refs_json", "sources_json", "suggestions_json", "response_meta_json",
    "field_sources_json", "diagnostics_json")
META_FIELDS = ("id", "thread_id", "sandbox_id", "user_id", "sequence", "context_policy", "status", "mode",
    "task_id", "task_relationship", "error_code", "created_at", "updated_at", "completed_at")
TASK_JSON_FIELDS = ("fic_json", "plan_json", "analysis_json", "result_json")


def _byte_length(session, column):
    if session.get_bind().dialect.name == "postgresql":
        return func.coalesce(func.octet_length(cast(column, Text)), 0)
    return func.coalesce(func.length(cast(column, LargeBinary)), 0)


def _sum_sizes(session, table, fields):
    return sum((_byte_length(session, table.c[name]) for name in fields), literal(0))


def _input_size(session):
    """Conservatively include related source packages used by history audits."""
    turn = ChatTurn.__table__
    task, record = Task.__table__.alias("history_parent"), RuntimeRecord.__table__.alias("history_parent_record")
    parent = select(_sum_sizes(session, task, TASK_JSON_FIELDS) + _byte_length(session, record.c.metadata_json)).select_from(
        task.outerjoin(record, record.c.task_id == task.c.id)).where(task.c.id == turn.c.task_id,
        task.c.user_id == turn.c.user_id, task.c.sandbox_id == turn.c.sandbox_id).correlate(turn).scalar_subquery()
    child, child_record = Task.__table__.alias("history_child"), RuntimeRecord.__table__.alias("history_child_record")
    op = RuntimeOperation.__table__.alias("history_operation")
    children = select(func.coalesce(func.sum(_sum_sizes(session, child, TASK_JSON_FIELDS)
        + _byte_length(session, child_record.c.metadata_json) + _sum_sizes(session, op, ("args_json", "depends_on_json"))), 0)).select_from(
        op.join(child, child.c.id == op.c.child_task_id).outerjoin(child_record, child_record.c.task_id == child.c.id)).where(
        op.c.parent_task_id == turn.c.task_id, op.c.active == 1, child.c.user_id == turn.c.user_id,
        child.c.sandbox_id == turn.c.sandbox_id).correlate(turn).scalar_subquery()
    snapshots = PlanSnapshot.__table__.alias("history_plan")
    plans = select(func.coalesce(func.sum(_sum_sizes(session, snapshots, ("proposal_json", "plan_json"))), 0)).where(
        snapshots.c.task_id == turn.c.task_id).correlate(turn).scalar_subquery()
    return (_sum_sizes(session, turn, (*JSON_FIELDS, "message", "answer")) + func.coalesce(parent, 0)
        + func.coalesce(children, 0) + func.coalesce(plans, 0))


def input_source_bytes(session):
    """Shared SQL expression for ChatTurn-based bounded read/context queries.

    Callers must apply their own current principal/thread ownership predicate.
    This expression measures size; it never grants access or loads JSON.
    """
    return _input_size(session)


def _owner_conditions(principal, conversation_id):
    return (ChatTurn.thread_id == conversation_id, ChatTurn.user_id == principal.user_id,
        ChatTurn.sandbox_id == principal.sandbox_id)


def source_size_status(session, principal, conversation_id, turn_id):
    """Identity-filtered SQL scalars only, including before session.get/JSON."""
    row = session.execute(select(ChatTurn.id, _input_size(session).label("source_bytes")).where(
        *_owner_conditions(principal, conversation_id), ChatTurn.id == turn_id)).first()
    return None if row is None else row.source_bytes <= MAX_TURN_BYTES


def require_bounded_read_source(session, principal, conversation_id, turn_id):
    from .conversation_service import owned_thread
    thread = owned_thread(session, principal, conversation_id)
    if thread.status != "ACTIVE":
        raise DomainError("CUSTOMER_READ_CONVERSATION_CLOSED", "请在当前有效会话重新查询。", 403)
    status = source_size_status(session, principal, conversation_id, turn_id)
    if status is False:
        if actor_role(session, principal.sandbox_id, principal.user_id) == "FAMILY_HELPER":
            raise DomainError("CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED", "此前资料的读取范围暂时无法核验，请重新查询。", 403)
        raise DomainError("CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED", "这次查询的原始记录暂时无法核对，请重新查询。", 409)


def bounded_source_get(session, model, identifier, principal):
    """Repeat the byte guard in the actual ORM load, not just its preflight.

    A source changing between SQL statements must be rejected before its JSON
    decoder runs. Existing ownership/semantic validators still follow this load.
    """
    table = model.__table__; primary = next(iter(table.primary_key.columns))
    conditions = [primary == identifier]
    if "user_id" in table.c and "sandbox_id" in table.c:
        conditions.extend((table.c.user_id == principal.user_id, table.c.sandbox_id == principal.sandbox_id))
    fields = [column.name for column in table.c if isinstance(column.type, (JSON, Text))]
    size = _input_size(session) if model is ChatTurn else _sum_sizes(session, table, fields)
    result = session.scalar(select(model).where(*conditions, size <= MAX_TURN_BYTES).execution_options(populate_existing=True))
    if result is None and session.scalar(select(primary).where(*conditions)) is not None:
        if actor_role(session, principal.sandbox_id, principal.user_id) == "FAMILY_HELPER":
            raise DomainError("CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED", "此前资料的读取范围暂时无法核验，请重新查询。", 403)
        raise DomainError("CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED", "这次查询的原始记录暂时无法核对，请重新查询。", 409)
    return result


@dataclass
class HistoryProjection:
    view: dict
    fallback_sources: dict
    unavailable_turn_ids: set


def customer_unavailable_turn(view):
    """Do not let STATUS/REVISE generic wording revive an unaudited reply."""
    from .customer_presentation import customer_turn_view
    result = customer_turn_view(view)
    result.update(answer=view["answer"], sources=[], suggestions=[], reply_snapshot=None,
        reply_snapshot_status="UNAVAILABLE", status_snapshot=None, status_snapshot_status="UNAVAILABLE",
        read_result_snapshot_status="UNAVAILABLE")
    result.pop("response_meta", None)
    return result


def _unavailable_view(session, principal, row):
    from .conversation_turn_state import _turn_language
    # No old source is treated as authorized merely because its body is large.
    # A helper cannot use this projection to revive an unprovable private grant.
    if actor_role(session, principal.sandbox_id, principal.user_id) == "FAMILY_HELPER":
        raise DomainError("CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED", "此前资料的读取范围暂时无法核验，请重新查询。", 403)
    row.diagnostics_json = {}
    language = _turn_language(row)
    link = None
    if row.task_id:
        task = session.execute(select(Task.id, Task.state, Task.version).where(Task.id == row.task_id,
            Task.user_id == principal.user_id, Task.sandbox_id == principal.sandbox_id)).first()
        if task is None:
            raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
        link = {"task_id": task.id, "state": task.state, "version": task.version,
            "relationship": row.task_relationship or "CURRENT"}
    return {"turn_id": row.id, "conversation_id": row.thread_id, "sequence": row.sequence,
        "message": row.message or ("This earlier message cannot be expanded here." if language == "en" else "此前消息暂不能在这里展开。"),
        "status": row.status, "mode": row.mode, "reply_language": language,
        "answer": ("This earlier reply cannot be expanded right now. You can check its original request or ask again."
            if language == "en" else "这条历史回复暂时不能展开。你可以查看原单，或重新查询。"),
        "sources": [], "task_link": link, "suggestions": [], "response_meta": {}, "field_sources": [],
        "diagnostics": {"schema_version": "history-diagnostic-unavailable-v1", "reason": "SOURCE_BUDGET_EXCEEDED"},
        "status_snapshot": None, "status_snapshot_status": "UNAVAILABLE", "reply_snapshot": None,
        "reply_snapshot_status": "UNAVAILABLE", "reviewed_evidence_refs": [], "error_code": row.error_code,
        "created_at": as_utc(row.created_at).isoformat(), "updated_at": as_utc(row.updated_at).isoformat()}


def build_history_projection(session, principal, conversation_id, before_sequence=None, *, customer=False, turn_id=None):
    from .conversation_service import owned_thread, thread_view, turn_view
    from .product_knowledge import public_sources
    thread = owned_thread(session, principal, conversation_id)
    if before_sequence is not None and (type(before_sequence) is not int or before_sequence < 1):
        raise DomainError("CHAT_HISTORY_CURSOR_INVALID", "历史游标须为正整数", 422)
    table = ChatTurn.__table__
    base = select(*(table.c[key] for key in META_FIELDS),
        case((_byte_length(session, table.c.message) <= MAX_MESSAGE_BYTES, table.c.message), else_=None).label("message"),
        _input_size(session).label("source_bytes")).where(*_owner_conditions(principal, conversation_id))
    if before_sequence is not None:
        base = base.where(table.c.sequence < before_sequence)
    if turn_id is not None:
        base = base.where(table.c.id == turn_id)
    base = base.order_by(table.c.sequence.desc()).limit(200).cte("history_source_sizes").prefix_with("MATERIALIZED", dialect="postgresql")
    budget = select(*base.c, func.sum(case((base.c.source_bytes <= MAX_TURN_BYTES, base.c.source_bytes), else_=0)).over(
        order_by=base.c.sequence.desc()).label("page_bytes"), func.count().over().label("turn_count")).cte("history_source_budget").prefix_with("MATERIALIZED", dialect="postgresql")
    # Reserve every message shell, even where a later payload cannot fit. A
    # blocked row must not make the actual response exceed the page budget.
    allowed = and_(budget.c.source_bytes <= MAX_TURN_BYTES,
        budget.c.page_bytes + budget.c.turn_count * TURN_SHELL_RESERVE_BYTES + PAGE_HEADER_RESERVE_BYTES <= MAX_PAGE_BYTES)
    # The CASE is part of the same statement/snapshot as the measurement. The
    # database never returns over-budget JSON to the driver's JSON decoder.
    query = select(*(budget.c[key] for key in (*META_FIELDS, "message")), allowed.label("payload_available"),
        *(case((allowed, table.c[key]), else_=None).label(key) for key in (*JSON_FIELDS, "answer"))).select_from(
        budget.join(table, table.c.id == budget.c.id)).order_by(budget.c.sequence.asc())
    records = list(session.execute(query).mappings())
    views, fallbacks, unavailable = [], {}, set()
    for record in records:
        values = dict(record); available = values.pop("payload_available")
        row = SimpleNamespace(**values)
        if not available:
            view = _unavailable_view(session, principal, row); unavailable.add(row.id)
        else:
            view = turn_view(session, principal, row, include_diagnostics=not customer)
            if row.status == "ANSWERED" and row.mode == "CONSULT" and row.answer == view.get("answer") \
                    and public_sources(row.sources_json) == view.get("sources"):
                fallbacks[row.id] = row.sources_json
        views.append(view)
    earliest = records[0]["sequence"] if records else None
    has_earlier = bool(earliest and session.scalar(select(ChatTurn.id).where(*_owner_conditions(principal, conversation_id),
        ChatTurn.sequence < earliest).limit(1)))
    return HistoryProjection({**thread_view(thread), "turns": views, "has_earlier": has_earlier,
        "before_cursor": earliest if has_earlier else None,
        "history_projection": {"schema_version": VERSION, "unavailable_turn_count": len(unavailable)}}, fallbacks, unavailable)


def stored_turn_audit_chunk(session, principal, conversation_id, turn_id, *, version, field,
        offset=0, length=MAX_AUDIT_CHUNK_BYTES, observation=None):
    """Explicit original-field byte audit, never a current fact/authorization DTO."""
    from .conversation_service import owned_thread
    owned_thread(session, principal, conversation_id)
    require_sandbox_owner(session, principal)  # No ADMIN or accountless helper audit expansion.
    if version != AUDIT_VERSION or field not in JSON_FIELDS or type(offset) is not int or offset < 0 \
            or type(length) is not int or not 1 <= length <= MAX_AUDIT_CHUNK_BYTES:
        raise DomainError("CHAT_AUDIT_REQUEST_INVALID", "请提供明确的原记录审计版本、字段及有限分片。", 422)
    table = ChatTurn.__table__; column = table.c[field]
    size = _byte_length(session, column)
    # The original-field audit is byte-preserving; it cannot silently redact
    # server-only choices and still claim to return the original record.
    # Inspect key presence in the database rather than materializing a possibly
    # large diagnostics document just to enforce this boundary.
    private_choices = or_(table.c.diagnostics_json['recipient_choice_set_v1'].as_string().is_not(None),
        table.c.diagnostics_json['recipient_choice_selection_v1'].as_string().is_not(None),
        # Natural-language choice receipts can carry private candidate bindings
        # in a nested source bundle without a top-level click-selection marker.
        cast(table.c.diagnostics_json, Text).contains('"OWNED_RECIPIENT_CHOICE"'))
    header = session.execute(select(ChatTurn.id, ChatTurn.status, ChatTurn.updated_at, ChatTurn.request_hash,
        size.label("total_bytes"), private_choices.label("private_choices")).where(
            *_owner_conditions(principal, conversation_id), ChatTurn.id == turn_id)).first()
    if header is None:
        raise DomainError("CHAT_AUDIT_TURN_NOT_FOUND", "原记录不属于当前身份。", 404)
    if field == 'diagnostics_json' and header.private_choices:
        raise DomainError("CHAT_AUDIT_PRIVATE_CHOICES", "这项内部记录包含收款选项凭据，不能通过原始字段审计导出。", 403)
    if header.status not in {"ANSWERED", "FAILED"}:
        raise DomainError("CHAT_AUDIT_SOURCE_CHANGED", "原记录仍在更新，请稍后核对。", 409)
    if header.total_bytes > MAX_AUDIT_FIELD_BYTES:
        raise DomainError("CHAT_AUDIT_FIELD_TOO_LARGE", "原记录超过本入口的有限审计范围，数据仍完整保留。", 413)
    stamp = canonical_hash({"turn_id": turn_id, "field": field, "updated_at": as_utc(header.updated_at).isoformat(),
        "request_hash": header.request_hash, "bytes": header.total_bytes})
    if (offset > 0 and observation != stamp) or observation is not None and observation != stamp:
        raise DomainError("CHAT_AUDIT_SOURCE_CHANGED", "原记录已变化，请重新核对审计观察标识。", 409)
    if offset > header.total_bytes:
        raise DomainError("CHAT_AUDIT_REQUEST_INVALID", "审计分片位置无效。", 422)
    binary = func.convert_to(cast(column, Text), "UTF8") if session.get_bind().dialect.name == "postgresql" else cast(column, LargeBinary)
    part = session.scalar(select(func.substr(binary, offset + 1, length)).where(*_owner_conditions(principal, conversation_id),
        ChatTurn.id == turn_id, ChatTurn.updated_at == header.updated_at, size == header.total_bytes))
    if part is None:
        raise DomainError("CHAT_AUDIT_SOURCE_CHANGED", "原记录已变化，请重新核对审计观察标识。", 409)
    part = bytes(part)
    return {"schema_version": AUDIT_VERSION, "authority": "AUDIT_ONLY_NOT_CURRENT_FACTS_OR_AUTHORIZATION",
        "conversation_id": conversation_id, "turn_id": turn_id, "field": field, "observation": stamp,
        "total_bytes": header.total_bytes, "offset_bytes": offset, "chunk_bytes": len(part), "encoding": "base64-utf8-bytes",
        "data": b64encode(part).decode("ascii"), "next_offset": offset + len(part) if offset + len(part) < header.total_bytes else None,
        "read_only": True, "bank_confirmation_permitted": False}
