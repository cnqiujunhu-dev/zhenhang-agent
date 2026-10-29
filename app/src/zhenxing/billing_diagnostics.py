"""Upgraded-owner bill pipeline. Legacy single-account receipts stay v1.

Every total, category, comparison and largest-expense target is projected from
the same canonical economic rows. No current balances, inferred credit limit,
or model arithmetic participate in a posted bill.
"""
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import inspect, select

from .analytics import SHANGHAI, as_utc
from .billing_evidence import (collect_owned_billing_evidence, summarize_economic_rows,
    detect_billing_anomalies, owned_billing_source_revision, instrument_breakdown, INSTRUMENT_BREAKDOWN_VERSION)
from .contracts import BillAnalysis, DomainError
from .models import Account
from .planning import canonical_hash
from .redaction import redact_credentials
from .reporting_clock import owned_economic_clock


VERSION = "owned-bill-analysis-v2"
LARGEST_VERSION = "owned-economic-largest-expense-v2"
EPOCH = datetime(1970, 1, 1, tzinfo=SHANGHAI)


def upgraded_bill_owner(session, sandbox_id, account_id):
    """Explicit migration marker only. A read never initializes payment data."""
    from .credit_models import PaymentUpgradeRecord
    from .credit_bootstrap import UPGRADE_REVISION
    account = session.get(Account, account_id)
    if account is None or account.sandbox_id != sandbox_id:
        return None
    if not inspect(session.connection()).has_table(PaymentUpgradeRecord.__tablename__):
        return None
    migrated = session.scalar(select(PaymentUpgradeRecord.id).where(
        PaymentUpgradeRecord.sandbox_id == sandbox_id, PaymentUpgradeRecord.user_id == account.user_id,
        PaymentUpgradeRecord.revision == UPGRADE_REVISION))
    return account.user_id if migrated else None


def _principal(sandbox_id, user_id):
    from .services import Principal
    return Principal(user_id, sandbox_id)


def _public_row(row):
    return {**{key: row.get(key) for key in ("evidence_id", "transaction_id", "posting_id", "account_id",
        "card_id", "instrument_type", "currency", "amount_minor", "occurred_at", "source_kind")},
        "merchant": redact_credentials(row["merchant"]), "category": redact_credentials(row["category"]),
        "cash_flow_type": {"CONSUMPTION": "EXPENSE", "FEE": "FEE", "REFUND": "REFUND",
            "INCOME": "INCOME", "MOVEMENT": "MOVEMENT", "RECLASSIFICATION": "RECLASSIFICATION"}[row["economic_kind"]]}


def _summary(rows, evidence):
    cash_currencies = set(evidence["query_scope"]["cash_currencies"])
    return summarize_economic_rows(rows, cash_scope_present=bool(evidence["query_scope"]["account_ids"]),
        cash_scope_currencies=cash_currencies)


def _largest(rows, start, end, category, coverage, source_hash):
    values = []
    for currency in sorted({row["currency"] for row in rows}):
        expenses = [row for row in rows if row["currency"] == currency and row["economic_kind"] in {"CONSUMPTION", "FEE"}]
        maximum = max((-row["amount_minor"] for row in expenses), default=None)
        matches = [row for row in expenses if -row["amount_minor"] == maximum]
        values.append({"currency": currency, "status": "FOUND" if matches else "NO_RECORDED_EXPENSE",
            "maximum_expense_minor": maximum, "tie_count": len(matches),
            "records": [_public_row(row) for row in sorted(matches, key=lambda row: (row["occurred_at"], row["evidence_id"]), reverse=True)]})
    result = {"version": LARGEST_VERSION, "authority": "ALL_OWNED_CANONICAL_ECONOMIC_POSTINGS",
        "source_complete": True, "completeness_basis": "ALL_RECORDED_ROWS_NOT_EXTERNAL_FEED_COMPLETENESS",
        "period_start": start.isoformat() if start else None, "period_end": end.isoformat() if end else None,
        "category_filter": category, "recorded_row_count": len(rows), "by_currency": values,
        "currencies_combined": False, "source_fingerprint": source_hash, "coverage": coverage,
        "read_only": True, "bank_state_changed": False}
    result["snapshot_hash"] = canonical_hash(result)
    return result


def _signed_body(analysis):
    result = deepcopy(analysis.model_dump() if hasattr(analysis, "model_dump") else analysis)
    (result.get("financial_insights") or {}).pop("read_fingerprint", None)
    return result


def _seal(analysis):
    analysis.financial_insights["read_fingerprint"] = canonical_hash(_signed_body(analysis))
    return analysis


def _base_body(analysis):
    result = _signed_body(analysis)
    result["period_fallback"] = None
    diagnostics = result["financial_insights"]
    diagnostics.pop("period_fallback", None)
    diagnostics.pop("requested_question", None)
    return result


