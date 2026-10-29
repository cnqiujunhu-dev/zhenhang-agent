"""A current explicit lock may resolve one latest, explicitly queried card.

The read supplies an owned object reference, not write permission. Every use
rechecks the original DB user query, immutable snapshot, sole result and live
ownership/version. No model may choose a different card or extra operation.
"""
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatTurn
from .intelligence import IntentProposal, rule_proposal, ground_proposal, _intent_text
from .models import Card, Task
from .permissions import actor_role
from .planning import canonical_hash
from .runtime_models import RuntimeRecord, PlanSnapshot

VERSION = 'card-lock-reference-v1'
SCOPE = 'CURRENT_EXPLICIT_CARD_LOCK_REFERENCE'
_TAIL = re.compile(r'(?:卡尾号|尾号|末四位)\s*([0-9a-fA-F]{4})(?![0-9a-fA-F])')


def _source_query_proposal(session, principal, source, task, record, snapshot):
    """Reprove a literal card selection, including an owned read continuation.

    A snapshot alone is not user evidence. The newest user's exact tail must
    ground either a standalone read or the DB-verified READ_ONLY chain that
    compiled it. Assistant text and a model-selected card are never authority.
    """
    from .financial_intent import foreign_financial_request
    from .intelligence import _private_read_veto
    if (_intent_text(source.message) != source.message or len(source.message) > 160
            or foreign_financial_request(source.message) or _private_read_veto(source.message)):
        return None
    tails = _TAIL.findall(source.message)
    if len(tails) != 1:
        return None
    tail = tails[0].lower()
    try:
        proposal = IntentProposal.model_validate(snapshot.proposal_json)
        actions = proposal.actions
        if (len(actions) != 1 or actions[0].kind != 'CARD_STATUS' or actions[0].depends_on
                or not actions[0].slots.card_last4 or actions[0].slots.card_last4.lower() != tail):
            return None
        binding = record.metadata_json.get('owned_user_dialogue')
        if binding:
            from .owned_user_dialogue import VERSION as READ_VERSION, PRIVATE_KEY, load_verified_dialogue, ground_dialogue_proposal
            if binding.get('version') != READ_VERSION or binding.get('scope') != 'READ_ONLY':
                return None
            proof = load_verified_dialogue(session, task, record.metadata_json)
            if not proof or proof.scope != 'READ_ONLY' or proof.utterances[-1] != source.message:
                return None
            role = actor_role(session, principal.sandbox_id, principal.user_id)
            # The read proof checks DB ownership/hash/order; submission roles
            # must also remain the same throughout this new write reference.
            for ref in binding.get('source_refs', []):
                row = session.get(ChatTurn, ref.get('turn_id'))
                if not row or row.diagnostics_json.get('actor_role_at_submission') != role:
                    return None
            grounded = ground_dialogue_proposal(proposal, source.message, {PRIVATE_KEY: proof})
        else:
            raw = rule_proposal(source.message).actions
            if len(raw) != 1 or raw[0].kind != 'CARD_STATUS' or not raw[0].slots.card_last4 or raw[0].slots.card_last4.lower() != tail:
                return None
            grounded = ground_proposal(proposal, source.message)
        if (len(grounded.actions) != 1 or grounded.actions[0].kind != 'CARD_STATUS'
                or grounded.actions[0].slots.card_last4.lower() != tail
                or grounded.actions[0].slots.model_dump() != actions[0].slots.model_dump()):
            return None
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return tail, canonical_hash(binding) if binding else None


def lock_reference_request(message):
    if _intent_text(message)!=message or len(message)>160 or re.search(r'不要|别|不是|不想|引用|示例|他(?:的)?|她(?:的)?|别人|朋友|\b(?:not|never|his|her|their|example|quote)\b',message,re.I):
        return False
    if not re.search(r'这张卡|那张卡|\b(?:this|that)\s+card\b',message,re.I):
        return False
    actions=rule_proposal(message).actions
    return len(actions)==1 and actions[0].kind=='CARD_LOCK' and not actions[0].depends_on and not actions[0].slots.model_dump(exclude_none=True) and len(ground_proposal(IntentProposal(actions=actions),message).actions)==1


