"""Private planner/compile/confirmation bridge for verified result references.

An object selected from a previous query supplies identity, not instructions or
money. The current raw user text supplies the new action and its new fields.
Later amendments belong to ``dialogue_slot_updates``; this binding is only the
immutable genesis of that ledger. It never confirms or executes a bank command.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .dialogue_references import (build_result_reference_catalog, public_result_reference_catalog,
    resolve_user_reference, validate_result_reference_catalog, result_reference_explanation_sources, entity_allowed_fields)
from .dialogue_state import ACTION_ALLOWED_FIELDS, dialogue_actor_scope
from .intelligence import (ActionSlots, IntentProposal, ProposedAction, READ_KINDS,
    _intent_text, _cny_money_evidence, _usd_money_evidence, wealth_units_in_text, ground_proposal, rule_proposal)
from .models import Confirmation, Task
from .planning import canonical_hash
from .redaction import redact_credentials
from .runtime_models import RuntimeOperation, RuntimeRecord

VERSION = "owned-result-reference-genesis-v1"
METADATA_KEY = "dialogue_result_reference"
PRIVATE_KEY = "_verified_result_reference"
_AUTHORITY = object()
_PENDING = {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION"}
_CNY_ONLY_REFERENCE_KINDS = frozenset({"CARD_SET_LIMIT", "WEALTH_BUY", "WEALTH_REDEEM"})
_CONSULT = re.compile(r"为什么|为啥|什么时候|何时|什么影响|只解释|只是问|只说说|可以.{0,8}吗|能不能|什么意思|权限|支持.{0,6}吗|\b(?:why|when|explain|how|can\s+I|what\s+about)\b", re.I)
_IMPLICIT_WHY = re.compile(r"^(?:为什么|为啥|怎么).{0,3}(?:这么|那么)(?:高|多|贵|大|少|低).{0,6}[？?。!！]?$|^why\s+(?:(?:is\s+it|was\s+it)\s+)?so\s+(?:high|much|expensive|low)[?.!\s]*$", re.I)


def _deny():
    raise DomainError("DIALOGUE_REFERENCE_SOURCE_CHANGED", "这次选择的对象、需求或权限已经变化，请重新核对；不会重复办理。", 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def _turn_ref(turn):
    return {"turn_id": turn.id, "sequence": turn.sequence, "message_hash": canonical_hash({"message": turn.message})}


def _valid_turn(session, principal, thread, turn, scope_hash, *, answered=False):
    scope, actual = dialogue_actor_scope(session, principal)
    return bool(_owned(thread, principal) and thread.status == "ACTIVE" and _owned(turn, principal)
        and turn.thread_id == thread.id and turn.context_policy == "AUTO" and not turn.reviewed_evidence_refs_json
        and not turn.error_code and not turn.diagnostics_json.get("input_redacted")
        and _intent_text(turn.message) == turn.message and redact_credentials(turn.message) == turn.message
        and (not answered or turn.status == "ANSWERED") and actual == scope_hash
        and turn.diagnostics_json.get("actor_role_at_submission") == scope["role"]
        and turn.diagnostics_json.get("dialogue_actor_scope_hash", actual) == actual
        and (scope["family_context"] is None or turn.diagnostics_json.get("family_context_origin") == scope["family_context"]))


def _focus_candidates(session, principal, thread, turn, catalog):
    """Latest raw user mentions in this selected read, never old-topic focus."""
    source_sequences = [session.get(ChatTurn, row["source_turn_id"]).sequence for row in catalog.get("sources", [])]
    floor = max(source_sequences, default=turn.sequence)
    rows = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.sequence >= floor, ChatTurn.sequence < turn.sequence, ChatTurn.status == "ANSWERED")
        .order_by(ChatTurn.sequence.desc()).limit(16)))
    _, epoch = dialogue_actor_scope(session, principal)
    for row in rows:
        if not _valid_turn(session, principal, thread, row, epoch, answered=True): break
        if row.mode in {"NEW_TASK", "REVISE", "CLARIFY"} and row.task_id != catalog.get("selected_task_id"): break
        if re.search(r"新话题|换个话题|不(?:查|看).{0,6}(?:刚才|之前)|\b(?:new\s+topic|do\s+not\s+inherit)\b", row.message, re.I): break
        yield row


def _prove_focus(session, principal, thread, turn, catalog, domain, entity_ref, receipt=None):
    if not entity_ref:
        return None
    _, epoch = dialogue_actor_scope(session, principal)
    candidates = list(_focus_candidates(session, principal, thread, turn, catalog))
    if receipt is not None:
        candidates = [row for row in candidates if isinstance(receipt, dict) and row.id == receipt.get("turn_id")]
    for source in candidates:
        if not source.sequence < turn.sequence or not _valid_turn(session, principal, thread, source, epoch, answered=True):
            continue
        selected = resolve_user_reference(session, principal, thread, source.message, catalog, domain=domain)
        if selected and selected.get("status") == "RESOLVED" and selected.get("reference_kind") in {"NAMED", "ORDINAL", "LARGEST_TRANSACTION"} \
                and selected.get("entity_ref") == entity_ref:
            proof = {**_turn_ref(source), "actor_scope_hash": epoch, "catalog_hash": catalog["catalog_hash"],
                "entity_ref": entity_ref, "resolution_hash": canonical_hash(selected)}
            if receipt is not None and proof != receipt:
                _deny()
            return proof
    _deny()  # A model-selected entity alone is not a user-focus receipt.


def _inferred_focus(session, principal, thread, turn, catalog, domain, resolution):
    if not (_IMPLICIT_WHY.fullmatch(turn.message.strip()) or re.search(r"这个|那个|这张|那张|它|这类|那类|\b(?:this|that|it)\b", turn.message, re.I)):
        return None
    allowed = set(resolution.get("candidate_refs", [])) if resolution and resolution.get("status") == "AMBIGUOUS" else None
    for source in _focus_candidates(session, principal, thread, turn, catalog):
        prior = resolve_user_reference(session, principal, thread, source.message, catalog, domain=domain)
        if prior and prior.get("status") == "RESOLVED" and prior.get("reference_kind") in {"NAMED", "ORDINAL", "LARGEST_TRANSACTION"}:
            if allowed is not None and prior["entity_ref"] not in allowed: continue
            return prior["entity_ref"]
    return None


def _fresh_slots(message, resolution):
    kind = resolution["expected_action_kind"]
    slots = deepcopy(resolution["expected_slots"])
    # Do not silently erase another independently requested capability.
    literal = rule_proposal(message).actions
    bare_card_ordinal = re.fullmatch(r"\s*(?:只看|仅看|只查看|只显示|仅显示)\s*(?P<reference>第\s*[一二两三四五六七八九十\d]+\s*张(?:银行卡|卡片|卡)?)\s*[。.!！?？]*\s*", message)
    if kind == "CARD_STATUS" and resolution.get("domain") == "CARDS" and resolution.get("reference_kind") == "ORDINAL" \
            and bare_card_ordinal and len(literal) == 1 and literal[0].kind == "BILL_ANALYSIS" \
            and set(literal[0].slots.model_dump(exclude_none=True)) == {"category_filter"} \
            and literal[0].slots.category_filter == bare_card_ordinal["reference"] \
            and not re.search(r"账单|消费|支出|扣款|\b(?:bills?|spending|expenses?|transactions?)\b",message,re.I):
        # The rule's context-free bill-category shortcut misreads "第二张"
        # as a category. Only this complete single card-reference phrase may
        # remove that artificial read; real bill requests stay intact.
        literal = []
    if any(row.kind != kind for row in literal) or len(literal) > 1:
        return None
    if kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
        from .subscription_intent import subscription_request
        request=subscription_request(_intent_text(message))
        if not request or request["kind"] != kind or request["mode"] != "COMMERCE":return None
        slots.update({field:value for field,value in request["slots"].items() if field in {"subscription_months","subscription_auto_renew"}})
    if kind in {"WEALTH_BUY", "WEALTH_REDEEM", "CARD_SET_LIMIT"}:
        money = {value for _, _, value in _cny_money_evidence(message, allow_limits=kind == "CARD_SET_LIMIT")}
        dollars = {value for _, _, value in _usd_money_evidence(message)}
        units = wealth_units_in_text(message) if kind in {"WEALTH_BUY", "WEALTH_REDEEM"} else set()
        if dollars or len(money) > 1 or len(units) > 1 or money and units:
            return None
        if money: slots["amount_minor"] = next(iter(money))
        if units: slots["units"] = next(iter(units))
        # Missing quantity remains missing and is clarified by the kernel.
        if re.search(r"人民币|\bCNY\b|\bRMB\b", message, re.I): slots["currency"] = "CNY"
    if kind == "CARD_STATUS":
        currencies = set()
        if re.search(r"美元|美金|\bUSD\b", message, re.I): currencies.add("USD")
        if re.search(r"人民币|\bCNY\b|\bRMB\b", message, re.I): currencies.add("CNY")
        if len(currencies) > 1: return None
        if currencies: slots["currency"] = next(iter(currencies))
    if not set(slots) <= ACTION_ALLOWED_FIELDS.get(kind, set()):
        return None
    try:
        action = ProposedAction(kind=kind, slots=ActionSlots.model_validate(slots))
    except ValueError:
        return None
    return [{"kind": action.kind, "slots": action.slots.model_dump(exclude_none=True), "depends_on": []}]


def _proposal(actions, message):
    return IntentProposal(summary=message[:500], actions=[ProposedAction(kind=row["kind"],
        slots=ActionSlots.model_validate(row["slots"]), depends_on=list(row["depends_on"])) for row in actions])


def _actions(proposal):
    return [{"kind": row.kind, "slots": row.slots.model_dump(exclude_none=True), "depends_on": list(row.depends_on)} for row in proposal.actions]


def build_result_reference_binding(session, principal, thread, turn, *, catalog=None, origin_task_id=None,
        domain=None, focus_entity_ref=None, focus_receipt=None):
    """Build before accept_task; persist this under METADATA_KEY after linkage.

    Supplied catalogs/focus must be from the backend's dialogue state, never
    accepted from a client or model. Their database proof is still rechecked.
    Existing pending-task field changes should use build_slot_update instead.
    """
    if isinstance(thread, str): thread = session.get(ChatThread, thread)
    _, epoch = dialogue_actor_scope(session, principal)
    if not _valid_turn(session, principal, thread, turn, epoch): return None
    catalog = catalog or build_result_reference_catalog(session, principal, thread,
        task_id=origin_task_id, before_sequence=turn.sequence)
    validate_result_reference_catalog(session, principal, thread, catalog)
    resolution = resolve_user_reference(session, principal, thread, turn.message, catalog,
        domain=domain, focus_entity_ref=focus_entity_ref)
    if focus_entity_ref is None and (resolution is None or resolution.get("status") != "RESOLVED"):
        focus_entity_ref = _inferred_focus(session, principal, thread, turn, catalog, domain, resolution)
        if focus_entity_ref:
            resolution = resolve_user_reference(session, principal, thread, turn.message, catalog,
                domain=domain, focus_entity_ref=focus_entity_ref)
    focus = _prove_focus(session, principal, thread, turn, catalog, domain, focus_entity_ref, focus_receipt)
    if resolution is None and focus_entity_ref and _IMPLICIT_WHY.fullmatch(turn.message.strip()):
        entity = next(item for item in catalog["entities"] if item["entity_ref"] == focus_entity_ref)
        source = session.get(ChatTurn, focus["turn_id"])
        prior = resolve_user_reference(session, principal, thread, source.message, catalog, domain=domain)
        resolution = {**prior, "reference_kind": "FOCUSED_EXPLANATION", "expected_action_kind": entity["read_kind"],
            "expected_slots": {entity["field"]:entity["value"]} if entity_allowed_fields(entity) else {},
            "write_instruction_current":False, "explanation_requested":True,
            "current_message_hash":canonical_hash({"message":turn.message.strip()})}
    if not resolution or resolution.get("status") != "RESOLVED": return None
    actions = _fresh_slots(turn.message, resolution)
    if actions is None: return None
    mode = "WRITE_PROPOSAL" if resolution["write_instruction_current"] else "EXPLANATION" \
        if resolution.get("explanation_requested") or _CONSULT.search(turn.message) else "READ_QUERY"
    return {"version": VERSION, "authority": "CURRENT_RAW_USER_REQUEST_WITH_VERIFIED_OBJECT_NOT_BANK_AUTHORIZATION",
        "conversation_id": thread.id, "actor_scope_hash": epoch, "current_ref": _turn_ref(turn),
        "original_message": turn.message, "catalog": deepcopy(catalog), "resolution": deepcopy(resolution),
        "domain": domain, "focus_entity_ref": focus_entity_ref, "focus_receipt": focus,
        "mode": mode, "expected_actions": actions, "write_authorized": False}


def result_reference_slot_carrier(binding):
    """Same target-reference shape consumed by dialogue_slot_updates."""
    return {key: deepcopy(binding[key]) for key in ("catalog", "resolution", "domain", "focus_entity_ref")}


@dataclass(frozen=True)
class VerifiedResultReference:
    authority: object
    original_message: str
    expected_actions: tuple[dict, ...]
    binding: dict
    task_id: str

    @property
    def expected_proposal(self):
        return _proposal(self.expected_actions, self.original_message)

    def public(self):
        # Full bank target IDs/card numbers stay private. The model selects
        # the opaque entity already proved against the user's current words.
        safe_actions = deepcopy(list(self.expected_actions))
        for action in safe_actions:
            for field in self.binding["resolution"]["expected_slots"]:
                action["slots"].pop(field, None)
        defaults = {"currency": "CNY"} if self.expected_actions[0]["kind"] in _CNY_ONLY_REFERENCE_KINDS \
            and "currency" not in self.expected_actions[0]["slots"] else {}
        return {"version": VERSION, "latest_user_message": self.original_message,
            "selected_entity_ref": self.binding["resolution"]["entity_ref"], "expected_action_kind": self.expected_actions[0]["kind"],
            "current_user_fields": safe_actions[0]["slots"], "schema_defaults": defaults,
            "schema_defaults_authority": "REGISTERED_OPERATION_SCHEMA_NOT_USER_FIELD_OR_FX_AUTHORIZATION",
            "catalog": public_result_reference_catalog(self.binding["catalog"]),
            "instruction": "对象身份已由用户序号/明确指代核对。不能猜别的对象、增加动作或继承旧金额；仍须核对确认。",
            "write_authorized": False}


def _lifecycle(session, parent, actions, execution_context):
    from .conversation_provenance import _DialogueExecutionContext
    execution = isinstance(execution_context, _DialogueExecutionContext) and execution_context.permits(parent)
    if all(row["kind"] in READ_KINDS for row in actions):
        return parent.state not in {"FAILED", "UNKNOWN", "CLOSED"}
    if parent.state not in _PENDING and not execution: return False
    ops = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
        RuntimeOperation.action_kind.not_in(READ_KINDS))))
    affected = any(op.status in {"SUCCEEDED", "AUTHORIZED", "RUNNING", "UNKNOWN"} or op.child_task_id and
        (child := session.get(Task, op.child_task_id)) and child.state in {"SUCCEEDED", "EXECUTING", "UNKNOWN", "AUTHORIZED"} for op in ops)
    ids = [parent.id, *[op.child_task_id for op in ops if op.child_task_id]]
    used = session.scalar(select(Confirmation.id).where(Confirmation.task_id.in_(ids),
        (Confirmation.status == "CONSUMED") | (Confirmation.consumed_at.is_not(None))).limit(1))
    return not used and (not affected or execution)


def _load(session, parent, binding, *, principal=None, execution_context=None, genesis=False):
    from .services import Principal
    record = session.get(RuntimeRecord, parent.id)
    principal = principal or Principal(parent.user_id, parent.sandbox_id,
        family_context=(record.metadata_json or {}).get("family_context_origin") if record else None)
    if not isinstance(binding, dict) or binding.get("version") != VERSION or binding.get("write_authorized") is not False:
        _deny()
    thread = session.get(ChatThread, binding.get("conversation_id"))
    turn = session.get(ChatTurn, (binding.get("current_ref") or {}).get("turn_id"))
    if not _owned(parent, principal) or turn is None or turn.task_id != parent.id or not _valid_turn(
        session, principal, thread, turn, binding.get("actor_scope_hash"), answered=True) \
        or _turn_ref(turn) != binding.get("current_ref") or turn.message != binding.get("original_message") \
        or (not genesis and parent.message != turn.message):
        _deny()
    rebuilt = build_result_reference_binding(session, principal, thread, turn, catalog=binding.get("catalog"),
        domain=binding.get("domain"), focus_entity_ref=binding.get("focus_entity_ref"), focus_receipt=binding.get("focus_receipt"))
    if rebuilt != binding or not _lifecycle(session, parent, binding["expected_actions"], execution_context): _deny()
    return VerifiedResultReference(_AUTHORITY, turn.message, tuple(deepcopy(binding["expected_actions"])), deepcopy(binding), parent.id)


def load_reference_genesis(session, parent, binding, *, execution_context=None):
    """For the slot ledger's immutable genesis only; not latest-turn grounding.

    Caller must compare the genesis source turn/binding hash and then validate
    all later slot updates. Never use this to bypass current parent.message.
    """
    return _load(session, parent, binding, execution_context=execution_context, genesis=True)


def load_verified_result_reference(session, parent, metadata, *, execution_context=None):
    binding = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    return _load(session, parent, binding, execution_context=execution_context) if binding else None


def inherited_result_reference_bill_period(session, principal, parent):
    """Reuse only the exact displayed period of a verified bill-category read.

    The catalog loader rechecks actor, user receipts, child result, snapshot,
    entity facts and canonical hashes. Dates are never parsed from an assistant
    answer or freshly inferred from today's reporting clock.
    """
    from datetime import datetime
    from .owned_user_dialogue import _bill_result_scope
    if not _owned(parent, principal):
        _deny()
    record=session.get(RuntimeRecord,parent.id)
    proof=load_verified_result_reference(session,parent,record.metadata_json) if record else None
    if not proof:
        return None
    if proof.binding['mode']!='READ_QUERY' or [row['kind'] for row in proof.expected_actions]!=['BILL_ANALYSIS']:
        return None
    binding=proof.binding
    entity=next((row for row in binding['catalog']['entities']
        if row['entity_ref']==binding['resolution']['entity_ref']),None)
    if not entity or (entity.get('read_kind'),entity.get('domain'),entity.get('field'))!=('BILL_ANALYSIS','BILLS','category_filter'):
        _deny()
    source=next((row for row in binding['catalog']['sources']
        if row.get('result_ref')==entity.get('result_ref')),None)
    origin=session.get(Task,source.get('origin_task_id')) if source else None
    if not source or source.get('read_kind')!='BILL_ANALYSIS' or not _owned(origin,principal) or origin.state!='SUCCEEDED':
        _deny()
    scope=_bill_result_scope(session,principal,origin)
    facts=entity.get('facts')
    if not scope or not isinstance(facts,dict) or any(
            scope.get(key)!=facts.get(key) for key in ('period_start','period_end')):
        _deny()
    start,end=facts.get('period_start'),facts.get('period_end')
    if start is None and end is None:
        return None  # Genuine unbounded historical scope, not missing proof.
    try:
        left,right=datetime.fromisoformat(start),datetime.fromisoformat(end)
    except (ValueError,TypeError):
        _deny()
    if left.tzinfo is None or right.tzinfo is None or left>=right:
        _deny()
    return left,right


def reference_verified(context):
    value = (context or {}).get(PRIVATE_KEY)
    return value if isinstance(value, VerifiedResultReference) and value.authority is _AUTHORITY else None


def reference_rule_proposal(message, context, **kwargs):
    proof = reference_verified(context)
    if proof and proof.original_message == message:
        return proof.expected_proposal.model_copy(update={"fallback": kwargs.get("fallback", False), "model_label": "verified-reference-rules"})
    return rule_proposal(message, **kwargs)


def ground_result_reference_proposal(proposal, message, context):
    proof = reference_verified(context)
    if proof is None or proof.original_message != message: return ground_proposal(proposal, message)
    # A model may omit the private target or return the source-exact target;
    # neither a different target nor an extra field/action is accepted.
    expected = deepcopy(list(proof.expected_actions))
    supplied = _actions(proposal)
    if len(supplied) != 1 or supplied[0]["kind"] != expected[0]["kind"] or supplied[0]["depends_on"] != expected[0]["depends_on"]:
        return proposal.model_copy(update={"actions": []})
    # Only the currently selected, source-verified card can use its public
    # suffix as an identity alias. Do not accept an arbitrary four digits or
    # the legacy suffix of another card, and never discard a conflicting PAN.
    canonical_card = proof.binding["resolution"]["expected_slots"].get("card_number")
    if canonical_card and "card_last4" in supplied[0]["slots"]:
        tail = supplied[0]["slots"]["card_last4"]
        number = supplied[0]["slots"].get("card_number")
        if not isinstance(tail, str) or tail.lower() != canonical_card[-4:].lower() \
                or number is not None and (not isinstance(number, str) or number.lower() != canonical_card.lower()):
            return proposal.model_copy(update={"actions": []})
        supplied[0]["slots"].pop("card_last4")
        supplied[0]["slots"]["card_number"] = canonical_card
    if expected[0]["kind"] in _CNY_ONLY_REFERENCE_KINDS and "currency" not in expected[0]["slots"] \
            and supplied[0]["slots"].get("currency") == "CNY":
        # This is a harmless schema spelling of a CNY-only operation, not
        # inherited user intent. USD and explicit currency conflicts remain.
        supplied[0]["slots"].pop("currency")
    for field, value in proof.binding["resolution"]["expected_slots"].items():
        if field not in supplied[0]["slots"]:
            supplied[0]["slots"][field] = deepcopy(value)
        elif field in {"product_name", "merchant", "grantee_name", "owner_name"}:
            selected = next(row for row in proof.binding["catalog"]["entities"] if row["entity_ref"] == proof.binding["resolution"]["entity_ref"])
            if supplied[0]["slots"][field] == selected["label"]:
                supplied[0]["slots"][field] = deepcopy(value)
    if canonical_hash(supplied) != canonical_hash(expected): return proposal.model_copy(update={"actions": []})
    return proof.expected_proposal.model_copy(update={"model_label": proposal.model_label, "fallback": proposal.fallback,
        "failure_reasons": proposal.failure_reasons})


def validate_result_reference_proposal(session, parent, proposal, metadata, *, execution_context=None):
    proof = load_verified_result_reference(session, parent, metadata, execution_context=execution_context)
    if proof and canonical_hash(_actions(proposal)) != canonical_hash(list(proof.expected_actions)): _deny()
    return proof


def reference_explanation_sources(session, principal, thread, binding):
    """Freshly validate an unlinked consultation binding, without any task."""
    turn = session.get(ChatTurn, (binding.get("current_ref") or {}).get("turn_id"))
    rebuilt = build_result_reference_binding(session, principal, thread, turn, catalog=binding.get("catalog"),
        domain=binding.get("domain"), focus_entity_ref=binding.get("focus_entity_ref"), focus_receipt=binding.get("focus_receipt"))
    if rebuilt != binding or binding.get("mode") != "EXPLANATION": _deny()
    return result_reference_explanation_sources(session, principal, thread, binding["catalog"], binding["resolution"])