def _retained_closed_economic_body(session, sandbox_id, user_id, value):
    """Finite historical comparison, anchored to an existing owned READ.

    The global all-posting revision may change after an unrelated period's
    payment. It is not itself a change to this complete closed-period result.
    Never use this exception for an unretained DTO, incomplete/truncated rows,
    open/current periods, unknown versions, or an altered original evidence.
    """
    from .models import Task, EvidenceSnapshot
    try:
        diagnostics=value['financial_insights'];largest=diagnostics['largest_expense']
        query=diagnostics['source_query'];rows=value['statement_transactions']
        start,end=(datetime.fromisoformat(value[key]) for key in ('period_start','period_end'))
        observed=datetime.fromisoformat(query['calendar_time'])
        if value.get('billing_schema_version')!=VERSION or diagnostics.get('version')!=VERSION \
                or largest.get('version')!=LARGEST_VERSION or query.get('question')!='OVERVIEW' \
                or query.get('comparison_period') is not None or query.get('include_statement_transactions') is not True \
                or not all(point.tzinfo is not None for point in (start,end,observed)) or not start<end<=observed \
                or value.get('period_fallback') is not None or diagnostics.get('period_fallback') is not None \
                or (value.get('coverage') or {}).get('status')!='COMPLETE' \
                or value.get('statement_transactions_truncated') is not False \
                or not isinstance(rows,list) or type(value.get('transaction_count')) is not int \
                or len(rows)!=value['transaction_count'] \
                or type(diagnostics.get('source_fingerprint')) is not str \
                or largest.get('source_fingerprint')!=diagnostics['source_fingerprint'] \
                or largest.get('snapshot_hash')!=canonical_hash({key:item for key,item in largest.items() if key!='snapshot_hash'}):
            return None
        import re
        if re.fullmatch(r'[0-9a-f]{64}',diagnostics['source_fingerprint']) is None:
            return None
        # Merely rehashing a fabricated old body does not establish a retained
        # source. The original finished child, its plan's evidence ID and its
        # exact finite evidence body must all agree with this original result.
        expected_evidence={key:value.get(key) for key in ('transaction_count','spending_minor',
            'period_start','period_end','coverage','financial_insights','period_fallback','category_filter')}
        expected_evidence['anomaly_ids']=[item['transaction_id'] for item in value.get('anomalies',[])]
        candidates=session.execute(select(Task,EvidenceSnapshot).join(EvidenceSnapshot,EvidenceSnapshot.task_id==Task.id).where(
            Task.user_id==user_id,Task.sandbox_id==sandbox_id,Task.state=='SUCCEEDED',Task.intent_type=='BILL_ANALYSIS',
            EvidenceSnapshot.sandbox_id==sandbox_id,
            EvidenceSnapshot.payload_json['financial_insights']['read_fingerprint'].as_string()==diagnostics['read_fingerprint']).limit(2)).all()
        if len(candidates)!=1:
            return None
        child,evidence=candidates[0]
        bill_nodes=[node for node in (child.plan_json or {}).get('nodes',[]) if node.get('tool_name')=='bill.analyze.v1']
        if len(bill_nodes)!=1 or bill_nodes[0].get('evidence_id')!=evidence.id \
                or canonical_hash(child.analysis_json)!=canonical_hash(value) \
                or canonical_hash(evidence.payload_json)!=canonical_hash(expected_evidence):
            return None
        body=_signed_body(value)
        body['financial_insights'].pop('source_fingerprint')
        body['financial_insights']['largest_expense'].pop('source_fingerprint')
        body['financial_insights']['largest_expense'].pop('snapshot_hash')
        return body
    except (KeyError,TypeError,ValueError,AttributeError):
        return None


