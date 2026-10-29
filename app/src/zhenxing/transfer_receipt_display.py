"""Read-only presentation facts for the exact owned posted transfer.

Never rewrite a historical task/plan/ledger, resolve today's default card, or
infer a receiver from a username or arbitrary user/assistant message.
"""
from sqlalchemy import select

from .models import Account, Card, Confirmation, Payee, ToolCall, Transaction, Transfer, User
from .planning import canonical_hash

VERSION = 'transfer-receipt-display-v1'


def bound_confirmation_card_numbers(session, parent, plan):
    """Full numbers for the two already signed selected instruments only.

    This never selects a default; the signed scope has already selected one.
    The caller retains all normal pending-confirmation and availability gates.
    """
    from .card_identifiers import card_identifier_view
    from .transfer_payment_scope import transfer_confirmation_scope
    payment = plan.get('transfer_payment_scope')
    if payment is None:
        return {}
    display = transfer_confirmation_scope(payment)
    if display is None or (payment.get('amount_minor'), payment.get('currency'),
            (payment.get('source') or {}).get('account_id'),
            (payment.get('recipient') or {}).get('account_id')) != (
            plan.get('amount_minor'), 'CNY', plan.get('source_account_id'), plan.get('recipient_account_id')):
        return None
    numbers = {}
    for side in ('source', 'recipient'):
        snapshot = payment.get(side)
        if snapshot is None and side == 'recipient' and plan.get('transfer_channel') == 'EXTERNAL_SIM':
            continue
        if not isinstance(snapshot, dict):
            return None
        card = session.get(Card, snapshot.get('card_id'))
        account = session.get(Account, snapshot.get('account_id'))
        expected_owner = (parent.user_id, parent.sandbox_id) if side == 'source' else (
            snapshot.get('user_id'), snapshot.get('sandbox_id'))
        if card is None or account is None or card.account_id != account.id \
                or (card.user_id, card.sandbox_id) != expected_owner \
                or (account.user_id, account.sandbox_id) != expected_owner \
                or (snapshot.get('user_id'), snapshot.get('sandbox_id')) != expected_owner \
                or account.id != plan.get(side + '_account_id') or account.currency != 'CNY' \
                or snapshot.get('instrument_type') != 'DEBIT' or snapshot.get('currency') != 'CNY':
            return None
        view = card_identifier_view(session, card) or {}
        number, tail = view.get('card_number'), view.get('display_last4')
        if not isinstance(number, str) or not number or tail != snapshot.get('display_last4'):
            return None
        context = plan.get('source_card_context' if side == 'source' else 'recipient_context') or {}
        number_key = 'card_number' if side == 'source' else 'recipient_card_number'
        if context.get(number_key) and context[number_key] != number:
            return None
        numbers[side + '_card_number'] = number
    return numbers


def _receiver_card(session, transfer, settlement, plan):
    """The actual credit statement, not today's primary receiving card."""
    from .card_identifiers import card_identifier_view
    from .semantic_status_facts import _receipt_card
    rows = list(session.scalars(select(Transaction).where(
        Transaction.operation_ref == 'transfer:' + transfer.id,
        Transaction.sandbox_id == settlement.receiver_sandbox_id,
        Transaction.account_id == settlement.receiver_account_id,
        Transaction.source_kind == 'TRANSFER')))
    if len(rows) != 1 or rows[0].amount_minor != transfer.amount_minor:
        return False, None
    context = plan.get('recipient_context') or {}
    payment = plan.get('transfer_payment_scope')
    selected = (payment.get('recipient') or {}) if isinstance(payment, dict) else {}
    actual_id = rows[0].card_id
    if context.get('recipient_card_id') and context['recipient_card_id'] != actual_id \
            or selected.get('card_id') and selected['card_id'] != actual_id:
        return False, None
    if not actual_id:
        return True, None  # Old postings did not always record a card identity.
    card = session.get(Card, actual_id)
    identity = _receipt_card(session, actual_id, settlement.receiver_account_id,
        settlement.receiver_user_id, settlement.receiver_sandbox_id, settlement.currency,
        context.get('recipient_card_display_last4'), posted_at=transfer.created_at)
    if identity is None or card is None:
        return False, None
    number = (card_identifier_view(session, card) or {}).get('card_number')
    if context.get('recipient_card_number') and context['recipient_card_number'] != number:
        return False, None
    return True, number


