"""Confirmed repayment identifies its owned debit card, never its old authority.

The next repayment prepares a new live funding snapshot, debt amount and
confirmation. Separate projections expose the same card for a balance read.
"""
from .contracts import DomainError
from .models import Card, Task
from .credit_models import CreditRepayment
from .planning import canonical_hash
from .runtime_models import RuntimeOperation

PAY_KIND = 'COMPLETED_REPAYMENT_FUNDING_IDENTITY'
READ_KIND = 'COMPLETED_REPAYMENT_FUNDING_READ_IDENTITY'
TARGET_KIND = 'COMPLETED_REPAYMENT_TARGET_IDENTITY'


def target_identities(session, principal, thread, parent, before_sequence):
    from .semantic_credit_identity import card_identities as verified_cards
    if getattr(principal, 'family_context', None):
        return []
    return [{**proof, 'source_kind': TARGET_KIND, 'identity_only': True, 'bank_write_authorized': False,
        'label': proof['label'].split(' · ')[0] + ' · 刚才还款的信用卡'}
        for proof in verified_cards(session, principal, thread, parent, before_sequence)
        if session.get(RuntimeOperation, proof['operation_id']).action_kind == 'CREDIT_REPAY']


def _deny():
    raise DomainError('SEMANTIC_REPAYMENT_SOURCE_CHANGED', '需要重新核对还款使用的借记卡。', 409)


def card_identities(session, principal, thread, parent, before_sequence):
    from .semantic_credit_identity import card_identities as verified_cards
    from .card_identifiers import card_identifier_view
    if getattr(principal, 'family_context', None):
        return []
    values = []
    for proof in verified_cards(session, principal, thread, parent, before_sequence):
        operation = session.get(RuntimeOperation, proof['operation_id'])
        if operation.action_kind != 'CREDIT_REPAY':
            continue
        child = session.get(Task, proof['child_task_id'])
        receipt = child.result_json
        posted = session.get(CreditRepayment, receipt.get('repayment_id'))
        instrument = receipt.get('source_instrument_id', '')
        if not isinstance(instrument, str) or not instrument.startswith('debit:'):
            _deny()
        card = session.get(Card, instrument.split(':', 1)[1])
        owned = lambda row: row and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)
        if not owned(posted) or not owned(card) or {**posted.receipt_json, 'bank_state_changed': True} != receipt \
                or card.account_id != receipt.get('account_id'):
            _deny()
        number = (card_identifier_view(session, card) or {}).get('card_number')
        if not number:
            _deny()
        identity = {key: proof[key] for key in ('task_id', 'source_turn_id', 'before_sequence',
            'conversation_id', 'operation_id', 'child_task_id', 'receipt_hash')}
        identity.update(funding_card_id=card.id, funding_account_id=card.account_id,
            funding_identity_hash=canonical_hash({'card_id': card.id, 'account_id': card.account_id,
                'card_number': number, 'user_id': card.user_id, 'sandbox_id': card.sandbox_id}),
            repayment_id=posted.id, value=number, identity_only=True, bank_write_authorized=False)
        for kind, field in ((PAY_KIND, 'source_card'), (READ_KIND, 'card_number')):
            values.append({**identity, 'source_kind': kind, 'field': field,
                'label': '刚才还款使用的本人借记卡 · 尾号' + number[-4:]})
    return values


def checked_identity(session, principal, thread, entry, field, action_kind):
    if entry.get('source_kind') == TARGET_KIND:
        parent = session.get(Task, entry.get('task_id'))
        if field != 'card_reference' or action_kind not in {'CREDIT_REPAY','CREDIT_VIEW','CREDIT_STATEMENT'} \
                or parent is None or entry not in target_identities(session, principal, thread, parent, entry.get('before_sequence')):
            _deny()
        return entry['value']
    expected = {PAY_KIND: ('source_card', 'CREDIT_REPAY'), READ_KIND: ('card_number', 'CARD_STATUS')}
    if expected.get(entry.get('source_kind')) != (field, action_kind):
        _deny()
    parent = session.get(Task, entry.get('task_id'))
    if parent is None or entry not in card_identities(session, principal, thread, parent, entry.get('before_sequence')):
        _deny()
    return entry['value']