def analyze_owned_bill(session, sandbox_id, account_id, message, *, period=None, calendar_time=None,
        allow_recent_fallback=False, include_statement_transactions=False, category_filter=None, question=None,
        comparison_period=None, card_reference=None, currency=None, instrument_breakdown_version=None,
        query_context_version=None):
    from .financial_diagnostics import CATEGORY_LABELS_EN, financial_question, _previous_period
    from .reports import bill_request_period, bill_request_text, comparative_report_periods, period_boundaries
    if instrument_breakdown_version not in (None, INSTRUMENT_BREAKDOWN_VERSION):
        raise DomainError('BILL_EVIDENCE_SCOPE_INVALID', '账单分项版本需要重新核对。', 422)
    from .bill_query_context import VERSION as QUERY_CONTEXT_VERSION, MARKER as QUERY_CONTEXT_MARKER
    if query_context_version not in (None, QUERY_CONTEXT_VERSION):
        raise DomainError('BILL_EVIDENCE_SCOPE_INVALID', '账单查询上下文版本需要重新核对。', 422)
    user_id = upgraded_bill_owner(session, sandbox_id, account_id)
    if user_id is None:
        raise DomainError("BILL_UPGRADED_OWNER_REQUIRED", "这份账单需使用本人已升级的付款账户。", 409)
    principal = _principal(sandbox_id, user_id)
    clock = owned_economic_clock(session, principal, calendar_time=calendar_time)
    evidence = collect_owned_billing_evidence(session, principal, EPOCH, clock.calendar_time + timedelta(microseconds=1),
        business_time=clock.calendar_time, historical_through=clock.historical_data_through,
        card_reference=card_reference, currency=currency)
    if card_reference is not None or currency is not None:
        clock = owned_economic_clock(session, principal, calendar_time=clock.calendar_time, evidence=evidence)
    question = question or financial_question(bill_request_text(message))
    if question not in {"OVERVIEW", "SPENDING_CHANGE", "CASH_FLOW", "SAVING_OPTIONS"}:
        raise DomainError("BILL_QUESTION_INVALID", "账单问题维度尚未登记。", 422)
    if period is None:
        period = bill_request_period(message, clock.calendar_time)
    if period is None and question == "SPENDING_CHANGE":
        local = clock.calendar_time.astimezone(SHANGHAI)
        period = period_boundaries("month", local.year, local.month)
    start, end = (tuple(as_utc(value) for value in period) if period else (None, None))
    if period and start >= end:
        raise DomainError("BILL_EVIDENCE_PERIOD_INVALID", "请明确有效的账单期间。", 422)
    categories = {row["category"] for row in evidence["rows"]}
    if category_filter is not None:
        if not isinstance(category_filter, str) or not 1 <= len(category_filter) <= 32:
            raise DomainError("BILL_CATEGORY_INVALID", "请明确一个账单类别。", 422)
        known_categories = set(categories)
        if category_filter not in known_categories and (card_reference is not None or currency is not None):
            owner_evidence = collect_owned_billing_evidence(session, principal, EPOCH, clock.calendar_time + timedelta(microseconds=1),
                business_time=clock.calendar_time, historical_through=clock.historical_data_through)
            known_categories.update(row["category"] for row in owner_evidence["rows"])
        if category_filter not in known_categories:
            raise DomainError("BILL_CATEGORY_UNKNOWN", "本人的现有账单中没有这个类别，请核对已有类别。", 422)
    def window(left, right):
        return [row for row in evidence["rows"] if (left is None or as_utc(datetime.fromisoformat(row["occurred_at"])) >= left)
            and (right is None or as_utc(datetime.fromisoformat(row["occurred_at"])) < right)
            and (category_filter is None or row["category"] == category_filter)]
    rows = window(start, end)
    totals = _summary(rows, evidence)
    cny = next((row for row in totals["totals_by_currency"] if row["currency"] == "CNY"), {})
    coverage = {**clock.coverage(start, end), "scope": "ALL_OWNED_CASH_AND_REGISTERED_CREDIT_ECONOMIC_ROWS",
        "credit_ledger_authority": "COMPLETE_REGISTERED_INTERNAL_POSTINGS_NOT_EXTERNAL_BANK_FEED",
        "foreign_cash_wallet_movements_included": False}
    selection = evidence["query_scope"].get("card_filter")
    if card_reference is not None or currency is not None:
        coverage["scope"] = "SELECTED_OWNED_CARD_AND_CURRENCY_ECONOMIC_ROWS"
    if evidence["coverage"].get("scope_status") == "USD_DEBIT_WALLET_SOURCE_UNAVAILABLE":
        coverage.update(status="UNKNOWN", basis="UNAVAILABLE_BILL_SOURCE", scope_status="USD_DEBIT_WALLET_SOURCE_UNAVAILABLE",
            zero_is_full_period_total=False,
            notice="这张借记卡的美元钱包流水不在当前账单来源中，不能把未提供的数据当作零消费。")
    known = coverage["status"] not in {"UNKNOWN", "INSUFFICIENT"}
    income_categories = {}
    for row in rows:
        if row["currency"] == "CNY" and row["economic_kind"] == "INCOME":
            income_categories[row["category"]] = income_categories.get(row["category"], 0) + row["amount_minor"]
    source_query = {key: item for key, item in evidence["query_scope"].items() if key not in {"observed_through", "period_end"}}
    source_query["observation_calendar_date"] = clock.calendar_time.astimezone(SHANGHAI).date().isoformat()
    source_hash = canonical_hash({"owner": {"sandbox_id": sandbox_id, "user_id": user_id},
        "query_scope": source_query, "rows": evidence["rows"], "excluded": evidence["excluded_rows"],
        "all_posting_revision": owned_billing_source_revision(session, principal)})
    facts = []
    for item in totals["totals_by_currency"] if known else []:
        for field, label in (("economic_spending_minor", "已入账消费及费用"), ("refund_minor", "已入账退款"),
                ("income_minor", "已入账收入"), ("movement_in_minor", "资金搬移流入"), ("movement_out_minor", "资金搬移流出")):
            field_name = ("spending_minor" if field == "economic_spending_minor" else field)
            suffix = "" if item["currency"] == "CNY" else "_" + item["currency"].lower()
            facts.append({"fact_id": "facts:bill-diagnostics." + field_name + suffix,
                "source_id": "facts:bill-diagnostics", "field": field_name + suffix, "value": item[field],
                "currency": item["currency"], "unit": "MINOR", "label": label,
                "label_en": field_name.replace("_minor", "").replace("_", " ")})
    comparison = {"status": "NOT_REQUESTED", "growth_claim_allowed": False, "spending_change_minor": None,
        "currencies_combined": False, "by_currency": [], "category_changes": []}
    if question == "SPENDING_CHANGE" and period:
        previous = comparison_period or _previous_period(start, end)
        requested = comparative_report_periods(message, clock.calendar_time)
        if comparison_period is None and len(requested) == 2:
            previous = next(item for item in requested if item != period)
        previous = tuple(as_utc(value) for value in previous)
        original_previous = {"start": previous[0].isoformat(), "end": previous[1].isoformat()}
        other_coverage = clock.coverage(*previous)
        comparison.update(current={"start": start.isoformat(), "end": end.isoformat()},
            previous={"start": previous[0].isoformat(), "end": previous[1].isoformat()},
            previous_coverage=other_coverage, status="INSUFFICIENT", reason="FULL_OR_EQUAL_ELAPSED_COVERAGE_REQUIRED")
        sparse = coverage["basis"] in {"LIVE_ACTIVITY_ONLY", "HISTORY_AND_LIVE_ACTIVITY"} or other_coverage["basis"] in {"LIVE_ACTIVITY_ONLY", "HISTORY_AND_LIVE_ACTIVITY"}
        if not sparse and coverage["status"] == other_coverage["status"] == "COMPLETE":
            current_end, previous_end, status = end, previous[1], "COMPLETE_PERIODS"
        elif not sparse and known and clock.historical_data_start and clock.historical_data_start <= min(start, previous[0]):
            elapsed = min(end - start, previous[1] - previous[0], clock.historical_data_through + timedelta(microseconds=1) - start,
                clock.historical_data_through + timedelta(microseconds=1) - previous[0])
            current_end, previous_end, status = start + elapsed, previous[0] + elapsed, "EQUAL_ELAPSED" if elapsed > timedelta(0) else "INSUFFICIENT"
        else:
            current_end, previous_end, status = end, previous[1], "INSUFFICIENT"
        if status != "INSUFFICIENT":
            left = {item["currency"]: item for item in _summary(window(start, current_end), evidence)["totals_by_currency"]}
            right = {item["currency"]: item for item in _summary(window(previous[0], previous_end), evidence)["totals_by_currency"]}
            comparison.update(status=status, reason=None, current={"start": start.isoformat(), "end": current_end.isoformat()},
                previous={"start": previous[0].isoformat(), "end": previous_end.isoformat()})
            for compared_currency in sorted(set(left) | set(right)):
                lval, rval = left.get(compared_currency, {}), right.get(compared_currency, {})
                old, new = rval.get("economic_spending_minor", 0), lval.get("economic_spending_minor", 0)
                changes = [{"category": category, "change_minor": lval.get("category_spending_minor", {}).get(category, 0) - rval.get("category_spending_minor", {}).get(category, 0)}
                    for category in sorted(set(lval.get("category_spending_minor", {})) | set(rval.get("category_spending_minor", {})))]
                item = {"currency": compared_currency, "current_spending_minor": new, "previous_spending_minor": old,
                    "spending_change_minor": new - old, "zero_baseline": old == 0, "growth_claim_allowed": old > 0,
                    "category_changes": changes}
                comparison["by_currency"].append(item)
                if compared_currency == "CNY":
                    comparison.update({key: item[key] for key in ("current_spending_minor", "previous_spending_minor", "spending_change_minor", "zero_baseline", "growth_claim_allowed", "category_changes")})
    category_meta = {"version": "bill-category-filter-v1", "basis": "CATEGORY_EXACT_MATCH", "category": category_filter,
        "currency": "SEPARATE_BY_CURRENCY", "read_only": True, "bank_state_changed": False} if category_filter is not None else None
    diagnostics = {"version": VERSION, "question": question, "currency": "CNY", "unit": "MINOR", "facts": facts,
        "comparison": comparison, "insights": [{"code": "POSTED_ECONOMIC_FLOW_BY_CURRENCY" if known else "PERIOD_NOT_COVERED", "severity": "NOTICE", "fact_refs": []}],
        "coverage": coverage, "read_only": True, "bank_state_changed": False, "model_calculated_money": False,
        "money_scope": "POSTED_ECONOMIC_ROWS_NOT_CURRENT_BALANCES", "historical_balance_change_known": False,
        "source_fingerprint": source_hash, "owner_scope_hash": canonical_hash({"sandbox_id": sandbox_id, "user_id": user_id}),
        "source_query": {"calendar_time": clock.calendar_time.isoformat(), "question": question,
            "include_statement_transactions": include_statement_transactions,
            "comparison_period": original_previous if question == "SPENDING_CHANGE" and period else None},
        "category_filter": category_meta, "category_labels_en": {key: CATEGORY_LABELS_EN.get(key, key) for key in categories},
        "largest_expense": _largest(rows, start, end, category_filter, coverage, source_hash), "review_candidates": []}
    if selection is not None:
        diagnostics["source_query"]["card_reference"] = selection["instrument_id"]
    if currency is not None:
        diagnostics["source_query"]["currency"] = currency
    if query_context_version is not None:
        diagnostics['source_query'][QUERY_CONTEXT_MARKER] = query_context_version
    if instrument_breakdown_version is not None:
        diagnostics['source_query']['instrument_breakdown_version'] = instrument_breakdown_version
        diagnostics['instrument_breakdown'] = instrument_breakdown(rows, totals['totals_by_currency'], known=known)
        labels = {'DEBIT': ('借记卡', 'Debit card'), 'CREDIT': ('信用卡', 'Credit card'), 'UNKNOWN': ('未标明卡种', 'Card type unspecified')}
        for group in diagnostics['instrument_breakdown']['groups']:
            for field, zh, en in (('consumption_minor', '消费', 'consumption'), ('fees_minor', '已登记费用', 'recorded fees'), ('refund_minor', '退款', 'refunds')):
                name = 'instrument_' + group['instrument_type'].lower() + '_' + group['currency'].lower() + '_' + field
                facts.append({'fact_id': 'facts:bill-diagnostics.' + name, 'source_id': 'facts:bill-diagnostics',
                    'field': name, 'value': group[field], 'currency': group['currency'], 'unit': 'MINOR',
                    'label': labels[group['instrument_type']][0] + zh,
                    'label_en': labels[group['instrument_type']][1] + ' ' + en})
    if question == "SAVING_OPTIONS":
        diagnostics["review_candidates"] = [{"category": name, "currency": item["currency"], "amount_minor": amount,
            "unit": "MINOR", "action": "REVIEW_ONLY", "guaranteed_saving_minor": None}
            for item in totals["totals_by_currency"] for name, amount in item["category_spending_minor"].items()]
    displayed = sorted(rows, key=lambda row: (row["occurred_at"], row["evidence_id"]), reverse=True)
    result = BillAnalysis(transaction_count=len(rows), spending_minor=cny.get("economic_spending_minor", 0),
        refund_minor=cny.get("refund_minor", 0), category_spending_minor=cny.get("category_spending_minor", {}),
        income_minor=cny.get("income_minor", 0), net_minor=cny.get("net_economic_change_minor", 0) + cny.get("movement_in_minor", 0) - cny.get("movement_out_minor", 0),
        movement_in_minor=cny.get("movement_in_minor", 0), movement_out_minor=cny.get("movement_out_minor", 0),
        category_income_minor=income_categories,
        monthly=[{**item, "month": item["period"]} for item in totals["monthly"]],
        daily=[{**item, "day": item["period"]} for item in totals["daily"]],
        anomalies=detect_billing_anomalies(rows), period_start=start.isoformat() if start else None,
        period_end=end.isoformat() if end else None, coverage=coverage, financial_insights=diagnostics,
        category_filter=category_meta, statement_transactions=[_public_row(row) for row in displayed[:50]] if include_statement_transactions else [],
        statement_transactions_truncated=include_statement_transactions and len(rows) > 50,
        billing_schema_version=VERSION, currency="CNY", totals_by_currency=totals["totals_by_currency"],
        billing_scope={"scope": "ALL_OWNED_CASH_AND_CREDIT", "scalar_fields_currency": "CNY", "currencies_combined": False,
            "cash_account_count": len(evidence["query_scope"]["account_ids"]), "credit_economic_rows_included": True,
            "historical_card_links_preserved": True},
        explanation="已按本人全部现金账户及信用经济流水汇总；各币种独立，兼容金额字段仅指人民币。消费与手续费记一次，信用还款属资金搬移，分期本金不新增消费；退款单列。期间净额不是当前余额。")
    if selection is not None:
        result.billing_scope["card_filter"] = {key: selection[key] for key in ("instrument_type", "display_last4")}
        result.billing_scope["scope"] = "SELECTED_OWNED_CARD"
        result.billing_scope["credit_economic_rows_included"] = selection["instrument_type"] == "CREDIT"
        result.explanation = "已按选中卡片的原始流水归属整理，历史记录不随当前绑卡账户改写；各币种独立，期间净额不是当前余额。"
    if currency is not None:
        result.billing_scope["currency_filter"] = currency
    if allow_recent_fallback:
        alternative = _recent_fallback(session, sandbox_id, account_id, message, result, evidence, clock,
            category_filter, include_statement_transactions)
        if alternative is not None:
            result = alternative
    return _seal(result)


