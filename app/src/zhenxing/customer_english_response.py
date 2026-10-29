"""Deterministic English result narration. Amounts/IDs/statuses are not translated."""
from decimal import Decimal, InvalidOperation

from .customer_financial_narrative import amount, financial_summary
from .redaction import redact_credentials


def _read(kind, result, trace):
    if kind in {'CREDIT_VIEW','CREDIT_STATEMENT'}:
        from .credit_presentation import credit_summary
        return credit_summary(kind,result,english=True)
    if kind in {'OCCASION_MEMBER_VIEW','OCCASION_VIEW','LIFE_PLAN_VIEW','MERCHANT_CATALOG','MERCHANT_ORDERS'}:
        from .life_plan_presentation import life_action_summary
        return life_action_summary(kind,result,english=True)
    if kind=='AA_VIEW':return 'Your own AA records are ready.',f"{len(result.get('payable_obligations',[]))} payable records and {len(result.get('collections',[]))} collections; this read does not pay."
    from .customer_response import _rows, _int
    if kind == "BILL_ANALYSIS":
        analysis = trace.get("analysis") or {}
        from .customer_bill_fallback import checked_bill_fallback
        if checked_bill_fallback(trace.get("supplementary_bill")):
            analysis = trace["supplementary_bill"]
        specific = financial_summary(analysis, english=True)
        if specific:
            from .customer_reference_views import reference_summary
            selected=analysis.get('category_filter')
            scoped=isinstance(selected,dict) and selected.get('version')=='bill-category-filter-v1' and selected.get('basis')=='CATEGORY_EXACT_MATCH' \
                and selected.get('currency')=='CNY' and selected.get('read_only') is True and selected.get('bank_state_changed') is False \
                and (analysis.get('financial_insights') or {}).get('category_filter')==selected and isinstance(selected.get('category'),str)
            from .customer_response import _text
            category_intro=f" Only {_text(selected['category'])} transactions are included; these are not full-statement totals." if scoped else ''
            return specific[0]+category_intro, specific[1] + reference_summary(analysis, english=True)
        return "Your recorded bill summary is ready.", f"The query returned {analysis.get('transaction_count', 0)} recorded transactions."
    if kind == "SUBSCRIPTION_LIST":
        from .customer_response import _subscription_summary
        return _subscription_summary(result, english=True)
    if kind == "CARD_STATUS":
        rows = _rows(result.get("cards")) or []
        active = sum(row.get("status") == "ACTIVE" for row in rows)
        locked = sum(row.get("status") == "LOCKED" for row in rows)
        detail = f"{len(rows)} cards are recorded; {active} active and {locked} temporarily locked. " if all(isinstance(row.get("status"), str) and row["status"] in {"ACTIVE", "LOCKED", "LOST", "CLOSED"} for row in rows) else f"{len(rows)} card records are available, but some statuses are incomplete or unrecognized. "
        credit_rows=_rows(result.get("credit_cards"))
        if credit_rows:detail="Debit cards: "+detail+f"Another {len(credit_rows)} credit cards are listed separately; credit limits are not deposit cash. "
        from .customer_response import _card_balances_complete
        balances_complete = _card_balances_complete(rows)
        from .customer_response import _card_balance_scope_detail
        detail += _card_balance_scope_detail(result, english=True)
        return ("Your card information and linked account balances are ready." if balances_complete
            else "Card information returned; some balances remain unverified."), detail.strip()
    if kind == "WEALTH_COMPARE":
        rows = _rows(result.get("products")) or []
        suitable = sum(row.get("suitable") is True for row in rows)
        detail = f"{len(rows)} products are listed; {suitable} meet the recorded suitability conditions. " if all(type(row.get("suitable")) is bool for row in rows) else f"{len(rows)} products are listed, but some suitability checks are incomplete. "
        return "The product comparison is ready.", detail + "Suitability does not guarantee principal or returns; transaction checks still apply."
    if kind == "FRAUD_ANALYZE":
        level = (result.get("risk") or {}).get("risk_level")
        answer={"HIGH": ("High-risk signals need attention.", "Stop contact and transfers. Do not share your screen or verification codes. Check independently through the bank or platform's official channels. If money has been lost, keep the evidence and contact your bank and the police. Ordinary confirmation cannot override a risk restriction."),
                "LOW": ("No strong risk signal was found in the supplied records.", "This is not a guarantee of safety. Verify the recipient, purpose and information source."),
                "UNKNOWN": ("There is not enough information to determine the risk.", "Verify the recipient and purpose before considering payment.")}[level]
        from .customer_response import deferred_security_write_notice
        return answer[0],answer[1]+' '+deferred_security_write_notice(trace,english=True)
    if kind == "FAMILY_VIEW":
        rows = _rows(result.get("delegations")) or []
        return "The family assistance scope is ready.", f"{len(rows)} delegation records are available. A helper cannot execute banking actions for the owner; the owner must review and independently confirm."
    if kind == "GOAL_VIEW":
        from .customer_response import _goal_what_if_summary
        what_if = _goal_what_if_summary(result, english=True)
        if what_if:
            return what_if
        rows = _rows(result.get("goals")) or []
        return "Your goals and cash-flow information are ready.", f"{len(rows)} goals are available. Forecasts depend on recorded coverage and do not guarantee future income or automatically reserve funds."
    if kind == "CARBON_VIEW":
        from .customer_response import carbon_query_summary
        return carbon_query_summary(result, english=True)
    if kind == "FX_QUOTE":
        market = result.get("market_rate") or {}
        detail = "See the recorded reference rate and conversion conditions. "
        try:
            rate = Decimal(market.get("rate", ""))
            if rate.is_finite() and rate > 0 and market.get("base_currency") == "USD" and market.get("quote_currency") == "CNY":
                detail = f"Recorded USD/CNY reference rate: {rate}. "
        except (InvalidOperation, TypeError):
            pass
        return "The reference exchange rate is ready.", detail + "It is a dated daily reference, not a guaranteed bank execution rate. No money was exchanged."
    return "Your query results are ready.", "See the verified business details. No new banking action was executed by this query."


