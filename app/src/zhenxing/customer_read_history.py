"""Immutable, exact-turn READ evidence; never a current banking command.

Capture happens from a fresh runtime trace before post-tool narration. A
retained read remains readable through an ordinary later write revision, but
its source turn, child result, original plan and current access are rechecked
on every GET. Old records without this snapshot are deliberately not inferred.
"""
from copy import deepcopy

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Task
from .planning import canonical_hash
from .runtime_models import RuntimeRecord, RuntimeOperation, PlanSnapshot
from .services import Principal

STORE_KEY = 'conversation_read_snapshots'
STORE_VERSION = 'conversation-read-snapshots-v1'
ITEM_VERSION = 'conversation-read-snapshot-v1'
LIMIT = 128
TURN_PROOF_KEY = 'customer_read_snapshot_binding'


def _missing():
    raise DomainError('CUSTOMER_READ_SNAPSHOT_NOT_RETAINED', '这次查询没有保留可核对的原始结果，可以重新查询。', 404)


def _changed():
    raise DomainError('CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED', '这次查询的原始记录暂时无法核对，请重新查询。', 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def _store(record):
    store = (record.metadata_json or {}).get(STORE_KEY) if record else None
    if store is None:
        return {'schema_version': STORE_VERSION, 'limit': LIMIT, 'items': []}
    if not isinstance(store, dict) or store.get('schema_version') != STORE_VERSION \
            or not isinstance(store.get('items'), list) or len(store['items']) > LIMIT \
            or any(not isinstance(item, dict) for item in store['items']):
        _changed()
    return store


def _source(session, principal, thread, turn, parent, binding):
    from .dialogue_state import dialogue_actor_scope
    from .permissions import require_banking_actor
    from .semantic_proposal import _validate_prior_semantic
    role = require_banking_actor(session, principal)
    _scope, epoch = dialogue_actor_scope(session, principal)
    if not _owned(thread, principal) or not _owned(turn, principal) or not _owned(parent, principal) \
            or thread.status != 'ACTIVE' or turn.thread_id != thread.id or turn.task_id != parent.id \
            or turn.status != 'ANSWERED' or turn.mode not in {'NEW_TASK', 'REVISE', 'CLARIFY'}:
        _changed()
    if not isinstance(binding, dict) or binding.get('actor_scope_hash') != epoch \
            or (turn.diagnostics_json or {}).get('dialogue_actor_scope_hash') != epoch \
            or (turn.diagnostics_json or {}).get('actor_role_at_submission') != role:
        raise DomainError('CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED', '此前查询的访问权限已变化，请重新进入会话查询。', 403)
    if binding.get('source_turn_id') != turn.id or binding.get('conversation_id') != thread.id \
            or binding.get('source_sequence') != turn.sequence:
        _changed()
    # This audit deliberately accepts the ORIGINAL semantic binding even if
    # the parent now has a later binding/message. It gives no execution proof.
    try:
        _validate_prior_semantic(session, principal, thread, parent, binding, audit_only=True)
    except ValueError:
        # Invalid or no-longer-supported sealed semantic input is not an
        # execution proof. Fail this exact snapshot closed, rather than let
        # a validation error turn an entire owned conversation into HTTP 500.
        # Access DomainError and database/programming failures are not caught.
        _changed()
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, parent)
    return role, epoch