def completed_transfer_receipt_display(session, principal, parent, operation, child):
    """Return a copy-only display supplement, or nothing when any proof fails.

    Current cash/card versions are intentionally not compared with old plan
    versions: another legitimate later transaction cannot erase a receipt.
    The existing access gate, consumed confirmation, signed plan, exact tool
    receipt, business idempotency and actual posting still have to agree.
    """
    from .conversation_provenance import current_plan_completed_write
    from .internal_banking import settlement_integrity
    from .internal_banking_models import InternalTransferSettlement
    if child is None or operation.action_kind != 'TRANSFER' or operation.status != 'SUCCEEDED' \
            or child.state != 'SUCCEEDED' or child.intent_type != 'TRANSFER' \
            or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or (child.user_id, child.sandbox_id) != (parent.user_id, parent.sandbox_id) \
            or operation.child_task_id != child.id or operation.parent_task_id != parent.id \
            or operation.sandbox_id != parent.sandbox_id:
        return None
    result = child.result_json
    if not isinstance(result, dict) or result.get('error') or not isinstance(result.get('transfer_id'), str) \
            or type(result.get('amount_minor')) is not int or result['amount_minor'] <= 0 \
            or not current_plan_completed_write(session, parent, operation):
        return None
    call = session.scalar(select(ToolCall).where(ToolCall.task_id == child.id,
        ToolCall.status == 'SUCCESS', ToolCall.idempotency_key == operation.idempotency_key,
        ToolCall.tool_name == 'transfer.commit.v1'))
    confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
        Confirmation.user_id == principal.user_id, Confirmation.sandbox_id == principal.sandbox_id,
        Confirmation.plan_hash == child.plan_hash, Confirmation.status == 'CONSUMED',
        Confirmation.consumed_at.is_not(None))))
    if call is None or call.sandbox_id != principal.sandbox_id or call.result_json != result or len(confirmations) != 1:
        return None
    confirmation = confirmations[0]
    plan = next((node for node in (child.plan_json or {}).get('nodes', [])
        if node.get('node_id') == confirmation.node_key and node.get('tool_name') == 'transfer.commit.v1'), None)
    transfer = session.get(Transfer, result['transfer_id'])
    if plan is None or transfer is None or transfer.status != 'POSTED' \
            or (transfer.user_id, transfer.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or transfer.idempotency_key != operation.idempotency_key \
            or transfer.amount_minor != result['amount_minor'] or transfer.amount_minor != plan.get('amount_minor') \
            or transfer.payee_id != plan.get('payee_id') or transfer.payee_id != confirmation.target_id \
            or transfer.source_account_id != plan.get('source_account_id') or transfer.memo != plan.get('memo', '') \
            or not settlement_integrity(session, transfer).get('balanced'):
        return None
    source = session.get(Account, transfer.source_account_id)
    payee = session.get(Payee, transfer.payee_id)
    if source is None or payee is None or source.currency != 'CNY' \
            or (source.user_id, source.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or (payee.user_id, payee.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return None
    for key in ('recipient_context', 'source_card_context'):
        if plan.get(key) is not None and canonical_hash(plan[key]) != canonical_hash(result.get(key)):
            return None
    channel = plan.get('transfer_channel', 'EXTERNAL_SIM')
    if result.get('transfer_channel') != channel:
        return None
    settlement = session.get(InternalTransferSettlement, transfer.id)
    name, number = None, None
    if channel == 'INTERNAL_BANK':
        if settlement is None or (settlement.sender_user_id, settlement.sender_sandbox_id,
                settlement.sender_account_id, settlement.amount_minor, settlement.currency) != (
                principal.user_id, principal.sandbox_id, source.id, transfer.amount_minor, 'CNY') \
                or settlement.receiver_account_id != plan.get('recipient_account_id') \
                or settlement.receiver_username != plan.get('recipient_username') \
                or result.get('recipient_account_id') != settlement.receiver_account_id \
                or result.get('recipient_username') != settlement.receiver_username:
            return None
        context = plan.get('recipient_context') or {}
        if context and (context.get('recipient_bank_account_id'), context.get('recipient_username')) != (
                settlement.receiver_account_id, settlement.receiver_username):
            return None
        receiver = session.get(Account, settlement.receiver_account_id)
        person = session.get(User, settlement.receiver_user_id)
        if receiver is None or person is None or (receiver.user_id, receiver.sandbox_id) != (
                settlement.receiver_user_id, settlement.receiver_sandbox_id) \
                or person.sandbox_id != settlement.receiver_sandbox_id:
            return None
        if context.get('recipient_login_account_id'):
            from .access_models import LoginAccount
            login = session.get(LoginAccount, context['recipient_login_account_id'])
            from .login_name_history import recorded_login_name_matches
            # A login name is mutable display data, not the bank identity. The
            # posted receipt keeps its original name, while the immutable login,
            # person and account bindings must still match after a rename.
            if login is None or (login.user_id, login.sandbox_id) != (
                    person.id, person.sandbox_id) or not recorded_login_name_matches(session,login,settlement.receiver_username):
                return None
        payment = plan.get('transfer_payment_scope')
        if payment is not None:
            from .transfer_payment_scope import transfer_confirmation_scope
            display = transfer_confirmation_scope(payment)
            if display is None or (payment.get('amount_minor'), payment.get('currency'),
                    (payment.get('source') or {}).get('account_id'),
                    (payment.get('recipient') or {}).get('account_id')) != (
                    transfer.amount_minor, 'CNY', source.id, receiver.id) \
                    or result.get('payment_confirmation') != display:
                return None
        valid, number = _receiver_card(session, transfer, settlement, plan)
        if not valid:
            return None
        name = person.display_name if isinstance(person.display_name, str) and person.display_name.strip() else None
    elif channel == 'EXTERNAL_SIM' and settlement is None:
        name = payee.display_name
        # The external record has only a suffix, not a full bank-card number.
    else:
        return None
    return {'recipient_display_name': name, 'recipient_card_number': number, 'memo': transfer.memo,
        'status': 'POSTED', 'transfer_receipt_display': {'version': VERSION,
            'authority': 'OWNED_POSTED_TRANSFER_PRESENTATION_ONLY', 'bank_state_changed': False}}
