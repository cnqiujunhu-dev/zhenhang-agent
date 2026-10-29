"""An accepted semantic origin must not fall through to a legacy planner."""
from sqlalchemy import select
from .conversation_models import ChatTurn
from .contracts import DomainError


def requires_semantic_proof(session,parent,metadata):
    if metadata.get('cloud_semantic_intent') or (metadata.get('conversation_context') or {}).get('authority')=='VERIFIED_CLOUD_SEMANTIC_USER_INTENT':
        return True
    # Removing runtime metadata cannot erase the independently persisted
    # message receipt and thereby enable another planner or rule fallback.
    sources=session.scalars(select(ChatTurn).where(ChatTurn.task_id==parent.id,
        ChatTurn.user_id==parent.user_id,ChatTurn.sandbox_id==parent.sandbox_id,
        ChatTurn.status=='ANSWERED').order_by(ChatTurn.sequence.desc()).limit(64))
    return any(row.diagnostics_json.get('semantic_entry_version')=='unified-cloud-semantics-v1' for row in sources)


def require_semantic_proof(session,parent,metadata,proof):
    if requires_semantic_proof(session,parent,metadata) and proof is None:
        raise DomainError('SEMANTIC_PROOF_REQUIRED','这项需求的来源需要重新核对；我不会重复规划或执行。',409)
