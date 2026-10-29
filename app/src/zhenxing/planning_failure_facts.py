"""Owned, historical preparation denials; never card state or payment authority.

Only a real DomainError in the PLAN job may create this record. Read-time
consumers recheck the owned failed job and audit chain before sealing a small
display projection. Raw exception text, PANs and model metadata are not facts.
"""
from copy import deepcopy
import hashlib
import hmac
import re
import secrets

from sqlalchemy import select

from .contracts import DomainError
from .planning import canonical_hash


CODE = 'PAYMENT_INSTRUMENT_UNAVAILABLE'
VERSION = 'controlled-planning-failure-v1'
PRIVATE_KEY = '_verified_planning_failure'
AUTHORITY = 'HISTORICAL_PREPARATION_DENIAL_NOT_CURRENT_STATE_OR_EXECUTION_RECEIPT'
_KEY = secrets.token_bytes(32)
_REASONS = {'LOCKED', 'LOST', 'INACTIVE', 'UNAVAILABLE'}


def _seal(domain, value):
    return hmac.new(_KEY, (domain + canonical_hash(value)).encode(), hashlib.sha256).hexdigest()


def payment_instrument_unavailable(principal, snapshot):
    """Keep the original rejection; attach only a checked owner's finite cause."""
    error = DomainError(CODE, '指定卡已锁定、挂失或未激活，不会自动换卡付款。', 409)
    if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 'payment-instrument-v1'
        or (snapshot.get('sandbox_id'), snapshot.get('user_id')) != (principal.sandbox_id, principal.user_id)
        or snapshot.get('scope_hash') != canonical_hash({key: value for key, value in snapshot.items() if key != 'scope_hash'})
        or not isinstance(snapshot.get('instrument_type'), str) or snapshot['instrument_type'] not in {'DEBIT', 'CREDIT'}
        or not isinstance(snapshot.get('status'), str) or snapshot.get('paymentable') is not False):
        error._controlled_payment_denial = {'invalid_source': True}
        return error
    tail = snapshot.get('display_last4')
    details = {'sandbox_id': principal.sandbox_id, 'user_id': principal.user_id,
        'reason': snapshot['status'] if snapshot.get('status') in {'LOCKED', 'LOST'} else 'INACTIVE',
        'instrument_type': snapshot['instrument_type'],
        'card_last4': tail if isinstance(tail, str) and re.fullmatch(r'[0-9a-fA-F]{4}', tail) else None}
    error._controlled_payment_denial = {'facts': details, 'seal': _seal('source:', details)}
    return error


def record_plan_failure(parent, job, error):
    if not isinstance(error, DomainError) or error.code != CODE or parent.state != 'FAILED' or job.kind != 'PLAN' \
        or job.task_id != parent.id or job.sandbox_id != parent.sandbox_id or job.status != 'FAILED':
        return None
    reason, instrument, tail = 'UNAVAILABLE', None, None
    source = getattr(error, '_controlled_payment_denial', None)
    if source is not None:
        if not isinstance(source, dict) or set(source) != {'facts', 'seal'}:
            return None
        details = source['facts']
        if not isinstance(details, dict) or source['seal'] != _seal('source:', details) \
            or (details.get('sandbox_id'), details.get('user_id')) != (parent.sandbox_id, parent.user_id):
            return None
        reason, instrument, tail = details['reason'], details['instrument_type'], details['card_last4']
    result = {'schema_version': VERSION, 'authority': AUTHORITY, 'stage': 'PLAN', 'code': CODE,
        'reason': reason, 'instrument_type': instrument, 'card_last4': tail,
        'task_id': parent.id, 'task_version': parent.version, 'source_job_id': job.id}
    job.result_json = {**(job.result_json or {}), 'controlled_planning_failure': deepcopy(result)}
    return result


def _public(record):
    required = {'schema_version', 'authority', 'stage', 'code', 'reason', 'instrument_type', 'card_last4'}
    if not isinstance(record, dict) or not required <= set(record) or record.get('schema_version') != VERSION \
        or record.get('authority') != AUTHORITY or record.get('stage') != 'PLAN' or record.get('code') != CODE \
        or not isinstance(record.get('reason'), str) or record['reason'] not in _REASONS \
        or (record.get('instrument_type') is not None and (not isinstance(record['instrument_type'], str)
            or record['instrument_type'] not in {'DEBIT', 'CREDIT'})):
        return None
    tail = record.get('card_last4')
    if tail is not None and (not isinstance(tail, str) or re.fullmatch(r'[0-9a-fA-F]{4}', tail) is None):
        return None
    return {key: record[key] for key in ('schema_version', 'authority', 'stage', 'code', 'reason', 'instrument_type', 'card_last4')}