def _live_read(session, principal, parent, snapshot, fact):
    from .intelligence import READ_KINDS, action_signature
    from .customer_response import _read_recorded
    from .simulation import as_utc
    from .conversation_history_projection import bounded_source_get
    op = bounded_source_get(session, RuntimeOperation, fact.get('operation_id'), principal)
    child = bounded_source_get(session, Task, fact.get('child_task_id'), principal)
    child_record = bounded_source_get(session, RuntimeRecord, child.id, principal) if child else None
    nodes = snapshot.plan_json.get('nodes', []) if isinstance(snapshot.plan_json, dict) else []
    matches = [node for node in nodes if isinstance(node, dict) and node.get('operation_id') == fact.get('operation_id')]
    if len(matches) != 1 or not op or not _owned(child, principal) or not child_record:
        _changed()
    node = matches[0]
    if op.parent_task_id != parent.id or op.sandbox_id != principal.sandbox_id \
            or op.action_kind not in READ_KINDS or op.status != 'SUCCEEDED' or child.state != 'SUCCEEDED' \
            or op.child_task_id != child.id or op.action_kind != fact.get('action') \
            or op.operation_key != fact.get('node_id') or fact.get('status') != 'SUCCEEDED' \
            or op.args_json.get('kind') != op.action_kind or canonical_hash(action_signature(op.args_json)) != op.fingerprint \
            or node.get('node_id') != op.operation_key or node.get('kind') != op.action_kind \
            or canonical_hash(node.get('args')) != canonical_hash(op.args_json) \
            or node.get('depends_on') != op.depends_on_json \
            or child_record.kind != 'CHILD' or child_record.root_task_id != parent.id \
            or child_record.metadata_json.get('operation_id') != op.id \
            or canonical_hash(child.result_json) != canonical_hash(fact.get('result')) \
            or canonical_hash(child.analysis_json or None) != canonical_hash(fact.get('analysis')) \
            or fact.get('observed_at') != as_utc(child.updated_at).isoformat() \
            or not _read_recorded(op.action_kind, fact.get('result'), {'analysis': fact.get('analysis')}):
        _changed()
    from .permissions import actor_role
    if child_record.metadata_json.get('prepared_actor_role') != actor_role(session, principal.sandbox_id, principal.user_id):
        raise DomainError('CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED', '此前查询的访问权限已变化，请重新查询。', 403)
    if op.action_kind == 'FAMILY_VIEW' and fact['result'].get('authorized_balances'):
        from .extensions import is_family_helper, family_view
        if is_family_helper(session, principal.sandbox_id, principal.user_id):
            required = {row.get('owner_user_id') for row in fact['result'].get('delegations', [])
                if row.get('valid') and row.get('grantee_user_id') == principal.user_id and 'READ_BALANCE' in row.get('scopes', [])}
            current = family_view(session, principal.sandbox_id, principal.user_id)
            readable = {row['owner_user_id'] for row in current['delegations']
                if row['valid'] and row['grantee_user_id'] == principal.user_id and 'READ_BALANCE' in row['scopes']}
            if not required or None in required or not required <= readable:
                raise DomainError('FAMILY_HISTORY_ACCESS_CHANGED', '此前余额资料的授权范围已失效，请重新核对当前委托。', 403)
    return op


def _checked_facts(trace):
    from .customer_presentation import completed_reads_for_pending, _COMPLETED_READ_FIELDS
    from .customer_response import _read_recorded
    from .intelligence import READ_KINDS
    if trace.get('state') == 'AWAITING_CONFIRMATION':
        return completed_reads_for_pending(trace)
    outcome, nodes = trace.get('outcome'), trace.get('nodes')
    if trace.get('state') != 'SUCCEEDED' or not isinstance(outcome, dict) \
            or outcome.get('kind') != 'QUERY_RESULT' or outcome.get('verified') is not True \
            or not isinstance(nodes, list) or not nodes or any(node.get('action') not in READ_KINDS for node in nodes):
        return []
    facts = trace.get('completed_read_results')
    if not isinstance(facts, list) or len(facts) != len(nodes):
        return []
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != _COMPLETED_READ_FIELDS \
                or fact.get('schema_version') != 'completed-read-result-v1' or fact.get('task_id') != trace.get('task_id') \
                or type(fact.get('task_version')) is not int or fact['task_version'] != trace.get('version') \
                or fact.get('operation_id') in seen or fact.get('status') != 'SUCCEEDED' or fact.get('action') not in READ_KINDS:
            return []
        matching = [node for node in nodes if node.get('operation_id') == fact['operation_id']]
        if len(matching) != 1 or any(matching[0].get(key) != fact[key] for key in ('node_id', 'child_task_id', 'action', 'status')) \
                or canonical_hash(matching[0].get('result')) != canonical_hash(fact.get('result')) \
                or not _read_recorded(fact['action'], fact.get('result'), {'analysis': fact.get('analysis')}):
            return []
        seen.add(fact['operation_id'])
    return deepcopy(facts)