def _recent_fallback(session, sandbox_id, account_id, message, original, evidence, clock, category, include_rows):
    from .bill_period_fallback import allows_recent_statement, FALLBACK_VERSION, _period_ref
    from .reports import period_boundaries
    if not allows_recent_statement(message) or not original.period_start or not original.period_end:
        return None
    start, end = datetime.fromisoformat(original.period_start), datetime.fromisoformat(original.period_end)
    local = clock.calendar_time.astimezone(SHANGHAI)
    if (start, end) != period_boundaries("month", local.year, local.month):
        return None
    # A narrowed category does not hide a real current-period posting and
    # thereby justify silently swapping another month into the current query.
    current = [row for row in evidence["rows"] if start <= datetime.fromisoformat(row["occurred_at"]) < end]
    empty = not current and original.coverage["status"] in {"UNKNOWN", "INSUFFICIENT"}
    movement_only = bool(current) and all(row["economic_kind"] in {"MOVEMENT", "RECLASSIFICATION"} for row in current)
    if not (empty or movement_only and original.coverage.get("basis") == "LIVE_ACTIVITY_ONLY"):
        return None
    prior = [row for row in evidence["rows"] if datetime.fromisoformat(row["occurred_at"]) < start]
    if not prior:
        return None
    latest = datetime.fromisoformat(prior[-1]["occurred_at"]).astimezone(SHANGHAI)
    displayed = period_boundaries("month", latest.year, latest.month)
    alternative = analyze_owned_bill(session, sandbox_id, account_id, "分析已有账单", period=displayed,
        calendar_time=clock.calendar_time, category_filter=category, include_statement_transactions=include_rows,
        card_reference=(evidence["query_scope"].get("card_filter") or {}).get("instrument_id"),
        currency=evidence["query_scope"].get("currency_filter"),
        instrument_breakdown_version=original.financial_insights['source_query'].get('instrument_breakdown_version'),
        query_context_version=original.financial_insights['source_query'].get('bill_query_context_version'))
    unfiltered_count = sum(displayed[0] <= datetime.fromisoformat(row["occurred_at"]) < displayed[1] for row in prior)
    if not alternative.transaction_count and category is None:
        return None
    metadata = {"version": FALLBACK_VERSION, "status": "APPLIED", "reason": "REQUESTED_PERIOD_ONLY_MOVEMENTS" if movement_only else "REQUESTED_PERIOD_NOT_COVERED",
        "requested": _period_ref(start, end), "displayed": _period_ref(*displayed),
        "requested_coverage": original.coverage, "available_transaction_count": alternative.transaction_count,
        "unfiltered_period_transaction_count": unfiltered_count,
        "read_only": True, "bank_state_changed": False}
    if movement_only:
        metadata["requested_activity"] = {"schema_version": "owned-bill-activity-v2", "transaction_count": len(current),
            "totals_by_currency": _summary(current, evidence)["totals_by_currency"]}
    alternative.period_fallback = metadata
    alternative.financial_insights["period_fallback"] = deepcopy(metadata)
    alternative.financial_insights["requested_question"] = original.financial_insights["question"]
    return alternative