def attach_planning_failure(session, principal, parent, trace, events):
    """A past denial stays past even if the user has since unlocked the card."""
    from .audit import verify_audit_chain
    from .runtime_models import WorkflowJob
    if parent.state != 'FAILED' or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
        or trace.get('pending_confirmation') is not None \
        or any(event.task_id != parent.id or event.sandbox_id != parent.sandbox_id for event in events) \
        or not verify_audit_chain(events):
        return trace
    latest = next((event for event in reversed(events) if event.event_type == 'WORKFLOW_JOB_FAILED'), None)
    payload = latest.payload_json if latest else {}
    if not isinstance(payload, dict) or payload.get('kind') != 'PLAN' or payload.get('error') != CODE:
        return trace
    if payload.get('job_id'):
        job = session.get(WorkflowJob, payload['job_id'])
    else:
        # Real legacy audit events had only kind/error. They prove a generic
        # unavailable instrument, not a specific historical LOCKED/LOST state.
        job = session.scalar(select(WorkflowJob).where(WorkflowJob.task_id == parent.id,
            WorkflowJob.kind == 'PLAN', WorkflowJob.status == 'FAILED', WorkflowJob.last_error == CODE)
            .order_by(WorkflowJob.created_at.desc(), WorkflowJob.id.desc()).limit(1))
    if not job or (job.task_id, job.sandbox_id, job.kind, job.status, job.last_error) \
            != (parent.id, parent.sandbox_id, 'PLAN', 'FAILED', CODE):
        return trace
    if not isinstance(job.result_json, dict): return trace
    record = job.result_json.get('controlled_planning_failure')
    if payload.get('controlled_planning_failure_hash'):
        if payload.get('planning_failure_capture_version') != VERSION \
            or not isinstance(record, dict) or canonical_hash(record) != payload['controlled_planning_failure_hash'] \
            or (record.get('task_id'), record.get('task_version'), record.get('source_job_id')) \
                != (parent.id, parent.version, job.id):
            return trace
    elif record is not None or payload.get('job_id') or payload.get('planning_failure_capture_version'):
        return trace
    else:
        record = {'schema_version': VERSION, 'authority': AUTHORITY, 'stage': 'PLAN', 'code': CODE,
            'reason': 'UNAVAILABLE', 'instrument_type': None, 'card_last4': None}
    public = _public(record)
    if public is None:
        return trace
    bound = {'task_id': parent.id, 'version': parent.version, 'state': 'FAILED', 'facts': public}
    intro,detail = failure_text(public,trace.get('reply_language') or 'zh-CN')
    return {**trace, 'status_message':intro+' '+detail, PRIVATE_KEY: {**bound, 'seal': _seal('view:', bound)}}


def verified_failure(trace):
    value = trace.get(PRIVATE_KEY)
    if trace.get('state') != 'FAILED' or trace.get('pending_confirmation') is not None \
        or not isinstance(value, dict) or set(value) != {'task_id', 'version', 'state', 'facts', 'seal'}:
        return None
    bound = {key: value[key] for key in ('task_id', 'version', 'state', 'facts')}
    try: matches = value['seal'] == _seal('view:', bound)
    except (ValueError, TypeError): return None
    if not matches or (value['task_id'], value['version'], value['state']) \
        != (trace.get('task_id'), trace.get('version'), trace.get('state')):
        return None
    return _public(value['facts'])


def failure_text(facts, language='zh-CN', *, previous_completed=False):
    english = language == 'en'
    kind = facts.get('instrument_type')
    label = ({'CREDIT': 'credit card', 'DEBIT': 'debit card'}.get(kind, 'payment card') if english else
        {'CREDIT': '信用卡', 'DEBIT': '借记卡'}.get(kind, '付款卡'))
    tail = facts.get('card_last4')
    if tail: label += (' (ending '+tail+')') if english else '（尾号'+tail+'）'
    reason = facts['reason']
    if english:
        cause = {'LOCKED': 'was locked', 'LOST': 'was reported lost',
            'INACTIVE': 'was not available for payment',
            'UNAVAILABLE': 'was unavailable (locked, reported lost or not yet active)' }[reason]
        intro = 'The payment could not be prepared.'
        detail = 'At preparation time, the selected '+label+' '+cause+'. No other card was selected automatically. Check the card status before reviewing this request again.'
        if previous_completed: detail += ' Previously completed steps remain recorded; this failure does not reverse them.'
    else:
        cause = {'LOCKED': '处于锁定状态', 'LOST': '已挂失', 'INACTIVE': '尚不可用于付款',
            'UNAVAILABLE': '不可用（可能已锁定、挂失或尚未激活）'}[reason]
        intro = '这次付款未能进入确认。'
        detail = '准备时，所选'+label+cause+'。系统不会自动换卡付款；请先处理卡片状态，再核对这次需求。'
        if previous_completed: detail += '此前已完成的步骤仍保留，这次失败不代表那些业务已撤回。'
    return intro, detail


def controlled_failure_response(trace, language='zh-CN'):
    facts = verified_failure(trace)
    if facts is None: return None
    from .intelligence import READ_KINDS
    previous = bool((trace.get('outcome') or {}).get('completed_operation_ids')) or any(
        isinstance(node, dict) and node.get('status') == 'SUCCEEDED' and node.get('action') not in READ_KINDS
        for node in trace.get('nodes', []))
    intro, detail = failure_text(facts, language, previous_completed=previous)
    return {'intro': intro, 'detail': detail, 'detail_kind': 'NONE', 'state_kind': 'FAILED',
        'read_only': False, 'reply_language': language}