def query_card_proof(session, principal, source):
    if not source or source.status!='ANSWERED' or source.mode not in {'NEW_TASK','REVISE'} or source.context_policy!='AUTO' or source.diagnostics_json.get('input_redacted') or source.reviewed_evidence_refs_json or (source.user_id,source.sandbox_id)!=(principal.user_id,principal.sandbox_id):
        return None
    if source.diagnostics_json.get('actor_role_at_submission')!=actor_role(session,principal.sandbox_id,principal.user_id):
        return None  # Historical roles are not retroactively asserted.
    task=session.get(Task,source.task_id) if source.task_id else None
    if not task or (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) or task.state!='SUCCEEDED':
        return None
    record=session.get(RuntimeRecord,task.id)
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version)) if record and record.kind=='PARENT' and record.plan_version else None
    if not snapshot or snapshot.plan_hash != task.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
        return None
    selected = _source_query_proposal(session, principal, source, task, record, snapshot)
    if not selected:
        return None
    tail, read_binding_hash = selected
    from .runtime import task_trace
    trace=task_trace(session,principal,task.id)
    nodes=trace.get('nodes',[])
    outcome = trace.get('outcome') or {}
    cards=nodes[0].get('result',{}).get('cards',[]) if len(nodes)==1 and nodes[0].get('action')=='CARD_STATUS' and nodes[0].get('status')=='SUCCEEDED' and outcome.get('verified') is True and outcome.get('kind') == 'QUERY_RESULT' and nodes[0].get('operation_id') in outcome.get('completed_operation_ids', []) else []
    if len(cards)!=1 or not isinstance(cards[0],dict):
        return None
    row=cards[0]
    from .card_identifiers import resolve_owned_cards, card_identifier_view
    matches=resolve_owned_cards(session,principal.sandbox_id,principal.user_id,tail)
    if len(matches)!=1:
        return None
    card=matches[0]
    mapping=card_identifier_view(session,card)
    if card.id!=row.get('card_id') or card.version!=row.get('version') or card.status!='ACTIVE' or row.get('status')!='ACTIVE' or not mapping or mapping.get('card_number')!=row.get('card_number'):
        return None
    return {'origin_task_id':task.id,'source_turn_id':source.id,'source_message_hash':canonical_hash({'message':source.message}),
        'origin_snapshot_id':snapshot.id,'origin_snapshot_hash':canonical_hash(snapshot.proposal_json),
        'origin_plan_hash': snapshot.plan_hash, 'source_read_binding_hash': read_binding_hash,
        'read_result_hash':canonical_hash(row),'card_id':card.id,'card_version':card.version,
        'card_last4':tail,'card_number':mapping['card_number']}


def reference_candidate(session, principal, thread, turn):
    role=actor_role(session,principal.sandbox_id,principal.user_id)
    if role not in {'CUSTOMER','DEV_TEST','DEMO_OWNER','OWNER'} or turn.diagnostics_json.get('actor_role_at_submission') != role or turn.context_policy!='AUTO' or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get('input_redacted') or not lock_reference_request(turn.message):
        return None
    source=session.scalar(select(ChatTurn).where(ChatTurn.thread_id==thread.id,ChatTurn.user_id==principal.user_id,
        ChatTurn.sandbox_id==principal.sandbox_id,ChatTurn.sequence<turn.sequence,ChatTurn.status=='ANSWERED').order_by(ChatTurn.sequence.desc()).limit(1))
    if not source or source.task_id!=thread.current_task_id or turn.requested_task_id and turn.requested_task_id!=source.task_id:
        return None
    proof=query_card_proof(session,principal,source)
    return {**proof,'version':VERSION,'scope':SCOPE,'conversation_id':thread.id,'turn_id':turn.id,
        'message_hash':canonical_hash({'message':turn.message}),'source_role':role} if proof else None


def load_reference(session, parent, binding):
    from .conversation_models import ChatThread
    from .services import Principal
    owner=(parent.user_id,parent.sandbox_id)
    current=session.get(ChatTurn,binding.get('turn_id')); thread=session.get(ChatThread,binding.get('conversation_id'))
    source=session.get(ChatTurn,binding.get('source_turn_id'))
    def deny():
        raise DomainError('CONVERSATION_SCOPE_CHANGED','原查询卡片或当前权限已变化，请重新核对具体卡片；未执行锁卡。',409)
    if not current or not thread or not source or binding.get('scope')!=SCOPE or (thread.user_id,thread.sandbox_id)!=owner or (current.user_id,current.sandbox_id)!=owner or current.thread_id!=thread.id or current.task_id!=parent.id or current.status!='ANSWERED' or current.message!=parent.message or current.context_policy!='AUTO' or current.reviewed_evidence_refs_json or current.diagnostics_json.get('input_redacted') or binding.get('message_hash')!=canonical_hash({'message':current.message}) or binding.get('source_role')!=actor_role(session,parent.sandbox_id,parent.user_id) or current.diagnostics_json.get('actor_role_at_submission') != binding.get('source_role') or not lock_reference_request(current.message) or source.thread_id!=thread.id or not source.sequence<current.sequence:
        deny()
    latest=session.scalar(select(ChatTurn.id).where(ChatTurn.thread_id==thread.id,ChatTurn.user_id==parent.user_id,
        ChatTurn.sandbox_id==parent.sandbox_id,ChatTurn.sequence<current.sequence,ChatTurn.status=='ANSWERED').order_by(ChatTurn.sequence.desc()).limit(1))
    if latest!=source.id:
        deny()
    proof=query_card_proof(session,Principal(*owner),source)
    if not proof or any(binding.get(key)!=value for key,value in proof.items()):
        deny()
    return source.message,current.message,binding['card_last4'],binding['card_number']