def checked_owned_bill_analysis(session, sandbox_id, user_id, analysis):
    """Strict same-owner/full-source rebuild; never accepts a public DTO as proof."""
    def deny():
        raise DomainError("BILL_ECONOMIC_SOURCE_CHANGED", "账单来源或范围已变化，请重新查询。", 409)
    try:
        value = analysis.model_dump() if hasattr(analysis, "model_dump") else deepcopy(analysis)
        if not isinstance(value, dict) or value.get("billing_schema_version") != VERSION:
            deny()
        diagnostics = value["financial_insights"]
        if diagnostics.get("read_fingerprint") != canonical_hash(_signed_body(value)):
            deny()
        accounts = list(session.scalars(select(Account).where(Account.sandbox_id == sandbox_id, Account.user_id == user_id).order_by(Account.id)))
        if not accounts or upgraded_bill_owner(session, sandbox_id, accounts[0].id) != user_id:
            deny()
        if diagnostics.get("owner_scope_hash") != canonical_hash({"sandbox_id": sandbox_id, "user_id": user_id}):
            deny()
        query = diagnostics["source_query"]
        calendar = datetime.fromisoformat(query["calendar_time"])
        if calendar.tzinfo is None:
            deny()
        period = (datetime.fromisoformat(value["period_start"]), datetime.fromisoformat(value["period_end"])) if value["period_start"] else None
        if period and any(item.tzinfo is None for item in period):
            deny()
        expected = analyze_owned_bill(session, sandbox_id, accounts[0].id, "分析已有账单", period=period,
            calendar_time=calendar, question=query["question"], include_statement_transactions=query["include_statement_transactions"],
            category_filter=(value.get("category_filter") or {}).get("category"),
            card_reference=query.get("card_reference"), currency=query.get("currency"),
            instrument_breakdown_version=query.get('instrument_breakdown_version'),
            query_context_version=query.get('bill_query_context_version'),
            comparison_period=tuple(datetime.fromisoformat(query["comparison_period"][key]) for key in ("start", "end"))
                if query.get("comparison_period") else None)
        if _base_body(expected) != _base_body(value):
            retained=_retained_closed_economic_body(session,sandbox_id,user_id,value)
            if retained is None:
                deny()
            # Only the three derived global-revision/hash fields differ. The
            # complete original economic body, selector, period, currency,
            # rows, amounts, refunds and anomalies must stay exactly equal.
            current=_signed_body(expected)
            current['financial_insights'].pop('source_fingerprint')
            current['financial_insights']['largest_expense'].pop('source_fingerprint')
            current['financial_insights']['largest_expense'].pop('snapshot_hash')
            if retained!=current:
                deny()
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        deny()


