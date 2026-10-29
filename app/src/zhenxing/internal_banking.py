"""Account-to-account simulation settlement; authorization stays in ToolGateway.

The caller owns the transaction, step-up, confirmation, facts/policy validation,
audit and outbox. This module resolves identities and conserves both bank ledgers;
it cannot accept arbitrary client database IDs as a transfer destination.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select

from .access_models import LoginAccount
from .contracts import DomainError
from .internal_banking_models import InternalPayeeBinding, InternalTransferSettlement
from .models import Account, Card, LedgerEntry, Payee, Transaction, Transfer
from .posting import record_cash_transaction


INTERNAL_CHANNEL = "INTERNAL_BANK"
EXTERNAL_CHANNEL = "EXTERNAL_SIM"
NAMED_BANK_ROLES = {"CUSTOMER", "DEV_TEST"}
TRANSFER_LIMIT_MINOR = 5_000_000
ACCOUNT_BALANCE_LIMIT_MINOR = 2_147_483_647


def account_reference(account_id: str) -> str:
    return "SIM-" + account_id.replace("-", "")[-4:].upper()


def _login(session, sandbox_id, user_id, lock=False):
    query = select(LoginAccount).where(LoginAccount.sandbox_id == sandbox_id, LoginAccount.user_id == user_id)
    return session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def _require_named_actor(session, sandbox_id, user_id, lock=False):
    row = _login(session, sandbox_id, user_id, lock)
    if row is None or row.status != "ACTIVE" or row.role not in NAMED_BANK_ROLES:
        raise DomainError("NAMED_BANK_ACCOUNT_REQUIRED", "银行互转需要本人有效业务账号，管理员或协助人不能收发", 403)
    return row


def _bank_account(session, login):
    rows = list(session.scalars(select(Account).where(Account.sandbox_id == login.sandbox_id, Account.user_id == login.user_id)))
    if len(rows) != 1:
        from .payment_instruments import resolve_recipient_instrument
        snapshot = resolve_recipient_instrument(session, login.username)
        bank = session.get(Account, snapshot.get("account_id"))
        if bank and (bank.user_id, bank.sandbox_id, bank.currency) == (login.user_id, login.sandbox_id, "CNY"):
            return bank
    if len(rows) != 1 or rows[0].currency != "CNY":
        raise DomainError("RECIPIENT_ACCOUNT_UNAVAILABLE", "收款账号没有唯一可用银行账户", 409)
    return rows[0]


def _bound_entities(session, payee):
    binding = session.get(InternalPayeeBinding, payee.id)
    if binding is None:
        return None, None, None
    login = session.get(LoginAccount, binding.recipient_login_account_id)
    bank = session.get(Account, binding.recipient_bank_account_id)
    if login is None or bank is None or binding.username_snapshot != login.username \
            or bank.user_id != login.user_id or bank.sandbox_id != login.sandbox_id \
            or payee.display_name != binding.username_snapshot or payee.account_last4 != account_reference(bank.id)[-4:]:
        raise DomainError("RECIPIENT_BINDING_CHANGED", "实名收款绑定事实不一致，不能改为外部出账", 409)
    return binding, login, bank


def list_internal_recipients(session, sandbox_id: str, user_id: str, query: str | None = None, limit: int = 30) -> list[dict]:
    _require_named_actor(session, sandbox_id, user_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise DomainError("INVALID_DIRECTORY_LIMIT", "收款目录查询数量须为1至100", 422)
    rows = select(LoginAccount).where(LoginAccount.status == "ACTIVE", LoginAccount.role.in_(NAMED_BANK_ROLES),
                                      LoginAccount.user_id != user_id)
    if query:
        if not isinstance(query, str) or len(query) > 32:
            raise DomainError("INVALID_RECIPIENT_QUERY", "收款查询应为账号名称", 422)
        # No caller-supplied wildcard can turn a typed prefix into a broad query.
        rows = rows.where(LoginAccount.username.startswith(query.strip().lower(), autoescape=True))
    result = []
    for login in session.scalars(rows.order_by(LoginAccount.username).limit(limit)):
        bank = _bank_account(session, login)
        result.append({"username": login.username, "account_reference": account_reference(bank.id),
                       "transfer_channel": INTERNAL_CHANNEL})
    return result


def resolve_transfer_payees(session, sandbox_id: str, user_id: str, name: str) -> list[Payee]:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise DomainError("INVALID_RECIPIENT_NAME", "请明确收款账号或已有外部联系人", 422)
    name = name.strip()
    selected_bank_id = None
    # Confirmed personal roles and exact simulated card identifiers refer to a
    # bank account, not to a mutable global display name or external clearing.
    from .contacts import find_alias, _alias_target, registered_reference
    role_alias = find_alias(session, sandbox_id, user_id, name)
    if role_alias:
        target_login, _target_bank, _target_card = _alias_target(session, role_alias)
        selected_bank_id = _target_bank.id
        name = target_login.username
    elif name.lower().startswith(("zhenhang", "xiaozhao")):
        target_login, _target_bank, _target_card = registered_reference(session, name)
        selected_bank_id = _target_bank.id
        name = target_login.username
    username = name.lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username):
        return list(session.scalars(select(Payee).where(Payee.sandbox_id == sandbox_id, Payee.user_id == user_id,
            Payee.display_name == name, Payee.status == "ACTIVE")))
    receiver = session.scalar(select(LoginAccount).where(LoginAccount.username == username))
    if receiver is None:
        # A known external contact may legitimately have an ASCII name. Its
        # spelling alone must not silently migrate it to the internal channel,
        # nor may an unknown username invent an external clearing destination.
        legacy = list(session.scalars(select(Payee).where(Payee.sandbox_id == sandbox_id,
            Payee.user_id == user_id, Payee.display_name == name, Payee.status == "ACTIVE")))
        if legacy and all(session.get(InternalPayeeBinding, payee.id) is None for payee in legacy):
            return legacy
    if receiver is None or receiver.status != "ACTIVE" or receiver.role not in NAMED_BANK_ROLES:
        raise DomainError("RECIPIENT_ACCOUNT_UNAVAILABLE", "收款账号不存在、停用或不是可收款业务账号", 409)
    sender = _require_named_actor(session, sandbox_id, user_id)
    if receiver.user_id == sender.user_id:
        raise DomainError("SELF_TRANSFER_FORBIDDEN", "不能向本人同一银行账户转账", 409)
    # Carrier creation also acquires FK key-share locks on the recipient. Never
    # pre-lock only the sender: two inverse preparations could deadlock while
    # each inserts its binding to the other's already locked registry row.
    locked = {row.id: row for row in session.scalars(select(LoginAccount).where(LoginAccount.id.in_({sender.id, receiver.id}))
        .order_by(LoginAccount.id).with_for_update().execution_options(populate_existing=True))}
    sender, receiver = locked[sender.id], locked[receiver.id]
    if sender.status != "ACTIVE" or sender.role not in NAMED_BANK_ROLES:
        raise DomainError("NAMED_BANK_ACCOUNT_REQUIRED", "付款账号当前不能登记银行收款对象", 403)
    if receiver.status != "ACTIVE" or receiver.role not in NAMED_BANK_ROLES:
        raise DomainError("RECIPIENT_ACCOUNT_UNAVAILABLE", "收款账号当前不可收款", 409)
    bank = session.get(Account, selected_bank_id) if selected_bank_id else _bank_account(session, receiver)
    if bank is None or (bank.user_id, bank.sandbox_id, bank.currency) != (receiver.user_id, receiver.sandbox_id, "CNY"):
        raise DomainError("RECIPIENT_BINDING_CHANGED", "收款账户不属于明确的收款账号", 409)
    matches = list(session.scalars(select(Payee).where(Payee.sandbox_id == sandbox_id, Payee.user_id == user_id,
                                                       func.lower(Payee.display_name) == username)))
    # New cards may use independent accounts. Preserve each original immutable
    # binding, and choose the current explicit/default account rather than
    # rewriting history or treating the username as an ambiguous external name.
    for existing in matches:
        binding, login, bound_bank = _bound_entities(session, existing)
        if binding is None or login.id != receiver.id:
            raise DomainError("RECIPIENT_NAME_CONFLICT", "该名称已有不同渠道收款对象，不能改绑历史记录", 409)
    matches = [existing for existing in matches if session.get(InternalPayeeBinding, existing.id).recipient_bank_account_id == bank.id]
    if len(matches) > 1:
        raise DomainError("RECIPIENT_NAME_CONFLICT", "该实名收款账户存在重复绑定，请先核实", 409)
    if matches:
        payee = matches[0]
        binding, login, bound_bank = _bound_entities(session, payee)
        if binding is None:
            raise DomainError("RECIPIENT_NAME_CONFLICT", "该名称已有外部联系人，不能悄悄改为银行互转", 409)
        if payee.status != "ACTIVE" or login.id != receiver.id or bound_bank.id != bank.id:
            raise DomainError("RECIPIENT_BINDING_CHANGED", "已登记实名收款对象发生变化", 409)
        return [payee]
    payee = Payee(sandbox_id=sandbox_id, user_id=user_id, display_name=receiver.username,
                  account_last4=account_reference(bank.id)[-4:])
    session.add(payee)
    session.flush()
    session.add(InternalPayeeBinding(payee_id=payee.id, recipient_login_account_id=receiver.id,
                                   recipient_bank_account_id=bank.id, username_snapshot=receiver.username))
    session.flush()
    return [payee]


def recipient_scope(session, payee: Payee) -> dict:
    binding, login, bank = _bound_entities(session, payee)
    if binding:
        return {"transfer_channel": INTERNAL_CHANNEL, "recipient_username": login.username, "recipient_account_id": bank.id}
    return {"transfer_channel": EXTERNAL_CHANNEL, "recipient_username": None, "recipient_account_id": None}


def _validate_expected_scope(session, payee, expected):
    current = recipient_scope(session, payee)
    if not isinstance(expected, dict) or set(expected) != set(current) or current != expected:
        raise DomainError("RECIPIENT_SCOPE_CHANGED", "确认的收款渠道或实名账户范围发生变化", 409)
    return current


def lock_transfer_accounts(session, sandbox_id: str, user_id: str, source_id: str, payee: Payee) -> Account:
    if payee.sandbox_id != sandbox_id or payee.user_id != user_id:
        raise DomainError("TRANSFER_TARGET_CHANGED", "收款登记不属于付款主体", 409)
    binding, receiver_login, receiver_bank = _bound_entities(session, payee)
    sender_login = _login(session, sandbox_id, user_id)
    login_ids = {row.id for row in (sender_login, receiver_login) if row is not None}
    locked_logins = {row.id: row for row in session.scalars(select(LoginAccount).where(LoginAccount.id.in_(login_ids))
        .order_by(LoginAccount.id).with_for_update().execution_options(populate_existing=True))}
    if sender_login:
        sender_login = locked_logins[sender_login.id]
        if sender_login.status != "ACTIVE" or sender_login.role not in NAMED_BANK_ROLES:
            raise DomainError("NAMED_BANK_ACCOUNT_REQUIRED", "付款账号当前不能执行银行转账", 403)
    if binding:
        if sender_login is None:
            raise DomainError("NAMED_BANK_ACCOUNT_REQUIRED", "内部互转需要实名业务账号", 403)
        receiver_login = locked_logins[receiver_login.id]
        if receiver_login.status != "ACTIVE" or receiver_login.role not in NAMED_BANK_ROLES:
            raise DomainError("RECIPIENT_ACCOUNT_UNAVAILABLE", "实名收款账号当前不可收款", 409)
        if receiver_login.user_id == user_id:
            raise DomainError("SELF_TRANSFER_FORBIDDEN", "不能向本人账户转账", 409)
    from .banking_locks import lock_financial_users
    lock_financial_users(session, {(sandbox_id, user_id)} | (
        {(receiver_login.sandbox_id, receiver_login.user_id)} if receiver_bank else set()))
    account_ids = {source_id, receiver_bank.id} if receiver_bank else {source_id}
    banks = {row.id: row for row in session.scalars(select(Account).where(Account.id.in_(account_ids)).order_by(Account.id)
        .with_for_update().execution_options(populate_existing=True))}
    source = banks.get(source_id)
    if source is None or source.sandbox_id != sandbox_id or source.user_id != user_id or payee.status != "ACTIVE":
        raise DomainError("TRANSFER_TARGET_CHANGED", "付款账户或收款人状态发生变化", 409)
    if receiver_bank:
        receiver = banks.get(receiver_bank.id)
        if receiver is None or receiver.user_id != receiver_login.user_id or receiver.sandbox_id != receiver_login.sandbox_id \
                or source.id == receiver.id or source.currency != receiver.currency or source.currency != "CNY":
            raise DomainError("RECIPIENT_BINDING_CHANGED", "收付款账户绑定或币种发生变化", 409)
    return source


def settlement_integrity(session, transfer: Transfer) -> dict:
    """Observe original posting evidence, independent of later balances/statuses."""
    session.flush()
    settlement = session.get(InternalTransferSettlement, transfer.id)
    binding = session.get(InternalPayeeBinding, transfer.payee_id)
    errors = []
    payee = session.get(Payee, transfer.payee_id)
    if transfer.status != "POSTED" or payee is None or (payee.sandbox_id, payee.user_id) != (transfer.sandbox_id, transfer.user_id):
        errors.append("TRANSFER_PAYER_SCOPE_MISMATCH")
    if binding is not None and settlement is None:
        errors.append("INTERNAL_SETTLEMENT_MISSING")
    if settlement:
        channel = INTERNAL_CHANNEL
        if (settlement.sender_sandbox_id, settlement.sender_user_id, settlement.sender_account_id, settlement.amount_minor) \
                != (transfer.sandbox_id, transfer.user_id, transfer.source_account_id, transfer.amount_minor):
            errors.append("SETTLEMENT_TRANSFER_MISMATCH")
        sender = session.get(Account, settlement.sender_account_id)
        receiver = session.get(Account, settlement.receiver_account_id)
        if sender is None or receiver is None or (sender.sandbox_id, sender.user_id) != (settlement.sender_sandbox_id, settlement.sender_user_id) \
                or (receiver.sandbox_id, receiver.user_id) != (settlement.receiver_sandbox_id, settlement.receiver_user_id) \
                or sender.id == receiver.id or sender.currency != settlement.currency or receiver.currency != settlement.currency:
            errors.append("SETTLEMENT_ACCOUNT_SCOPE_MISMATCH")
        if binding is None or binding.recipient_bank_account_id != settlement.receiver_account_id \
                or binding.username_snapshot != settlement.receiver_username:
            errors.append("SETTLEMENT_BINDING_MISMATCH")
        sender_login = _login(session, settlement.sender_sandbox_id, settlement.sender_user_id)
        receiver_login = _login(session, settlement.receiver_sandbox_id, settlement.receiver_user_id)
        from .login_name_history import recorded_login_name_matches
        if not recorded_login_name_matches(session,sender_login,settlement.sender_username) \
                or not recorded_login_name_matches(session,receiver_login,settlement.receiver_username) or binding is None \
                or binding.recipient_login_account_id != receiver_login.id or payee is None \
                or payee.display_name != settlement.receiver_username:
            errors.append("SETTLEMENT_IDENTITY_SCOPE_MISMATCH")
        clearing = "INTERNAL_CLEARING:" + transfer.id
        expected_ledger = sorted([(settlement.sender_sandbox_id, settlement.sender_account_id, -transfer.amount_minor, settlement.currency),
            (settlement.sender_sandbox_id, clearing, transfer.amount_minor, settlement.currency),
            (settlement.receiver_sandbox_id, clearing, -transfer.amount_minor, settlement.currency),
            (settlement.receiver_sandbox_id, settlement.receiver_account_id, transfer.amount_minor, settlement.currency)])
        expected_statements = sorted([(settlement.sender_sandbox_id, settlement.sender_account_id, -transfer.amount_minor),
                                     (settlement.receiver_sandbox_id, settlement.receiver_account_id, transfer.amount_minor)])
    else:
        channel = EXTERNAL_CHANNEL
        expected_ledger = sorted([(transfer.sandbox_id, transfer.source_account_id, -transfer.amount_minor, "CNY"),
                                 (transfer.sandbox_id, "CLEARING:" + transfer.payee_id, transfer.amount_minor, "CNY")])
        expected_statements = [(transfer.sandbox_id, transfer.source_account_id, -transfer.amount_minor)]
    ledger = list(session.scalars(select(LedgerEntry).where(LedgerEntry.transfer_id == transfer.id)))
    observed = sorted((row.sandbox_id, row.account_ref, row.delta_minor, row.currency) for row in ledger)
    if observed != expected_ledger:
        errors.append("TRANSFER_LEDGER_SHAPE_MISMATCH")
    statement = list(session.scalars(select(Transaction).where(Transaction.operation_ref == "transfer:" + transfer.id)))
    observed_statements = sorted((row.sandbox_id, row.account_id, row.amount_minor) for row in statement)
    if observed_statements != expected_statements or any(row.source_kind != "TRANSFER" for row in statement):
        errors.append("TRANSFER_STATEMENT_SHAPE_MISMATCH")
    return {"transfer_id": transfer.id, "transfer_channel": channel, "balanced": not errors, "errors": errors,
            "ledger_entry_count": len(ledger), "statement_count": len(statement),
            "ledger_net_minor": sum(row.delta_minor for row in ledger),
            "clearing_net_minor": sum(row.delta_minor for row in ledger if row.account_ref.startswith("INTERNAL_CLEARING:"))}


def _existing_transfer(session, source, payee, amount, memo, key, expected, source_card_id=None, recipient_card_id=None):
    transfer = session.scalar(select(Transfer).where(Transfer.sandbox_id == source.sandbox_id, Transfer.idempotency_key == key))
    if transfer is None:
        return None
    if (transfer.user_id, transfer.source_account_id, transfer.payee_id, transfer.amount_minor, transfer.memo) \
            != (source.user_id, source.id, payee.id, amount, memo):
        raise DomainError("TRANSFER_IDEMPOTENCY_CONFLICT", "原业务键已绑定不同转账范围", 409)
    original_scope = _validate_expected_scope(session, payee, expected)
    if original_scope["transfer_channel"] == EXTERNAL_CHANNEL and recipient_card_id is not None:
        raise DomainError("TRANSFER_IDEMPOTENCY_CONFLICT", "原外部转账没有本地收款卡，不能为重放附加卡片归属", 409)
    if not settlement_integrity(session, transfer)["balanced"]:
        raise DomainError("TRANSFER_SETTLEMENT_INCOMPLETE", "原转账入账证据不完整，只能查原单不能再次执行", 409)
    statements = list(session.scalars(select(Transaction).where(Transaction.operation_ref == "transfer:" + transfer.id)))
    for row in statements:
        expected_card = source_card_id if (row.sandbox_id, row.account_id) == (source.sandbox_id, source.id) else recipient_card_id
        if row.card_id != expected_card:
            # Legacy null stays null. A replay must not attach a newly inferred
            # card, erase a recorded card or switch to another same-account card.
            raise DomainError("TRANSFER_IDEMPOTENCY_CONFLICT", "原转账流水的卡片归属与本次范围不一致，只能核对原单", 409)
    return transfer


def _validate_posting_cards(session, source, receiver, amount_minor, source_card_id, recipient_card_id):
    for card_id, account, funding in ((source_card_id, source, True), (recipient_card_id, receiver, False)):
        if card_id is None:
            continue  # No selected/verified card in a legacy scope: never infer.
        if not isinstance(card_id, str) or not card_id or account is None:
            raise DomainError("TRANSFER_STATEMENT_CARD_SCOPE_CHANGED", "流水卡片不在已确认收付款范围内", 409)
        card = session.scalar(select(Card).where(Card.id == card_id).with_for_update().execution_options(populate_existing=True))
        if card is None or (card.sandbox_id, card.user_id, card.account_id) != (account.sandbox_id, account.user_id, account.id) \
                or card.status != "ACTIVE" or funding and amount_minor > card.limit_minor:
            raise DomainError("TRANSFER_STATEMENT_CARD_SCOPE_CHANGED", "收付款卡与已确认账户或当前卡控不一致", 409)


def _commit_transfer_money_impl(session, source: Account, payee: Payee, amount_minor: int, memo: str,
                               original_key: str, expected_recipient_scope: dict, *,
                               source_card_id: str | None = None, recipient_card_id: str | None = None) -> Transfer:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or not 1 <= amount_minor <= TRANSFER_LIMIT_MINOR:
        raise DomainError("INVALID_AMOUNT", "转账金额不在允许范围", 409)
    if not isinstance(memo, str) or len(memo) > 160 or not isinstance(original_key, str) or not 1 <= len(original_key) <= 120:
        raise DomainError("INVALID_TRANSFER_SCOPE", "转账备注或原业务键范围无效", 422)
    existing = _existing_transfer(session, source, payee, amount_minor, memo, original_key, expected_recipient_scope,
        source_card_id, recipient_card_id)
    if existing:
        return existing
    source = lock_transfer_accounts(session, source.sandbox_id, source.user_id, source.id, payee)
    # A concurrent original-key request may have committed while we awaited rows.
    existing = _existing_transfer(session, source, payee, amount_minor, memo, original_key, expected_recipient_scope,
        source_card_id, recipient_card_id)
    if existing:
        return existing
    from .aa_participant_payments import validate_aa_bank_posting
    aa_payment = validate_aa_bank_posting(session, source, payee, amount_minor, original_key)
    from .transfer_risk import check_transfer_limits
    checked_limits = check_transfer_limits(session, source, payee, amount_minor, for_commit=True)
    scope = _validate_expected_scope(session, payee, expected_recipient_scope)
    from .payment_instruments import cash_reserved_minor
    from .services import Principal
    if source.currency != "CNY" or source.balance_minor - cash_reserved_minor(session,
            Principal(source.user_id, source.sandbox_id), source.id) < amount_minor:
        raise DomainError("INSUFFICIENT_FUNDS", "可支出余额不足，未动用已预留目标", 409)
    receiver = session.get(Account, scope["recipient_account_id"]) if scope["transfer_channel"] == INTERNAL_CHANNEL else None
    _validate_posting_cards(session, source, receiver, amount_minor, source_card_id, recipient_card_id)
    if receiver and receiver.balance_minor + amount_minor > ACCOUNT_BALANCE_LIMIT_MINOR:
        raise DomainError("RECIPIENT_BALANCE_LIMIT", "接收账户余额超过可记账整数容量，未入账", 409)
    transfer = Transfer(sandbox_id=source.sandbox_id, user_id=source.user_id, source_account_id=source.id,
                        payee_id=payee.id, amount_minor=amount_minor, memo=memo, idempotency_key=original_key)
    session.add(transfer)
    session.flush()
    if receiver:
        sender_login = _login(session, source.sandbox_id, source.user_id)
        receiver_login = _login(session, receiver.sandbox_id, receiver.user_id)
        settlement = InternalTransferSettlement(transfer_id=transfer.id, sender_sandbox_id=source.sandbox_id,
            sender_user_id=source.user_id, sender_account_id=source.id, receiver_sandbox_id=receiver.sandbox_id,
            receiver_user_id=receiver.user_id, receiver_account_id=receiver.id, sender_username=sender_login.username,
            receiver_username=receiver_login.username, amount_minor=amount_minor, currency=source.currency)
        session.add(settlement)
        clearing = "INTERNAL_CLEARING:" + transfer.id
        session.add_all([LedgerEntry(sandbox_id=source.sandbox_id, transfer_id=transfer.id, account_ref=source.id, delta_minor=-amount_minor),
            LedgerEntry(sandbox_id=source.sandbox_id, transfer_id=transfer.id, account_ref=clearing, delta_minor=amount_minor),
            LedgerEntry(sandbox_id=receiver.sandbox_id, transfer_id=transfer.id, account_ref=clearing, delta_minor=-amount_minor),
            LedgerEntry(sandbox_id=receiver.sandbox_id, transfer_id=transfer.id, account_ref=receiver.id, delta_minor=amount_minor)])
        receiver.balance_minor += amount_minor
        receiver.version += 1
        record_cash_transaction(session, sandbox_id=receiver.sandbox_id, account_id=receiver.id, delta_minor=amount_minor,
            merchant="银行互转来自" + sender_login.username, category="转账", source_kind="TRANSFER", operation_ref="transfer:" + transfer.id,
            card_id=recipient_card_id)
    else:
        session.add_all([LedgerEntry(sandbox_id=source.sandbox_id, transfer_id=transfer.id, account_ref=source.id, delta_minor=-amount_minor),
            LedgerEntry(sandbox_id=source.sandbox_id, transfer_id=transfer.id, account_ref="CLEARING:" + payee.id, delta_minor=amount_minor)])
    source.balance_minor -= amount_minor
    source.version += 1
    record_cash_transaction(session, sandbox_id=source.sandbox_id, account_id=source.id, delta_minor=-amount_minor,
        merchant=("银行互转至" if receiver else "外部清算转出至") + payee.display_name,
        category="转账", source_kind="TRANSFER", operation_ref="transfer:" + transfer.id, card_id=source_card_id)
    from .transfer_risk import record_transfer_limit_posting
    record_transfer_limit_posting(session, source, transfer, checked_limits)
    from .aa_participant_payments import finish_aa_bank_posting
    finish_aa_bank_posting(session, aa_payment, transfer)
    session.flush()
    if not settlement_integrity(session, transfer)["balanced"]:
        raise DomainError("TRANSFER_SETTLEMENT_INCOMPLETE", "转账跨方分录与现金镜像不完整，整笔不得提交", 500)
    return transfer


def commit_transfer_money(session, source: Account, payee: Payee, amount_minor: int, memo: str,
                          original_key: str, expected_recipient_scope: dict, *,
                          source_card_id: str | None = None, recipient_card_id: str | None = None) -> Transfer:
    """Money is atomic even when a scheduler catches a DomainError and commits.

    A SAVEPOINT is part of the caller's same PostgreSQL transaction, never a
    separate commit. Workflow authentication/receipts still share its outer tx.
    """
    if session.bind.dialect.name == "sqlite":
        connection = session.connection()
        # sqlite3's legacy transaction mode does not BEGIN on SELECT. Without
        # a physical outer BEGIN, RELEASE of the first SAVEPOINT commits cash
        # prematurely even if the caller's later workflow transaction fails.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        return _commit_transfer_money_impl(session, source, payee, amount_minor, memo, original_key, expected_recipient_scope,
            source_card_id=source_card_id, recipient_card_id=recipient_card_id)
