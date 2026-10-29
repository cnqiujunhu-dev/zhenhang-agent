"""Read-only multi-account economic bills; never repair or rewrite postings.

Cash-account rows and credit economic postings are different sources. A credit
repayment is cash movement, not a second purchase; installment principal is a
debt reclassification, while a separately posted fee is a real expense.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select

from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .models import Account, Card, Transaction, utcnow
from .planning import canonical_hash


MOVEMENTS = frozenset({"TRANSFER", "INVESTMENT", "AA_COLLECTION", "DEV_TEST_ADJUSTMENT", "FX",
    "CREDIT_REPAYMENT", "CREDIT_INSTALLMENT_SETTLEMENT", "CREDIT_REPAY", "OWN_ACCOUNT_TRANSFER"})
RECLASSIFICATIONS = frozenset({"CREDIT_INSTALLMENT", "CREDIT_INSTALLMENT_PRINCIPAL", "INSTALLMENT_PRINCIPAL",
    "CREDIT_PRINCIPAL_RECLASSIFICATION"})
CREDIT_MIRRORS = frozenset({"CREDIT_PURCHASE", "CREDIT_REFUND", "CREDIT_FEE"})
KINDS = frozenset({"CONSUMPTION", "FEE", "REFUND", "INCOME", "MOVEMENT", "RECLASSIFICATION"})
CURRENCIES = frozenset({"CNY", "USD"})
LIVE_CASH_SOURCES = MOVEMENTS | RECLASSIFICATIONS | {"MERCHANT_PAYMENT", "MERCHANT_REFUND"}
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
ANOMALY_RULE_VERSION = "billing-anomaly-v2"
INSTRUMENT_BREAKDOWN_VERSION = "owned-bill-instrument-breakdown-v1"


def _error(code, message="账务证据范围或内容未通过核验。", status=422):
    raise DomainError(code, message, status)


def _time(value):
    try:
        return as_utc(datetime.fromisoformat(value) if isinstance(value, str) else value)
    except (TypeError, ValueError, AttributeError):
        _error("BILL_EVIDENCE_TIME_INVALID", "请提供明确的账单时间范围。")


def _cash_kind(transaction):
    source = transaction.source_kind
    if source in RECLASSIFICATIONS: return "RECLASSIFICATION"
    if source in MOVEMENTS: return "MOVEMENT"
    if transaction.amount_minor < 0: return "CONSUMPTION"
    if source == "MERCHANT_REFUND" or any(part in transaction.category for part in ("退款", "冲正")):
        return "REFUND"
    return "INCOME"


def verify_owned_cash_movement_row(session, transaction, principal=None):
    """A new internal-movement label is not sufficient ledger evidence."""
    if transaction.source_kind != "OWN_ACCOUNT_TRANSFER":
        return
    from .own_account_transfer_models import OwnAccountTransferRecord
    from .own_account_transfers import own_transfer_posting_integrity
    parts = transaction.operation_ref.split(":") if isinstance(transaction.operation_ref, str) else []
    if (len(parts) != 3 or parts[0] != "own-transfer" or parts[2] not in {"OUT", "IN"}
            or not inspect(session.connection()).has_table(OwnAccountTransferRecord.__tablename__)):
        _error("BILL_OWN_TRANSFER_POSTING_UNVERIFIED", "本人账户划转的完整账务来源尚未通过核对。", 409)
    record = session.get(OwnAccountTransferRecord, parts[1])
    if (record is None or record.sandbox_id != transaction.sandbox_id
            or principal is not None and (record.sandbox_id, record.user_id) != (principal.sandbox_id, principal.user_id)
            or not own_transfer_posting_integrity(session, record)):
        _error("BILL_OWN_TRANSFER_POSTING_UNVERIFIED", "本人账户划转的双边流水或分录不一致，请先查单。", 409)
    out = parts[2] == "OUT"
    expected = (record.source_account_id, record.source_card_id, -record.amount_minor) if out else (
        record.destination_account_id, record.destination_card_id, record.amount_minor)
    if (transaction.account_id, transaction.card_id, transaction.amount_minor) != expected:
        _error("BILL_OWN_TRANSFER_POSTING_UNVERIFIED", "本人账户划转的原账户、卡片或金额不一致。", 409)


def _owned_internal_transfers(session, principal):
    """A missing pair of statements must not erase a real domain posting."""
    from .own_account_transfer_models import OwnAccountTransferRecord
    from .own_account_transfers import own_transfer_posting_integrity
    if not inspect(session.connection()).has_table(OwnAccountTransferRecord.__tablename__):
        return []
    records = list(session.scalars(select(OwnAccountTransferRecord).where(
        OwnAccountTransferRecord.sandbox_id == principal.sandbox_id,
        OwnAccountTransferRecord.user_id == principal.user_id).order_by(OwnAccountTransferRecord.id)))
    if any(not own_transfer_posting_integrity(session, record) for record in records):
        _error("BILL_OWN_TRANSFER_POSTING_UNVERIFIED", "本人账户划转的原记录、双边流水或分录不完整，请先查单。", 409)
    return records


def resolve_bill_selection(session, principal, *, card_reference=None, currency=None):
    """Resolve identity only. No balance/price/permission is inferred from prose."""
    if currency is not None and (not isinstance(currency, str) or currency not in CURRENCIES):
        _error("BILL_CURRENCY_INVALID", "账单币种请选择人民币或美元。")
    if card_reference is None:
        return None, currency
    if not isinstance(card_reference, str) or not card_reference.strip() or len(card_reference) > 100:
        _error("BILL_CARD_REFERENCE_INVALID", "请明确本人的卡号或唯一尾号。")
    from .payment_instruments import resolve_source_instrument
    snapshot = resolve_source_instrument(session, principal, card_reference, "CNY", purpose="READ")
    selection = {key: snapshot[key] for key in ("instrument_id", "instrument_type", "card_id", "display_last4")}
    # The foreign cash wallet is a separate ledger, not Account.Transaction.
    # An unsupported cash source must never become a zero-spending statement.
    selection["source_supported"] = not (selection["instrument_type"] == "DEBIT" and currency == "USD")
    return selection, currency


def collect_owned_billing_evidence(session, principal, start, end, *, account_ids=None,
        credit_card_reference=None, include_credit=None, business_time=None, category=None,
        historical_through=None, card_reference=None, currency=None):
    """Default all owned Account rows + credit postings; explicit selectors narrow.

    Explicit account_ids never silently widens to credit. An explicit credit
    card alone excludes debit accounts. [] is a deliberate empty cash scope.
    Caller owns source-bound semantic selection; this function never parses
    user sentences, changes history card_id, or performs any banking write.
    """
    require_profile_actor(session, principal)
    _owned_internal_transfers(session, principal)
    start, end = _time(start), _time(end)
    if start >= end:
        _error("BILL_EVIDENCE_PERIOD_INVALID")
    if include_credit is not None and type(include_credit) is not bool:
        _error("BILL_EVIDENCE_SCOPE_INVALID")
    if account_ids is not None and (not isinstance(account_ids, (list, tuple)) or
            any(not isinstance(value, str) or not value for value in account_ids)):
        _error("BILL_EVIDENCE_SCOPE_INVALID")
    all_accounts = session.scalars(select(Account).where(Account.sandbox_id == principal.sandbox_id,
        Account.user_id == principal.user_id)).all()
    accounts_by_id = {row.id: row for row in all_accounts}
    selection, currency = resolve_bill_selection(session, principal, card_reference=card_reference, currency=currency)
    if selection is not None:
        if credit_card_reference is not None:
            _error("BILL_EVIDENCE_SCOPE_INVALID", "请只选择一个明确的银行卡范围。")
        if selection["instrument_type"] == "CREDIT":
            if account_ids or include_credit is False:
                _error("BILL_EVIDENCE_SCOPE_INVALID", "信用卡账单不能混入另选的现金账户。")
            account_ids, include_credit, credit_card_reference = [], True, selection["instrument_id"]
        else:
            if include_credit is True:
                _error("BILL_EVIDENCE_SCOPE_INVALID", "选中借记卡时不会扩大到信用卡账单。")
            include_credit = False
    explicit = account_ids is not None
    chosen = sorted(set(account_ids)) if explicit else ([] if credit_card_reference else sorted(accounts_by_id))
    if set(chosen) - set(accounts_by_id):
        _error("BILL_EVIDENCE_ACCOUNT_NOT_OWNED", "账单账户不属于当前本人。", 403)
    if include_credit is None:
        include_credit = not explicit or credit_card_reference is not None
    if credit_card_reference and not include_credit:
        _error("BILL_EVIDENCE_SCOPE_INVALID", "已选信用卡不能被另一个关闭信用账目的参数悄悄忽略。")
    cutoff = _time(business_time or utcnow())
    historical = _time(historical_through) if historical_through is not None else None
    cards = {card.id for card in session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
        Card.user_id == principal.user_id))}
    query = select(Transaction).where(Transaction.sandbox_id == principal.sandbox_id,
        Transaction.account_id.in_(chosen), Transaction.occurred_at >= start, Transaction.occurred_at < end,
        Transaction.occurred_at <= cutoff)
    if selection is not None and selection["instrument_type"] == "DEBIT":
        # Historical card_id is immutable: do not filter by today's rebound
        # card.account_id and lose its purchases on an older owned account.
        query = query.where(Transaction.card_id == selection["card_id"])
    transactions = session.scalars(query.order_by(Transaction.occurred_at, Transaction.id)).all() if chosen else []
    rows, excluded = [], []
    for tx in transactions:
        tx_currency = accounts_by_id[tx.account_id].currency
        if currency is not None and tx_currency != currency:
            continue
        verify_owned_cash_movement_row(session, tx, principal)
        if historical is not None and _time(tx.occurred_at) > historical and tx.source_kind not in LIVE_CASH_SOURCES:
            excluded.append({"source_id": "transaction:" + tx.id, "reason": "UNIMPORTED_HISTORY_NOT_UNLOCKED_BY_LIVE_ACTIVITY"})
            continue
        if tx.source_kind in CREDIT_MIRRORS:
            excluded.append({"source_id": "transaction:" + tx.id, "reason": "CREDIT_MIRROR_REQUIRES_CANONICAL_POSTING"})
            continue
        if tx_currency not in CURRENCIES:
            _error("BILL_EVIDENCE_CURRENCY_UNSUPPORTED")
        rows.append({"evidence_id": "transaction:" + tx.id, "posting_id": tx.id, "transaction_id": tx.id,
            "source_kind": tx.source_kind, "evidence_type": "OWNED_ACCOUNT_TRANSACTION", "account_id": tx.account_id,
            "card_id": tx.card_id, "instrument_id": "debit:" + tx.card_id if tx.card_id in cards else None,
            "instrument_type": "DEBIT", "card_identity_verified": tx.card_id in cards,
            "occurred_at": as_utc(tx.occurred_at).isoformat(), "amount_minor": tx.amount_minor,
            "cash_amount_minor": 0 if tx.source_kind in RECLASSIFICATIONS else tx.amount_minor,
            "currency": tx_currency, "merchant": tx.merchant, "category": tx.category,
            "economic_kind": _cash_kind(tx), "flag": tx.flag, "operation_ref": tx.operation_ref})
    if include_credit:
        from .credit_service import credit_bill_rows
        from .credit_models import CreditCard
        credit_ids = {card.id for card in session.scalars(select(CreditCard).where(
            CreditCard.sandbox_id == principal.sandbox_id, CreditCard.user_id == principal.user_id))}
        for row in credit_bill_rows(session, principal, start, end, card_reference=credit_card_reference):
            if _time(row["occurred_at"]) > cutoff or currency is not None and row["currency"] != currency:
                continue
            rows.append({**row, "evidence_id": row["source_kind"].lower() + ":" + row["posting_id"],
                "transaction_id": None, "account_id": None, "cash_amount_minor": 0,
                "card_identity_verified": row.get("card_id") in credit_ids, "flag": "NORMAL"})
    if category is not None:
        if not isinstance(category, str) or not category.strip() or len(category) > 80:
            _error("BILL_EVIDENCE_CATEGORY_INVALID")
        rows = [row for row in rows if row["category"] == category]
    normalized = normalize_economic_rows(rows)
    cash_currencies = {accounts_by_id[key].currency for key in chosen}
    if currency is not None:
        cash_currencies.intersection_update({currency})
    result = {"schema_version": "owned-billing-evidence-v1",
        "query_scope": {"account_ids": chosen, "include_credit": include_credit,
            "cash_currencies": sorted(cash_currencies),
            "credit_card_selected": credit_card_reference is not None, "category": category,
            "period_start": start.isoformat(), "period_end": end.isoformat(), "observed_through": cutoff.isoformat(),
            "historical_through": historical.isoformat() if historical else None},
        "rows": normalized, "excluded_rows": excluded,
        "summary": summarize_economic_rows(normalized, cash_scope_present=bool(chosen),
            cash_scope_currencies=cash_currencies),
        "anomalies": detect_billing_anomalies(normalized),
        "coverage": {"source": "REGISTERED_LOCAL_LEDGER_NOT_EXTERNAL_BANK_FEED",
            "period_state": "PARTIAL_CURRENT_OR_FUTURE" if end > cutoff else "CLOSED_REQUESTED_PERIOD",
            "canonical_credit_postings_used": include_credit, "unverified_credit_mirrors_excluded": len(excluded),
            "foreign_cash_wallet_movements_included": False,
            "cash_flow_scope": "Selected Account.Transaction rows only; no claim about foreign-wallet cash flows."},
        "bank_state_changed": False, "automatic_action_authorized": False}
    if selection is not None:
        result["query_scope"]["card_filter"] = selection
        if not selection["source_supported"]:
            result["coverage"]["scope_status"] = "USD_DEBIT_WALLET_SOURCE_UNAVAILABLE"
    if currency is not None:
        result["query_scope"]["currency_filter"] = currency
    result["evidence_hash"] = canonical_hash({key: result[key] for key in ("query_scope", "rows", "excluded_rows")})
    return result


def normalize_economic_rows(rows):
    if not isinstance(rows, (list, tuple)):
        _error("BILL_EVIDENCE_ROWS_INVALID")
    unique = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("evidence_id"), str) or not row["evidence_id"]
                or type(row.get("amount_minor")) is not int or not 0 < abs(row["amount_minor"]) <= 2**53 - 1
                or row.get("currency") not in CURRENCIES or row.get("economic_kind") not in KINDS
                or type(row.get("cash_amount_minor", 0)) is not int):
            _error("BILL_EVIDENCE_ROWS_INVALID")
        value = {**row, "occurred_at": _time(row.get("occurred_at")).isoformat()}
        kind, amount = value["economic_kind"], value["amount_minor"]
        if (kind in {"CONSUMPTION", "FEE"} and amount >= 0) or (kind in {"REFUND", "INCOME"} and amount <= 0):
            _error("BILL_EVIDENCE_SIGN_INVALID")
        previous = unique.get(value["evidence_id"])
        if previous is not None and previous != value:
            _error("BILL_EVIDENCE_POSTING_CONFLICT", "同一账务证据标识出现冲突，不能覆盖其中一份。", 409)
        unique[value["evidence_id"]] = value
    return sorted(unique.values(), key=lambda row: (row["occurred_at"], row["evidence_id"]))


def instrument_breakdown(rows, totals, *, known=True):
    """Selected full rows only; a cash-domain label does not prove a card."""
    groups = {}
    if known:
        for row in normalize_economic_rows(rows):
            kind = row.get('instrument_type')
            if (row.get('card_identity_verified') is not True or kind not in {'DEBIT', 'CREDIT'}
                    or not isinstance(row.get('card_id'), str) or not row['card_id']):
                kind = 'UNKNOWN'
            group = groups.setdefault((row['currency'], kind), {'currency': row['currency'],
                'instrument_type': kind, 'row_count': 0, 'consumption_minor': 0, 'fees_minor': 0, 'refund_minor': 0})
            group['row_count'] += 1
            if row['economic_kind'] in {'CONSUMPTION', 'FEE'}:
                group['consumption_minor' if row['economic_kind'] == 'CONSUMPTION' else 'fees_minor'] -= row['amount_minor']
            elif row['economic_kind'] == 'REFUND':
                group['refund_minor'] += row['amount_minor']
    value = {'version': INSTRUMENT_BREAKDOWN_VERSION, 'status': 'RECORDED' if known else 'UNAVAILABLE',
        'groups': [groups[key] for key in sorted(groups)], 'currencies_combined': False}
    if not instrument_breakdown_valid(value, totals, known=known):
        _error('BILL_EVIDENCE_SCOPE_INVALID')
    return value


def instrument_breakdown_valid(value, totals, *, known=True):
    try:
        if (not isinstance(value, dict) or set(value) != {'version', 'status', 'groups', 'currencies_combined'}
                or value['version'] != INSTRUMENT_BREAKDOWN_VERSION or value['currencies_combined'] is not False
                or value['status'] != ('RECORDED' if known else 'UNAVAILABLE') or not isinstance(value['groups'], list)):
            return False
        if not known:
            return value['groups'] == []
        if len(value['groups']) > 6:
            return False
        seen = set(); fields = ('row_count', 'consumption_minor', 'fees_minor', 'refund_minor')
        for row in value['groups']:
            if (not isinstance(row, dict) or set(row) != {'currency', 'instrument_type', *fields}
                    or row['currency'] not in CURRENCIES or row['instrument_type'] not in {'DEBIT', 'CREDIT', 'UNKNOWN'}
                    or any(type(row[key]) is not int or not 0 <= row[key] <= 2**53-1 for key in fields)
                    or row['row_count'] < 1 or (row['currency'], row['instrument_type']) in seen):
                return False
            seen.add((row['currency'], row['instrument_type']))
        if {row['currency'] for row in value['groups']} != {row['currency'] for row in totals}:
            return False
        return all(sum(row[key] for row in value['groups'] if row['currency'] == total['currency']) == total[key]
            for total in totals for key in fields)
    except (KeyError, TypeError, ValueError):
        return False


def owned_billing_source_revision(session, principal):
    """Private all-posting revision also sees rows posted after observation.

    Fixed calendar bounds remain immutable for presentation, but must not make
    a new posting in the requested month invisible to a current-source check.
    Balances/card controls are not bill amounts and are not included here.
    """
    require_profile_actor(session, principal)
    accounts = list(session.scalars(select(Account).where(Account.sandbox_id == principal.sandbox_id,
        Account.user_id == principal.user_id).order_by(Account.id)))
    transactions = list(session.scalars(select(Transaction).where(Transaction.sandbox_id == principal.sandbox_id,
        Transaction.account_id.in_([row.id for row in accounts])).order_by(Transaction.id)))
    for row in transactions:
        verify_owned_cash_movement_row(session, row, principal)
    cash = [{key: (as_utc(getattr(row, key)).isoformat() if key == "occurred_at" else getattr(row, key))
        for key in ("id", "account_id", "card_id", "occurred_at", "amount_minor", "merchant", "category", "flag", "source_kind", "operation_ref")}
        for row in transactions]
    from .credit_service import credit_bill_rows
    credit = credit_bill_rows(session, principal, datetime(1970, 1, 1, tzinfo=LOCAL_TIMEZONE),
        datetime(9999, 12, 31, tzinfo=LOCAL_TIMEZONE))
    body = {"owner": {"sandbox_id": principal.sandbox_id, "user_id": principal.user_id},
        "accounts": [{"account_id": row.id, "currency": row.currency} for row in accounts], "cash": cash, "credit": credit}
    transfers = _owned_internal_transfers(session, principal)
    if transfers:
        body["own_transfers"] = [{"record_id": row.id, "journal_id": row.journal_id,
            "request_hash": row.request_hash, "receipt": row.receipt_json} for row in transfers]
    return canonical_hash(body)


def summarize_economic_rows(rows, *, cash_scope_present=True, cash_scope_currencies=None):
    rows = normalize_economic_rows(rows)
    if cash_scope_currencies is None:
        cash_scope_currencies = {row["currency"] for row in rows if row.get("account_id") is not None}
    if set(cash_scope_currencies) - CURRENCIES:
        _error("BILL_EVIDENCE_CURRENCY_UNSUPPORTED")
    groups, daily, monthly = {}, {}, {}
    for row in rows:
        currency, kind, amount = row["currency"], row["economic_kind"], row["amount_minor"]
        value = groups.setdefault(currency, {"currency": currency, "row_count": 0, "consumption_minor": 0,
            "fees_minor": 0, "refund_minor": 0, "income_minor": 0, "movement_in_minor": 0,
            "movement_out_minor": 0, "reclassified_principal_minor": 0,
            "cash_flow_net_minor": 0 if cash_scope_present and currency in cash_scope_currencies else None,
            "category_spending_minor": {}})
        value["row_count"] += 1
        if kind in {"CONSUMPTION", "FEE"}:
            value["consumption_minor" if kind == "CONSUMPTION" else "fees_minor"] -= amount
            category = row.get("category") or "未分类"
            value["category_spending_minor"][category] = value["category_spending_minor"].get(category, 0) - amount
        elif kind == "REFUND": value["refund_minor"] += amount
        elif kind == "INCOME": value["income_minor"] += amount
        elif kind == "MOVEMENT": value["movement_in_minor" if amount > 0 else "movement_out_minor"] += abs(amount)
        else: value["reclassified_principal_minor"] += abs(amount)
        if value["cash_flow_net_minor"] is not None: value["cash_flow_net_minor"] += row.get("cash_amount_minor", 0)
        local = _time(row["occurred_at"]).astimezone(LOCAL_TIMEZONE)
        for table, key in ((daily, local.strftime("%Y-%m-%d")), (monthly, local.strftime("%Y-%m"))):
            bucket = table.setdefault((currency, key), {"currency": currency, "period": key,
                "transaction_count": 0, "spending_minor": 0, "refund_minor": 0, "income_minor": 0,
                "movement_net_minor": 0, "movement_in_minor": 0, "movement_out_minor": 0, "net_minor": 0})
            bucket["transaction_count"] += 1
            if kind != "RECLASSIFICATION": bucket["net_minor"] += amount
            if kind in {"CONSUMPTION", "FEE"}: bucket["spending_minor"] -= amount
            elif kind == "REFUND": bucket["refund_minor"] += amount
            elif kind == "INCOME": bucket["income_minor"] += amount
            elif kind == "MOVEMENT":
                bucket["movement_net_minor"] += amount
                bucket["movement_in_minor" if amount > 0 else "movement_out_minor"] += abs(amount)
    for value in groups.values():
        value["economic_spending_minor"] = value["consumption_minor"] + value["fees_minor"]
        value["net_economic_spending_minor"] = value["economic_spending_minor"] - value["refund_minor"]
        value["net_economic_change_minor"] = value["income_minor"] - value["net_economic_spending_minor"]
        if any(type(item) is int and abs(item) > 2**53 - 1 for item in value.values()):
            _error("BILL_EVIDENCE_TOTAL_OUT_OF_RANGE")
    return {"totals_by_currency": [groups[key] for key in sorted(groups)], "daily": [daily[key] for key in sorted(daily)],
        "monthly": [monthly[key] for key in sorted(monthly)], "currencies_combined": False,
        "meaning": "Economic spending counts purchases and posted fees once. Repayments are cash movements; installment principal is not a new purchase. Economic net change is not an account balance."}


def detect_billing_anomalies(rows, *, duplicate_window_seconds=120, baseline_minimum=5,
        large_multiple=5, large_minimum_minor=50000, frequency_window_seconds=600,
        frequency_minimum=5, unusual_start_hour=0, unusual_end_hour=6):
    """Evidence-backed candidates only; not fraud decisions or card permissions."""
    rows = normalize_economic_rows(rows)
    if (type(duplicate_window_seconds) is not int or not 1 <= duplicate_window_seconds <= 600
            or type(baseline_minimum) is not int or baseline_minimum < 3
            or type(large_multiple) is not int or large_multiple < 2 or type(large_minimum_minor) is not int or large_minimum_minor <= 0
            or type(frequency_window_seconds) is not int or not 1 <= frequency_window_seconds <= 3600
            or type(frequency_minimum) is not int or not 3 <= frequency_minimum <= 100
            or type(unusual_start_hour) is not int or type(unusual_end_hour) is not int
            or not 0 <= unusual_start_hour < unusual_end_hour <= 24):
        _error("BILL_ANOMALY_POLICY_INVALID")
    policy = {"rule_version": ANOMALY_RULE_VERSION, "duplicate_window_seconds": duplicate_window_seconds,
        "baseline_minimum": baseline_minimum, "large_multiple": large_multiple,
        "large_minimum_minor": large_minimum_minor, "frequency_window_seconds": frequency_window_seconds,
        "frequency_minimum": frequency_minimum, "unusual_start_hour": unusual_start_hour,
        "unusual_end_hour": unusual_end_hour, "timezone": "Asia/Shanghai"}
    policy_hash = canonical_hash(policy)
    anomalies, recent, history, frequency = [], defaultdict(list), defaultdict(list), defaultdict(list)
    frequency_last_alert = {}
    def add(kind, affected, reason, basis):
        cards = {row.get("card_id") for row in affected if row.get("card_identity_verified") is True and row.get("card_id")}
        instruments = {row.get("instrument_id") for row in affected if row.get("card_identity_verified") is True and row.get("instrument_id")}
        anomalies.append({"anomaly_id": canonical_hash({"kind": kind, "rule_version": ANOMALY_RULE_VERSION,
                "policy_hash": policy_hash, "evidence_ids": [row["evidence_id"] for row in affected]}),
            "rule": kind, "rule_version": ANOMALY_RULE_VERSION, "policy_hash": policy_hash,
            "evidence_ids": [row["evidence_id"] for row in affected],
            "transaction_id": affected[-1].get("transaction_id"), "card_id": next(iter(cards)) if len(cards) == 1 else None,
            "instrument_id": next(iter(instruments)) if len(instruments) == 1 else None,
            "instrument_type": affected[-1].get("instrument_type"),
            "currency": affected[-1]["currency"], "amount_minor": affected[-1]["amount_minor"],
            "occurred_at": affected[-1]["occurred_at"],
            "merchant": affected[-1].get("merchant"), "reason": reason, "basis": basis,
            "severity": "NEEDS_USER_VERIFICATION", "fraud_confirmed": False, "automatic_action_authorized": False})
    for row in rows:
        if row.get("flag") == "ANOMALY" and row["economic_kind"] in {"CONSUMPTION", "FEE"}:
            add("BANK_MARKED_FOR_VERIFICATION", [row], "账务记录标记为待核验；不代表已确认欺诈。", {"source": "BANK_POSTING_FLAG"})
        if row["economic_kind"] != "CONSUMPTION": continue
        currency, amount, current = row["currency"], -row["amount_minor"], _time(row["occurred_at"])
        identity = row.get("instrument_id") or ("account:" + str(row["account_id"]) if row.get("account_id") else None)
        key = (currency, identity, row.get("merchant"), amount)
        prior = [old for old in recent[key] if timedelta(0) <= current - _time(old["occurred_at"]) <= timedelta(seconds=duplicate_window_seconds)]
        if identity is not None and prior:
            add("POSSIBLE_DUPLICATE_CHARGE", [prior[-1], row],
                "同一付款来源短时间内出现同商户同金额消费，可能是重复也可能是独立订单，请核对。",
                {"window_seconds": duplicate_window_seconds, "same_currency_and_source": True, "independent_orders_not_excluded": True})
        recent[key].append(row)
        if identity is not None:
            frequency_key = (currency, identity)
            window = [old for old in frequency[frequency_key]
                if timedelta(0) <= current - _time(old["occurred_at"]) <= timedelta(seconds=frequency_window_seconds)]
            window.append(row)
            frequency[frequency_key] = window
            previous_alert = frequency_last_alert.get(frequency_key)
            if len(window) >= frequency_minimum and (previous_alert is None or
                    current - previous_alert > timedelta(seconds=frequency_window_seconds)):
                add("HIGH_FREQUENCY_SPENDING", window,
                    "同一付款来源在短时间内出现多笔消费，请核对是否为本人独立订单；不要求同商户或同金额，也不代表已确认欺诈。",
                    {"window_seconds": frequency_window_seconds, "minimum_count": frequency_minimum,
                     "observed_count": len(window), "window_start": window[0]["occurred_at"],
                     "window_end": row["occurred_at"], "window_boundary": "INCLUSIVE",
                     "same_currency_and_source": True, "merchant_or_amount_match_required": False,
                     "repeat_notice_suppression_seconds": frequency_window_seconds,
                     "scope": "RECORDED_SELECTED_PERIOD_ONLY"})
                frequency_last_alert[frequency_key] = current
        local = current.astimezone(LOCAL_TIMEZONE)
        if unusual_start_hour <= local.hour < unusual_end_hour:
            add("UNUSUAL_HOUR_SPENDING", [row],
                "消费发生在上海时间凌晨时段，请结合实际订单核对；夜间消费本身不代表异常，不据此推断个人习惯或欺诈。",
                {"timezone": "Asia/Shanghai", "start_hour_inclusive": unusual_start_hour,
                 "end_hour_exclusive": unusual_end_hour, "local_occurred_at": local.isoformat(),
                 "personal_habit_inferred": False, "time_of_day_only": True})
        baseline = history[currency]
        if len(baseline) >= baseline_minimum:
            ordered = sorted(baseline)
            center = (ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2]) // 2
            if amount >= large_minimum_minor and amount >= center * large_multiple:
                add("LARGE_RELATIVE_TO_PRIOR_SPENDING", [row], "金额显著高于此前已取得的同币种消费中位数，请核对；大额本身不代表异常扣款。",
                    {"prior_count": len(baseline), "prior_median_minor": center, "multiple": large_multiple,
                        "absolute_minimum_minor": large_minimum_minor})
        baseline.append(amount)
    return anomalies
