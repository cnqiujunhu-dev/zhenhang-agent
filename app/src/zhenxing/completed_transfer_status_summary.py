"""Concise STATUS presentation from selected, verified posted transfer facts.

This module never interprets a user's phrasing, fills payment slots, confirms
an operation or changes a bank record. Missing proof is not a success claim.
"""
from decimal import Decimal
import re

from sqlalchemy import select

from .contracts import DomainError
from .intelligence import READ_KINDS
from .planning import canonical_hash
from .redaction import redact_credentials

VERSION = 'verified-transfer-status-summary-v1'
TRANSFER_KINDS = frozenset({'TRANSFER', 'FX_TRANSFER','FX_EXCHANGE'})
MAX_RECEIPTS = 8


def _card(value, currency):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'instrument_type', 'display_last4', 'currency'} \
            or value.get('instrument_type') != 'DEBIT' or value.get('currency') != currency \
            or not isinstance(value.get('display_last4'), str) \
            or not re.fullmatch(r'[0-9a-fA-F]{4}', value['display_last4']):
        raise ValueError('UNVERIFIED_MASKED_INSTRUMENT')
    return value['display_last4']


def _checked_fact(fact):
    if not isinstance(fact, dict) or fact.get('kind') not in TRANSFER_KINDS \
            or fact.get('receipt_hash') != canonical_hash({key: value for key, value in fact.items() if key != 'receipt_hash'}):
        raise ValueError('UNVERIFIED_TRANSFER_RECEIPT')
    name = fact.get('recipient')
    currency = fact.get('currency')
    source_currency = fact.get('source_currency', currency)
    debit = fact.get('debit_minor')
    credit = fact.get('credit_minor')
    verified_credit = fact.get('recipient_credit_verified')
    source_id = fact.get('source_id')
    if not isinstance(name, str) or not 1 <= len(name) <= 100 or re.search(r'[\x00-\x1f<>]', name) \
            or redact_credentials(name) != name or currency not in {'CNY', 'USD'} \
            or source_currency not in {'CNY', 'USD'} or type(debit) is not int or not 0 < debit <= 2**53-1 \
            or type(verified_credit) is not bool or not isinstance(source_id, str) \
            or not re.fullmatch(r'bank-receipt:[A-Za-z0-9-]{1,100}', source_id):
        raise ValueError('UNVERIFIED_TRANSFER_RECEIPT')
    if verified_credit and (type(credit) is not int or not 0 < credit <= 2**53-1):
        raise ValueError('UNVERIFIED_RECIPIENT_POSTING')
    source_tail = _card(fact.get('source_card'), source_currency)
    target_tail = _card(fact.get('recipient_card'), currency)
    if target_tail is not None and not verified_credit:
        raise ValueError('UNVERIFIED_RECIPIENT_POSTING')
    return name, source_currency, currency, debit, credit, verified_credit, source_tail, target_tail


def render_completed_transfer_summary(facts, language='zh-CN'):
    """All-or-nothing masked receipt text; no default card or requested amount."""
    if not isinstance(facts, list) or not 1 <= len(facts) <= MAX_RECEIPTS:
        return None
    english = language == 'en'
    lines = []
    source_ids = set()
    for fact in facts:
        try:
            name, source_currency, currency, debit, credit, verified_credit, source_tail, target_tail = _checked_fact(fact)
        except (ValueError, TypeError, KeyError):
            return None
        if fact['source_id'] in source_ids:
            return None
        source_ids.add(fact['source_id'])
        debit_text = f'{Decimal(debit)/100:.2f}'
        target_text = f'{Decimal(credit)/100:.2f}' if verified_credit else None
        if fact['kind']=='FX_EXCHANGE':
            if not verified_credit:return None
            lines.append((f'Exchange completed: your card ending {source_tail or "—"} exchanged {debit_text} {source_currency} for {target_text} {currency}.' if english else
                f'换汇已完成：本人尾号{source_tail or "—"}卡换出{debit_text}{"美元" if source_currency=="USD" else "元人民币"}，换入{target_text}{"美元" if currency=="USD" else "元人民币"}。'))
            continue
        if english:
            source = f'Your debit card ending {source_tail}' if source_tail else 'The source account (its card is not recorded in the original receipt)'
            target = f'{name} (receiving card ending {target_tail})' if target_tail else name
            if verified_credit:
                lines.append(f'Transfer completed: {source} was debited {debit_text} {source_currency}; '
                    f'{target} was credited {target_text} {currency}.')
            else:
                lines.append(f'{source} was debited {debit_text} {source_currency} for {target}. '
                    'The outward clearing entry is posted; receipt by the external recipient is not verified.')
        else:
            source = f'从本人尾号{source_tail}借记卡' if source_tail else '原回执未记录付款卡，已'
            target = f'{name}（收款卡尾号{target_tail}）' if target_tail else name
            debit_unit = '元人民币' if source_currency == 'CNY' else '美元'
            target_unit = '元人民币' if currency == 'CNY' else '美元'
            if verified_credit:
                lines.append(f'转账已完成：{source}转出{debit_text}{debit_unit}，'
                    f'收款人{target}已入账{target_text}{target_unit}。')
            else:
                lines.append(f'{source}向{target}转出{debit_text}{debit_unit}，外部清算已记账；对方到账状态尚未核验。')
    return '\n'.join(lines)


