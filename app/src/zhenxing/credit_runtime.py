"""Credit actions through the ordinary plan, confirmation and ToolGateway gate."""
from datetime import timedelta

from sqlalchemy import select

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError
from .credit_billing import (create_credit_installment, owned_credit_card, repay_credit,
    installment_pricing, installment_terms, settle_credit_installment, statement_view)
from .credit_lifecycle import control_credit_card, submit_credit_application
from .credit_models import CreditApplication, CreditCard, CreditFeeCharge, CreditInstallment, CreditObligation, PaymentRecord
from .credit_service import _facility, _fail, credit_view
from .customer_profile_common import require_profile_actor
from .models import Confirmation, EvidenceSnapshot, OutboxMessage, TaskNode, ToolCall, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task
from .credit_atomic import atomic_domain


ACTION_TOOLS = {"CREDIT_VIEW": "credit.view.v1", "CREDIT_STATEMENT": "credit.statement.v1",
    "CREDIT_REPAY": "credit.repay.v1", "CREDIT_INSTALLMENT": "credit.installment.v1",
    "CREDIT_SETTLE_INSTALLMENT": "credit.settle_installment.v1", "CREDIT_APPLY": "credit.apply.v1",
    "CREDIT_LIMIT_REQUEST": "credit.limit_request.v1", "CREDIT_ACTIVATE": "credit.activate.v1"}
CONTROL_ACTIONS = {"CARD_LOCK": "LOCK", "CARD_UNLOCK": "UNLOCK", "CARD_REPORT_LOSS": "REPORT_LOSS",
    "CARD_REPLACEMENT": "REQUEST_REPLACEMENT", "CARD_SET_LIMIT": "SET_LIMIT"}
ACTION_TOOLS.update({kind: "credit.card.control.v1" for kind in CONTROL_ACTIONS})
READ_ACTIONS = {"CREDIT_VIEW", "CREDIT_STATEMENT"}
WRITE_ACTIONS = set(ACTION_TOOLS)-READ_ACTIONS
TOOL_DEFINITIONS = {tool: {"read_only": action in READ_ACTIONS, "risk_level": "L2" if action in WRITE_ACTIONS else "L1",
    "owner": "credit"} for action, tool in ACTION_TOOLS.items()}


def _principal(task):
    from .services import Principal
    return Principal(task.user_id, task.sandbox_id)


def is_credit_reference(session, principal, reference):
    """Exact owned union resolution, no text regex/source guess or fallback."""
    if not reference: return False
    from .payment_instruments import resolve_source_instrument
    return resolve_source_instrument(session, principal, reference, purpose="READ")["instrument_type"] == "CREDIT"


def _debt_card_reference(session, principal, kind, slots, explicit_reference):
    """Resolve an already selected debt to its original card, never a default.

    The exact owned payment is the identity source. This only supplies a plan
    selector; eligibility, versioning, fees and confirmation remain mandatory.
    """
    payment_id = slots.get("payment_id") if kind == "CREDIT_INSTALLMENT" else None
    obligation = None
    if kind == "CREDIT_SETTLE_INSTALLMENT" and slots.get("installment_id"):
        item = session.get(CreditInstallment, slots["installment_id"])
        if not item:
            _fail("CREDIT_INSTALLMENT_NOT_OWNED", status=403)
        _facility(session, principal, item.credit_account_id, lock=False)
        obligation = session.get(CreditObligation, item.obligation_id)
        if not obligation or obligation.credit_account_id != item.credit_account_id \
                or obligation.installment_id != item.id:
            _fail("CREDIT_INSTALLMENT_NOT_OWNED", status=403)
        payment_id = obligation.payment_id
    if not payment_id:
        return explicit_reference
    payment = session.get(PaymentRecord, payment_id)
    if not payment or (payment.sandbox_id, payment.user_id, payment.instrument_type) != \
            (principal.sandbox_id, principal.user_id, "CREDIT"):
        _fail("CREDIT_PAYMENT_NOT_OWNED", status=403)
    card = owned_credit_card(session, principal, payment.instrument_id)
    if payment.instrument_id != "credit:" + card.id or payment.credit_account_id != card.credit_account_id \
            or obligation is not None and obligation.credit_account_id != payment.credit_account_id:
        _fail("CREDIT_PAYMENT_NOT_OWNED", status=403)
    if explicit_reference and owned_credit_card(session, principal, explicit_reference).id != card.id:
        _fail("CREDIT_PAYMENT_NOT_OWNED", status=403)
    return "credit:" + card.id