def _write(kind, result):
    if kind=='OWN_ACCOUNT_TRANSFER':
        from .own_transfer_presentation import own_transfer_summary
        return own_transfer_summary(kind,result,english=True)
    if kind.startswith('CREDIT_') or result.get('schema_version')=='credit-card-control-v1':
        from .credit_presentation import credit_summary
        summary=credit_summary(kind,result,english=True)
        if summary:return summary
    if kind in {'OCCASION_MEMBER_SET','OCCASION_SET','LIFE_PLAN_AUTHORIZE','LIFE_PLAN_CANCEL','MERCHANT_PURCHASE'}:
        from .life_plan_presentation import life_action_summary
        return life_action_summary(kind,result,english=True)
    if kind=='AA_PAY':return 'Your AA share was paid.', 'Only your verified share was posted; this does not establish that other participants paid.'
    if kind in {'SUBSCRIPTION_START', 'SUBSCRIPTION_RENEW'}:
        from .customer_response import _subscription_write_summary
        return _subscription_write_summary(kind, result, english=True)
    if kind == 'SUBSCRIPTION_CANCEL' and result.get('auto_renew') is False and result.get('entitlement_preserved') is True and type(result.get('refunded_minor')) is int and result.get('refunded_minor') == 0:
        return 'Automatic renewal was turned off.', 'Existing paid access keeps its original expiry. No refund was issued, and other subscriptions were not affected.'
    if kind in {"TRANSFER", "TRANSFER_SCHEDULE"}:
        value = amount(result.get("amount_minor"), result.get("currency", "CNY"), True)
        if result.get("transfer_channel") == "EXTERNAL_SIM":
            return "The external debit was recorded.", "The recipient's external credit has not been independently verified. Do not treat this as proof that the recipient received the money."
        return "The transfer was posted.", (f"Recorded amount: {value}. " if value else "Check the recorded amount below. ") + "Use the corresponding debit/credit records as the receipt."
    if kind in {"FX_TRANSFER", "FX_EXCHANGE"}:
        debit = amount(result.get("debit_minor"), result.get("source_currency"), True)
        credit = amount(result.get("credit_minor"), result.get("target_currency"), True)
        return ("The currency transfer was posted on the confirmed route." if kind == "FX_TRANSFER" else "Your currency exchange was posted."), \
               (f"Debited or exchanged out: {debit}; credited or exchanged in: {credit}. " if debit and credit else "Check each recorded currency leg. ") + "CNY and USD are accounted for separately."
    descriptions = {
        "CARD_LOCK": ("The card was temporarily locked.", "A temporary lock is not a loss report. Unlocking requires a separate review and confirmation."),
        "CARD_UNLOCK": ("The card unlock result was recorded.", "Check its current verified status."),
        "CARD_REPORT_LOSS": ("The card loss report was recorded.", "This is different from a temporary lock. Check the replacement options and current status."),
        "CARD_REPLACEMENT": ("The replacement request was accepted.", "This does not mean a physical card has been issued or shipped."),
        "CARD_SET_LIMIT": ("The card limit was updated.", "See the verified limit. Other payment checks still apply."),
        "WEALTH_BUY": ("The investment purchase was posted.", "See the holding and money records. Suitability does not guarantee principal or returns."),
        "WEALTH_REDEEM": ("The redemption was posted.", "See the actual units and money changes. This is not a promise of future returns."),
        "SUBSCRIPTION_CANCEL": ("The supported subscription cancellation was recorded.", "Other services were not cancelled. Unconnected external services still need provider verification."),
        "FAMILY_GRANT": ("The family delegation was saved.", "The helper still cannot execute banking actions for the owner. Check its scope, limit and expiry."),
        "FAMILY_REVOKE": ("The family delegation was revoked.", "The old permission cannot be reused; completed business records remain intact."),
        "FAMILY_PROPOSE": ("The assistance proposal was saved for the owner's review.", "No transfer or card action was executed. The owner must independently confirm any bank action."),
        "GOAL_CREATE": ("The savings goal was created.", "Creating a goal did not debit or reserve funds."),
        "GOAL_RESERVE": ("The goal reservation was updated.", "A reservation affects available spending capacity, not total cash balance; it is not an external payment."),
        "GOAL_RELEASE": ("The goal reservation was released.", "Available spending capacity was restored. No external payment was made."),
        "CARBON_CLAIM": ("Activity points were recorded.", "Points are not cash or certified carbon assets. The same activity source cannot be claimed twice."),
        "CARBON_REDEEM": ("The activity benefit was exchanged.", "This is not cash, a verified saving, a certified offset or proof of external merchant acceptance."),
        "AA_REQUEST": ("The split-payment collection request was created.", "Participants still need to pay themselves. Creating a request is not proof of receiving funds."),
        "CONTACT_BIND": ("Your personal recipient label was saved.", "The label applies only to you. Saving it did not make a payment."),
    }
    return descriptions.get(kind, ("The business result was recorded.", "See the verified details below."))