def retain_customer_read_snapshot(session, parent, record, trace):
    """First immutable successful READ group per exact owned source turn.

    Missing legacy provenance is not backfilled. The caller controls the fresh
    trace; malformed sources are recorded as unretained, never bank failures.
    """
    if not record or record.kind != 'PARENT' or trace.get('task_id') != parent.id \
            or trace.get('version') != parent.version or trace.get('state') != parent.state:
        return False
    context = (record.metadata_json or {}).get('conversation_context')
    binding = (record.metadata_json or {}).get('cloud_semantic_intent')
    if not isinstance(context, dict) or not isinstance(binding, dict):
        return False
    thread = session.get(ChatThread, context.get('conversation_id'))
    turn = session.get(ChatTurn, context.get('turn_id'))
    if not thread or not turn or context.get('authority') != 'VERIFIED_CLOUD_SEMANTIC_USER_INTENT' \
            or context.get('bank_write_authorized') is not False \
            or context.get('turn_id') != binding.get('source_turn_id') \
            or context.get('conversation_id') != binding.get('conversation_id') \
            or context.get('original_user_message') != turn.message or parent.message != turn.message:
        return False
    facts = _checked_facts(trace)
    if not facts:
        return False
    principal = Principal(parent.user_id, parent.sandbox_id,
        family_context=(record.metadata_json or {}).get('family_context_origin'))
    role, epoch = _source(session, principal, thread, turn, parent, binding)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if not snapshot or snapshot.plan_hash != parent.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
        _changed()
    if snapshot.proposal_json != binding.get('expected_proposal'):
        _changed()
    for fact in facts:
        _live_read(session, principal, parent, snapshot, fact)
    previous = _store(record)
    if any(item.get('source_turn_id') == turn.id for item in previous['items'] if isinstance(item, dict)):
        return False
    if (turn.diagnostics_json or {}).get(TURN_PROOF_KEY) is not None:
        # Removal of an existing immutable snapshot must not become authority
        # to recapture a different version under the same source turn.
        return False
    if len(previous['items']) >= LIMIT:
        record.metadata_json = {**record.metadata_json, STORE_KEY: {**deepcopy(previous),
            'capacity_reached': True, 'last_unretained_turn_id': turn.id, 'last_unretained_reason': 'CAPACITY_REACHED'}}
        return False
    payload = {'schema_version': ITEM_VERSION, 'source_conversation_id': thread.id, 'source_turn_id': turn.id,
        'task_id': parent.id, 'task_version': parent.version, 'plan_version': snapshot.version,
        'source_snapshot_id': snapshot.id, 'source_plan_hash': snapshot.plan_hash,
        'source_proposal_hash': canonical_hash(snapshot.proposal_json), 'source_message': turn.message,
        'actor_scope_hash': epoch, 'actor_role': role, 'semantic_binding': deepcopy(binding),
        'facts': deepcopy(facts), 'reply_language': trace.get('reply_language') or 'zh-CN',
        'created_at': trace.get('created_at'), 'observed_at': max(fact['observed_at'] for fact in facts),
        'authority': 'ORIGINAL_READ_ONLY_RESULTS_NOT_CURRENT_STATE_OR_WRITE_AUTHORIZATION'}
    if len(facts)==1 and facts[0]['action']=='BILL_ANALYSIS':
        from .customer_bill_fallback import prepare_customer_bill_supplement,checked_bill_fallback,matches_supplement_source
        supplemental=prepare_customer_bill_supplement(session,principal,trace)
        if supplemental is not None:
            if not checked_bill_fallback(supplemental) or not matches_supplement_source(facts[0]['analysis'],supplemental):
                _changed()
            from .models import utcnow
            from .simulation import as_utc
            observed=as_utc(utcnow()).isoformat()
            payload.update({'supplementary_bill':deepcopy(supplemental),
                'supplementary_bill_observed_at':observed,
                'supplementary_bill_binding':{'schema_version':'owned-supplementary-read-binding-v1',
                    'source_conversation_id':thread.id,'source_turn_id':turn.id,'task_id':parent.id,
                    'operation_id':facts[0]['operation_id'],'child_task_id':facts[0]['child_task_id'],
                    'owner_hash':canonical_hash({'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}),
                    'actor_scope_hash':epoch,'original_analysis_hash':canonical_hash(facts[0]['analysis']),
                    'supplementary_bill_hash':canonical_hash(supplemental),'observed_at':observed}})
    payload['snapshot_hash'] = canonical_hash(payload)
    record.metadata_json = {**record.metadata_json, STORE_KEY: {**deepcopy(previous),
        'items': [*deepcopy(previous['items']), payload], 'capacity_reached': bool(previous.get('capacity_reached'))}}
    turn.diagnostics_json = {**deepcopy(turn.diagnostics_json), TURN_PROOF_KEY: {
        'schema_version': 'conversation-read-source-binding-v1', 'task_id': parent.id,
        'source_conversation_id': thread.id, 'source_turn_id': turn.id,
        'task_version': parent.version, 'snapshot_hash': payload['snapshot_hash']}}
    return True


def _load(session, principal, conversation_id, turn_id):
    from .permissions import require_banking_actor
    require_banking_actor(session, principal)
    from .conversation_history_projection import require_bounded_read_source
    require_bounded_read_source(session, principal, conversation_id, turn_id)
    from .conversation_history_projection import bounded_source_get
    thread, turn = session.get(ChatThread, conversation_id), bounded_source_get(session, ChatTurn, turn_id, principal)
    if not _owned(thread, principal) or not _owned(turn, principal) or turn.thread_id != conversation_id:
        _missing()
    if thread.status != 'ACTIVE':
        raise DomainError('CUSTOMER_READ_CONVERSATION_CLOSED', '请在当前有效会话重新查询。', 403)
    parent = bounded_source_get(session, Task, turn.task_id, principal) if turn.task_id else None
    if not _owned(parent, principal):
        _missing()
    record = bounded_source_get(session, RuntimeRecord, parent.id, principal)
    if not record or record.kind != 'PARENT':
        _missing()
    entries = [item for item in _store(record)['items'] if isinstance(item, dict) and item.get('source_turn_id') == turn.id]
    if not entries:
        _missing()
    if len(entries) != 1:
        _changed()
    item = deepcopy(entries[0]); digest = item.pop('snapshot_hash', None)
    if digest != canonical_hash(item) or item.get('schema_version') != ITEM_VERSION \
            or item.get('source_conversation_id') != thread.id or item.get('task_id') != parent.id \
            or item.get('source_message') != turn.message or type(item.get('task_version')) is not int or item['task_version'] < 1:
        _changed()
    if (turn.diagnostics_json or {}).get(TURN_PROOF_KEY) != {
            'schema_version': 'conversation-read-source-binding-v1', 'task_id': parent.id,
            'source_conversation_id': thread.id, 'source_turn_id': turn.id,
            'task_version': item['task_version'], 'snapshot_hash': digest}:
        _changed()
    role, epoch = _source(session, principal, thread, turn, parent, item.get('semantic_binding'))
    if item.get('actor_scope_hash') != epoch or item.get('actor_role') != role:
        raise DomainError('CUSTOMER_READ_SNAPSHOT_ACCESS_CHANGED', '此前查询的访问权限已变化，请重新查询。', 403)
    snapshot = bounded_source_get(session, PlanSnapshot, item.get('source_snapshot_id'), principal)
    if not snapshot or snapshot.task_id != parent.id or snapshot.version != item.get('plan_version') \
            or snapshot.plan_hash != item.get('source_plan_hash') or canonical_hash(snapshot.plan_json) != snapshot.plan_hash \
            or canonical_hash(snapshot.proposal_json) != item.get('source_proposal_hash') \
            or snapshot.proposal_json != item['semantic_binding'].get('expected_proposal'):
        _changed()
    facts = item.get('facts')
    if not isinstance(facts, list) or not 1 <= len(facts) <= 30 or len({fact.get('operation_id') for fact in facts if isinstance(fact, dict)}) != len(facts):
        _changed()
    for fact in facts:
        if not isinstance(fact, dict) or fact.get('task_id') != parent.id or fact.get('task_version') != item['task_version']:
            _changed()
        _live_read(session, principal, parent, snapshot, fact)
    if item.get('observed_at') != max(fact['observed_at'] for fact in facts):
        _changed()
    supplemental_keys={'supplementary_bill','supplementary_bill_observed_at','supplementary_bill_binding'}
    declared=supplemental_keys&set(item)
    if declared:
        if declared!=supplemental_keys or len(facts)!=1 or facts[0]['action']!='BILL_ANALYSIS':_changed()
        supplemental,observed=item['supplementary_bill'],item['supplementary_bill_observed_at']
        from .customer_bill_fallback import checked_bill_fallback,matches_supplement_source
        from datetime import datetime
        if not isinstance(observed,str):_changed()
        try:
            if datetime.fromisoformat(observed).tzinfo is None:_changed()
        except (ValueError,TypeError):_changed()
        expected_binding={'schema_version':'owned-supplementary-read-binding-v1',
            'source_conversation_id':thread.id,'source_turn_id':turn.id,'task_id':parent.id,
            'operation_id':facts[0]['operation_id'],'child_task_id':facts[0]['child_task_id'],
            'owner_hash':canonical_hash({'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}),
            'actor_scope_hash':epoch,'original_analysis_hash':canonical_hash(facts[0]['analysis']),
            'supplementary_bill_hash':canonical_hash(supplemental),'observed_at':observed}
        if item['supplementary_bill_binding']!=expected_binding or not checked_bill_fallback(supplemental) \
                or not matches_supplement_source(facts[0]['analysis'],supplemental):_changed()
    return parent, item


def retained_bill_read_scope(session,principal,origin):
    """Exact displayed bill source from the currently bound retained READ.

    None means a genuine older record with no retained store/proof/failure
    marker. A corrupt, removed or mismatched store is NEVER that compatibility
    case, and this helper never freshly generates a supplementary statement.
    """
    from .permissions import require_banking_actor
    require_banking_actor(session,principal)
    if not _owned(origin,principal):_changed()
    record=session.get(RuntimeRecord,origin.id)
    if not record or record.kind!='PARENT':return None
    metadata=record.metadata_json or {}
    context,binding=metadata.get('conversation_context'),metadata.get('cloud_semantic_intent')
    declared=STORE_KEY in metadata
    turns=list(session.scalars(select(ChatTurn).where(ChatTurn.task_id==origin.id,
        ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id)))
    proof_declared=any(TURN_PROOF_KEY in (turn.diagnostics_json or {}) for turn in turns)
    if not declared and not proof_declared:
        if metadata.get('customer_read_snapshot_unavailable'):_changed()
        return None
    if not isinstance(context,dict) or not isinstance(binding,dict) \
            or context.get('conversation_id')!=binding.get('conversation_id') \
            or context.get('turn_id')!=binding.get('source_turn_id') \
            or context.get('original_user_message')!=origin.message:
        _changed()
    if not declared or metadata.get(STORE_KEY) is None:_changed()
    try:parent,item=_load(session,principal,context.get('conversation_id'),context.get('turn_id'))
    except DomainError as error:
        if error.status_code==404:_changed()
        raise
    if parent.id!=origin.id or origin.state!='SUCCEEDED' or origin.version!=item['task_version'] \
            or record.plan_version!=item['plan_version'] or origin.plan_hash!=item['source_plan_hash'] \
            or origin.message!=item['source_message'] or binding!=item['semantic_binding'] \
            or len(item['facts'])!=1 or item['facts'][0]['action']!='BILL_ANALYSIS':
        _changed()
    original=item['facts'][0]['analysis']
    if not isinstance(original,dict):_changed()
    displayed=item.get('supplementary_bill',original)
    return {'original_analysis':deepcopy(original),'displayed_analysis':deepcopy(displayed),
        'source_kind':'OWNED_SUPPLEMENTARY_READ' if 'supplementary_bill' in item else 'ORIGINAL_QUERY_RESULT',
        'source_origin':'IMMUTABLE_READ_SNAPSHOT','source_turn_id':item['source_turn_id'],
        'task_version':item['task_version'],'snapshot_hash':(next(entry for entry in _store(record)['items']
            if entry['source_turn_id']==item['source_turn_id']))['snapshot_hash']}


def customer_read_snapshot_status(session, principal, conversation_id, turn_id):
    """No writes, no task inference; distinguish missing from invalid sources."""
    try:
        _parent, item = _load(session, principal, conversation_id, turn_id)
        return {'read_result_snapshot_status': 'RETAINED', 'read_result_snapshot_version': item['task_version'],
            'read_result_snapshot_observed_at': item['observed_at']}
    except DomainError as error:
        if error.status_code in {401, 403}:
            raise
        return {'read_result_snapshot_status': 'NOT_RETAINED' if error.status_code == 404 else 'UNAVAILABLE'}


def customer_read_results(session, principal, conversation_id, turn_id):
    """Exact-turn TaskTrace projection with zero usable financial actions."""
    parent, item = _load(session, principal, conversation_id, turn_id)
    from .customer_presentation import customer_task_view, validated_customer_read_coverage
    nodes = [{'node_id': fact['node_id'], 'operation_id': fact['operation_id'], 'child_task_id': fact['child_task_id'],
        'action': fact['action'], 'label': session.get(RuntimeOperation, fact['operation_id']).label,
        'status': 'SUCCEEDED', 'depends_on': [], 'result': deepcopy(fact['result']), 'can_compensate': False}
        for fact in item['facts']]
    analysis = next((deepcopy(fact['analysis']) for fact in item['facts'] if fact['action'] == 'BILL_ANALYSIS'), None)
    trace = {'schema_version': '2.0', 'task_id': parent.id, 'version': item['task_version'], 'state': 'SUCCEEDED',
        'message': item['source_message'], 'summary': item['source_message'], 'nodes': nodes, 'analysis': analysis,
        'receipt': None, 'pending_confirmation': None, 'allowed_actions': [], 'clarification_questions': [],
        'created_at': item['created_at'], 'updated_at': item['observed_at'], 'result_observed_at': item['observed_at'],
        'reply_language': item['reply_language'], 'outcome': {'kind': 'QUERY_RESULT', 'state': 'SUCCEEDED',
            'verified': True, 'completed_operation_ids': [fact['operation_id'] for fact in item['facts']]}}
    # _load has verified this exact original turn, sealed semantic binding and
    # current access. Preserve its question focus, not the latest task's focus
    # or a role-based default; a later permissions question must not render the
    # earlier balance layout simply because both use FAMILY_VIEW.
    from .customer_query_focus import FAMILY_FOCUS
    original_focus=(item['semantic_binding'].get('decision') or {}).get('presentation_focus')
    if original_focus in FAMILY_FOCUS and any(node['action']=='FAMILY_VIEW' for node in nodes):
        trace['customer_query_focus']=original_focus
    # No old/current post-tool LLM narration is copied here. The typed query
    # formatter derives natural copy only from these original read facts.
    # Image matching is explicitly a fresh supplemental check. Do not alter
    # the immutable bank results or store image-derived text in their snapshot.
    from .reviewed_bill_reply import attach_reviewed_bill_comparison
    trace=attach_reviewed_bill_comparison(session,principal,parent,trace,source_turn_id=turn_id,conversation_id=conversation_id)
    result = customer_task_view(trace,supplementary_bill=deepcopy(item.get('supplementary_bill')),
        supplementary_bill_observed_at=item.get('supplementary_bill_observed_at'),
        read_coverage_by_operation=validated_customer_read_coverage(session,principal,trace))
    # A read-only projection of a mixed workflow is still a PRE-action query,
    # not the completed workflow's answer. Derive this from the already audited
    # immutable source plan; never rewrite a historical snapshot or infer it
    # from customer prose/current task state.
    from .intelligence import READ_KINDS
    source_plan = session.get(PlanSnapshot, item['source_snapshot_id'])
    source_actions = (source_plan.proposal_json or {}).get('actions', [])
    mixed = any(action.get('kind') not in READ_KINDS for action in source_actions)
    return {**result, 'schema_version': '2.0', 'source_turn_id': turn_id, 'source_conversation_id': conversation_id,
        'read_only': True, 'bank_confirmation_permitted': False,
        'source_business_kind': 'MIXED_WORKFLOW' if mixed else 'QUERY_ONLY',
        'read_phase': 'BEFORE_ACTION' if mixed else 'QUERY_RESULT',
        'historical_read_result': True, 'observed_at': item['observed_at']}