def read_recent_owned_bill_fallback(session, sandbox_id, account_id, message, original, *, include_movement_only=False):
    """New-domain supplementary GET, never a mutation of saved query/receipt."""
    user_id = upgraded_bill_owner(session, sandbox_id, account_id)
    if user_id is None or original.period_fallback:
        return None
    try:
        checked_owned_bill_analysis(session, sandbox_id, user_id, original)
        if original.transaction_count and not include_movement_only:
            return None
        principal = _principal(sandbox_id, user_id)
        calendar = datetime.fromisoformat(original.financial_insights["source_query"]["calendar_time"])
        clock = owned_economic_clock(session, principal, calendar_time=calendar)
        evidence = collect_owned_billing_evidence(session, principal, EPOCH, calendar + timedelta(microseconds=1),
            business_time=calendar, historical_through=clock.historical_data_through,
            card_reference=original.financial_insights["source_query"].get("card_reference"),
            currency=original.financial_insights["source_query"].get("currency"))
        if original.financial_insights["source_query"].get("card_reference") or original.financial_insights["source_query"].get("currency"):
            clock = owned_economic_clock(session, principal, calendar_time=calendar, evidence=evidence)
        alternative = _recent_fallback(session, sandbox_id, account_id, message, original, evidence, clock,
            (original.category_filter or {}).get("category"), original.financial_insights["source_query"]["include_statement_transactions"])
        return _seal(alternative) if alternative else None
    except (DomainError, KeyError, ValueError, TypeError):
        return None