def english_assistant_response(trace):
    from .customer_response import AssistantResponse, ACTION_PRESENTATIONS, _recorded, _result, _identifier
    from .intelligence import READ_KINDS
    nodes = [node for node in trace.get("nodes", []) if isinstance(node, dict)]
    kinds = [node.get("action") for node in nodes]
    known = [kind for kind in kinds if kind in ACTION_PRESENTATIONS]
    details = list(dict.fromkeys(ACTION_PRESENTATIONS[kind][0] for kind in known))
    detail_kind = details[0] if len(details) == 1 else "MIXED" if details else "NONE"
    readonly = bool(kinds) and all(kind in READ_KINDS for kind in kinds)

    def response(intro, detail, state_kind, card_detail=None, readonly=readonly):
        return AssistantResponse(intro=redact_credentials(intro), detail=redact_credentials(detail) if detail else None,
            card_detail=redact_credentials(card_detail) if card_detail else None,
            detail_kind=detail_kind, state_kind=state_kind, read_only=readonly, reply_language="en").model_dump()

    state = trace.get("state")
    if state == "NEED_CLARIFICATION":
        from .customer_reply_gateway import naturalize_needed_questions, needed_question_sources
        raw = needed_question_sources(trace)
        questions = naturalize_needed_questions(raw,trace.get("customer_needed_fields"),"en",user_message=trace.get('customer_user_message',trace.get('message','')))
        return response(questions[0] if questions else "What would you like to check or arrange?", "\n".join(questions[1:])[:1800] or None, "PENDING")
    early = {
        "UNKNOWN": ("The outcome is not yet known.", "Check the original request first. Do not resend a payment because of a timeout or assume it failed.", "UNKNOWN"),
        "EXECUTING": ("Your confirmed request is being processed.", "The result will be shown when verified. Do not submit the same payment again.", "EXECUTING"),
        "FAILED": ("This request was not completed.", "Review the cause and any completed results. A failure notice does not undo earlier payments.", "FAILED"),
        "CLOSED": ("This request is closed.", "Unexecuted steps will not continue; completed transactions and receipts remain intact.", "CLOSED"),
        "PARTIAL": ("Some results are available; other items still need attention.", "Check each item's verified status. Do not treat unfinished steps as completed.", "PARTIAL"),
    }
    if state in early:
        return response(*early[state])
    if state in {"QUEUED", "UNDERSTANDING", "PLANNING"}:
        return response("I am reading the requested information." if readonly else "Your request is being prepared.",
            "The result will be organized once returned; this query does not change banking data." if readonly else "You will receive verified information or a proposal to review before any bank action.", "PROGRESS")
    if state == "AWAITING_CONFIRMATION" or trace.get("pending_confirmation"):
        from .customer_deferred_preparation import deferred_preparation_notice
        later=deferred_preparation_notice(trace,english=True)
        detail="This step has not been executed. A chat reply such as 'yes' does not replace the bank confirmation process."
        return response("Please review the confirmation card before proceeding.", detail+(' '+later if later else ''), "PENDING")
    if state == "WAITING":
        scheduled = any(node.get("action") == "TRANSFER_SCHEDULE" and _identifier(_result(node), "schedule_id") for node in nodes)
        return response("The scheduled transfer is registered and waiting for its date." if scheduled else "This request is waiting for its agreed condition.",
            "Registration is not payment. The recipient and funds will be checked again before execution.", "SCHEDULED")
    if state != "SUCCEEDED":
        return response("Waiting for the latest verified status.", "Use the actual business result rather than a message or loading indicator.", "PROGRESS")
    if len(nodes) == 1 and kinds[0] in {'SUBSCRIPTION_START','SUBSCRIPTION_RENEW'} and (trace.get('outcome') or {}).get('verified') is True \
        and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT':
        result=_result(nodes[0])
        if result.get('no_effect_kind') == 'REGISTERED_UNAVAILABLE_GUIDANCE' and result.get('can_start_here') is False \
            and result.get('can_renew_here') is False and type(result.get('charged_minor')) is int and result['charged_minor']==0 \
            and not result.get('payment_id') and not result.get('transaction_id'):
            return response('This service cannot currently be started or renewed here.','Check its status and options with the provider. No subscription or charge was made.','GUIDANCE',readonly=True)
    if len(nodes) == 1 and kinds[0] == 'SUBSCRIPTION_START':
        from .customer_response import _subscription_already_active, _text
        if (trace.get('outcome') or {}).get('verified') is True and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT' and _subscription_already_active(_result(nodes[0])):
            if _result(nodes[0]).get('no_effect_kind') == 'START_PAID_RIGHTS_ACTIVE':
                return response(f"Your paid access to {_text(_result(nodes[0]).get('merchant'))} remains valid.", 'No duplicate charge or extension was made. Automatic renewal remains off.', 'QUERY_RESULT', readonly=True)
            return response(f"{_text(_result(nodes[0]).get('merchant'))} is already active.", 'Only its current status was read. No duplicate subscription or charge was made.', 'QUERY_RESULT', readonly=True)
    if len(nodes) == 1 and kinds[0] == "SUBSCRIPTION_CANCEL":
        result = _result(nodes[0])
        if (trace.get('outcome') or {}).get('verified') is True and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT' \
            and result.get('already_cancelled') is True and result.get('auto_renew') is False and result.get('status') == 'ACTIVE' \
            and result.get('entitlement_preserved') is True and _identifier(result,'subscription_id') and type(result.get('refunded_minor')) is int and result['refunded_minor']==0:
            return response('Automatic renewal was already off.','Only its status was read. No cancellation or refund was repeated, and the original paid-access expiry is unchanged.','QUERY_RESULT',readonly=True)
        if result.get("source_type") == "EXTERNAL_UNCONNECTED" and result.get("can_cancel_here") is False:
            return response("This external service cannot be cancelled here.", "Check and stop it through the provider. This reply did not cancel the external service.", "GUIDANCE")
        if (trace.get("outcome") or {}).get("verified") is True and result.get("already_cancelled") is True and result.get("status") == "CANCELLED" and _identifier(result, "subscription_id"):
            return response("The subscription was already cancelled; its status was read.", "No cancellation was executed again, and no other service was affected.", "QUERY_RESULT")
    recorded = [node for node in nodes if _recorded(node, trace)]
    if not nodes or len(recorded) != len(nodes) or (trace.get("outcome") or {}).get("verified") is not True:
        return response("Returned information still needs outcome verification.", "There is not enough verified receipt evidence to claim every step completed. Do not resend payment from this message alone.", "UNVERIFIED")
    from .customer_bill_read_projection import bill_node_trace
    summaries = [(_read(node["action"], _result(node), bill_node_trace(trace, node) if node['action'] == 'BILL_ANALYSIS' else trace)
        if node["action"] in READ_KINDS else _write(node["action"], _result(node))) for node in recorded]
    intro, detail = summaries[0] if len(summaries) == 1 else ("Your query results are ready." if readonly else "The verified business results are ready.", "\n".join(a + " " + b for a, b in summaries)[:2300])
    state_kind = "QUERY_RESULT" if readonly else "PROPOSAL_SAVED" if all(kind == "FAMILY_PROPOSE" for kind in kinds) else "COLLECTION_CREATED" if all(kind == "AA_REQUEST" for kind in kinds) else "CONTACT_SAVED" if all(kind == "CONTACT_BIND" for kind in kinds) else "COMMITTED"
    card_detail = None
    if len(recorded) == 1 and kinds == ["SUBSCRIPTION_LIST"]:
        from .customer_response import _subscription_summary
        card_detail = _subscription_summary(_result(recorded[0]), english=True, compact=True)[1]
    return response(intro, detail, state_kind, card_detail=card_detail)
