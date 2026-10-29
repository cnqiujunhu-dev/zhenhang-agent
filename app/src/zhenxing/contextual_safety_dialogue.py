"""Actor-bound fraud/family case continuity is explanation, never bank authority.

The record contains typed safety facts, not a third party's raw instructions.
Every use rebuilds the original read, current permissions and original message
hashes. No model, task creation, confirmation, execution or transaction commit
is performed by this module.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import or_, select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Task
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeRecord

VERSION = "contextual-safety-consultation-v1"
AUTHORITY = "OWNED_SAFETY_CASE_EXPLANATION_NOT_BANK_AUTHORIZATION"
DIAGNOSTIC_KEY = "contextual_safety_source"
MAX_SOURCES = 32
SOURCE_ID = "facts:contextual-safety-case"

# Public authority, checked 2026-10-10. These are prevention/recovery guidance,
# not a promise of a bank freeze, successful cancellation or recovered funds.
FRAUD_POLICY = {
    "source_id": "kb:fraud.case-continuity-v1", "kind": "PRODUCT_KNOWLEDGE",
    "title": "风险核实与疑似受骗后的处理", "title_en": "Risk checks and suspected fraud recovery",
    "version": VERSION,
    "reference": "https://app.www.gov.cn/govdata/gov/202407/25/517630/article.html",
    "content": "对方自称客服、银行或警方并不能证明身份。通过自行查找的机构官方渠道核实，不使用对方给的电话或链接。"
        "面对安全账户、验证码索取、屏幕共享或退款先交费用，不继续转钱或提供敏感信息。"
        "如果你表示钱可能已转出，应停止后续付款，保存聊天、通话和交易截图等证据，尽快向公安机关反映，并通过银行官方渠道核查原交易。"
        "不要相信付费追回、所谓网警或黑客承诺。是否实际转出、到账、止付或追回，需要银行或警方核实；本次咨询不能宣称已冻结、已报警、已退款或保证追回。",
    "content_en": "A caller's claim to be support, a bank or police does not verify identity. Independently find the institution's official contact to verify it; do not use a contact or link supplied by the caller. Do not keep sending money or disclose sensitive information in response to safe-account, code, screen-sharing or upfront-refund-fee demands. If you report that money may have been sent, stop further payments, retain chat, call and transaction evidence, report to police promptly and check the original transaction through your bank's official channel. Do not trust paid recovery, fake cyber-police or hacker promises. Actual settlement, freezing and recovery require bank or police verification; this consultation cannot claim those actions were performed or guarantee recovery.",
}

_QUOTE = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|`[^`\n]*`')
_RESET = re.compile(r"新话题|换个话题|不聊(?:这|诈骗|风险|授权)|结束(?:这|诈骗|风险|授权)|\b(?:new topic|change the subject|stop discussing)\b", re.I)
_INDEPENDENT = re.compile(r"(?:另外|另行|单独|无关|现在|马上|立即|请帮我|帮我|请).{0,12}(?:转账?|转帐|汇款|付款|支付|锁卡|挂失|取消|申购|赎回|授权|建立|设置)|\b(?:separately|now|please|instead)\b.{0,30}\b(?:transfer|send|pay|lock|grant|authorize|cancel|buy|redeem)\b", re.I)
_QUESTION = re.compile(r"[吗么？?]|该不该|能不能|是否|怎么|如何|\b(?:should|can|could|whether|how|what\s+if)\b", re.I)
_PAID = re.compile(r"(?:我|本人)(?:刚才|刚|已经|已|还是)?(?:转(?:账|帐|钱|出)?|汇|付)(?:了|过|出去|出去了)|\bI\s+(?:have\s+)?(?:already\s+)?(?:paid|sent|transferred)\b", re.I)
_FRAUD_IDENTITY = re.compile(r"(?:他|她|对方|那人|来电|客服).{0,24}(?:是|说|声称|自称|工号|认证)|(?:那|这个|这样).{0,15}(?:客服|身份|可信)|\b(?:he|she|they|the caller).{0,30}\b(?:support|bank|police|claims?|says?)\b", re.I)
_FRAUD_DECISION = re.compile(r"(?:还|那|现在|我|到底|要|该).{0,16}(?:转|付|给|继续|相信)|(?:不|别|先不)(?:转|付|给)|(?:能|可以|该不该).{0,12}(?:信|转|付)|\b(?:should\s+I|can\s+I|do\s+I|don't|do\s+not).{0,30}\b(?:pay|send|transfer|trust|continue)\b", re.I)
_FAMILY = re.compile(r"家人|家庭|孩子|儿子|女儿|父母|爸爸|妈妈|余额|委托|授权|权限|只读|只看|只查|(?:不让|不许|禁止|不允许|不能).{0,10}(?:转|付款|支付)|\b(?:family|child|son|daughter|parent|balance|permission|read[- ]only|delegat\w*|access)\b", re.I)
_FAMILY_NEGATION = re.compile(r"(?:不让|不许|禁止|不允许|不能|不可以|不要|先不).{0,18}(?:转|付款|支付|授权|建立|创建|设置)|\b(?:don't|do\s+not|no|without|not).{0,30}\b(?:transfer|pay|grant|create|authorize)\b", re.I)
_FAMILY_BALANCE = re.compile(r"只.{0,12}(?:看|查|读).{0,8}余额|只读|仅查看|\b(?:read[- ]only|balance[- ]only|only.{0,15}(?:balance|view))\b", re.I)
_FAMILY_REFERENCE = re.compile(r"(?:他|她|那|这样|这项).{0,20}(?:能|可|建议|提案|直接|代付|办理|操作|权限)|\b(?:he|she|they|that).{0,25}\b(?:can|may|permission|pay|act|operate|suggest)\b", re.I)
_OTHER_DOMAIN = re.compile(r"账单|订阅|续费|理财|申购|赎回|积分|低碳|换汇|汇率|查询(?:我的|本人)?(?:银行卡|余额)|银行卡余额|余额(?:没|变|多少|不足)|\b(?:my\s+balance|statements?|subscriptions?|renewals?|investments?|carbon|points|exchange\s+rate)\b", re.I)
_SAFE_CODE = re.compile(r"[A-Z0-9_]{1,80}\Z")
_RISKY_PRONOUN_PAY = re.compile(
    r"\s*(?:(?:那就|那|现在|直接|那还是|还是|就|请|请帮我|帮我|我(?:想|要)?|好(?:吧|的)?)[，,\s]*){0,3}"
    r"(?:(?:给|向)(?:他|她|他们|她们|对方|这个客服|那个客服|这位客服|那位客服|那个人|这个人)(?:转(?:账|帐|钱)?|汇款|付款|打款|支付|打过去)|"
    r"(?:转(?:账|帐|钱)?给|汇给|付给|打给)(?:他|她|他们|她们|对方|这个客服|那个客服|那个人|这个人))"
    r"[^，,。；;!?！？\n]{0,60}[吧。.!！]?\s*|"
    r"\s*(?:(?:please|now|then|I\s+want\s+to)\s+){0,3}"
    r"(?:(?:send|pay|transfer|wire)\s+(?:him|her|them|that\s+caller|the\s+caller|that\s+person)\b[^,.;!?\n]{0,60}|"
    r"(?:send|pay|transfer|wire)\s+[^,.;!?\n]{1,40}\s+to\s+(?:him|her|them|that\s+caller|the\s+caller|that\s+person))"
    r"[.!]?\s*", re.I)


def _message_hash(turn):
    return canonical_hash({"message": turn.message})


def _owned_turn(turn, thread, principal, scope, scope_hash, *, current=False):
    from .redaction import redact_credentials
    diagnostics = turn.diagnostics_json or {}
    redacted_safety_text = diagnostics.get("input_redacted") is True and "[REDACTED]" in turn.message \
        and redact_credentials(turn.message) == turn.message
    return (turn.thread_id, turn.user_id, turn.sandbox_id) == (thread.id, principal.user_id, principal.sandbox_id) \
        and turn.context_policy == "AUTO" and not turn.error_code \
        and (turn.status in {"QUEUED", "LEASED", "ANSWERED"} if current else turn.status == "ANSWERED") and not diagnostics.get("credential_only") \
        and (not diagnostics.get("input_redacted") or redacted_safety_text) and not turn.reviewed_evidence_refs_json \
        and diagnostics.get("actor_role_at_submission") == scope["role"] \
        and diagnostics.get("dialogue_actor_scope_hash", scope_hash) == scope_hash \
        and diagnostics.get("actor_login_id", scope["login_account_id"]) == scope["login_account_id"] \
        and diagnostics.get("actor_login_version", scope["login_account_version"]) == scope["login_account_version"] \
        and (scope["family_context"] is None or diagnostics.get("family_context_origin") == scope["family_context"])


def _stage(topic, message):
    from .intelligence import _intent_text
    visible = _QUOTE.sub("", message)
    owned = _intent_text(message)
    if _RESET.search(visible):
        return None
    if _OTHER_DOMAIN.search(owned) and not (topic == "FAMILY" and _FAMILY_BALANCE.search(visible)
        and _QUESTION.search(visible) and not re.search(r"账单|订阅|理财|积分|低碳|换汇|\b(?:bill|subscription|invest|carbon|exchange)\b", visible, re.I)):
        return None  # A prior safety case must not swallow a new service request.
    # Only the user's whole, unquoted current command can refer back to the
    # risky actor. It supplies no payee identity or write permission. A named
    # independent recipient or a second clause does not match this guard.
    if topic == "FRAUD" and owned == message and _RISKY_PRONOUN_PAY.fullmatch(owned):
        return "QUESTION_PAY"
    # Any distinct positive bank instruction belongs in the normal workflow.
    # A denial/question/report of a third party's instruction is not positive.
    distinct_request = re.search(r"另外|另行|单独|请帮我|帮我|请|立即|马上|\b(?:separately|please)\b", owned, re.I)
    safety_question = _QUESTION.search(owned) and not distinct_request
    if _INDEPENDENT.search(owned) and not safety_question \
            and not (_FAMILY_NEGATION.search(owned) and not re.search(r"另外|另行|单独|\bseparately\b", owned, re.I)):
        return None
    if re.search(r"(?:给|向|转给|汇给).{0,40}(?:\d|一|二|三|百|千|万)|\b(?:send|transfer|pay)\s+(?:CNY|USD)?\s*\d", owned, re.I) \
            and not (_QUESTION.search(visible) or _PAID.search(visible) or _FRAUD_IDENTITY.search(visible) or _FAMILY_NEGATION.search(visible)):
        return None
    if topic == "FRAUD":
        if _PAID.search(visible):
            return "REPORTED_PAYMENT_RECOVERY"
        if _FRAUD_IDENTITY.search(visible):
            return "IDENTITY_VERIFICATION"
        if _FRAUD_DECISION.search(visible):
            return "PAYMENT_DECISION"
        if re.search(r"(?:那|这|接下来|怎么办|为什么|凭什么|是不是|如何)|\b(?:why|what\s+next|what\s+if|how|is\s+that)\b", visible, re.I):
            return "RISK_EXPLANATION"
    if topic == "FAMILY" and (_FAMILY.search(visible) or _FAMILY_REFERENCE.search(visible)):
        if _FAMILY_NEGATION.search(visible):
            return "NO_TRANSFER_PERMISSION"
        if _FAMILY_BALANCE.search(visible):
            return "BALANCE_ONLY_POLICY"
        if _QUESTION.search(visible) or re.search(r"只能|仅允许|\bonly\b", visible, re.I):
            return "DELEGATION_POLICY"
    return None


def _read_anchor(session, principal, thread, source):
    task = session.get(Task, source.task_id) if source.task_id else None
    if task is None or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id) or task.state != "SUCCEEDED":
        return None
    record = session.get(RuntimeRecord, task.id)
    if record is None or record.kind != "PARENT":
        return None
    from .conversation_provenance import validate_current_continuation
    from .runtime import task_trace, validate_runtime_family_context
    from .intelligence import IntentProposal, ground_proposal
    validate_current_continuation(session, task)
    validate_runtime_family_context(session, task)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id, PlanSnapshot.version == record.plan_version))
    if snapshot is None or snapshot.plan_hash != task.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
        return None
    proposal = IntentProposal.model_validate(snapshot.proposal_json)
    if not proposal.actions or {action.kind for action in proposal.actions} not in ({"FRAUD_ANALYZE"}, {"FAMILY_VIEW"}):
        return None
    metadata = record.metadata_json or {}
    if not metadata.get("owned_user_dialogue") and (metadata.get("conversation_context") or {}).get("authority") != "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS":
        if source.message != task.message or ground_proposal(proposal, source.message).actions != proposal.actions:
            return None
    trace = task_trace(session, principal, task.id)
    outcome = trace.get("outcome") or {}
    if outcome.get("verified") is not True or outcome.get("state") != "SUCCEEDED" or outcome.get("kind") != "QUERY_RESULT" or trace.get("pending_confirmation"):
        return None
    nodes = trace.get("nodes") or []
    if not nodes or any(node.get("action") not in {"FRAUD_ANALYZE", "FAMILY_VIEW"} or node.get("status") != "SUCCEEDED"
                        or not isinstance(node.get("result"), dict) or node["result"].get("error") for node in nodes):
        return None
    topic = "FRAUD" if proposal.actions[0].kind == "FRAUD_ANALYZE" else "FAMILY"
    projection = {"task_id": task.id, "task_version": task.version, "task_state": task.state,
        "plan_hash": task.plan_hash, "snapshot_hash": canonical_hash(snapshot.proposal_json),
        "read_result_hash": canonical_hash({"nodes": nodes, "outcome": outcome}), "topic": topic}
    if topic == "FRAUD":
        from .extensions import evaluate_transfer_risk
        result = nodes[0]["result"].get("risk")
        if not isinstance(result, dict) or result.get("risk_level") not in {"LOW", "HIGH", "UNKNOWN"} \
                or result.get("decision") not in {"ALLOW", "BLOCK", "HOLD_FOR_REVIEW"} \
                or not isinstance(result.get("reason_codes"), list) or not 1 <= len(result["reason_codes"]) <= 32 \
                or not all(isinstance(code, str) and _SAFE_CODE.fullmatch(code) for code in result["reason_codes"]):
            return None
        fresh = evaluate_transfer_risk(session, task, {"payee_id": result.get("payee_id")})
        if not all(isinstance(code, str) and _SAFE_CODE.fullmatch(code) for code in fresh["reason_codes"]):
            return None
        projection["current_bank_read_hash"] = canonical_hash(fresh)
        safe = {key: result[key] for key in ("risk_level", "decision", "reason_codes", "rules_version", "confirmation_can_override") if key in result}
        safe["current_risk"] = {key: fresh[key] for key in ("risk_level", "decision", "reason_codes", "rules_version", "confirmation_can_override")}
    else:
        safe = {}
    return projection, safe


def _family_projection(session, principal):
    from .extensions import family_view
    from .extensions_models import FamilyMember
    view = family_view(session, principal.sandbox_id, principal.user_id)
    # Use only current access properties for narration. No parent's balance,
    # proxy token, account identifier or historical grant is supplied to LLM.
    safe = {"permissions": view["permissions"], "delegations": [
        {key: row[key] for key in ("scopes", "status", "valid", "version")}
        for row in view["delegations"]]}
    members = list(session.scalars(select(FamilyMember).where(FamilyMember.sandbox_id == principal.sandbox_id,
        FamilyMember.helper_user_id.is_not(None),
        or_(FamilyMember.owner_user_id == principal.user_id, FamilyMember.helper_user_id == principal.user_id))
        .order_by(FamilyMember.id)))
    member_scope = [{"id": member.id, "owner_user_id": member.owner_user_id,
        "helper_user_id": member.helper_user_id, "display_name": member.display_name} for member in members]
    return safe, canonical_hash({"view": view, "member_scope": member_scope})


def contextual_safety_consultation(session, principal, thread, turn):
    """Return bounded safe explanation records for an owned prior safety case.

    Caller persists ``source_binding`` under ``DIAGNOSTIC_KEY`` on the answered
    CONSULT turn, validates it before publishing, and uses its normal cloud
    explanation pipeline. A positive independent bank request returns None.
    """
    try:
        from .dialogue_state import dialogue_actor_scope
        scope, scope_hash = dialogue_actor_scope(session, principal)
        if thread.status != "ACTIVE" or (thread.user_id, thread.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                or not _owned_turn(turn, thread, principal, scope, scope_hash, current=True):
            return None
        rows = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
            ChatTurn.sequence < turn.sequence).order_by(ChatTurn.sequence.desc()).limit(MAX_SOURCES)))
        if not rows:
            return None
        refs, anchor, safe = [], None, {}
        for source in rows:
            if not _owned_turn(source, thread, principal, scope, scope_hash):
                return None
            ref = {"turn_id": source.id, "sequence": source.sequence, "message_hash": _message_hash(source)}
            if source.mode in {"GREETING", "DISCOVERY", "FEEDBACK"}:
                refs.append(ref)
                continue
            if source.mode == "CONSULT" and isinstance((source.diagnostics_json or {}).get(DIAGNOSTIC_KEY), dict):
                proof = source.diagnostics_json[DIAGNOSTIC_KEY]
                if not validate_source(session, principal, thread, source, proof):
                    return None
                # Flatten provenance instead of copying an assistant's words.
                for previous in proof["source_refs"]:
                    if previous not in refs:
                        refs.append(previous)
                refs.append(ref)
                anchor = proof["anchor"]
                anchor_turn = session.get(ChatTurn, anchor["turn_id"])
                original = _read_anchor(session, principal, thread, anchor_turn) if anchor_turn and anchor.get("task_id") else None
                safe = original[1] if original else {}
                break
            if source.mode == "CONSULT" and source.diagnostics_json.get("family_permission_consultation") is True:
                from .product_knowledge import knowledge_sources, source_fingerprint
                policy = [row for row in knowledge_sources("家庭委托授权家人") if row["source_id"] == "kb:family.scope-v1"]
                if not source.sources_json or source_fingerprint(source.sources_json) != source_fingerprint(policy):
                    return None
                refs.append(ref)
                anchor = {"topic": "FAMILY", "turn_id": source.id, "task_id": None,
                    "public_policy_hash": source_fingerprint(policy)}
                break
            if source.mode in {"NEW_TASK", "REVISE", "CLARIFY"}:
                original = _read_anchor(session, principal, thread, source)
                if original:
                    anchor, safe = original
                    anchor = {**anchor, "turn_id": source.id}
                    refs.append(ref)
                break
            return None  # A substantive unrelated topic is not an old case.
        if anchor is None or len(refs) > MAX_SOURCES:
            return None
        topic = anchor["topic"]
        stage = _stage(topic, turn.message)
        if stage is None:
            return None
        if stage == "QUESTION_PAY" and not any(isinstance(risk, dict) and risk.get("risk_level") == "HIGH"
                and risk.get("decision") == "BLOCK" for risk in (safe, safe.get("current_risk"))):
            return None  # This narrowly protects a verified same high-risk case.
        refs = sorted(refs, key=lambda ref: ref["sequence"])
        if len({ref["turn_id"] for ref in refs}) != len(refs):
            return None
        if topic == "FAMILY":
            from .product_knowledge import knowledge_sources, source_fingerprint
            policy = [row for row in knowledge_sources("家庭委托授权家人") if row["source_id"] == "kb:family.scope-v1"]
            safe, fresh_hash = _family_projection(session, principal)
            anchor = {**anchor, "current_bank_read_hash": fresh_hash, "public_policy_hash": source_fingerprint(policy)}
        else:
            policy = [dict(FRAUD_POLICY)]
            # Typed risk signals can supplement an old case, but never supply
            # the third party's payee, amount, credentials or write instruction.
            from .fraud_signals import risk_reason_codes
            safe["new_reported_risk_reasons"] = risk_reason_codes(turn.message)
        binding = {"version": VERSION, "authority": AUTHORITY, "conversation_id": thread.id,
            "current_turn_id": turn.id, "current_sequence": turn.sequence,
            "current_message_hash": _message_hash(turn), "actor_scope_hash": scope_hash,
            "source_refs": refs, "anchor": anchor, "topic": topic, "stage": stage,
            "safe_facts_hash": canonical_hash(safe), "policy_hash": canonical_hash(policy),
            "bank_write_authorized": False, "bank_operation_executed": False}
        binding["binding_hash"] = canonical_hash(binding)
        facts = {"topic": topic, "stage": stage, "safe_case_facts": safe,
            "user_reports_prior_payment": stage == "REPORTED_PAYMENT_RECOVERY",
            "reported_payment_is_verified_bank_receipt": False,
            "establish_or_change_delegation_requested": False,
            "bank_write_authorized": False, "bank_operation_executed": False,
            "authority": AUTHORITY}
        record = {"source_id": SOURCE_ID, "kind": "SCOPED_BUSINESS_FACTS", "title": "当前对话中的安全咨询事实",
            "title_en": "Safety consultation facts in this conversation", "version": VERSION,
            "reference": "conversation:" + thread.id, "content": json.dumps(facts, ensure_ascii=False, sort_keys=True),
            "source_binding": binding}
        return {"records": policy + [record], "source_binding": binding, "topic": topic, "stage": stage}
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return None


def validate_source(session, principal, thread, turn, source_binding):
    """Recompute all source, read, role, delegation and family-token epoch hashes."""
    if not isinstance(source_binding, dict) or source_binding.get("version") != VERSION:
        return False
    fresh = contextual_safety_consultation(session, principal, thread, turn)
    return fresh is not None and fresh["source_binding"] == source_binding


def contextual_safety_sources_current(session, principal, records):
    """History-read authorization hook for this source type; unknown rows fail closed."""
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        return False
    for record in records:
        if isinstance(record, dict) and record.get("source_id") == SOURCE_ID:
            proof = record.get("source_binding")
            thread = session.get(ChatThread, proof.get("conversation_id")) if isinstance(proof, dict) else None
            turn = session.get(ChatTurn, proof.get("current_turn_id")) if isinstance(proof, dict) else None
            if thread is None or turn is None or not validate_source(session, principal, thread, turn, proof):
                return False
            fresh = contextual_safety_consultation(session, principal, thread, turn)
            if fresh is None or record != fresh["records"][-1]:
                return False
    return True