@atomic_domain
def prepare_credit_task(session, task, action, context=None):
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind, slots = action.get("kind"), action.get("slots") or {}
    if hasattr(slots, "model_dump"): slots = slots.model_dump(exclude_none=True)
    if kind not in ACTION_TOOLS: _fail("CREDIT_ACTION_UNSUPPORTED", status=422)
    principal = _principal(task); require_profile_actor(session, principal)
    from .credit_preparation_contract import (application_facility, missing_credit_fields, prepare_credit_clarification)
    missing = missing_credit_fields(kind, slots)
    if missing:
        return prepare_credit_clarification(task, kind, missing)
    reference = slots.get("card_reference") or slots.get("card_number") or slots.get("card_last4")
    if kind in {"CREDIT_INSTALLMENT", "CREDIT_SETTLE_INSTALLMENT"}:
        reference = _debt_card_reference(session, principal, kind, slots, reference)
    application = None
    if kind == "CREDIT_ACTIVATE" and slots.get("application_id"):
        application = session.get(CreditApplication, slots["application_id"])
        if not application or (application.sandbox_id, application.user_id, application.kind, application.status) != \
                (principal.sandbox_id, principal.user_id, "CARD", "APPROVED") or not application.card_id:
            _fail("CREDIT_ACTIVATION_APPLICATION_INVALID", status=403)
        if reference and owned_credit_card(session, principal, reference).id != application.card_id: _fail("CREDIT_ACTIVATION_APPLICATION_INVALID")
        reference = "credit:"+application.card_id
    currency = slots.get("currency") or "CNY"
    args = {}
    if kind in READ_ACTIONS:
        try:
            result = credit_view(session, principal, reference) if kind == "CREDIT_VIEW" else statement_view(
                session, principal, reference, currency=slots.get("currency"))
        except DomainError as error:
            if error.code != 'CREDIT_CARD_REFERENCE_REQUIRED' or reference:
                raise
            return prepare_credit_clarification(task, kind, ['card_reference'])
    else:
        no_card_application = kind == "CREDIT_APPLY" and not reference
        if no_card_application:
            try: facility = application_facility(session, principal)
            except DomainError as error:
                if error.code != 'CREDIT_APPLICATION_POOL_SELECTION_REQUIRED': raise
                return prepare_credit_clarification(task, kind, ['card_reference'])
            card = None
        else:
            card = owned_credit_card(session, principal, reference)
            facility = _facility(session, principal, card.credit_account_id, lock=False)
        args = {"card_reference": "credit:" + card.id if card else None, "expected_facility_version": facility.version if facility else None,
            "expected_card_version": card.version if card else None, "currency": currency}
        if no_card_application and facility is not None:
            args['application_credit_account_id'] = facility.id
        if application: args["application_id"] = application.id
        if kind in {"CREDIT_REPAY", "CREDIT_SETTLE_INSTALLMENT"}:
            from .payment_instruments import resolve_source_instrument
            if kind == "CREDIT_SETTLE_INSTALLMENT":
                item = session.get(CreditInstallment, slots.get("installment_id"))
                if not item or item.credit_account_id != facility.id or item.status != "ACTIVE": _fail("CREDIT_INSTALLMENT_NOT_OWNED", status=403)
                currency = item.currency; args["currency"] = currency
            source_currency = slots.get("source_currency") or currency
            source = resolve_source_instrument(session, principal, slots.get("source_card"), source_currency, purpose="REPAYMENT")
            args["source_snapshot"] = source
            # Never consume an LLM/client-provided raw rate or quote object.
            args["fx_quote_id"] = None
            if kind == "CREDIT_REPAY":
                view = statement_view(session, principal, args["card_reference"], currency=currency)["statements_by_currency"][0]
                mode = slots.get("repayment_mode") or "AMOUNT"
                if mode not in {"FULL", "MINIMUM", "AMOUNT"}: _fail("CREDIT_REPAYMENT_MODE_INVALID", status=422)
                amount = view["outstanding_minor"] if mode == "FULL" else view["minimum_due_minor"] if mode == "MINIMUM" else slots.get("amount_minor")
                if amount == 0: _fail("CREDIT_NOTHING_TO_REPAY", "该币种当前没有对应的应还金额，未扣款。", 422)
                from .payment_instruments import _amount
                _amount(amount)
                if amount > view["outstanding_minor"]: _fail("CREDIT_REPAYMENT_EXCEEDS_DEBT", status=422)
                args.update(amount_minor=amount, repayment_mode=mode)
            else:
                obligation = session.get(CreditObligation, item.obligation_id)
                fees = sum(session.scalars(select(CreditFeeCharge.remaining_minor).where(CreditFeeCharge.installment_id == item.id)))
                args.update(installment_id=item.id, amount_minor=obligation.remaining_minor+fees, currency=item.currency)
            if source_currency != currency:
                if (source_currency, currency) != ("CNY", "USD"): _fail("CREDIT_REPAYMENT_CURRENCY_MISMATCH", status=422)
                from .credit_service import prepare_credit_fx_quote
                rate = (context or {}).get("fx_market_rate")
                if not rate: _fail("CREDIT_VERIFIED_FX_QUOTE_REQUIRED", "购汇还款需要先取得服务器报价并一并确认。", 422)
                args["fx_quote_id"] = prepare_credit_fx_quote(session, principal, args["amount_minor"], rate)["quote_id"]
        elif kind == "CREDIT_INSTALLMENT":
            args.update(payment_id=slots.get("payment_id"), periods=slots.get("periods"))
            obligation = session.scalar(select(CreditObligation).where(CreditObligation.payment_id == args["payment_id"],
                CreditObligation.credit_account_id == facility.id))
            if not obligation or obligation.remaining_minor <= 0 or obligation.installment_id: _fail("CREDIT_INSTALLMENT_NOT_ELIGIBLE", status=422)
            if type(args["periods"]) is not int or args["periods"] not in facility.policy_json.get("installment_periods", []): _fail("CREDIT_INSTALLMENT_PERIODS_INVALID", status=422)
            args.update(amount_minor=obligation.remaining_minor, currency=obligation.currency)
            pricing = installment_pricing(facility, obligation.remaining_minor, args["periods"])
            args.update(expected_policy_hash=pricing["policy_hash"], fee_minor=pricing["fee_minor"], fee_policy_version=pricing["fee_policy_version"])
            args["expected_terms"] = installment_terms(facility, obligation.remaining_minor, args["periods"])
        elif kind == "CREDIT_LIMIT_REQUEST":
            limit = slots.get("requested_limit_minor")
            if type(limit) is not int or not 10_000 <= limit <= 10_000_000: _fail("CREDIT_LIMIT_REQUEST_INVALID", status=422)
            args["requested_limit_minor"] = limit
        elif kind == "CARD_SET_LIMIT":
            args["limit_minor"] = slots.get("amount_minor") or slots.get("limit_minor")
            if type(args["limit_minor"]) is not int or not 10_000 <= args["limit_minor"] <= 2_000_000: _fail("CREDIT_CARD_LIMIT_INVALID", status=422)
        allowed = {"CARD_LOCK": {"ACTIVE"}, "CARD_UNLOCK": {"LOCKED"}, "CARD_REPORT_LOSS": {"ACTIVE", "LOCKED", "ISSUED"},
            "CARD_REPLACEMENT": {"LOST"}, "CARD_SET_LIMIT": {"ACTIVE"}, "CREDIT_ACTIVATE": {"ISSUED"}}
        if kind in allowed and card.status not in allowed[kind]: _fail("CREDIT_CARD_STATE_INVALID", status=422)
        preview = {"action": kind, "card_number": card.card_number if card else None, "instrument_type": "CREDIT",
            "first_application": facility is None, "adult_review_required": facility is None,
            **({'shared_existing_credit': True} if args.get('application_credit_account_id') else {}),
            "credit_is_cash": False, **{key: value for key, value in args.items() if key in {
                "currency", "amount_minor", "periods", "repayment_mode", "requested_limit_minor", "limit_minor", "fx_quote_id", "fee_minor", "fee_policy_version"}}}
        if args.get("source_snapshot"):
            preview["source_card_last4"] = args["source_snapshot"]["display_last4"]
            preview["source_currency"] = args["source_snapshot"]["currency"]
        if args.get("expected_terms"):
            preview["schedule"] = args["expected_terms"]["schedule"]
            preview["total_fee_bps"] = args["expected_terms"]["pricing"]["total_fee_bps"]
            preview["fee_refund_policy"] = args["expected_terms"]["pricing"]["fee_refund_policy"]
        if args.get("fx_quote_id"):
            from .credit_models import CreditFxQuote
            quote = session.get(CreditFxQuote, args["fx_quote_id"])
            from .credit_service import _exposure
            preview["cash_debit_minor"] = _exposure(session, principal, currency, args["amount_minor"], quote.id)
            preview["rate"] = str(quote.rate)
        result = {"preview": preview, "message": "请核对信用卡办理内容，确认后执行。", "bank_state_changed": False}
    tool = ACTION_TOOLS[kind]; read_only = kind in READ_ACTIONS; node_key = kind.lower()
    node = {"node_id": node_key, "tool_name": tool, "read_only": read_only,
        "status": "SUCCEEDED" if read_only else "WAITING_CONFIRMATION", "requires_confirmation": not read_only,
        "action_kind": kind, "args": args}
    plan = {"task_id": task.id, "sandbox_id": task.sandbox_id, "plan_version": 1,
        "policy_version": "credit-v1", "nodes": [node]}
    task.plan_json, task.plan_hash, task.result_json = plan, canonical_hash(plan), result
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY"}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
        decision="ALLOW" if read_only else "REQUIRE_CONFIRMATION", reason_codes=["OWNED_CREDIT_BOOK"], policy_version="credit-v1")
    session.add(EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="OWNED_CREDIT_BOOK",
        source_version="credit-v1", payload_json=result if read_only else args))
    transition_task(task, "SUCCEEDED" if read_only else "AWAITING_CONFIRMATION")
    if not read_only:
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id, node_key=node_key,
            action_type=tool, target_id=card.id if card else None, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}), expires_at=utcnow()+timedelta(minutes=5)))
    return task