def _unverified_summary(language):
    return {'version': VERSION, 'verified': False, 'receipts': [],
        'answer': ('The original transfer receipt is not fully verified. Check the original record; do not repeat the payment.'
            if language == 'en' else '这笔转账的原回执还需要核验，请先查看原记录，不要重复付款。')}


def selected_completed_transfer_consultation(session, principal, thread, turn, decision, offered):
    """Consume only the model-selected receipt summary/status consultation.

    This is deliberately separate from balance or card-status queries. It does
    not rewrite intent, recover an unselected task, or create a banking action.
    """
    from .semantic_transfer_identity import KIND, RECEIPT_KIND, RECEIPT_VERSION, checked_receipt
    if decision.mode != 'CONSULT' or not decision.entity_refs:
        return None
    aspects = set(decision.question_aspects)
    if not aspects or not aspects <= {'SUMMARY', 'STATUS'} or decision.rule_aspects \
            or decision.retrieval_query is not None:
        return None
    refs = list(decision.entity_refs)
    if len(refs) != len(set(refs)) or len(refs) > MAX_RECEIPTS * 2:
        return None
    entries = [offered.get('entity_bindings', {}).get(ref) for ref in refs]
    if any(not isinstance(entry, dict) or entry.get('source_kind') not in {KIND, RECEIPT_KIND} for entry in entries):
        return None
    facts, selected = {}, []
    for ref, entry in zip(refs, entries):
        frame_ref = next((key for key, binding in offered['frame_bindings'].items()
            if binding.get('task_id') == entry['task_id']), None)
        if frame_ref is None or decision.focus_ref is not None and decision.focus_ref != frame_ref:
            raise DomainError('SEMANTIC_TRANSFER_RECEIPT_FOCUS_CHANGED', '这笔转账的回执对象不一致，请重新核对。', 409)
        fact = checked_receipt(session, principal, thread, entry)
        prior = facts.get(fact['source_id'])
        if prior is not None and prior != fact:
            raise DomainError('SEMANTIC_TRANSFER_IDENTITY_SOURCE_CHANGED', '转账回执发生变化，请重新核对。', 409)
        facts[fact['source_id']] = fact
        selected.append({'entity_ref': ref, 'task_id': entry['task_id'], 'task_version': entry['task_version'],
            'source_id': fact['source_id'], 'receipt_hash': fact['receipt_hash']})
    answer = render_completed_transfer_summary(list(facts.values()), (turn.diagnostics_json or {}).get('reply_language', 'zh-CN'))
    if answer is None:
        raise DomainError('SEMANTIC_TRANSFER_RECEIPT_UNVERIFIED', '这笔转账的原回执暂时不能完整核验。', 409)
    return {'answer': answer, 'version': RECEIPT_VERSION, 'verified': True,
        'selected': selected, 'bank_write_authorized': False}


