"""A source-bound, per-action user slot ledger, never banking authority.

Model changes are suggestions: each value is reparsed from its exact current
user span, or from a live verified object reference. The immutable genesis
snapshot and all raw source receipts are rechecked at every planning/confirm
boundary. A mutable Task version is deliberately not the genesis identity.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .dialogue_state import ACTION_ALLOWED_FIELDS, dialogue_actor_scope
from .intelligence import ActionSlots, IntentProposal, ProposedAction, READ_KINDS, _intent_text, ground_proposal, rule_proposal
from .models import Confirmation, Task
from .planning import canonical_hash
from .redaction import redact_credentials
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

VERSION = "owned-dialogue-slot-ledger-v1"
PRIVATE_KEY = "_verified_slot_update"
METADATA_KEY = "dialogue_slot_update"
MAX_TURNS = 32
MAX_CHANGES = 64
MAX_LEGACY_SOURCE_TURNS = 8
_AUTHORITY = object()
_MUTABLE = {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION"}
_STRING_FIELDS = {"payee_name", "product_name", "merchant", "grantee_name", "owner_name", "behavior_name", "reward_name", "recipient_alias"}
_AUTO_OBJECT_FIELDS = _STRING_FIELDS - {"recipient_alias"}
_FIELD_LABELS = {
    "payee_name": "收款人|收款账号|收款账户|recipient", "product_name": "产品|理财|基金|product",
    "merchant": "订阅|会员|商户|merchant", "grantee_name": "协助人|家人|孩子|grantee",
    "owner_name": "本人|委托人|父母|owner", "memo": "备注|memo", "scopes": "权限|范围|scopes",
    "scheduled_at": "日期|时间|转账时间|date|time", "card_last4": "卡尾号|尾号|末四位",
    "card_number": "卡号|银行卡号|card number", "amount_minor": "金额|转账金额|限额|amount",
    "maximum_amount_minor": "上限|额度|maximum", "expires_days": "有效期|期限|天数|expiry",
    "subscription_months": "月数|订阅时长|会员时长|months|duration",
    "subscription_auto_renew": "自动续费|自动续订|auto[- ]?renew",
}


def _deny():
    raise DomainError("DIALOGUE_SLOT_SOURCE_CHANGED", "原需求的信息或权限已变化，请重新核对；不会重复办理。", 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def _actions(proposal):
    return [{"kind": row.kind, "slots": row.slots.model_dump(exclude_none=True), "depends_on": list(row.depends_on)} for row in proposal.actions]


def _proposal(actions, message=""):
    return IntentProposal(summary=message[:500], actions=[ProposedAction(kind=row["kind"],
        slots=ActionSlots.model_validate(row["slots"]), depends_on=list(row["depends_on"])) for row in actions])


def _lifecycle(session, task, *, execution_context=None, allow_completed=False):
    from .conversation_provenance import _DialogueExecutionContext
    authorized_execution = isinstance(execution_context, _DialogueExecutionContext) and execution_context.permits(task)
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
    completed = set()
    if allow_completed:
        try:
            from .conversation_provenance import current_plan_completed_write
            completed = {op.id for op in operations if op.action_kind not in READ_KINDS and
                current_plan_completed_write(session, task, op)}
        except ImportError:
            completed = set()  # Root's strict receipt verifier is mandatory.
    if task.state not in _MUTABLE and not authorized_execution and not (
            allow_completed and completed and task.state in {"SUCCEEDED", "PARTIAL"}):
        return False
    for operation in operations:
        if operation.action_kind in READ_KINDS:
            continue
        child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
        if operation.status == "UNKNOWN" or child is not None and child.state == "UNKNOWN":
            return False
        affected = operation.status in {"SUCCEEDED", "AUTHORIZED", "RUNNING"} or child is not None and child.state in {"SUCCEEDED", "AUTHORIZED", "EXECUTING"}
        if affected and operation.id not in completed and not authorized_execution:
            return False
        if affected and operation.status == "SUCCEEDED" and operation.id not in completed:
            return False  # A private grant never launders an old settled write.
    child_ids = [op.child_task_id for op in operations if op.child_task_id]
    consumed = session.scalars(select(Confirmation).where(Confirmation.task_id.in_([task.id, *child_ids]),
        (Confirmation.consumed_at.is_not(None)) | (Confirmation.status == "CONSUMED")))
    for confirmation in consumed:
        linked = [op for op in operations if op.child_task_id == confirmation.task_id]
        if not allow_completed or len(linked) != 1 or linked[0].id not in completed:
            return False
    return True


def _turn_valid(session, principal, thread, turn, scope_hash, *, prior=False):
    if not _owned(thread, principal) or thread.status != "ACTIVE" or not _owned(turn, principal) or turn.thread_id != thread.id \
        or turn.context_policy != "AUTO" or turn.reviewed_evidence_refs_json or turn.error_code \
        or (turn.diagnostics_json or {}).get("input_redacted") or redact_credentials(turn.message) != turn.message:
        return False
    scope, actual_hash = dialogue_actor_scope(session, principal)
    diagnostics = turn.diagnostics_json or {}
    if scope_hash != actual_hash or diagnostics.get("actor_role_at_submission") != scope["role"]:
        return False
    if diagnostics.get("dialogue_actor_scope_hash", actual_hash) != actual_hash:
        return False
    for key, expected in (("actor_login_id", scope["login_account_id"]), ("actor_login_version", scope["login_account_version"])):
        if key in diagnostics and diagnostics[key] != expected:
            return False
    from .dialogue_state_models import ConversationDialogueState
    state = session.get(ConversationDialogueState, thread.id)
    if state is not None and (state.actor_scope_hash != scope_hash or
        turn.sequence <= (state.checkpoint_json or {}).get("epoch_start_sequence", 0)):
        return False
    if scope["family_context"] is not None and diagnostics.get("family_context_origin") != scope["family_context"]:
        return False
    if prior and turn.status != "ANSWERED":
        return False
    return True


def _raw_span(message, span):
    if not isinstance(span, dict) or set(span) != {"start", "end"} or type(span["start"]) is not int or type(span["end"]) is not int \
        or not 0 <= span["start"] < span["end"] <= len(message):
        return None
    return message[span["start"]:span["end"]]


def _money(text, old_currency):
    from .conversation_service import _continuation_money
    return _continuation_money(text.strip(), old_currency or "CNY")


def _whole_count(text):
    from .intelligence import _chinese_integer
    try:
        if re.fullmatch(r"\d+", text): return int(text)
        if re.fullmatch(r"[零〇一二两三四五六七八九十百千万]+", text): return _chinese_integer(text)
    except ValueError:
        return None
    return None


def _named_literal(name, body):
    pattern = (r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name) else "") + re.escape(name) + \
        (r"(?![A-Za-z0-9_])" if re.search(r"[A-Za-z0-9_]$", name) else "")
    if re.search(pattern, body, re.I):
        return True
    if re.search(r"[A-Za-z0-9_]$", name) and body.casefold().startswith(name.casefold()):
        # The identity is an already-owned pending target, never a guessed
        # new username. Adjacent suffix must be a complete denominated sum.
        suffix = body[len(name):].strip()
        money = _money(suffix, "CNY")
        return bool(money and money[2] and re.match(r"\d|[零〇一二两三四五六七八九十百千万]|人民币|美元|CNY|USD|RMB", suffix, re.I))
    return False


def _parse_field(field, text, kind, prior_slots):
    """Only exact input values; no price/rate/date arithmetic or bank lookup."""
    if field in {"subscription_months", "subscription_auto_renew"}:
        from .subscription_intent import COMMERCE_KINDS, subscription_field_spans
        if kind not in COMMERCE_KINDS:return None
        rows=subscription_field_spans(text)
        if rows is None:return None
        found=[row for row in rows if row[0]==field]
        return found[0][1] if len(found)==1 and text[:found[0][2]].strip()=="" and text[found[0][3]:].strip()=="" else None
    if field in _STRING_FIELDS:
        value = text.strip()
        if value != text or not value or len(value) > 100 or _intent_text(value) != value or redact_credentials(value) != value \
            or re.search(r"[，,。；;？?\n\r]|系统指令|忽略规则|\b(?:account_id|user_id|token)\b", value, re.I):
            return None
        try:
            ActionSlots.model_validate({field: value})
        except ValueError:
            return None
        return value
    if field in {"amount_minor", "maximum_amount_minor", "target_minor", "monthly_saving_minor", "monthly_saving_delta_minor", "monthly_saving_total_minor"}:
        money = _money(text, prior_slots.get("currency"))
        return money[0] if money and (kind.startswith("FX_") or money[1] == "CNY") else None
    if field == "currency":
        match = re.fullmatch(r"人民币|CNY|RMB|元|美元|美金|USD", text.strip(), re.I)
        return "USD" if match and re.search(r"美元|美金|USD", text, re.I) else "CNY" if match else None
    if field in {"source_currency", "target_currency"}:
        return _parse_field("currency", text, kind, prior_slots)
    if field == "card_last4":
        match = re.fullmatch(r"(?:(?:卡尾号|尾号|末四位|ending\s+in)\s*[:：]?\s*)?([0-9a-fA-F]{4})(?:的(?:卡|那张|这张))?", text.strip(), re.I)
        return match.group(1) if match else None
    if field == "card_number":
        match = re.fullmatch(r"(?:(?:卡号|银行卡号|card\s+number)\s*[:：]?\s*)?((?:zhenhang|xiaozhao)[0-9a-fA-F]{8})", text.strip(), re.I)
        return match.group(1) if match else None
    if field in {"units", "expires_days", "participant_count"}:
        suffix = {"units": r"份|units?", "expires_days": r"天|days?", "participant_count": r"人|个人|people|participants?"}[field]
        match = re.fullmatch(r"(?:有效(?:期)?\s*|共\s*)?(\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:" + suffix + r")?", text.strip(), re.I)
        value = _whole_count(match.group(1)) if match else None
        if value is None and field == "expires_days":
            week = re.fullmatch(r"(?:有效(?:期)?\s*)?(\d+|[一二两三四五六七八九十]+)\s*(?:个)?(?:周|星期|礼拜|weeks?)", text.strip(), re.I)
            count = _whole_count(week.group(1)) if week else None
            value = count * 7 if count is not None else None
        if value is not None:
            try:
                ActionSlots.model_validate({field: value})
            except ValueError:
                return None
        return value
    if field == "scheduled_at":
        from .intelligence import _DATE_EXPRESSION
        from .language import EN_SCHEDULE_DATE
        value = text.strip()
        return value if _DATE_EXPRESSION.fullmatch(value) or EN_SCHEDULE_DATE.fullmatch(value) else None
    if field == "scopes":
        if re.search(r"不(?:让|许|准|能)|不要|禁止|\b(?:not|never)\b", text, re.I):
            # A restrictive "只..." is supported below; mixed grant/denial
            # needs separate explicit SET/CLEAR statements, not scope guessing.
            return None
        values = []
        if re.search(r"(?:查|看|查看|查询).{0,5}余额|read.?balance|view.{0,8}balance", text, re.I): values.append("READ_BALANCE")
        if re.search(r"(?:提出|提交|建议|协助|提案).{0,8}转账|转账.{0,5}(?:建议|提案)|propose.{0,8}transfer", text, re.I): values.append("PROPOSE_TRANSFER")
        if re.search(r"(?:提出|提交|建议|协助|提案).{0,8}锁卡|锁卡.{0,5}(?:建议|提案)|propose.{0,8}(?:lock|freeze)", text, re.I): values.append("PROPOSE_CARD_LOCK")
        return values or None
    if field == "proposal_kind":
        return "CARD_LOCK" if re.fullmatch(r"锁卡(?:建议|提案)?|card\s+lock", text, re.I) else "TRANSFER" if re.fullmatch(r"转账(?:建议|提案)?|transfer", text, re.I) else None
    if field == "limit_period":
        return "MONTH" if re.fullmatch(r"每月|月累计|月合计|monthly", text, re.I) else "PER_PROPOSAL" if re.fullmatch(r"单笔|每笔|per\s+proposal", text, re.I) else None
    if field == "participant_names":
        names = re.split(r"\s*(?:、|和|与|and|,)\s*", re.sub(r"^(?:参与者|参与人|联系人|名单|participants?)\s*[:：]?\s*", "", text.strip(), flags=re.I))
        return names if 1 <= len(names) <= 20 and len(set(names)) == len(names) and all(_parse_field("payee_name", name, kind, {}) for name in names) else None
    if field == "memo":
        # Reuse the source-preserving bank parser, including original quote
        # punctuation. An arbitrary string is not a valid memo SET here.
        from .intelligence import rule_proposal
        actions = rule_proposal("给收款人转1元，" + text).actions
        return actions[0].slots.memo if len(actions) == 1 and actions[0].kind == "TRANSFER" else None
    return None


def _quantity_replacement(text, kind, slots):
    """Only a current explicit replacement changes mutually exclusive axes."""
    if kind not in {"WEALTH_BUY", "WEALTH_REDEEM"} or slots.get("currency") not in {None, "CNY"}:
        return None
    match = re.fullmatch(r"(?:(?:金额|份额|数量)\s*)?(?:改成|改为|换成|改按|改用|调整为)\s*"
        r"(?:(?:申购|买入|购买|赎回|卖出)\s*)?(?P<body>[^，,。；;\n]{1,50})", text.strip())
    if not match:
        return None
    body = match.group("body")
    units = _parse_field("units", body, kind, slots) if re.search(r"份|units?|shares?", body, re.I) else None
    money = _money(body, "CNY")
    if units is not None and money is None:
        return {"set_field": "units", "set_value": units, "clear_field": "amount_minor",
            "body": body, "body_start": match.start("body"), "body_end": match.end("body")}
    if money and money[1] == "CNY" and units is None:
        return {"set_field": "amount_minor", "set_value": money[0], "clear_field": "units",
            "body": body, "body_start": match.start("body"), "body_end": match.end("body")}
    return None


def _clear_allowed(field, text):
    label = _FIELD_LABELS.get(field)
    return bool(label and re.fullmatch(r"(?:不要|无需|不加|清空|去掉|删除|clear|remove)\s*(?:" + label + r")", text.strip(), re.I))


def _denied_existing_target(field, text, slots):
    target = slots.get(field)
    if field != "payee_name" or not isinstance(target, str) or not target:
        return False
    return bool(re.fullmatch(r"(?:我说的)?不是(?:给|转给)?\s*" + re.escape(target) + r"[。.!！]?", text.strip()))


def _literal_reply_boundary(message):
    """A whole negative/stop/source reply is not an entity selection.

    Apply to the complete current reply, never to an object's embedded words:
    names in a positive command such as 给“房东”转账 remain ordinary data.
    """
    from .owned_user_dialogue import BOUNDARY
    text = message.strip()
    if re.fullmatch(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|\x27[^\x27\n]*\x27', text):
        return True
    if not BOUNDARY.search(text) and not re.match(r"(?:别|不用|不需要)(?:再)?转", text):
        return False
    return bool(re.fullmatch(r"(?:我说的)?不是(?:给|转给)?[^，,。；;\n]{1,80}[。.!！]?|"
        r"(?:不要|别|不用|不需要)(?:再)?(?:转(?:账|帐|了)?|付款|操作|执行)(?:了)?[。.!！]?|"
        r"(?:取消|撤销)(?:当前|刚才的)?(?:转账|付款|任务|操作)[。.!！]?|算了[。.!！]?", text))


def _change(message, ordinal, field, value, start, end, *, operation="SET"):
    return {"ordinal": ordinal, "field": field, "operation": operation, "value": value,
        "span": {"start": start, "end": end}, "span_hash": canonical_hash({"text": message[start:end]})}


def _auto_changes(message, actions):
    """Field replies target the named unique action, else a sole action only."""
    result = []
    focused = None
    clauses = list(re.finditer(r"[^，,；;\n]+", message))
    for clause in clauses:
        body = clause.group().strip()
        start = clause.start() + len(clause.group()) - len(clause.group().lstrip())
        named = []
        for index, action in enumerate(actions):
            names = [value for field, value in action["slots"].items() if field in _STRING_FIELDS and isinstance(value, str)]
            if any(_named_literal(name, body) for name in names):
                named.append(index)
        if len(named) > 1 or not named and len(actions) != 1:
            if not named and focused is not None and re.search(r"继续|刚才|之前|回到|接着|\b(?:resume|continue|previous)\b", message, re.I):
                index = focused
            else:
                continue
        else:
            index = named[0] if named else 0
        if len(named) == 1:
            focused = named[0]
        action = actions[index]
        allowed = ACTION_ALLOWED_FIELDS.get(action["kind"], set())
        slots = action["slots"]
        if action["kind"] in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
            from .subscription_intent import subscription_field_spans, subscription_field_reply_safe
            if not subscription_field_reply_safe(body):continue
            fields=subscription_field_spans(body)
            if fields:
                for field,value,left,right in fields:
                    if field in allowed:result.append(_change(message,index,field,value,start+left,start+right))
                continue
        replacement = _quantity_replacement(body, action["kind"], slots)
        if replacement and len(clauses) == 1:
            result.append(_change(message, index, replacement["set_field"], replacement["set_value"],
                start + replacement["body_start"], start + replacement["body_end"]))
            if replacement["set_field"] == "amount_minor" and "currency" in allowed:
                money = _money(replacement["body"], "CNY")
                denomination = re.search(r"人民币|CNY|RMB|元", replacement["body"], re.I) if money and money[2] else None
                if denomination:
                    result.append(_change(message, index, "currency", "CNY",
                        start + replacement["body_start"] + denomination.start(),
                        start + replacement["body_start"] + denomination.end()))
            if slots.get(replacement["clear_field"]) is not None:
                clear = _change(message, index, replacement["clear_field"], None, start, start + len(body), operation="CLEAR")
                clear["source"] = "USER_EXPLICIT_QUANTITY_REPLACEMENT"
                result.append(clear)
            continue
        amount_body = body
        amount_start = start
        for name in [value for field, value in slots.items() if field in _STRING_FIELDS and isinstance(value, str)]:
            if amount_body.casefold().startswith(name.casefold()):
                amount_body = amount_body[len(name):].lstrip()
                amount_start = start + body.index(amount_body) if amount_body else start + len(body)
                break
        # Context-proved amount applies to its operation, not a different
        # missing field. Duplicate/contradictory assignments fail below.
        money = _money(amount_body, slots.get("currency"))
        if not money and named and re.search(r"继续|刚才|之前|回到|接着|\b(?:resume|continue|previous)\b", body, re.I):
            stated = [row for row in rule_proposal(body).actions if row.kind == action["kind"] and row.slots.amount_minor]
            if len(stated) == 1:
                from .intelligence import _cny_money_evidence, _usd_money_evidence
                evidence = (_usd_money_evidence if slots.get("currency") == "USD" else _cny_money_evidence)(body)
                matching = [(left, right) for left, right, value in evidence if value == stated[0].slots.amount_minor]
                if len(matching) == 1:
                    left, right = matching[0]
                    amount_body, amount_start = body[left:right], start + left
                    money = _money(amount_body, slots.get("currency"))
        money_fields = allowed & {"amount_minor", "maximum_amount_minor", "target_minor"}
        if money and len(money_fields) == 1:
            result.append(_change(message, index, next(iter(money_fields)), money[0], amount_start, start + len(body)))
            if money[2] and "currency" in allowed:
                denomination = re.search(r"人民币|CNY|RMB|美元|美金|USD|元", amount_body, re.I)
                if denomination:
                    result.append(_change(message, index, "currency", money[1], amount_start + denomination.start(), amount_start + denomination.end()))
            continue
        for field in ("card_number", "card_last4", "units", "expires_days", "scheduled_at", "scopes", "participant_names", "participant_count", "limit_period", "proposal_kind", "memo"):
            if field not in allowed:
                continue
            if field == "participant_names" and not re.match(r"参与|联系人|名单|participants?", body, re.I):
                continue
            if field == "participant_count" and not re.search(r"人|people|participants?", body, re.I):
                continue
            if field == "expires_days" and not re.search(r"天|周|星期|礼拜|days?|weeks?", body, re.I):
                continue
            if field == "units" and not re.search(r"份|units?", body, re.I):
                continue
            value = _parse_field(field, body, action["kind"], slots)
            if value is not None:
                result.append(_change(message, index, field, value, start, start + len(body)))
                break
        else:
            missing = [field for field in _AUTO_OBJECT_FIELDS & allowed if slots.get(field) is None]
            object_text = not re.search(r"你是|谁|什么|怎么|为什么|如何|随便|算了|\b(?:who|what|why|how)\b", body, re.I) \
                and not re.fullmatch(r"(?:还是|改成|改为|用|是)?\s*(?:人民币|美元|美金|CNY|USD|RMB)", body, re.I)
            if object_text and not _literal_reply_boundary(message) and len(missing) == 1 and _parse_field(missing[0], body, action["kind"], slots) is not None:
                result.append(_change(message, index, missing[0], body, start, start + len(body)))
    return result


def _normalized_changes(message, actions, changes):
    if any(action['kind'] in {'SUBSCRIPTION_START','SUBSCRIPTION_RENEW'} for action in actions):
        from .subscription_intent import subscription_field_reply_safe
        if not subscription_field_reply_safe(message):return None
    if changes is None:
        changes = _auto_changes(message, actions)
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        return None
    normalized = []
    for value in changes:
        if not isinstance(value, dict):
            return None
        ordinal = value.get("ordinal")
        if ordinal is None and isinstance(value.get("action_ref"), str):
            try:
                ordinal = int(value["action_ref"].rsplit(":", 1)[1])
            except (ValueError, IndexError):
                return None
        if type(ordinal) is not int or not 0 <= ordinal < len(actions):
            return None
        field, operation = value.get("field"), value.get("operation", "SET")
        action = actions[ordinal]
        if field not in ACTION_ALLOWED_FIELDS.get(action["kind"], set()) or operation not in {"SET", "CLEAR"}:
            return None
        raw = _raw_span(message, value.get("span"))
        if raw is None:
            return None
        if operation == "SET" and field in _STRING_FIELDS and _literal_reply_boundary(message):
            return None
        if not _target_bound(message, actions, ordinal, field, value["span"]):
            return None
        parsed = _parse_field(field, raw, action["kind"], action["slots"]) if operation == "SET" else None
        replacement = _quantity_replacement(raw, action["kind"], action["slots"]) if operation == "CLEAR" and raw.strip() == message.strip() else None
        implicit_clear = bool(replacement and replacement["clear_field"] == field and action["slots"].get(field) is not None)
        denied_target = operation == "CLEAR" and _denied_existing_target(field, raw, action["slots"]) and raw.strip() == message.strip()
        if operation == "SET" and (parsed is None or parsed != value.get("value") or type(parsed) is not type(value.get("value"))) \
            or operation == "CLEAR" and (value.get("value") is not None or not (_clear_allowed(field, raw) or implicit_clear or denied_target)):
            return None
        change = _change(message, ordinal, field, deepcopy(parsed), value["span"]["start"], value["span"]["end"], operation=operation)
        if implicit_clear:
            change["source"] = "USER_EXPLICIT_QUANTITY_REPLACEMENT"
        elif denied_target:
            change["source"] = "USER_EXPLICIT_TARGET_RETRACTION"
        normalized.append(change)
    if len({(row["ordinal"], row["field"]) for row in normalized}) != len(normalized):
        return None  # Two conflicting assignments never silently select one.
    if len(actions) == 1:
        replacement = _quantity_replacement(message.strip(), actions[0]["kind"], actions[0]["slots"])
        if replacement and actions[0]["slots"].get(replacement["clear_field"]) is not None \
            and any(row["field"] == replacement["set_field"] and row["operation"] == "SET" and row["value"] == replacement["set_value"] for row in normalized) \
            and not any(row["field"] == replacement["clear_field"] for row in normalized):
            clear = _change(message, 0, replacement["clear_field"], None, 0, len(message), operation="CLEAR")
            clear["source"] = "USER_EXPLICIT_QUANTITY_REPLACEMENT"
            normalized.append(clear)
    return normalized


def _target_bound(message, actions, ordinal, field, span):
    """The model cannot assign an unlabelled sum to one of two real actions."""
    if len(actions) == 1:
        return True
    clause_start = max([match.end() for match in re.finditer(r"[，,；;\n]", message[:span["start"]])] or [0])
    end_match = re.search(r"[，,；;\n]", message[span["end"]:])
    clause_end = span["end"] + end_match.start() if end_match else len(message)
    clause = message[clause_start:clause_end]
    eligible = [index for index, action in enumerate(actions) if field in ACTION_ALLOWED_FIELDS.get(action["kind"], set())]
    named = []
    for index in eligible:
        for key, name in actions[index]["slots"].items():
            if key in _STRING_FIELDS and isinstance(name, str) and name and _named_literal(name, clause):
                named.append(index); break
    if named:
        return len(named) == 1 and named[0] == ordinal
    position = re.search(r"第([一二三四五六七八\d]+)(?:笔|项|个|步)", clause)
    if position:
        labels = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
        number = labels.get(position.group(1), int(position.group(1)) if position.group(1).isdigit() else 0)
        return number - 1 == ordinal
    if len(eligible) == 1:
        return eligible[0] == ordinal
    if re.search(r"继续|刚才|之前|回到|接着|\b(?:resume|continue|previous)\b", message, re.I):
        # Explicitly returning to a named operation in an earlier clause is
        # different from selecting the most recent arbitrary unfinished goal.
        preceding = message[:clause_start]
        mentions = [(match.start(), index) for index in eligible for key, name in actions[index]["slots"].items()
            if key in _STRING_FIELDS and isinstance(name, str) and name
            for match in re.finditer(re.escape(name), preceding)]
        if mentions:
            last_position = max(position for position, _ in mentions)
            named_indices = {index for position, index in mentions if position == last_position}
            return named_indices == {ordinal}
    missing = [index for index in eligible if actions[index]["slots"].get(field) is None]
    return len(missing) == 1 and missing[0] == ordinal


def _apply(actions, changes):
    result = deepcopy(actions)
    for change in changes:
        slots = result[change["ordinal"]]["slots"]
        if change["operation"] == "CLEAR":
            slots.pop(change["field"], None)
        else:
            slots[change["field"]] = deepcopy(change["value"])
    return _actions(_proposal(result))


def _card_targets_consistent(session, principal, actions):
    for action in actions:
        number, tail = action["slots"].get("card_number"), action["slots"].get("card_last4")
        if number and tail and number[-4:].casefold() != tail.casefold():
            from .card_identifiers import resolve_owned_cards
            numbers = resolve_owned_cards(session, principal.sandbox_id, principal.user_id, number)
            tails = resolve_owned_cards(session, principal.sandbox_id, principal.user_id, tail)
            if len(numbers) != 1 or len(tails) != 1 or numbers[0].id != tails[0].id:
                return False
    return True


def _source_ref(row):
    return {"turn_id": row.id, "sequence": row.sequence, "message_hash": canonical_hash({"message": row.message})}


def _snapshot(session, task):
    record = session.get(RuntimeRecord, task.id)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record and record.kind == "PARENT" and record.plan_version else None
    return record, snapshot


def _replay_legacy_sources(task_id, task_version, rows):
    """Recompose existing field contracts from raw receipts, not a summary."""
    from types import SimpleNamespace
    from .conversation_service import (_short_continuation, _short_recipient_continuation,
        _short_wealth_units_continuation, _short_quantity_replacement, _short_memo_continuation)
    from .owned_user_dialogue import _same_pending_restatement
    if not rows or not 1 <= len(rows) <= MAX_LEGACY_SOURCE_TURNS:
        return None
    message = rows[0].message
    for row in rows[1:]:
        source = SimpleNamespace(id=task_id, version=task_version, message=message)
        updated = _short_quantity_replacement(source, row.message, row.id) or \
            _short_wealth_units_continuation(source, row.message, row.id) or \
            _short_memo_continuation(source, row.message, row.id) or \
            _short_recipient_continuation(source, row.message, row.id) or \
            _short_continuation(source, row.message, row.id)
        if updated:
            message = updated[0]
        elif _same_pending_restatement([message], rule_proposal(message).actions, row.message):
            message = row.message
        else:
            return None
    return message


def _reference_changes(session, principal, thread, message, actions, reference_binding):
    if reference_binding is None:
        return [], None
    if not isinstance(reference_binding, dict) or not isinstance(reference_binding.get("catalog"), dict):
        return None, None
    from .dialogue_references import resolve_user_reference
    actual = resolve_user_reference(session, principal, thread, message, reference_binding["catalog"],
        domain=reference_binding.get("domain"), focus_entity_ref=reference_binding.get("focus_entity_ref"))
    if actual is None or actual.get("status") != "RESOLVED" or actual != reference_binding.get("resolution"):
        return None, None
    choices = [index for index, action in enumerate(actions) if set(actual["expected_slots"]) <= ACTION_ALLOWED_FIELDS.get(action["kind"], set())
        and all(action["slots"].get(field) in {None, value} for field, value in actual["expected_slots"].items())]
    if len(choices) != 1 or not actual["expected_slots"]:
        return None, None
    rows = [{"ordinal": choices[0], "field": field, "operation": "SET", "value": value,
        "source": "VERIFIED_RESULT_REFERENCE", "entity_ref": actual["entity_ref"], "result_ref": actual["result_ref"]}
        for field, value in actual["expected_slots"].items()]
    return rows, deepcopy(reference_binding)


@dataclass(frozen=True)
class VerifiedSlotUpdate:
    authority: object
    utterances: tuple[str, ...]
    expected_actions: tuple[dict, ...]
    field_sources: tuple[dict, ...]
    task_id: str
    private_reference_fields: tuple[tuple[int, str], ...] = ()

    @property
    def expected_proposal(self):
        return _proposal(self.expected_actions, self.utterances[-1])

    def public(self):
        safe_actions = deepcopy(list(self.expected_actions))
        selected_objects = []
        for ordinal, field in self.private_reference_fields:
            value = safe_actions[ordinal]["slots"].pop(field, None)
            selected_objects.append({"action_ref": "operation:" + str(ordinal), "field": field,
                "label": "尾号" + value[-4:] if isinstance(value, str) else "已选择的对象",
                "source": "SERVER_VERIFIED_REFERENCE_NOT_A_NEW_USER_AUTHORIZATION"})
        return {"version": VERSION, "authority": "VERIFIED_USER_FIELDS_NOT_BANK_FACTS_OR_CONFIRMATION",
            "original_user_messages": list(self.utterances[:-1]), "latest_user_message": self.utterances[-1],
            "expected_actions": safe_actions, "selected_objects": selected_objects,
            "instruction": "这是原用户需求的逐动作信息补全。保留未修改的对象、字段及依赖；不能增加动作、猜测字段、代替确认或重发已完成操作。"}


def build_slot_update(session, principal, thread, turn, task, *, changes=None, reference_binding=None):
    """Build before mutating Task; caller persists returned proof with revision."""
    _, scope_hash = dialogue_actor_scope(session, principal)
    if isinstance(thread, str): thread = session.get(ChatThread, thread)
    if not _owned(task, principal) or not _lifecycle(session, task) or not _turn_valid(session, principal, thread, turn, scope_hash):
        return None
    if _intent_text(turn.message) != turn.message or re.search(r"系统指令|忽略规则|新话题|换个话题", turn.message):
        return None
    from .dialogue_brain import local_dialogue_kind
    from .intelligence import _UNMET_CONDITION, _GLOBAL_NO_WRITE
    from .language import EN_NO_WRITE, EN_UNMET_CONDITION
    if local_dialogue_kind(turn.message) in {"GREETING", "DISCOVERY", "FEEDBACK"} \
        or _UNMET_CONDITION.search(turn.message) or _GLOBAL_NO_WRITE.search(turn.message) or EN_NO_WRITE.search(turn.message) or EN_UNMET_CONDITION.search(turn.message):
        return None
    if changes is not None:
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES or any(not isinstance(change, dict) for change in changes):
            return None
        for change in changes:
            ref = change.get("action_ref")
            if ref is not None and (not isinstance(ref, str) or ref != "action:" + task.id + ":" + str(change.get("ordinal", ref.rsplit(":", 1)[-1]))):
                return None
    record, snapshot = _snapshot(session, task)
    if record is None or snapshot is None or snapshot.plan_hash != task.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
        return None
    prior = (record.metadata_json or {}).get(METADATA_KEY)
    if prior:
        verified = _load_binding(session, task, prior, principal=principal)
        genesis, old_updates = deepcopy(prior["genesis"]), deepcopy(prior["updates"])
        current_actions = deepcopy(list(verified.expected_actions))
    else:
        source = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.task_id == task.id,
            ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
            ChatTurn.sequence < turn.sequence, ChatTurn.mode.in_(["NEW_TASK", "REVISE", "CLARIFY"]), ChatTurn.status == "ANSWERED")
            .order_by(ChatTurn.sequence.desc()).limit(1))
        if source is None or not _turn_valid(session, principal, thread, source, scope_hash, prior=True):
            return None  # An unchecked old composed command is not raw user authority.
        proposal = IntentProposal.model_validate(snapshot.proposal_json)
        reference_genesis = (record.metadata_json or {}).get("dialogue_result_reference")
        legacy_sources = None
        if reference_genesis:
            from .dialogue_reference_integration import load_reference_genesis
            checked = load_reference_genesis(session, task, reference_genesis)
            if _actions(checked.expected_proposal) != _actions(proposal) or reference_genesis.get("current_ref") != _source_ref(source):
                return None
        elif source.message != task.message:
            if (record.metadata_json.get("conversation_context") or {}).get("authority") != "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS":
                return None
            from .conversation_provenance import validate_continuation_proposal
            validate_continuation_proposal(session, task, proposal, record.metadata_json)
            legacy_sources = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.task_id == task.id,
                ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
                ChatTurn.sequence <= source.sequence, ChatTurn.mode.in_(["NEW_TASK", "REVISE", "CLARIFY"]), ChatTurn.status == "ANSWERED")
                .order_by(ChatTurn.sequence.asc()).limit(MAX_LEGACY_SOURCE_TURNS + 1)))
            if not 1 <= len(legacy_sources) <= MAX_LEGACY_SOURCE_TURNS or any(not _turn_valid(session, principal, thread, row, scope_hash, prior=True)
                for row in legacy_sources) or _replay_legacy_sources(task.id, task.version, legacy_sources) != task.message \
                or _actions(ground_proposal(proposal, task.message)) != _actions(proposal):
                return None
        elif _actions(ground_proposal(proposal, source.message)) != _actions(proposal) or not proposal.actions:
            return None
        prior_metadata = {key: deepcopy(record.metadata_json.get(key))
            for key in ("owned_user_dialogue", "conversation_context", "pending_subject_selection", "input_revision", "dialogue_result_reference")}
        genesis = {"task_id": task.id, "task_version": task.version, "snapshot_id": snapshot.id,
            "snapshot_hash": canonical_hash(snapshot.proposal_json), "plan_hash": snapshot.plan_hash,
            "source_ref": _source_ref(source), "original_message": task.message,
            "actions": _actions(proposal), "prior_metadata": prior_metadata,
            "prior_metadata_hash": canonical_hash(prior_metadata),
            "type": "VERIFIED_RESULT_REFERENCE" if reference_genesis else "LEGACY_RAW_REPLAY" if legacy_sources else "RAW_USER_SNAPSHOT",
            "source_refs": [_source_ref(row) for row in legacy_sources] if legacy_sources else [_source_ref(source)],
            "reference_binding_hash": canonical_hash(reference_genesis) if reference_genesis else None}
        old_updates, current_actions = [], deepcopy(genesis["actions"])
    current_intent = rule_proposal(turn.message).actions
    if current_intent:
        known_kinds = {action["kind"] for action in current_actions}
        # An independent balance/subscription query is not a literal name for
        # the pending recipient. Nor may a new unrelated write be erased into
        # an old field update. The semantic controller handles new requests.
        object_reply = len(current_actions) == 1 and not re.search(r"查询|查看|查一下|看看|分析|比较|列出|\b(?:query|check|show|analy[sz]e|compare|list)\b", turn.message, re.I) \
            and any(current_actions[0]["slots"].get(field) is None and _parse_field(field, turn.message,
                current_actions[0]["kind"], current_actions[0]["slots"]) is not None
                for field in {"merchant", "product_name", "grantee_name", "behavior_name", "reward_name", "card_last4", "card_number"}
                & ACTION_ALLOWED_FIELDS.get(current_actions[0]["kind"], set()))
        scope_reply = len(current_actions) == 1 and current_actions[0]["kind"] == "FAMILY_GRANT" \
            and _parse_field("scopes", turn.message, "FAMILY_GRANT", current_actions[0]["slots"]) is not None \
            and bool(re.match(r"(?:只)?(?:让|允许)(?:他|她|当前协助人)|(?:权限|范围)(?:改成|改为|是|为)?|只(?:能|允许)?(?:看|查|提出|提交)", turn.message))
        if not scope_reply and (any(action.kind not in known_kinds and not (action.kind in READ_KINDS and object_reply) for action in current_intent) \
            or any(action.kind in READ_KINDS for action in current_intent) and not object_reply):
            return None
        numeric_only = all(set(action.slots.model_dump(exclude_none=True)) <= {"amount_minor", "currency", "units", "subscription_months", "subscription_auto_renew"}
            for action in current_intent)
        returning_same = re.search(r"继续|刚才|之前|回到|接着|\b(?:resume|continue|previous)\b", turn.message, re.I) and all(
            any(row["kind"] == action.kind and all(row["slots"].get(field) == value for field, value in action.slots.model_dump(exclude_none=True).items()
                if field in _STRING_FIELDS) for row in current_actions) for action in current_intent)
        if not numeric_only and not object_reply and not scope_reply and not returning_same and (changes is None or not re.search(r"改成|改为|修改|这笔|这项|这次|补充|继续|还是|\b(?:change|revise|continue|instead)\b", turn.message, re.I)):
            return None
    if len(old_updates) >= MAX_TURNS:
        return None
    parsed = _normalized_changes(turn.message, current_actions, changes)
    references, reference = _reference_changes(session, principal, thread, turn.message, current_actions, reference_binding)
    if reference_binding is not None and references is None:
        return None
    combined = (parsed or []) + (references or [])
    if not combined or len({(row["ordinal"], row["field"]) for row in combined}) != len(combined):
        return None
    expected = _apply(current_actions, combined)
    if not _card_targets_consistent(session, principal, expected):
        return None
    update = {"source_ref": _source_ref(turn), "changes": combined,
        "reference_binding": reference, "before_hash": canonical_hash(current_actions), "after_hash": canonical_hash(expected)}
    return {"version": VERSION, "authority": "RAW_USER_SLOT_LEDGER_NOT_BANK_AUTHORIZATION", "conversation_id": thread.id,
        "task_id": task.id, "actor_scope_hash": scope_hash, "genesis": genesis, "updates": old_updates + [update],
        "expected_actions": expected, "latest_message_hash": canonical_hash({"message": turn.message})}


def _load_binding(session, parent, binding, *, principal=None, execution_context=None):
    from .services import Principal
    if principal is None:
        record = session.get(RuntimeRecord, parent.id)
        principal = Principal(parent.user_id, parent.sandbox_id, family_context=(record.metadata_json or {}).get("family_context_origin") if record else None)
    _, scope_hash = dialogue_actor_scope(session, principal)
    if not isinstance(binding, dict) or binding.get("version") != VERSION or binding.get("authority") != "RAW_USER_SLOT_LEDGER_NOT_BANK_AUTHORIZATION" \
        or binding.get("task_id") != parent.id or binding.get("actor_scope_hash") != scope_hash or not _owned(parent, principal) \
        or not _lifecycle(session, parent, execution_context=execution_context, allow_completed=True):
        _deny()
    thread = session.get(ChatThread, binding.get("conversation_id"))
    genesis = binding.get("genesis")
    updates = binding.get("updates")
    if not isinstance(genesis, dict) or genesis.get("task_id") != parent.id or not isinstance(updates, list) or not 1 <= len(updates) <= MAX_TURNS:
        _deny()
    if not isinstance(genesis.get("prior_metadata"), dict) or canonical_hash(genesis["prior_metadata"]) != genesis.get("prior_metadata_hash"):
        _deny()
    snapshot = session.get(PlanSnapshot, genesis.get("snapshot_id"))
    source = session.get(ChatTurn, (genesis.get("source_ref") or {}).get("turn_id"))
    if snapshot is None or snapshot.task_id != parent.id or canonical_hash(snapshot.proposal_json) != genesis.get("snapshot_hash") \
        or snapshot.plan_hash != genesis.get("plan_hash") or canonical_hash(snapshot.plan_json) != snapshot.plan_hash \
        or source is None or source.task_id != parent.id or not _turn_valid(session, principal, thread, source, scope_hash, prior=True) \
        or _source_ref(source) != genesis.get("source_ref"):
        _deny()
    proposal = IntentProposal.model_validate(snapshot.proposal_json)
    if _actions(proposal) != genesis.get("actions"):
        _deny()
    if genesis.get("type") == "VERIFIED_RESULT_REFERENCE":
        from .dialogue_reference_integration import load_reference_genesis
        reference = genesis["prior_metadata"].get("dialogue_result_reference")
        if not reference or canonical_hash(reference) != genesis.get("reference_binding_hash") or reference.get("current_ref") != _source_ref(source):
            _deny()
        checked = load_reference_genesis(session, parent, reference, execution_context=execution_context)
        if _actions(checked.expected_proposal) != _actions(proposal):
            _deny()
    elif genesis.get("type") == "LEGACY_RAW_REPLAY":
        refs = genesis.get("source_refs")
        if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_LEGACY_SOURCE_TURNS:
            _deny()
        rows = [session.get(ChatTurn, ref.get("turn_id")) for ref in refs if isinstance(ref, dict)]
        if len(rows) != len(refs) or any(row is None or row.task_id != parent.id or not _turn_valid(session, principal, thread, row, scope_hash, prior=True)
                or _source_ref(row) != ref for row, ref in zip(rows, refs)) \
            or rows[-1].id != source.id or any(rows[index].sequence >= rows[index + 1].sequence for index in range(len(rows) - 1)) \
            or _replay_legacy_sources(parent.id, genesis.get("task_version"), rows) != genesis.get("original_message") \
            or _actions(ground_proposal(proposal, genesis["original_message"])) != _actions(proposal):
            _deny()
    elif genesis.get("type") != "RAW_USER_SNAPSHOT" or source.message != genesis.get("original_message") \
        or _actions(ground_proposal(proposal, source.message)) != _actions(proposal):
        _deny()
    expected = deepcopy(genesis["actions"])
    utterances = [row.message for row in rows] if genesis.get("type") == "LEGACY_RAW_REPLAY" else [source.message]
    fields, previous_sequence = [], source.sequence
    for update in updates:
        if not isinstance(update, dict): _deny()
        row = session.get(ChatTurn, (update.get("source_ref") or {}).get("turn_id"))
        if row is None or row.task_id != parent.id or not previous_sequence < row.sequence \
            or not _turn_valid(session, principal, thread, row, scope_hash, prior=True) or _source_ref(row) != update.get("source_ref") \
            or canonical_hash(expected) != update.get("before_hash"):
            _deny()
        literals = [change for change in update.get("changes", []) if change.get("source") != "VERIFIED_RESULT_REFERENCE"]
        actual = _normalized_changes(row.message, expected, literals) if literals else []
        if actual is None or actual != literals:
            _deny()
        references, reference = _reference_changes(session, principal, thread, row.message, expected, update.get("reference_binding"))
        if references is None or actual + references != update.get("changes"):
            _deny()
        expected = _apply(expected, actual + references)
        if canonical_hash(expected) != update.get("after_hash"):
            _deny()
        for change in update["changes"]:
            fields.append({"action_ref": "action:" + parent.id + ":" + str(change["ordinal"]), "kind": expected[change["ordinal"]]["kind"],
                "field": change["field"], "value": deepcopy(change["value"]), "operation": change["operation"],
                "turn_id": row.id, "span": deepcopy(change.get("span")), "span_hash": change.get("span_hash"),
                "message_hash": canonical_hash({"message": row.message}), "source": change.get("source", "USER_CURRENT")})
        utterances.append(row.message); previous_sequence = row.sequence
    if expected != binding.get("expected_actions") or canonical_hash({"message": utterances[-1]}) != binding.get("latest_message_hash") \
        or parent.message != utterances[-1] or not _card_targets_consistent(session, principal, expected):
        _deny()
    private_fields = tuple((index, "card_number") for index, action in enumerate(expected)
        if isinstance(action["slots"].get("card_number"), str)
        and not any(action["slots"]["card_number"] in message for message in utterances))
    return VerifiedSlotUpdate(_AUTHORITY, tuple(utterances), tuple(expected), tuple(fields), parent.id, private_fields)


def load_verified_slot_update(session, parent, metadata, *, execution_context=None):
    binding = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    try:
        return _load_binding(session, parent, binding, execution_context=execution_context) if binding else None
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        _deny()


def _paused_shadow(session, principal, task, binding):
    """Private draft verification does not claim to be an executable plan."""
    from types import SimpleNamespace
    record = session.get(RuntimeRecord, task.id)
    pause = (record.metadata_json or {}).get("dialogue_pause") if record else None
    _, scope = dialogue_actor_scope(session, principal)
    if not isinstance(pause, dict) or pause.get("actor_scope_hash") != scope or pause.get("intent_message_hash") != canonical_hash({"message": task.message}) \
        or not _lifecycle(session, task) or not isinstance(binding, dict):
        _deny()
    source = session.get(ChatTurn, binding.get("updates", [{}])[-1].get("source_ref", {}).get("turn_id"))
    if source is None:
        _deny()
    return SimpleNamespace(id=task.id, user_id=task.user_id, sandbox_id=task.sandbox_id,
        state=task.state, version=task.version, plan_hash=task.plan_hash, message=source.message)


def validate_paused_slot_update(session, principal, task, binding):
    return _load_binding(session, _paused_shadow(session, principal, task, binding), binding, principal=principal)


def build_paused_slot_update(session, principal, thread, turn, task, *, changes=None):
    record = session.get(RuntimeRecord, task.id)
    pause = (record.metadata_json or {}).get("dialogue_pause") if record else None
    if not isinstance(pause, dict):
        return None
    previous = pause.get("pending_slot_update")
    if not previous:
        return build_slot_update(session, principal, thread, turn, task, changes=changes)
    verified = validate_paused_slot_update(session, principal, task, previous)
    _, scope_hash = dialogue_actor_scope(session, principal)
    if not _turn_valid(session, principal, thread, turn, scope_hash) or len(previous["updates"]) >= MAX_TURNS:
        return None
    current_actions = deepcopy(list(verified.expected_actions))
    # Pure field replies only; a new independent request is not smuggled into
    # the paused intent by parsing its whole sentence as a recipient name.
    offered = rule_proposal(turn.message).actions
    if changes is None and offered and any(action.kind in READ_KINDS or set(action.slots.model_dump(exclude_none=True)) - {"amount_minor", "currency", "units"} for action in offered):
        return None
    parsed = _normalized_changes(turn.message, current_actions, changes)
    if not parsed:
        return None
    expected = _apply(current_actions, parsed)
    if not _card_targets_consistent(session, principal, expected):
        return None
    update = {"source_ref": _source_ref(turn), "changes": parsed, "reference_binding": None,
        "before_hash": canonical_hash(current_actions), "after_hash": canonical_hash(expected)}
    return {**deepcopy(previous), "updates": [*deepcopy(previous["updates"]), update], "expected_actions": expected,
        "latest_message_hash": canonical_hash({"message": turn.message})}


def ground_slot_update_proposal(proposal, message, context):
    verified = (context or {}).get(PRIVATE_KEY)
    if not isinstance(verified, VerifiedSlotUpdate) or verified.authority is not _AUTHORITY or verified.utterances[-1] != message:
        return ground_proposal(proposal, message)
    offered = _actions(proposal)
    expected = deepcopy(list(verified.expected_actions))
    if len(offered) == len(expected):
        for ordinal, field in verified.private_reference_fields:
            if offered[ordinal]["kind"] != expected[ordinal]["kind"]:
                continue
            if field == "card_number" and "card_last4" in offered[ordinal]["slots"] and "card_last4" not in expected[ordinal]["slots"]:
                # Public prompt deliberately displays only this source-verified
                # card's tail. That exact tail is a harmless representation of
                # its immutable private PAN, never a new object selection.
                tail = offered[ordinal]["slots"]["card_last4"]
                number = offered[ordinal]["slots"].get("card_number")
                canonical = expected[ordinal]["slots"]["card_number"]
                if not isinstance(tail, str) or tail.casefold() != canonical[-4:].casefold() \
                    or number is not None and number.casefold() != canonical.casefold():
                    return proposal.model_copy(update={"actions": []})
                offered[ordinal]["slots"].pop("card_last4")
                offered[ordinal]["slots"]["card_number"] = canonical
            if field not in offered[ordinal]["slots"]:
                offered[ordinal]["slots"][field] = deepcopy(expected[ordinal]["slots"][field])
        for index, action in enumerate(expected):
            if action["kind"] in {"CARD_SET_LIMIT", "WEALTH_BUY", "WEALTH_REDEEM"}:
                if "currency" not in action["slots"] and offered[index]["slots"].get("currency") == "CNY":
                    offered[index]["slots"].pop("currency")  # Fixed CNY-only schema spelling, never USD conversion.
                elif action["slots"].get("currency") == "CNY" and "currency" not in offered[index]["slots"]:
                    offered[index]["slots"]["currency"] = "CNY"
    if offered != expected:
        return proposal.model_copy(update={"actions": [], "clarification_questions": ["请保留这次明确的信息和未修改的原需求，不增加或改写办理项目。"]})
    return verified.expected_proposal.model_copy(update={"model_label": proposal.model_label, "fallback": proposal.fallback,
        "failure_reasons": proposal.failure_reasons})


def validate_slot_update_proposal(session, parent, proposal, metadata, *, execution_context=None):
    verified = load_verified_slot_update(session, parent, metadata, execution_context=execution_context)
    if verified is not None and _actions(proposal) != list(verified.expected_actions):
        _deny()
    return verified
