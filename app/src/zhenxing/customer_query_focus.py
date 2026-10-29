"""Question-oriented display hints proven by the existing semantic receipt."""
from sqlalchemy import select
from .runtime_models import RuntimeRecord, PlanSnapshot

FAMILY_FOCUS = {'FAMILY_AUTHORIZED_BALANCES','FAMILY_DELEGATIONS'}


def verified_query_focus(session, principal, parent):
    record=session.get(RuntimeRecord,parent.id)
    binding=(record.metadata_json or {}).get('cloud_semantic_intent') if record else None
    focus=(binding.get('decision') or {}).get('presentation_focus') if isinstance(binding,dict) else None
    if focus not in FAMILY_FOCUS or not record.plan_version:
        return None
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,
        PlanSnapshot.version==record.plan_version))
    try:
        from .semantic_proposal import validate_semantic_source_snapshot
        proof=validate_semantic_source_snapshot(session,principal,parent,record.metadata_json,snapshot)
        if not any(action.get('kind')=='FAMILY_VIEW' for action in proof['proposal_json']['actions']):
            return None
    except Exception:
        return None  # Losing a display hint cannot hide or fail verified banking data.
    return focus