@atomic_domain
def commit_credit_task(session, task, principal, confirmation, operation_key, *, request_hash=None, node=None):
    require_profile_actor(session, principal, lock=True)
    plan_node = (task.plan_json.get("nodes") or [None])[0]
    if not isinstance(plan_node, dict) or plan_node.get("action_kind") not in WRITE_ACTIONS: _fail("CREDIT_CONFIRMATION_INVALID")
    kind, args, tool = plan_node["action_kind"], plan_node["args"], plan_node["tool_name"]
    if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id): _fail("CREDIT_OWNER_REQUIRED", status=403)
    pool_application = kind == 'CREDIT_APPLY' and args.get('application_credit_account_id') is not None
    first_application = kind == "CREDIT_APPLY" and args.get("card_reference") is None and not pool_application
    card = None if first_application or pool_application else owned_credit_card(session, principal, args["card_reference"])
    application_pool = None
    if pool_application:
        from .credit_preparation_contract import application_facility
        application_pool = application_facility(session, principal)
        if application_pool is None or application_pool.id != args['application_credit_account_id']:
            _fail('CREDIT_APPLICATION_STATE_CHANGED')
    if args.get("application_id"):
        application = session.get(CreditApplication, args["application_id"])
        if not application or (application.sandbox_id, application.user_id, application.kind, application.status, application.card_id) != \
                (principal.sandbox_id, principal.user_id, "CARD", "APPROVED", card.id if card else None): _fail("CREDIT_ACTIVATION_APPLICATION_INVALID")
    if first_application and session.scalar(select(CreditCard.id).where(CreditCard.sandbox_id == principal.sandbox_id,
            CreditCard.user_id == principal.user_id)) is not None: _fail("CREDIT_APPLICATION_STATE_CHANGED")
    if (confirmation is None or confirmation.task_id != task.id or confirmation.sandbox_id != task.sandbox_id
            or confirmation.user_id != task.user_id or confirmation.status != "PENDING"
            or as_utc(confirmation.expires_at) <= as_utc(utcnow()) or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation.plan_hash != task.plan_hash
            or confirmation.action_type != tool or confirmation.node_key != plan_node["node_id"]
            or confirmation.target_id != (card.id if card else None) or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args})):
        _fail("CREDIT_CONFIRMATION_INVALID")
    if card and card.version != args["expected_card_version"]: _fail("CREDIT_CARD_VERSION_CHANGED")
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == plan_node["node_id"]))
    if node is None or node.tool_name != tool or node.status != "WAITING_CONFIRMATION": _fail("CREDIT_CONFIRMATION_INVALID")
    if kind == "CREDIT_REPAY":
        result = repay_credit(session, principal, **{key: args[key] for key in ("card_reference", "source_snapshot", "currency", "amount_minor",
            "expected_facility_version", "fx_quote_id")}, operation_key=operation_key)
    elif kind == "CREDIT_INSTALLMENT":
        result = create_credit_installment(session, principal, **{key: args[key] for key in ("payment_id", "periods", "expected_facility_version", "expected_policy_hash", "expected_terms")}, operation_key=operation_key)
    elif kind == "CREDIT_SETTLE_INSTALLMENT":
        result = settle_credit_installment(session, principal, **{key: args[key] for key in ("installment_id", "source_snapshot", "expected_facility_version", "fx_quote_id", "card_reference")}, operation_key=operation_key)
    elif kind in {"CREDIT_APPLY", "CREDIT_LIMIT_REQUEST"}:
        facility = _facility(session, principal, card.credit_account_id) if card else (
            _facility(session, principal, application_pool.id) if application_pool else None)
        if facility and facility.version != args["expected_facility_version"]: _fail("CREDIT_VERSION_CHANGED")
        result = submit_credit_application(session, principal, kind="CARD" if kind == "CREDIT_APPLY" else "LIMIT",
            card_reference=args["card_reference"], requested_limit_minor=args.get("requested_limit_minor"), operation_key=operation_key,
            **({'credit_account_id': application_pool.id} if pool_application else {}))
    else:
        result = control_credit_card(session, principal, card_reference=args["card_reference"],
            action="ACTIVATE" if kind == "CREDIT_ACTIVATE" else CONTROL_ACTIONS[kind],
            operation_key=operation_key, expected_card_version=args["expected_card_version"], limit_minor=args.get("limit_minor"))
    result = {**result, "action": kind, "bank_state_changed": True}
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    task.result_json = result; finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool, idempotency_key=operation_key,
        request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}), status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="CREDIT_ACTION_COMPLETED",
        payload={"action": kind, "operation_key": operation_key})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED", payload_json={"task_id": task.id, "action": kind}))
    return task


def dispatch_credit_task(session, *, task, principal, request, idempotency_key):
    from .tools import ToolGateway
    plan_node = (task.plan_json.get("nodes") or [None])[0]
    if not isinstance(plan_node, dict) or plan_node.get("action_kind") not in WRITE_ACTIONS: _fail("CREDIT_ACTION_UNSUPPORTED")
    request_hash, node, confirmation, duplicate = ToolGateway().authorize_write(session, task=task,
        sandbox_id=principal.sandbox_id, user_id=principal.user_id, request=request, idempotency_key=idempotency_key,
        tool_name=plan_node["tool_name"], node_key=plan_node["node_id"])
    if duplicate: return task
    return commit_credit_task(session, task, principal, confirmation, idempotency_key, request_hash=request_hash, node=node)
