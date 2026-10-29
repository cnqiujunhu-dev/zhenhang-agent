"""Read server-owned ledger integrity before offering a manual model retry.

This is not a new credential/MAC scheme or remote-response signature. Clients
and the LLM cannot write ChatTurn diagnostics. The owned-turn API, actor epoch,
latest-turn, idempotency and existing two-attempt/45-second gates remain the
authority; a loose or legacy diagnostic dictionary never grants this branch.
"""
import re

from .conversation_models import ChatTurn
from .planning import canonical_hash
from .bounded_http import safe_http_diagnostics


def verified_connect_retry(turn):
    """Only one complete, unbound, known pre-response connection failure."""
    from .semantic_reunderstanding import KEY,metric_attempts
    if not isinstance(turn,ChatTurn) or turn.task_id is not None:
        return False
    try:
        diagnostics=turn.diagnostics_json or {};raw=diagnostics.get('semantic_diagnostics') or {}
        ledger=diagnostics.get(KEY);attempts=metric_attempts(ledger)
        if not attempts or len(attempts)!=1 or ledger.get('reserved_attempts')!=1 \
                or ledger.get('http_count_complete') is not True or type(ledger.get('model_api_call_count')) is not int \
                or ledger['model_api_call_count']!=1 or diagnostics.get('bank_write_authorized') is not False \
                or diagnostics.get('bank_task_created') is True:
            return False
        expected={
            'CONNECT_FAILURE':('ModelHTTPTransportFailure','HTTP_TRANSPORT_FAILED'),
            'CONNECT_TIMEOUT':('ModelHTTPTimeout','HTTP_TIMEOUT'),
        }.get(raw.get('http_failure_kind'))
        if expected is None or (raw.get('http_exception_type'),raw.get('transport_error_code'))!=expected \
                or raw.get('http_issued') is not True or raw.get('http_failure_issued') is not True \
                or raw.get('http_outcome_unknown') is not False or raw.get('http_status') is not None \
                or type(raw.get('model_api_call_count')) is not int or raw['model_api_call_count']!=1 \
                or raw.get('failure_reasons')!=['SEMANTIC_MODEL_UNAVAILABLE'] or raw.get('fallback') is not False \
                or raw.get('purpose')!='UNIFIED_SEMANTIC_UNDERSTANDING' or raw.get('authority')!='SEMANTICS_NOT_BANK_AUTHORITY' \
                or raw.get('decision_hash') is not None:
            return False
        attempt=attempts[0];saved=attempt.get('diagnostics') or {}
        if attempt.get('status')!='MODEL_UNAVAILABLE' or attempt.get('decision_hash') is not None \
                or attempt.get('diagnostics_hash')!=canonical_hash(raw) \
                or safe_http_diagnostics(saved)!=safe_http_diagnostics(raw):
            return False
        for field in ('input_hash','source_message_hash','routing_policy_version','actual_provider','requested_model','actual_model','model_api_call_count'):
            if saved.get(field)!=raw.get(field):return False
        hashes=('input_hash','source_message_hash','context_hash','actor_scope_hash','policy_hash')
        if any(not isinstance(ledger.get(field),str) or not re.fullmatch('[0-9a-f]{64}',ledger[field]) for field in hashes) \
                or ledger['input_hash']!=raw.get('input_hash') \
                or ledger['source_message_hash']!=raw.get('source_message_hash') \
                or ledger['source_message_hash']!=canonical_hash({'message':turn.message}) \
                or ledger['actor_scope_hash']!=diagnostics.get('dialogue_actor_scope_hash') \
                or type(ledger.get('thread_version')) is not int or ledger['thread_version']<1 \
                or type(ledger.get('policy_version')) is not int or ledger['policy_version']!=raw.get('routing_policy_version') \
                or not isinstance(raw.get('actual_provider'),str) or not raw['actual_provider'] \
                or not isinstance(raw.get('actual_model'),str) or not raw['actual_model'] \
                or raw.get('actual_model')!=raw.get('requested_model'):
            return False
        return True
    except (ValueError,TypeError,KeyError,AttributeError,IndexError):
        return False
