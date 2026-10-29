"""One finite semantic reconsideration, never a banking retry or authority.

The persisted ledger counts this purpose only. A failure permit is private,
process-bound and single-use; persisted JSON cannot reissue a model request.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import threading
import time

from .planning import canonical_hash

VERSION = 'semantic-structural-reunderstanding-v2'
KEY = 'semantic_reunderstanding'
PURPOSE = 'UNIFIED_SEMANTIC_UNDERSTANDING'
ERROR = 'SEMANTIC_FOCUS_NOT_MUTABLE'
ERRORS = frozenset({ERROR, 'SEMANTIC_QUERY_REFERENCE_READ_ONLY',
    'SEMANTIC_TARGET_SET_SCALAR_CONFLICT','SEMANTIC_SCOPE_PURPOSE_REQUIRED',
    'SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID','SEMANTIC_READ_SELECTOR_REQUIRED',
    'SEMANTIC_HISTORY_SCOPE_SOURCE_NOT_OFFERED','SEMANTIC_MERCHANT_CATEGORIES_SOURCE_INVALID',
    'SEMANTIC_CARD_TAIL_LITERAL_REQUIRED','SEMANTIC_CARD_REFERENCE_LITERAL_REQUIRED','SEMANTIC_QUERY_REFERENCE_MEMBERS_CONFLICT',
    'SEMANTIC_HISTORY_SCOPE_CONTEXT_CONFLICT','SEMANTIC_HISTORY_SCOPE_LITERAL_INVALID','SEMANTIC_CURRENCY_LITERAL_REQUIRED',
    'SEMANTIC_READ_SUBJECT_NOT_SELF','SEMANTIC_QUERY_SET_NOT_OFFERED','SEMANTIC_CURRENT_QUERY_FOCUS_REQUIRED',
    'SEMANTIC_ENTITY_CONSUMER_MISMATCH','SEMANTIC_CREDIT_TARGET_FUNDING_CONFLICT'})
WIRE_GRAMMAR_CODE = 'SEMANTIC_CURRENT_SOURCE_SPAN_INVALID'
WIRE_GRAMMAR_VERSION = 'literal-context-wire-grammar-feedback-v1'
_AUTHORITY = object()
_KEY = secrets.token_bytes(32)
_FEEDBACK = ('Reinterpret the same original message and full role-bound sources under the unchanged schema. '
    'Choose the valid mode, refs and fields yourself; clarify if unsafe. '
    'Do not invent field values/source evidence or grant bank authority.')
_ERROR_HINTS={
    'SEMANTIC_FOCUS_NOT_MUTABLE':(
        'The selected frame is not a mutable draft. Do not repeat REVISE/CONTINUE against it. '
        'A registered scheduled transfer in WAITING is an already-confirmed mandate, not a pending form. '
        'If the user requests cancellation rather than modification, CANCEL may target a WAITING frame with only '
        'TRANSFER_SCHEDULE and no possible write effect, with empty action_kinds/changes/dependencies. '
        'This cancels future occurrences only; it is not a new TRANSFER_SCHEDULE request. '
        'If TRANSFER_SCHEDULE with schedule_reference is offered, a requested future amendment is a new REQUEST: '
        'select the genuinely offered schedule_reference entity with the current anaphoric source span, '
        'and supply only the user\'s current changed fields under new:0. The domain rechecks the existing mandate '
        'and requires new confirmation before replacement; do not copy old authorization or cancel it during preparation. '
        'If no suitable owned identity is offered, query the owned schedules or clarify instead of guessing.'),
    'SEMANTIC_CREDIT_TARGET_FUNDING_CONFLICT':(
        'Credit repayment uses two different instruments: target card_reference/card_number/card_last4 identifies the credit debt; '
        'source_card identifies the paying debit card. The same literal occurrence cannot be both. '
        'Reinterpret their roles from the unchanged full current message, including preceding query clauses. '
        'Reuse the current credit-card literal across actions when explicitly identified; never invent another card or inherit payment authorization.'),
    'SEMANTIC_ENTITY_CONSUMER_MISMATCH':(
        'A selected entity does not permit the specific action that consumes it. '
        'In a multi-action request, check consumer_kinds for each individual change.action_ref. '
        'A READ-only completed receipt cannot supply a repayment or other WRITE field. '
        'If the current user message independently states the target card, select its exact current literal without a historical entity. '
        'The same current literal may be used for several actions; do not invent identifiers, inherit authorization or drop requested actions.'),
    'SEMANTIC_CURRENT_QUERY_FOCUS_REQUIRED':(
        'CURRENT filter inheritance needs a valid offered frame, not null. '
        'An independent new READ of a different resource kind uses its own genuine subject/selector '
        'rather than treating a previous membership/card result as the new object. '
        'Choose the correct base and refs from the same current need yourself; no frame, object or authority was inferred.'),
    'SEMANTIC_QUERY_SET_NOT_OFFERED':(
        'The chosen set_ref is not an offered query_scope collection. Never invent a set on an older frame. '
        'For selected verified result facts from different reads, CONSULT may select those entity_refs '
        'without a query_reference. For a fresh READ that changes the object, preserve unchanged filters '
        'through the actual CURRENT query scope or explicit HISTORY_USER sources, and set only the genuine '
        'source-bound identity field. Changing a card does not reset its period/currency. '
        'Choose valid refs and mode yourself; the server has not repaired the proposal or selected a target.'),
    'SEMANTIC_READ_SUBJECT_NOT_SELF':(
        'read_subjects is optional positive SELF literal proof, not a required owner classifier. '
        'Without a supplied compatible complete self literal, choose omission yourself; an entity/CURRENT selector still requires bank ownership checks. '
        'Genuine foreign or mixed ownership needs clarification, never relabel SELF or erase foreign source text.'),
    'SEMANTIC_HISTORY_SCOPE_CONTEXT_CONFLICT':(
        'Historical conditions require either an independent new READ or a verified completed CURRENT read collection. '
        'For CURRENT, retain both source selections only when the old USER conditions corroborate the bound card/currency/period; '
        'current explicit changes still apply. Do not invent a currency SET without a current literal, drop conditions or change the actor.'),
    'SEMANTIC_HISTORY_SCOPE_LITERAL_INVALID':(
        'A historical literal selector failed in its explicitly selected HISTORY_USER message, not the current message. '
        'Reinterpret the chosen base/source and copy a valid exact literal in that same offered USER message. '
        'A verified CURRENT collection can coexist with history when their original conditions agree; preserve both sources and explicit current changes.'),
    'SEMANTIC_CURRENCY_LITERAL_REQUIRED':(
        'Currency SET needs current explicit positive denomination evidence and its selected finite value. '
        'source_currency cannot cite recipient settlement; target_currency cannot cite payer funding. '
        'Preserve source-bound roles and values; do not invent another currency or swap fields. '
        'A verified CURRENT read retains its unchanged currency. Omitted operation still means SET. '
        'Without a current denomination, choose no current currency change yourself; '
        'explicitly selected HISTORY_USER conditions or a verified CURRENT read retain their audited currency through their own source mechanism.'),
    'SEMANTIC_CARD_TAIL_LITERAL_REQUIRED':(
        'A literal card_last4 must contain one actual four-hexadecimal tail from the current message, not an anaphoric phrase. '
        'If the same unique card is explicitly identified elsewhere in this same user message, you may select that same real source span for both actions. '
        'Otherwise choose an actually offered compatible identity reference or clarify; never invent/truncate an identifier or replace the actor.'),
    'SEMANTIC_CARD_REFERENCE_LITERAL_REQUIRED':(
        'A literal card selector must contain a real registered-format card number or four-hex tail, not an unresolved pronoun. '
        'Re-read the same full original user message: if the user already supplied the unique card identity for this action, '
        'select that exact original identity span again, including across dependent actions. Otherwise select a genuinely offered '
        'compatible entity reference or clarify. Keep funding and credit-target roles separate. The server will not infer a card, '
        'copy assistant text, manufacture a reference or execute the rejected proposal.'),
    'SEMANTIC_QUERY_REFERENCE_MEMBERS_CONFLICT':(
        'SUBSET member_refs and nonempty entity_refs disagree although all are members of the offered collection. '
        'Reinterpret the original selected objects and emit one consistent set with valid source evidence. '
        'The server will not drop extra references, choose the first object or widen the subset.'),
    'SEMANTIC_MERCHANT_CATEGORIES_SOURCE_INVALID':(
        'merchant_categories is a set-valued field. Its selected_options must equal the complete registered category set '
        'expressed by the selected current literal, not one arbitrary member of a multi-category literal. '
        'Reinterpret the requested set and source yourself; the server will not add/drop categories or broaden the scope.'),
    'SEMANTIC_READ_SELECTOR_REQUIRED':(
        'The selected SELF resource anchor names a particular registered object, but the READ has no object selector. '
        'SELF proves the subject only. Reinterpret the requested scope and supply its genuine source-bound field/target; '
        'do not silently widen a named target into all records or fabricate an identity.'),
    'SEMANTIC_HISTORY_SCOPE_SOURCE_NOT_OFFERED':(
        'No historical USER message is offered in this context. history_read_sources therefore cannot be selected. '
        'Reinterpret the current original message using its own source spans and offered refs; do not invent prior context.'),
    'SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID':(
        'card_balance_currencies requires a selected explicit currency literal for the balance READ. '
        'A generic cash/balance noun is not a denomination; another payment action denomination does not define this READ filter. '
        'Reconsider whether this optional READ filter was actually requested. Do not infer a currency or change any WRITE amount/source.'),
    'SEMANTIC_TARGET_SET_SCALAR_CONFLICT':(
        'An explicit target collection and a scalar identity cannot disagree or duplicate competing scalar identities. '
        'Choose one source-bound representation of the same requested objects; preserve each object and its original evidence.'),
    'SEMANTIC_SCOPE_PURPOSE_REQUIRED':(
        'scope_frame_refs/operation_scope describe an operation-boundary question, not ordinary query navigation. '
        'Choose the appropriate original purpose yourself; query navigation uses focus_ref/query_base without fabricating an OPERATION_SCOPE question.')}


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def policy_hash(policy):
    return canonical_hash(policy.model_dump())


def enabled(policy):
    return policy.cloud_enabled is True and policy.mode != 'LOCAL_ONLY' and policy.max_http_attempts >= 2


def valid_ledger(value):
    try:
        return isinstance(value, dict) and value.get('version') == VERSION \
            and value.get('purpose') == PURPOSE and type(value.get('reserved_attempts')) is int \
            and 1 <= value['reserved_attempts'] <= 2 and isinstance(value.get('attempts'), list) \
            and len(value['attempts']) <= value['reserved_attempts'] \
            and all(isinstance(row, dict) and type(row.get('ordinal')) is int and row['ordinal'] == i + 1 and
                row.get('status') in {'READY','MODEL_INVALID','MODEL_UNAVAILABLE','INPUT_INVALID'} and
                isinstance(row.get('diagnostics'), dict) for i, row in enumerate(value['attempts'])) \
            and len(value['attempts']) <= 2 \
            and isinstance(value.get('policy_hash'), str) \
            and isinstance(value.get('input_hash'), str) \
            and isinstance(value.get('source_message_hash'), str) \
            and 0 <= (_utc(datetime.fromisoformat(value['deadline_at'])) -
                _utc(datetime.fromisoformat(value['started_at']))).total_seconds() <= 45
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def start_ledger(policy, *, input_hash, source_hash, now, context_hash=None, thread_version=None, actor_scope_hash=None):
    now = _utc(now)
    return {'version': VERSION, 'purpose': PURPOSE, 'policy_hash': policy_hash(policy),
        'policy_version': policy.version, 'input_hash': input_hash, 'source_message_hash': source_hash,
        'context_hash': context_hash, 'thread_version': thread_version, 'actor_scope_hash': actor_scope_hash,
        'started_at': now.isoformat(),
        'deadline_at': (now + timedelta(seconds=min(policy.max_elapsed_seconds, 45))).isoformat(),
        'reserved_attempts': 1, 'attempts': [], 'status': 'RESERVED',
        'bank_write_authorized': False, 'counts_scope': PURPOSE}


def remaining(value, *, now):
    if not valid_ledger(value):
        return 0.0
    return max(0.0, min(45.0, (_utc(datetime.fromisoformat(value['deadline_at'])) - _utc(now)).total_seconds()))


@dataclass(frozen=True)
class AttemptBudget:
    input_hash: str
    source_hash: str
    policy_hash: str
    deadline: float
    ordinal: int
    authority: object = field(default=None, repr=False)


def attempt_budget(value, policy, *, now):
    if not valid_ledger(value) or value['policy_hash'] != policy_hash(policy):
        return None
    return AttemptBudget(value['input_hash'], value['source_message_hash'], value['policy_hash'],
        time.perf_counter() + remaining(value, now=now), value['reserved_attempts'], _AUTHORITY)


def budget_remaining(budget, *, policy, input_hash, source_hash):
    if not isinstance(budget, AttemptBudget) or budget.authority is not _AUTHORITY \
            or (budget.input_hash, budget.source_hash, budget.policy_hash) != (input_hash, source_hash, policy_hash(policy)):
        return 0.0
    return max(0.0, min(45.0, budget.deadline - time.perf_counter()))


class _Once:
    def __init__(self):
        self.lock = threading.Lock()
        self.used = False


@dataclass(frozen=True)
class FailureFeedback:
    data: dict
    seal: str
    authority: object = field(default=None, repr=False)
    once: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class _WireGrammarFailure:
    data: dict
    seal: str
    authority: object = field(default=None, repr=False)


@dataclass(frozen=True)
class _TargetGrammarFailure:
    data: dict
    seal: str
    authority: object = field(default=None, repr=False)


@dataclass(frozen=True)
class _ConsultRuleGrammarFailure:
    data: dict
    seal: str
    authority: object = field(default=None, repr=False)


def _sign(data):
    return hmac.new(_KEY, canonical_hash(data).encode('ascii'), hashlib.sha256).hexdigest()


def consult_rule_grammar_failure(error,prepared,raw,canonical,schema):
    from .semantic_consult_rule_grammar import consult_rule_grammar_witness
    data=consult_rule_grammar_witness(error,prepared,canonical,schema)
    if data is None:return None
    data['raw_decision_hash']=canonical_hash(raw)
    return _ConsultRuleGrammarFailure(data,_sign(data),_AUTHORITY)


def _consult_rule_grammar_data_valid(data,input_hash,source_hash):
    from .semantic_consult_rule_grammar import CODE,VERSION as RULE_VERSION,consult_rule_schema_relation
    from .semantic_router import QUESTION_ASPECTS,rule_aspect_vocabulary
    rules=rule_aspect_vocabulary()
    return isinstance(data,dict) and data.get('version')==RULE_VERSION \
        and data.get('classification')=='WIRE_GRAMMAR' and data.get('phase')=='CONSULT_OPTIONAL_RULE_METADATA' \
        and data.get('validation_code')==CODE and data.get('reason')=='RULE_METADATA_WITHOUT_RULES' \
        and data.get('location')==['rule_aspects'] and data.get('mode')=='CONSULT' \
        and isinstance(data.get('question_aspects'),list) and len(data['question_aspects'])<=8 \
        and all(type(item) is str and item in QUESTION_ASPECTS and item!='RULES' for item in data['question_aspects']) \
        and isinstance(data.get('rule_aspects'),list) and 1<=len(data['rule_aspects'])<=8 \
        and all(type(item) is str and item in rules for item in data['rule_aspects']) \
        and data.get('all_other_validation_passed') is True \
        and data.get('schema_relation_hash')==canonical_hash(consult_rule_schema_relation()) \
        and data.get('input_hash')==input_hash and data.get('source_message_hash')==source_hash \
        and all(type(data.get(key)) is str and len(data[key])==64 for key in
            ('raw_decision_hash','decoder_input_hash','validated_decision_hash','outgoing_schema_hash')) \
        and data.get('bank_write_authorized') is False


def consult_rule_grammar_observation(value):
    if type(value) is not _ConsultRuleGrammarFailure or value.authority is not _AUTHORITY \
            or not hmac.compare_digest(value.seal,_sign(value.data)):return None
    return deepcopy(value.data)


def _trusted_consult_rule_grammar(value,result):
    return consult_rule_grammar_observation(value) is not None \
        and _consult_rule_grammar_data_valid(value.data,result.input_hash,result.source_message_hash) \
        and result.diagnostics.get('consult_rule_grammar_failure')==value.data


def target_grammar_failure(error,prepared,raw,canonical):
    from .semantic_read_target_contract import CODE,target_field_grammar_witness
    if type(error) is not ValueError or str(error)!=CODE:return None
    data=target_field_grammar_witness(canonical,prepared)
    if data is None:return None
    data['raw_decision_hash']=canonical_hash(raw)
    return _TargetGrammarFailure(data,_sign(data),_AUTHORITY)


def _target_grammar_data_valid(data,input_hash,source_hash):
    from .semantic_read_target_contract import CODE,VERSION as TARGET_VERSION
    return isinstance(data,dict) and data.get('version')==TARGET_VERSION \
        and data.get('classification')=='WIRE_GRAMMAR' and data.get('phase')=='READ_TARGET_FIELD' \
        and data.get('validation_code')==CODE and data.get('reason')=='UNSUPPORTED_COLLECTION_FIELD' \
        and data.get('input_hash')==input_hash and data.get('source_message_hash')==source_hash \
        and isinstance(data.get('location'),list) and len(data['location'])==3 \
        and data['location'][0]=='read_targets' and data['location'][2]=='field' \
        and type(data['location'][1]) is int and 0<=data['location'][1]<8 \
        and all(isinstance(data.get(key),str) and len(data[key])==64 for key in ('raw_decision_hash','decoder_input_hash')) \
        and data.get('bank_write_authorized') is False


def target_grammar_observation(value):
    if not isinstance(value,_TargetGrammarFailure) or value.authority is not _AUTHORITY \
            or not hmac.compare_digest(value.seal,_sign(value.data)):return None
    return deepcopy(value.data)


def _trusted_target_grammar(value,result):
    return target_grammar_observation(value) is not None \
        and _target_grammar_data_valid(value.data,result.input_hash,result.source_message_hash) \
        and result.diagnostics.get('target_grammar_failure')==value.data


def wire_grammar_failure(error, prepared, raw, decoder_input):
    """Attest the real decoder's one grammar branch, never infer source truth.

    Recheck the same pure decoder/row, rather than duplicating its grammar or
    accepting an error-shaped JSON dictionary. No value is repaired here.
    """
    from .semantic_literal_span_wire import LiteralSpanWireError,CONTEXT_VERSION,normalize_literal_span_wire
    if type(error) is not LiteralSpanWireError or str(error)!=WIRE_GRAMMAR_CODE \
            or error.version!=CONTEXT_VERSION or error.diagnostic.get('reason')!='INVALID_CONTEXT_SELECTOR_SHAPE' \
            or not isinstance(prepared,dict) or type(prepared.get('message')) is not str or not prepared['message'].strip():
        return None
    location=error.diagnostic.get('location')
    if not isinstance(location,list) or len(location)!=3 or location[0]!='changes' or location[2]!='source_span' \
            or type(location[1]) is not int or not 0<=location[1]<24 \
            or not isinstance(decoder_input,dict) or not isinstance(decoder_input.get('changes'),list) \
            or not location[1]<len(decoder_input['changes'])<=24:
        return None
    row=decoder_input['changes'][location[1]]
    if not isinstance(row,dict) or not isinstance(row.get('source_span'),dict) or 'within' not in row['source_span']:
        return None
    try:normalize_literal_span_wire(decoder_input,prepared['message'])
    except LiteralSpanWireError as checked:
        if type(checked) is not LiteralSpanWireError or str(checked)!=str(error) or checked.version!=error.version \
                or checked.diagnostic!=error.diagnostic:return None
    else:return None
    data={'version':WIRE_GRAMMAR_VERSION,'classification':'WIRE_GRAMMAR','phase':'LITERAL_SPAN_WIRE',
        'validation_code':WIRE_GRAMMAR_CODE,'normalizer_version':CONTEXT_VERSION,
        'reason':'INVALID_CONTEXT_SELECTOR_SHAPE','location':deepcopy(location),
        'input_hash':canonical_hash(prepared),'source_message_hash':canonical_hash({'message':prepared['message']}),
        'raw_decision_hash':canonical_hash(raw),'decoder_input_hash':canonical_hash(decoder_input),
        'change_hash':canonical_hash(row),'failure_hash':canonical_hash(error.diagnostic),'bank_write_authorized':False}
    return _WireGrammarFailure(data,_sign(data),_AUTHORITY)


def _grammar_data_valid(data, input_hash, source_hash):
    from .semantic_literal_span_wire import CONTEXT_VERSION
    return isinstance(data,dict) and data.get('version')==WIRE_GRAMMAR_VERSION \
        and data.get('classification')=='WIRE_GRAMMAR' and data.get('phase')=='LITERAL_SPAN_WIRE' \
        and data.get('validation_code')==WIRE_GRAMMAR_CODE and data.get('normalizer_version')==CONTEXT_VERSION \
        and data.get('reason')=='INVALID_CONTEXT_SELECTOR_SHAPE' \
        and data.get('input_hash')==input_hash and data.get('source_message_hash')==source_hash \
        and isinstance(data.get('location'),list) and len(data['location'])==3 \
        and data['location'][0]=='changes' and data['location'][2]=='source_span' \
        and type(data['location'][1]) is int and 0<=data['location'][1]<24 \
        and all(isinstance(data.get(key),str) and len(data[key])==64 for key in
            ('raw_decision_hash','decoder_input_hash','change_hash','failure_hash')) \
        and data.get('bank_write_authorized') is False


def _trusted_grammar(value, result):
    if not isinstance(value,_WireGrammarFailure) or value.authority is not _AUTHORITY \
            or not hmac.compare_digest(value.seal,_sign(value.data)) \
            or not _grammar_data_valid(value.data,result.input_hash,result.source_message_hash):return False
    diagnostic=result.diagnostics.get('semantic_literal_span_wire_normalization')
    if not isinstance(diagnostic,dict):return False
    d=value.data;failure=diagnostic.get('failure')
    return result.diagnostics.get('wire_grammar_failure')==d and diagnostic.get('status')=='REJECTED' \
        and diagnostic.get('version')==d['normalizer_version'] \
        and diagnostic.get('context_hash')==d['input_hash'] and diagnostic.get('source_message_hash')==d['source_message_hash'] \
        and diagnostic.get('raw_decision_hash')==d['raw_decision_hash'] \
        and diagnostic.get('input_decision_hash')==d['decoder_input_hash'] and diagnostic.get('canonical_decision_hash') is None \
        and diagnostic.get('interpretation_changed') is False and diagnostic.get('bank_write_authorized') is False \
        and isinstance(failure,dict) and failure.get('version')==d['normalizer_version'] \
        and failure.get('reason')==d['reason'] and failure.get('location')==d['location'] \
        and canonical_hash(failure)==d['failure_hash']


def grammar_observation(value):
    if not isinstance(value,_WireGrammarFailure) or value.authority is not _AUTHORITY \
            or not hmac.compare_digest(value.seal,_sign(value.data)):return None
    return deepcopy(value.data)


def complete_usage(value):
    return isinstance(value, dict) and all(type(value.get(key)) is int and value[key] >= 0
        for key in ('input_tokens', 'output_tokens', 'total_tokens')) \
        and value['input_tokens'] + value['output_tokens'] == value['total_tokens']


def seal_failure(result, policy, *, _wire_grammar=None, _target_grammar=None, _consult_rule_grammar=None):
    """Called only at the router boundary after independently witnessed HTTP."""
    d = result.diagnostics
    errors = d.get('semantic_validation_errors')
    if not enabled(policy) or result.status != 'MODEL_INVALID' or result.decision is not None \
            or d.get('model_api_call_count') != 1 or d.get('http_issued') is not True \
            or d.get('http_status') != 200 or d.get('finish_reason') != 'stop' \
            or d.get('fallback') is not False or not complete_usage(d.get('usage')) \
            or not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0],dict) \
            or d.get('failure_reasons') != ['SEMANTIC_MODEL_INVALID']:
        return None
    code=errors[0].get('code')
    if not isinstance(code,str):return None
    grammar=code==WIRE_GRAMMAR_CODE and _trusted_grammar(_wire_grammar,result)
    from .semantic_read_target_contract import CODE as TARGET_CODE
    target_grammar=code==TARGET_CODE and _trusted_target_grammar(_target_grammar,result)
    from .semantic_consult_rule_grammar import CODE as CONSULT_RULE_CODE
    consult_rule_grammar=code==CONSULT_RULE_CODE and _trusted_consult_rule_grammar(_consult_rule_grammar,result)
    if code not in ERRORS and not grammar and not target_grammar and not consult_rule_grammar:return None
    data = {'version': VERSION, 'code': errors[0]['code'], 'input_hash': result.input_hash,
        'source_message_hash': result.source_message_hash, 'policy_hash': policy_hash(policy),
        'provider': d.get('actual_provider'), 'model': d.get('requested_model'),
        'failure_diagnostics_hash': canonical_hash(d), 'nonce': secrets.token_hex(16)}
    location=errors[0].get('location')
    if code=='SEMANTIC_CARD_REFERENCE_LITERAL_REQUIRED' and isinstance(location,list) and len(location)==3 \
            and location[0]=='changes' and type(location[1])is int and 0<=location[1]<24 and location[2]=='source_span':
        data['rejected_field_path']=location
    if grammar:data['wire_grammar_failure']=deepcopy(_wire_grammar.data)
    if target_grammar:data['target_grammar_failure']=deepcopy(_target_grammar.data)
    if consult_rule_grammar:data['consult_rule_grammar_failure']=deepcopy(_consult_rule_grammar.data)
    return FailureFeedback(data, _sign(data), _AUTHORITY, _Once())


def verified_feedback(value, *, result=None, policy, input_hash, source_hash, provider=None, model=None):
    if not enabled(policy) or not isinstance(value, FailureFeedback) or value.authority is not _AUTHORITY \
            or not isinstance(value.once, _Once) or not hmac.compare_digest(value.seal, _sign(value.data)):
        return False
    d = value.data
    allowed=d.get('code') in ERRORS or d.get('code')==WIRE_GRAMMAR_CODE \
        and _grammar_data_valid(d.get('wire_grammar_failure'),input_hash,source_hash)
    from .semantic_read_target_contract import CODE as TARGET_CODE
    allowed=allowed or d.get('code')==TARGET_CODE and _target_grammar_data_valid(d.get('target_grammar_failure'),input_hash,source_hash)
    from .semantic_consult_rule_grammar import CODE as CONSULT_RULE_CODE
    allowed=allowed or d.get('code')==CONSULT_RULE_CODE and _consult_rule_grammar_data_valid(d.get('consult_rule_grammar_failure'),input_hash,source_hash)
    if not allowed or (d.get('version'), d.get('input_hash'), d.get('source_message_hash'), d.get('policy_hash')) \
            != (VERSION, input_hash, source_hash, policy_hash(policy)):
        return False
    if provider is not None and (d.get('provider'), d.get('model')) != (provider, model):
        return False
    if result is not None and (result.status != 'MODEL_INVALID' or result.decision is not None or
            result.input_hash != input_hash or result.source_message_hash != source_hash or
            d.get('failure_diagnostics_hash') != canonical_hash(result.diagnostics)):
        return False
    return True


def consume_feedback(value, **bindings):
    if not verified_feedback(value, **bindings):
        return None
    with value.once.lock:
        if value.once.used:
            return None
        value.once.used = True
    result={'version': VERSION, 'validation_code': value.data['code'], 'first_failure_hash': value.data['failure_diagnostics_hash'],
        'feedback_hash': canonical_hash(value.data), 'bank_write_authorized': False}
    if 'wire_grammar_failure' in value.data:result['wire_grammar_failure']=deepcopy(value.data['wire_grammar_failure'])
    if 'target_grammar_failure' in value.data:result['target_grammar_failure']=deepcopy(value.data['target_grammar_failure'])
    if 'consult_rule_grammar_failure' in value.data:result['consult_rule_grammar_failure']=deepcopy(value.data['consult_rule_grammar_failure'])
    return result


def feedback_instruction(value):
    if value.data.get('code')=='SEMANTIC_CARD_REFERENCE_LITERAL_REQUIRED' and value.data.get('rejected_field_path'):
        index=value.data['rejected_field_path'][1]
        return (value.data['code']+'. The rejected field is changes['+str(index)+'].source_span. '
            +_ERROR_HINTS['SEMANTIC_CARD_REFERENCE_LITERAL_REQUIRED']+' '
            'Distinguish an identity explicitly written earlier in this SAME current sentence from historical/tool entities. '
            'An explicit card literal in this current sentence can be selected again for a dependent action without inventing an entity. '
            'Do not repeat an unresolved pronoun as a literal card field. '+_FEEDBACK)
    if 'consult_rule_grammar_failure' in value.data:
        return ('WIRE_GRAMMAR/consult-optional-rule-metadata: '+value.data['code']+'. '
            'For pure CONSULT the existing offered allOf relation requires empty/omitted optional rule_aspects '
            'when question_aspects has no RULES. Reinterpret the same original need and complete sources under the unchanged schema. '
            'Choose the appropriate aspects yourself: do not add RULES solely to pass validation, remove a genuine user question, '
            'change objects or sources, invent facts or create an action. The server has not repaired your proposal. No banking authority.')
    if 'target_grammar_failure' in value.data:
        return ('WIRE_GRAMMAR/read-target-field: '+value.data['code']+'. '
            'read_targets accepts only its closed collection fields and pure READ actions, not every scalar capability field. '
            'A category noun is not a specific card identifier. Reinterpret the original requested scope yourself: '
            'a category-wide request may omit identity selectors, while a particular object needs its exact literal or compatible offered entity. '
            'Never drop an explicit object, broaden its selection, invent an identity or bypass owner/source/confirmation checks. No banking authority.')
    if 'wire_grammar_failure' in value.data:
        return ('WIRE_GRAMMAR/context-v2: '+value.data['code']+' / INVALID_CONTEXT_SELECTOR_SHAPE. '
            'Reinterpret the same message/role/full sources under the same schema; choose valid modes, refs and fields yourself. '
            'Do not invent coordinates, alter intent or bypass source/owner/CF checks. No banking authority.')
    return 'The previous interpretation was rejected by the existing validator: '+value.data['code']+'. '+_ERROR_HINTS.get(value.data['code'],'')+' '+_FEEDBACK


def _archive_diagnostics(raw):
    """Closed, redaction-stable observation; not the READY receipt or permit.

    Keep original diagnostics' hash, but do not make an audit hash depend on
    secret-looking keys removed by the independent acceptance client.
    """
    keys=('purpose','actual_provider','requested_model','actual_model','response_model',
        'routing_policy_version','input_hash','source_message_hash','model_api_call_count',
        'http_issued','http_status','elapsed_ms','finish_reason','fallback','failure_reasons',
        'request_payload_hash','decision_hash','reunderstanding_stop','validated_reunderstanding_feedback','wire_grammar_failure',
        'semantic_history_read_wire','target_grammar_failure','consult_rule_grammar_failure')
    result={key:deepcopy(raw[key]) for key in keys if key in raw}
    from .bounded_http import safe_http_diagnostics
    result.update(safe_http_diagnostics(raw))
    result['usage']={key:raw['usage'][key] for key in ('input_tokens','output_tokens','total_tokens')
        if isinstance(raw.get('usage'),dict) and type(raw['usage'].get(key)) is int and raw['usage'][key]>=0}
    if isinstance(raw.get('semantic_validation_errors'),list):
        result['semantic_validation_errors']=[{'validation_reason':row['code'],'location':deepcopy(row.get('location',[]))}
            for row in raw['semantic_validation_errors'][:8] if isinstance(row,dict) and isinstance(row.get('code'),str)]
    if isinstance(raw.get('semantic_rejected_structure'),dict):
        result['semantic_rejected_structure']=deepcopy(raw['semantic_rejected_structure'])
    return result


def record_attempt(value, result, *, now=None):
    """Append, never overwrite a previous failed observation or final proof."""
    if not valid_ledger(value) or len(value['attempts']) != value['reserved_attempts'] - 1:
        raise ValueError('REUNDERSTANDING_LEDGER_CONFLICT')
    value = deepcopy(value)
    value['attempts'].append({'ordinal': len(value['attempts']) + 1, 'status': result.status,
        'diagnostics': _archive_diagnostics(result.diagnostics), 'diagnostics_hash':canonical_hash(result.diagnostics),
        'decision_hash': result.decision_hash,
        'receipt_hash': result.receipt_hash})
    value['status'] = 'OBSERVED'
    counts = [r['diagnostics'].get('model_api_call_count') for r in value['attempts']]
    value['http_count_complete'] = all(type(n) is int and n >= 0 for n in counts)
    value['model_api_call_count'] = sum(n for n in counts if type(n) is int and n >= 0)
    usages = [r['diagnostics'].get('usage') for r in value['attempts']]
    value['usage_complete'] = all(complete_usage(u) for u in usages)
    value['usage'] = {key: sum(u[key] for u in usages if complete_usage(u))
        for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    value['attempts_hash'] = canonical_hash(value['attempts'])
    value['model_elapsed_ms']=round(sum(row['diagnostics'].get('elapsed_ms',0.0) for row in value['attempts']),2)
    value['elapsed_ms']=round(max(0.0,(_utc(now or datetime.now(timezone.utc))-
        _utc(datetime.fromisoformat(value['started_at']))).total_seconds()*1000),2)
    return value


def reserve_second(value, policy, *, now, kind='MANUAL'):
    if not valid_ledger(value) or value['reserved_attempts'] != 1 or len(value['attempts']) != 1 \
            or not enabled(policy) or value['policy_hash'] != policy_hash(policy) or remaining(value, now=now) <= 0 \
            or type(value['attempts'][0]['diagnostics'].get('model_api_call_count')) is not int \
            or not 0<=value['attempts'][0]['diagnostics']['model_api_call_count']<2:
        return None
    value = deepcopy(value)
    if kind not in {'MANUAL','VALIDATED_STRUCTURE'}:return None
    value.update(reserved_attempts=2, status='SECOND_RESERVED',second_attempt_kind=kind)
    return value


def retry_available(value, *, now):
    return valid_ledger(value) and value['reserved_attempts'] < 2 \
        and len(value['attempts']) == value['reserved_attempts'] and remaining(value, now=now) > 0 \
        and type(value['attempts'][0]['diagnostics'].get('model_api_call_count')) is int \
        and 0<=value['attempts'][0]['diagnostics']['model_api_call_count']<2


def metric_attempts(value):
    """Validated shape/hash only, not a new authority for model or bank work."""
    if not valid_ledger(value) or value.get('attempts_hash') != canonical_hash(value['attempts']):
        return None
    return deepcopy(value['attempts'])