def _unexecuted_clarification_tombstone(session, principal, parent, record, operation):
    """Ignore only a source-proven old clarification that never had a grant.

    Inactive completed/authorized/unknown writes remain material. No result
    fields are stripped and exact original ToolCall equality is unchanged.
    """
    from .models import Task, ToolCall, Confirmation, Transfer
    from .runtime_models import RuntimeRecord, PlanSnapshot
    from .intelligence import action_signature
    if operation.action_kind != 'TRANSFER' or operation.active != 0 or operation.status != 'CANCELLED' \
            or operation.parent_task_id != parent.id or operation.sandbox_id != principal.sandbox_id \
            or not 0 < operation.plan_version < record.plan_version:
        return False
    child=session.get(Task,operation.child_task_id) if operation.child_task_id else None
    child_record=session.get(RuntimeRecord,operation.child_task_id) if child else None
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==operation.plan_version))
    if child is None or child.state!='NEED_CLARIFICATION' or child.intent_type!='TRANSFER' \
            or (child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
            or child_record is None or child_record.kind!='CHILD' or child_record.root_task_id!=parent.id \
            or child_record.metadata_json.get('operation_id')!=operation.id or snapshot is None \
            or snapshot.plan_hash!=canonical_hash(snapshot.plan_json):
        return False
    nodes=[node for node in (snapshot.plan_json or {}).get('nodes',[]) if isinstance(node,dict)
        and node.get('operation_id')==operation.id]
    if len(nodes)!=1 or nodes[0].get('node_id')!=operation.operation_key or nodes[0].get('kind')!='TRANSFER' \
            or canonical_hash(nodes[0].get('args'))!=canonical_hash(operation.args_json) \
            or operation.args_json.get('kind')!='TRANSFER' \
            or canonical_hash(action_signature(operation.args_json))!=operation.fingerprint:
        return False
    return session.scalar(select(ToolCall.id).where(ToolCall.task_id==child.id).limit(1)) is None \
        and session.scalar(select(Confirmation.id).where(Confirmation.task_id==child.id).limit(1)) is None \
        and session.scalar(select(Transfer.id).where(Transfer.sandbox_id==principal.sandbox_id,
            Transfer.idempotency_key==operation.idempotency_key).limit(1)) is None


def completed_transfer_scope_summary(session, principal, tasks, contexts, language='zh-CN'):
    """Use only the already-selected owned scope; never widen it to other tasks.

    Mixed write scopes keep the existing general boundary answer. A pure
    transfer scope is enriched only when every current and historical write is
    accounted for by the existing bank/ledger/card provenance verifier.
    """
    from .runtime_models import RuntimeOperation, RuntimeRecord
    from .semantic_status_facts import completed_transfer_facts
    from .task_dialogue_context import VERSION as CONTEXT_VERSION
    if not isinstance(tasks, list) or not isinstance(contexts, list) \
            or not 1 <= len(tasks) == len(contexts) <= MAX_RECEIPTS:
        return None
    if len({task.id for task in tasks}) != len(tasks):
        return None
    any_transfer = False
    for task, context in zip(tasks, contexts):
        if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                or not isinstance(context, dict) or context.get('version') != CONTEXT_VERSION \
                or context.get('authority') != 'OWNED_RUNTIME_FACTS_NOT_USER_AUTHORIZATION' \
                or context.get('task_id') != task.id or type(context.get('task_version')) is not int \
                or context['task_version'] != task.version or task.version < 1 \
                or context.get('state') != 'SUCCEEDED' or task.state != 'SUCCEEDED' \
                or context.get('possible_write_effect') is not False or context.get('verified') is not True:
            return None
        kinds = context.get('action_kinds')
        if not isinstance(kinds, list) or not kinds or any(not isinstance(kind, str) for kind in kinds):
            return None
        writes = [kind for kind in kinds if kind not in READ_KINDS]
        if any(kind not in TRANSFER_KINDS for kind in writes):
            return None
        if writes:
            if context.get('completed_write_present') is not True or context.get('outcome_kind') != 'BUSINESS_RECEIPT':
                return None
            any_transfer = True
        elif context.get('completed_write_present') is not False or context.get('outcome_kind') != 'QUERY_RESULT':
            return None
    if not any_transfer:
        return None
    facts = []
    for task, context in zip(tasks, contexts):
        record = session.get(RuntimeRecord, task.id)
        operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
        writes = [operation for operation in operations if operation.action_kind not in READ_KINDS]
        if record is not None and record.kind == 'PARENT':
            writes = [operation for operation in writes if not _unexecuted_clarification_tombstone(
                session, principal, task, record, operation)]
        if record is None or record.kind != 'PARENT' or any(operation.sandbox_id != principal.sandbox_id
                or operation.action_kind not in TRANSFER_KINDS or operation.status != 'SUCCEEDED'
                or operation.active != 1 or operation.plan_version != record.plan_version for operation in writes):
            return _unverified_summary(language)
        if not writes:
            continue
        try:
            task_facts = completed_transfer_facts(session, principal, task)
        except (DomainError, ValueError, TypeError, KeyError):
            return _unverified_summary(language)
        if not isinstance(task_facts, list) or len(task_facts) != len(writes):
            return _unverified_summary(language)
        facts.extend(task_facts)
    answer = render_completed_transfer_summary(facts, language)
    if answer is None:
        return _unverified_summary(language)
    return {'version': VERSION, 'verified': True, 'answer': answer,
        'receipts': [{'kind': fact['kind'], 'source_id': fact['source_id'], 'receipt_hash': fact['receipt_hash']}
            for fact in facts]}