def scoped_bill_diagnostics(analysis):
    """Actual model facts keep their clock; private full-read proof stays server-side."""
    original = analysis.model_dump() if hasattr(analysis, "model_dump") else deepcopy(analysis)
    diagnostics = deepcopy(original.get("financial_insights")) if isinstance(original, dict) else None
    try:
        from .billing_presentation import owned_bill_display_recorded
        if not owned_bill_display_recorded(original) or diagnostics.get("read_fingerprint") != canonical_hash(_signed_body(original)):
            return diagnostics
        diagnostics.pop("read_fingerprint", None)
        diagnostics["scoped_projection_version"] = "bill-diagnostics-model-facts-v2"
        diagnostics["scoped_projection_hash"] = canonical_hash(diagnostics)
        return diagnostics
    except (ValueError, TypeError, KeyError, AttributeError):
        return diagnostics


def normalize_scoped_bill_diagnostics(value):
    """Hash-comparison-only projection; never pass this result to the model.

    The exact registered self-hash must validate before clock normalization.
    All amounts, rows, source revisions, owner, policy and expiry stay covered.
    Invalid projections keep their raw identity rather than hiding tampering.
    """
    try:
        if (not isinstance(value, dict) or value.get("version") != VERSION
                or value.get("scoped_projection_version") != "bill-diagnostics-model-facts-v2"
                or "read_fingerprint" in value or value.get("read_only") is not True
                or value.get("bank_state_changed") is not False or value.get("model_calculated_money") is not False
                or value.get("currency") != "CNY" or value.get("unit") != "MINOR"
                or value.get("scoped_projection_hash") != canonical_hash({key: item for key, item in value.items() if key != "scoped_projection_hash"})):
            return None
        largest = value["largest_expense"]
        if largest.get("version") != LARGEST_VERSION or largest.get("snapshot_hash") != canonical_hash({key: item for key, item in largest.items() if key != "snapshot_hash"}):
            return None
        clock = datetime.fromisoformat(value["source_query"]["calendar_time"])
        if clock.tzinfo is None:
            return None
        day = clock.astimezone(SHANGHAI).date()
        normalized = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).isoformat()
        current = deepcopy(value)
        coverages = [current["coverage"], current["largest_expense"]["coverage"]]
        meta = current.get("period_fallback")
        if isinstance(meta, dict) and isinstance(meta.get("requested_coverage"), dict):
            coverages.append(meta["requested_coverage"])
        for coverage in coverages:
            if coverage.get("calendar_date") != day.isoformat() or datetime.fromisoformat(coverage["calendar_time"]) != clock:
                return None
        for coverage in coverages:
            coverage["calendar_time"] = normalized
        current["source_query"]["calendar_time"] = normalized
        current["largest_expense"]["snapshot_hash"] = canonical_hash({key: item for key, item in current["largest_expense"].items() if key != "snapshot_hash"})
        current["scoped_projection_hash"] = canonical_hash({key: item for key, item in current.items() if key != "scoped_projection_hash"})
        return current
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def bill_result_fact_entities(analysis):
    """Pure snapshot adapter, caller MUST first call checked_owned_bill_analysis."""
    if not isinstance(analysis, dict) or analysis.get("billing_schema_version") != VERSION:
        return []
    output = []
    def add(field, value, label, record_id, facts, ordinal=None, **extra):
        output.append({"read_kind": "BILL_ANALYSIS", "domain": "BILLS", "field": field, "value": value,
            "label": label, "record_id": record_id, "record_version": None, "ordinal": ordinal,
            "live_record_hash": canonical_hash(facts), "facts": facts, **extra})
    period = {key: analysis.get(key) for key in ("period_start", "period_end", "coverage", "category_filter")}
    scope = analysis.get("billing_scope") or {}
    if "card_filter" in scope or "currency_filter" in scope:
        period["billing_scope"] = deepcopy(scope)
    for ordinal, row in enumerate(analysis.get("statement_transactions", []), 1):
        facts = {**row, **period, "statement_transactions_truncated": analysis.get("statement_transactions_truncated") is True}
        add("transaction", row["evidence_id"], row["merchant"], row["evidence_id"], facts, ordinal)
    for group in analysis["financial_insights"]["largest_expense"]["by_currency"]:
        if group["status"] != "FOUND":
            continue
        snapshot_hash = analysis["financial_insights"]["largest_expense"]["snapshot_hash"]
        if group["tie_count"] > 1:
            facts = {**period, "amount_minor": -group["maximum_expense_minor"], "currency": group["currency"],
                "tie_count": group["tie_count"], "records": [{key: row[key] for key in ("merchant", "category", "occurred_at", "amount_minor", "currency")}
                    for row in group["records"]], "largest_scope": "FULL_RECORDED_WINDOW_SAME_CURRENCY",
                "largest_snapshot_hash": snapshot_hash}
            add("largest_expense_group", group["currency"] + "并列最大支出", group["currency"] + "并列最大支出",
                "largest-group:" + group["currency"] + ":" + snapshot_hash, facts, authoritative_largest=True)
            continue
        for row in group["records"]:
            existing = next((item for item in output if item["record_id"] == row["evidence_id"]), None)
            if existing is None:
                add("transaction", row["evidence_id"], row["merchant"], row["evidence_id"], {**row, **period})
                existing = output[-1]
            existing["authoritative_largest"] = True
            existing["facts"].update(largest_scope="FULL_RECORDED_WINDOW_SAME_CURRENCY", largest_snapshot_hash=snapshot_hash,
                largest_tie_count=group["tie_count"])
            existing["live_record_hash"] = canonical_hash(existing["facts"])
    ordinal = 0
    for totals in analysis.get("totals_by_currency", []):
        for name, amount in sorted(totals["category_spending_minor"].items(), key=lambda pair: (-pair[1], pair[0])):
            ordinal += 1
            facts = {**period, "category": name, "spending_minor": amount, "currency": totals["currency"]}
            add("category_filter", name, f"{name}（{totals['currency']}）", "category:" + canonical_hash(facts), facts, ordinal)
    from .bill_query_context import summary_entity
    summary = summary_entity(analysis)
    if summary is not None:
        output.insert(0, summary)
    return output


