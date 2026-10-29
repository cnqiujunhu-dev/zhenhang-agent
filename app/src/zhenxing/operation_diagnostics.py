"""Small internal failure observations; never retain exception messages."""
import re
from .audit import append_audit
from .models import utcnow

STAGES = {'CONTEXT','SEMANTIC_MODEL','SEMANTIC_APPLY','KNOWLEDGE_RETRIEVAL',
    'CONSULTATION_MODEL','CONSULTATION_COMMIT','CUSTOMER_REPLY','SHADOW_OBSERVATION'}


def failure_observation(error, *, stage, correlation_id, code):
    kind = type(error).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',kind):
        kind = 'Exception'
    result = {'schema_version':'operation-failure-v1',
        'stage':stage if stage in STAGES else 'SEMANTIC_APPLY',
        'error_type':kind,
        'code':code if isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',code) else 'PROCESSING_FAILED',
        'correlation_id':correlation_id if isinstance(correlation_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',correlation_id) else None,
        'observed_at':utcnow().isoformat()}
    from pydantic import ValidationError
    if isinstance(error, ValidationError):
        from .intelligence import ActionSlots, IntentProposal, ProposedAction
        fields = set(ActionSlots.model_fields) | set(IntentProposal.model_fields) | set(ProposedAction.model_fields)
        # Keep the failing contract location, never the offending input,
        # message, context or URL (these can embed private values).
        result['validation_errors'] = [
            {'type': row['type'] if re.fullmatch(r'[a-z_]{1,64}', row['type']) else 'validation_error', 'location': [
                item if type(item) is int else item if isinstance(item, str)
                and item in fields else '[field]'
                for item in row['loc'][:8]]}
            for row in error.errors(include_url=False, include_context=False, include_input=False)[:8]
        ]
    return result


def record_failure(session, *, sandbox_id, task_id, observation):
    # Chat interpretation may fail before any business task exists. Its
    # durable ChatTurn diagnostics are the correlation record; do not create
    # a fake task just to satisfy the financial audit table's foreign key.
    if task_id is None:
        return
    append_audit(session,sandbox_id=sandbox_id,task_id=task_id,
        event_type='OPERATION_PROCESSING_FAILED',payload=observation)