def owned_bill_report(session, sandbox_id, account_id, *, period_kind, year, month, start, end, calendar_time,
        card_reference=None, currency=None):
    """Full export uses the identical source universe, never legacy account rows."""
    analysis = analyze_owned_bill(session, sandbox_id, account_id, "分析账单", period=(start, end),
        calendar_time=calendar_time, include_statement_transactions=True, card_reference=card_reference, currency=currency)
    user_id = upgraded_bill_owner(session, sandbox_id, account_id)
    clock = owned_economic_clock(session, _principal(sandbox_id, user_id), calendar_time=calendar_time)
    evidence = collect_owned_billing_evidence(session, _principal(sandbox_id, user_id), start, end,
        business_time=clock.calendar_time, historical_through=clock.historical_data_through,
        card_reference=card_reference, currency=currency)
    reconciliations = []
    for total in analysis.totals_by_currency:
        rows = [row for row in evidence["rows"] if row["currency"] == total["currency"]]
        recorded_net = sum(row["amount_minor"] for row in rows if row["economic_kind"] != "RECLASSIFICATION")
        expected_net = total["net_economic_change_minor"] + total["movement_in_minor"] - total["movement_out_minor"]
        reconciliations.append({"currency": total["currency"], "recorded_net_minor": recorded_net,
            "reported_net_minor": expected_net, "category_spending_minor": sum(total["category_spending_minor"].values()),
            "reported_spending_minor": total["economic_spending_minor"],
            "balanced": recorded_net == expected_net and sum(total["category_spending_minor"].values()) == total["economic_spending_minor"],
            "cash_flow_net_minor": total["cash_flow_net_minor"], "scope": "POSTED_ECONOMIC_ROWS_NOT_CURRENT_BALANCES"})
    report = {"report_version": "owned-economic-bill-report-v2", "synthetic": True, "currency": "CNY",
        "currencies_combined": False, "account_id": None, "scope": analysis.billing_scope,
        "business_time": clock.data_through.isoformat(), "calendar_time": clock.calendar_time.isoformat(),
        "coverage": analysis.coverage,
        "period": {"kind": period_kind, "year": year, "month": month if period_kind == "month" else None,
            "label": f"{year}年{month}月" if period_kind == "month" else f"{year}年",
            "start": start.isoformat(), "end": end.isoformat(), "time_zone": "Asia/Shanghai", "boundary": "[start,end)",
            "partial": analysis.coverage["status"] != "COMPLETE", "status": "NOT_STARTED" if clock.calendar_time < start else "IN_PROGRESS" if clock.calendar_time < end else "CLOSED",
            "data_through": analysis.coverage.get("effective_end")},
        "summary": analysis.model_dump(), "currency_breakdown": {"totals_by_currency": analysis.totals_by_currency,
            "currencies_combined": False, "foreign_cash_wallet_movements_included": False},
        "transactions": [_public_row(row) for row in sorted(evidence["rows"], key=lambda row: (row["occurred_at"], row["evidence_id"]), reverse=True)],
        "reconciliation": {"balanced": all(row["balanced"] for row in reconciliations),
            "by_currency": reconciliations, "scope": "ALL_OWNED_CANONICAL_ECONOMIC_POSTINGS", "currencies_combined": False,
            "explanation": "各币种分别核对全部经济流水；还款不是再次消费，分期本金不重复计入，期间净额不等于当前余额。"}}
    if evidence["coverage"].get("scope_status") == "USD_DEBIT_WALLET_SOURCE_UNAVAILABLE":
        report["reconciliation"].update(balanced=None, status="SOURCE_UNAVAILABLE",
            explanation="借记美元钱包流水未接入当前来源，不据空清单宣称已对账或零消费。")
    from .customer_presentation import customer_business_value
    return customer_business_value(report)
