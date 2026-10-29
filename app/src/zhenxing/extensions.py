from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import re
import secrets
from statistics import median

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .analytics import analyze_bills, is_movement, load_bill_transactions
from .audit import append_audit
from .contracts import DomainError
from .carbon_settlement import reward_settlement_contract
from .models import (
    Account, BudgetEnvelope, Card, Confirmation, DemoSession, EvidenceSnapshot,
    OutboxMessage, Payee, Subscription, Task, TaskNode, ToolCall, User, utcnow,
)
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .simulation import add_month, as_utc, business_now
from .extensions_models import (
    CarbonBehavior, CarbonCredit, CarbonPointLedger, CarbonPool, CarbonRedemption, CarbonReward, CarbonWallet,
    CashflowGoal, FamilyDelegation, FamilyMember, FamilyProposal, FraudLabel, GoalLedgerEntry,
)
from .workflow import finish_node, transition_task
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .family_read_contract import KIND as FAMILY_READ_KIND, TOOL as FAMILY_READ_TOOL


EXTENSION_ACTION_TOOLS = {
    "FRAUD_ANALYZE": "fraud.analyze.v1", FAMILY_READ_KIND: FAMILY_READ_TOOL,
    "FAMILY_GRANT": "family.grant.v1", "FAMILY_REVOKE": "family.revoke.v1", "FAMILY_PROPOSE": "family.propose.v1",
    "GOAL_VIEW": "goal.view.v1", "GOAL_CREATE": "goal.create.v1", "GOAL_RESERVE": "goal.reserve.v1", "GOAL_RELEASE": "goal.release.v1",
    "CARBON_VIEW": "carbon.view.v1", "CARBON_CLAIM": "carbon.claim.v1", "CARBON_REDEEM": "carbon.redeem.v1",
}
READ_ACTIONS = {"FRAUD_ANALYZE", "FAMILY_VIEW", "GOAL_VIEW", "CARBON_VIEW"}
EXTENSION_TOOL_DEFINITIONS = {
    tool: {"read_only": kind in READ_ACTIONS, "risk_level": "L1" if kind in READ_ACTIONS else "L2",
           "owner": f"extensions.{tool.split('.')[0]}"}
    for kind, tool in EXTENSION_ACTION_TOOLS.items()
}
FAMILY_SCOPES = {"READ_BALANCE", "PROPOSE_TRANSFER", "PROPOSE_CARD_LOCK"}
CARBON_METHOD_VERSION = "personal-low-carbon-demo-v1"
CARBON_METHOD = {
    "methodology_version": CARBON_METHOD_VERSION, "synthetic": True,
    "factor_basis": "方法假设：相较基线小汽车，每公里估算避免排放克数。",
    "avoided_grams_per_km": {"WALK": 133, "BICYCLE": 133, "METRO": 83, "BUS": 53},
    "grams_per_point": 100, "rounding": "FLOOR_INTEGER",
    "certified_carbon_asset": False, "real_world_behavior_attested": False,
}


def _integer(value, label: str, maximum: int = 500_000_000, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DomainError("EXTENSION_INPUT_REQUIRED", f"请明确{label}及允许范围内的整数值", 422)
    return value


def _name(value, label: str, maximum: int = 60) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or re.search(r"[\r\n;；]", value):
        raise DomainError("EXTENSION_INPUT_REQUIRED", f"请明确{label}", 422)
    return value.strip()


def _account(session, sandbox_id, user_id, lock=False):
    query = select(Account).where(Account.sandbox_id == sandbox_id, Account.user_id == user_id)
    account = session.scalar(query.with_for_update() if lock else query)
    if account is None:
        raise DomainError("OWNER_ACCOUNT_REQUIRED", "当前身份没有可操作的本人资金账户", 403)
    return account


def _user(session, sandbox_id, user_id):
    user = session.scalar(select(User).where(User.id == user_id, User.sandbox_id == sandbox_id))
    if user is None:
        raise DomainError("ACTOR_NOT_FOUND", "当前主体不存在", 404)
    return user


def ensure_extension_fixtures(session: Session, sandbox_id: str, user_id: str) -> None:
    """Trusted bank fixture creation, never an interpretation of a user's claim."""
    _user(session, sandbox_id, user_id)
    clock = business_now(session, sandbox_id)
    if session.get(CarbonPool, sandbox_id) is None:
        session.add(CarbonPool(sandbox_id=sandbox_id, remaining_points=10000))
    if session.scalar(select(CarbonWallet).where(CarbonWallet.sandbox_id == sandbox_id, CarbonWallet.user_id == user_id)) is None:
        session.add(CarbonWallet(sandbox_id=sandbox_id, user_id=user_id, points=0))
    for name, mode, distance, days in [
        ("演示步行5公里", "WALK", 5000, 3), ("演示骑行8公里", "BICYCLE", 8000, 2),
        ("演示地铁20公里", "METRO", 20000, 1), ("待核验公交行程", "BUS", 10000, 1),
    ]:
        if session.scalar(select(CarbonBehavior.id).where(CarbonBehavior.user_id == user_id, CarbonBehavior.name == name)) is None:
            session.add(CarbonBehavior(sandbox_id=sandbox_id, user_id=user_id, name=name, mode=mode,
                distance_m=distance, source_event_key=f"seed-v1:{mode}:{distance}", source="SYNTHETIC_BANK_FEED",
                status="UNKNOWN" if name.startswith("待核验") else "VERIFIED_SYNTHETIC",
                occurred_at=clock - timedelta(days=days)))
    for name, cost, stock in [("演示节能主题权益", 10, 5), ("演示绿色出行权益券", 20, 2)]:
        if session.scalar(select(CarbonReward.id).where(CarbonReward.sandbox_id == sandbox_id, CarbonReward.name == name)) is None:
            session.add(CarbonReward(sandbox_id=sandbox_id, name=name, cost_points=cost, stock=stock))
    for payee in session.scalars(select(Payee).where(Payee.sandbox_id == sandbox_id, Payee.user_id == user_id)):
        if session.scalar(select(FraudLabel.id).where(FraudLabel.payee_id == payee.id, FraudLabel.user_id == user_id)) is None:
            session.add(FraudLabel(sandbox_id=sandbox_id, user_id=user_id, payee_id=payee.id,
                risk_level="LOW", reason_code="SYNTHETIC_CONTACT_NOT_FLAGGED"))
    session.flush()


def _root_message(session, task, context) -> str:
    if task is not None:
        from .runtime_models import RuntimeRecord
        managed = session.get(RuntimeRecord, task.id)
        root = task
        if managed and managed.root_task_id:
            parent = session.get(Task, managed.root_task_id)
            if parent and parent.sandbox_id == task.sandbox_id and parent.user_id == task.user_id:
                root = parent
                managed = session.get(RuntimeRecord, root.id)
        origin = managed.metadata_json.get("family_origin") if managed else None
        if origin:
            proposal = session.get(FamilyProposal, origin.get("proposal_id"))
            source = session.get(Task, origin.get("helper_source_task_id"))
            if proposal and source and proposal.owner_user_id == root.user_id and proposal.sandbox_id == root.sandbox_id \
                    and source.user_id == proposal.grantee_user_id and source.sandbox_id == root.sandbox_id:
                return source.message
        if root.id != task.id:
            return root.message
    # Only the integration's context is used before CHILD registration; model
    # action slots named context_message are never passed here by prepare.
    return (context or {}).get("message", task.message if task else "")


def evaluate_transfer_risk(session: Session, task: Task | None = None, context: dict | None = None) -> dict:
    context = context or {}
    sandbox_id = task.sandbox_id if task else context.get("sandbox_id")
    user_id = task.user_id if task else context.get("user_id")
    message = _root_message(session, task, context)
    from .fraud_signals import RULES_VERSION, risk_reason_codes
    reasons = risk_reason_codes(message)
    payee_id = context.get("payee_id")
    if task and not payee_id:
        payee_id = next((node.get("payee_id") for node in task.plan_json.get("nodes", []) if node.get("payee_id")), None)
    payee = session.scalar(select(Payee).where(Payee.id == payee_id, Payee.sandbox_id == sandbox_id, Payee.user_id == user_id)) if payee_id else None
    name = context.get("payee_name")
    if payee is None and name:
        matches = list(session.scalars(select(Payee).where(Payee.sandbox_id == sandbox_id, Payee.user_id == user_id, Payee.display_name == name)))
        payee = matches[0] if len(matches) == 1 else None
    label_query = select(FraudLabel).where(FraudLabel.payee_id == payee.id, FraudLabel.sandbox_id == sandbox_id,
                                          FraudLabel.user_id == user_id) if payee else None
    label = session.scalar(label_query.with_for_update() if context.get("for_commit") else label_query) if label_query is not None else None
    if label and label.risk_level == "HIGH":
        reasons.append(label.reason_code)
    if reasons:
        level, decision = "HIGH", "BLOCK"
    elif payee is None or payee.status != "ACTIVE" or (label and label.risk_level == "UNKNOWN"):
        level, decision = "UNKNOWN", "HOLD_FOR_REVIEW"
        reasons = [label.reason_code if label else "RECIPIENT_CONTEXT_NOT_RESOLVED"]
    else:
        level, decision = "LOW", "ALLOW"
        reasons = [label.reason_code if label else "KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG"]
    digest = hashlib.sha256(message.encode()).hexdigest()
    sources = [{"kind": "SYNTHETIC_BANK_RISK_FEED", "record_id": label.id, "version": label.version}] if label else (
        [{"kind": "SYNTHETIC_BANK_CONTACT_REGISTRY", "record_id": payee.id, "fields": ["status", "account_last4"]}] if payee else [])
    if payee:
        from .internal_banking_models import InternalPayeeBinding
        from .internal_banking import recipient_scope
        if session.get(InternalPayeeBinding, payee.id):
            scope = recipient_scope(session, payee)
            from .access_models import LoginAccount
            recipient = session.scalar(select(LoginAccount).where(LoginAccount.username == scope["recipient_username"]))
            if (recipient is None or recipient.status != "ACTIVE" or recipient.role not in {"CUSTOMER", "DEV_TEST"}) and decision != "BLOCK":
                level, decision, reasons = "UNKNOWN", "HOLD_FOR_REVIEW", ["REGISTERED_RECIPIENT_UNAVAILABLE"]
            sources.append({"kind": "REGISTERED_SYNTHETIC_BANK_ACCOUNT", "username": scope["recipient_username"],
                            "baseline": "SYNTHETIC_IDENTITY_AND_STATUS_NOT_REAL_WORLD_RISK_CERTIFICATION"})
    sources.append({"kind": "USER_REQUEST_CONTEXT", "context_hash": digest})
    return {"risk_level": level, "decision": decision, "reason_codes": reasons, "synthetic": True,
            "confirmation_can_override": False, "rules_version": RULES_VERSION,
            "payee_id": payee.id if payee else None,
            "sources": sources, "context_hash": digest,
            "explanation": "风险依据为当前风险标签与用户明确描述的诱导特征；未知单列等待核验，高风险不能通过普通确认跳过。"}


def create_family_helper(session, principal, display_name: str) -> dict:
    _account(session, principal.sandbox_id, principal.user_id, lock=True)
    name = _name(display_name, "协助人称呼", 40)
    if session.scalar(select(FamilyMember.id).where(FamilyMember.owner_user_id == principal.user_id, FamilyMember.display_name == name)):
        raise DomainError("FAMILY_MEMBER_EXISTS", "同名协助身份已经存在；新身份不能覆盖原授权", 409)
    helper = User(sandbox_id=principal.sandbox_id, display_name=name)
    session.add(helper)
    session.flush()
    session.add(FamilyMember(sandbox_id=principal.sandbox_id, owner_user_id=principal.user_id,
                            helper_user_id=helper.id, display_name=name))
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=4)
    session.add(DemoSession(sandbox_id=principal.sandbox_id, user_id=helper.id,
                           token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires))
    ensure_extension_fixtures(session, principal.sandbox_id, helper.id)
    return {"helper_user_id": helper.id, "display_name": name, "owner_user_id": principal.user_id,
            "role": "FAMILY_HELPER", "token": token, "expires_at": expires.isoformat(), "synthetic": True}


def _delegation_valid(delegation):
    return delegation.status == "ACTIVE" and as_utc(delegation.expires_at) > utcnow()


def is_family_helper(session, sandbox_id, user_id) -> bool:
    return session.scalar(select(FamilyMember.id).where(
        FamilyMember.sandbox_id == sandbox_id, FamilyMember.helper_user_id == user_id,
    )) is not None


def family_view(session, sandbox_id, user_id, *, owner_name=None, grantee_name=None, _principal=None) -> dict:
    user = _user(session, sandbox_id, user_id)
    owner = session.scalar(select(Account.id).where(Account.sandbox_id == sandbox_id, Account.user_id == user_id)) is not None
    members = list(session.scalars(select(FamilyMember).where(FamilyMember.sandbox_id == sandbox_id,
                         FamilyMember.helper_user_id.is_not(None),
                         or_(FamilyMember.owner_user_id == user_id, FamilyMember.helper_user_id == user_id))))
    actors = {user_id: {"user_id": user_id, "display_name": user.display_name, "role": "OWNER" if owner else "FAMILY_HELPER"}}
    for member in members:
        for actor_id, role in [(member.owner_user_id, "OWNER"), (member.helper_user_id, "FAMILY_HELPER")]:
            actor = session.get(User, actor_id)
            actors[actor_id] = {"user_id": actor_id, "display_name": actor.display_name, "role": role}
    from .family_read_contract import family_read_arguments, family_selector_matches
    selectors = family_read_arguments({"owner_name": owner_name, "grantee_name": grantee_name})
    selected = {}
    for field, name in selectors.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 80:
            raise DomainError("FAMILY_READ_MEMBER_REQUIRED", "请告诉我想查看哪位家人的协助信息。", 422)
        candidates = [actor for actor in actors.values()
                      if family_selector_matches(actor, field, name, actors[user_id])]
        if len(candidates) != 1:
            message = "有不止一位同名家人，请补充具体成员信息。" if candidates else "当前可查看的家庭协助中没有这位家人，请核对姓名。"
            raise DomainError("FAMILY_READ_MEMBER_AMBIGUOUS" if candidates else "FAMILY_READ_MEMBER_NOT_FOUND", message, 422)
        selected[field] = candidates[0]
    def matches(owner_id, grantee_id):
        return ("owner_name" not in selected or owner_id == selected["owner_name"]["user_id"]) and \
               ("grantee_name" not in selected or grantee_id == selected["grantee_name"]["user_id"])
    # Resolve names only inside the caller's actual relationship graph. A
    # selector cannot discover another tenant or another helper's delegation.
    if selectors:
        visible_ids = {user_id, *(actor["user_id"] for actor in selected.values())}
        for member in members:
            if matches(member.owner_user_id, member.helper_user_id):
                visible_ids.update((member.owner_user_id, member.helper_user_id))
        actors = {key: actor for key, actor in actors.items() if key in visible_ids}
    delegations = list(session.scalars(select(FamilyDelegation).where(FamilyDelegation.sandbox_id == sandbox_id,
                             or_(FamilyDelegation.owner_user_id == user_id, FamilyDelegation.grantee_user_id == user_id))))
    delegations = [row for row in delegations if matches(row.owner_user_id, row.grantee_user_id)]
    incoming = [row for row in delegations if row.grantee_user_id == user_id and _delegation_valid(row)]
    scopes = {scope for row in incoming for scope in row.scopes_json}
    proposals = []
    for row in session.scalars(select(FamilyProposal).where(FamilyProposal.sandbox_id == sandbox_id,
                or_(FamilyProposal.owner_user_id == user_id, FamilyProposal.grantee_user_id == user_id)).order_by(FamilyProposal.created_at.desc())):
        if not matches(row.owner_user_id, row.grantee_user_id):
            continue
        owner_user = session.get(User, row.owner_user_id)
        grantee = session.get(User, row.grantee_user_id)
        item = {"proposal_id": row.id, "owner_name": owner_user.display_name, "grantee_name": grantee.display_name,
                "proposal_kind": row.proposal_kind, "status": row.status, "version": row.version,
                **{key: row.args_json[key] for key in ("payee_name", "amount_minor", "card_last4") if key in row.args_json}}
        if row.owner_user_id == user_id:
            item["owner_task_id"] = row.owner_task_id
            if row.owner_task_id:
                task = session.get(Task, row.owner_task_id)
                item["owner_task_state"] = task.state if task else None
        else:
            item["redacted_request_id"] = row.id
        proposals.append(item)
    result = {"current_actor": actors[user_id], "actors": list(actors.values()), "synthetic": True,
        "delegations": [{"delegation_id": row.id, "owner_user_id": row.owner_user_id,
            "owner_name": session.get(User, row.owner_user_id).display_name,
            "grantee_user_id": row.grantee_user_id, "grantee_name": session.get(User, row.grantee_user_id).display_name,
            "scopes": row.scopes_json, "maximum_amount_minor": row.maximum_amount_minor,
            "limit_period": row.limit_period,
            "expires_at": as_utc(row.expires_at).isoformat(), "status": row.status, "version": row.version,
            "valid": _delegation_valid(row),
            "month_used_minor": family_month_used_minor(session, row, business_now(session, sandbox_id).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m"))
                                if row.limit_period == "MONTH" else None} for row in delegations],
        "permissions": {"can_grant": owner, "can_propose_transfer": "PROPOSE_TRANSFER" in scopes,
                        "can_propose_card_lock": "PROPOSE_CARD_LOCK" in scopes, "can_read_owner_balance": "READ_BALANCE" in scopes},
        "proposals": proposals}
    readable = [row for row in incoming if "READ_BALANCE" in row.scopes_json]
    if readable:
        # READ_BALANCE is an owner-level cash projection, not a selected card.
        # Count each owned CNY Account once even when two cards or multiple
        # valid delegations refer to it. Credit facilities are not cash.
        from .family_balance_projection import authorized_owner_cash_balances
        result["authorized_balances"] = authorized_owner_cash_balances(session, sandbox_id,
            {row.owner_user_id for row in readable})
    if selectors:
        from .read_selection import make_read_selection
        records = [{"field": field, "record_id": actor["user_id"], "record_version": None,
                    "record_hash": canonical_hash(actor)} for field, actor in selected.items()]
        result["read_selection"] = make_read_selection(session, sandbox_id, user_id, "FAMILY_VIEW", selectors, records, result,
                                                      principal=_principal)
    return result


def reserved_goal_cash_minor(session, sandbox_id, user_id, account_id=None) -> int:
    query = select(func.coalesce(func.sum(CashflowGoal.reserved_minor), 0)).where(
        CashflowGoal.sandbox_id == sandbox_id, CashflowGoal.user_id == user_id, CashflowGoal.reserved_minor > 0)
    if account_id:
        query = query.where(CashflowGoal.account_id == account_id)
    return int(session.scalar(query))


def _all_reserved_cash_minor(session, sandbox_id, user_id, account_id):
    # Goals remain a legacy feature, but cannot reserve money already assigned
    # to a confirmed birthday mandate. Keep the source-only function above
    # separate because the central reservation gate calls it too.
    from .services import Principal
    from .payment_instruments import cash_reserved_minor
    return cash_reserved_minor(session, Principal(user_id, sandbox_id), account_id)


def _cashflow_projection(session, sandbox_id, user_id, account, clock, reserved, emergency):
    end = clock + timedelta(microseconds=1)
    start = clock - timedelta(days=90)
    transactions = load_bill_transactions(session, sandbox_id, account.id, start=start, end=end, business_time=clock)
    operational = [row for row in transactions if not is_movement(row)]
    analysis = analyze_bills(session, sandbox_id, account.id, start=start, end=end, business_time=clock)
    covered_days = min(90, max(1, (clock - min(as_utc(row.occurred_at) for row in operational)).days + 1)) if operational else 0
    subscriptions = list(session.scalars(select(Subscription).where(Subscription.sandbox_id == sandbox_id,
        Subscription.user_id == user_id, Subscription.status != ARCHIVED_RESOURCE_STATUS)))
    subscription_fingerprints = {(row.merchant, -row.amount_minor) for row in subscriptions}
    variable_expense = sum(-row.amount_minor for row in operational if row.amount_minor < 0
                           and (row.merchant, row.amount_minor) not in subscription_fingerprints)
    variable_90 = (variable_expense * 90 + covered_days - 1) // covered_days if covered_days else 0
    grouped_income = {}
    stable_categories = {"工资", "薪资", "养老金", "固定收入"}
    for row in operational:
        if row.amount_minor > 0 and row.category in stable_categories:
            grouped_income.setdefault((row.category, row.merchant), []).append(row)
    income_90 = 0
    repeat_samples = 0
    income_sources = []
    recurring_observed = 0
    future_end = clock + timedelta(days=90)
    for (category, merchant), rows in grouped_income.items():
        rows.sort(key=lambda row: as_utc(row.occurred_at))
        amount = int(median(row.amount_minor for row in rows))
        recurring_observed += sum(row.amount_minor for row in rows)
        recurring = len(rows) >= 2 and all(20 <= (as_utc(right.occurred_at) - as_utc(left.occurred_at)).days <= 40
                                         for left, right in zip(rows, rows[1:]))
        repeat_samples += len(rows) if recurring else 0
        due = add_month(as_utc(rows[-1].occurred_at))
        while due <= clock:
            due = add_month(due)
        count = 0
        while due <= future_end:
            count += 1
            due = add_month(due)
        projected = amount * count
        income_90 += projected
        income_sources.append({"category": category, "merchant": merchant, "samples": len(rows),
                               "observed_pattern": "MONTHLY" if recurring else "UNCONFIRMED_SINGLE_SAMPLE",
                               "assumed_amount_minor": amount, "assumed_occurrences_90d": count,
                               "scenario_income_90d_minor": projected, "guaranteed": False})
    known_debits = []
    uncertain_debits = []
    known_subscription_90 = uncertain_subscription_90 = 0
    for row in subscriptions:
        if row.status != "ACTIVE":
            continue
        due = as_utc(row.next_due_at)
        dates = []
        if due <= clock:
            dates.append(clock.isoformat())
            while due <= clock:
                due = add_month(due)
        while due <= future_end:
            dates.append(due.isoformat())
            due = add_month(due)
        amount = len(dates) * row.amount_minor
        item = {"subscription_id": row.id, "merchant": row.merchant, "amount_minor": row.amount_minor,
                "due_dates": dates, "total_90d_minor": amount, "source_type": row.source_type,
                "version": row.version, "changes_applied": False}
        if row.source_type in {"BANK_MANDATE", "MERCHANT_SIM"}:
            known_subscription_90 += amount
            known_debits.append({**item, "certainty": "KNOWN_SIMULATION_SCHEDULE"})
        else:
            uncertain_subscription_90 += amount
            uncertain_debits.append({**item, "certainty": "EXTERNAL_UNVERIFIED"})
    from .banking_extension_models import ScopedScheduledTransfer
    scheduled = list(session.scalars(select(ScopedScheduledTransfer).where(
        ScopedScheduledTransfer.sandbox_id == sandbox_id, ScopedScheduledTransfer.user_id == user_id,
        ScopedScheduledTransfer.status == "WAITING", ScopedScheduledTransfer.due_at <= future_end)))
    scheduled_90 = sum(row.amount_minor for row in scheduled)
    sufficient = covered_days >= 60 and len(operational) >= 10 and repeat_samples >= 2
    sample_status = "SUFFICIENT_FOR_SCENARIO" if sufficient else "INSUFFICIENT_HISTORY"
    free = account.balance_minor - reserved
    scenarios = {}
    for name, income_percent, expense_percent, include_external in [
        ("conservative", 85 if sufficient else 0, 115, True),
        ("baseline", 100, 100, False),
        ("less_conservative", 105 if sufficient else 100, 90, False),
    ]:
        projected_income = income_90 * income_percent // 100
        projected_variable = (variable_90 * expense_percent + 99) // 100
        projected_expense = projected_variable + known_subscription_90 + scheduled_90 + (uncertain_subscription_90 if include_external else 0)
        net = projected_income - projected_expense
        ending = free + net
        scenarios[name] = {"income_90d_minor": projected_income, "variable_spending_90d_minor": projected_variable,
            "known_subscription_90d_minor": known_subscription_90, "scheduled_transfer_90d_minor": scheduled_90,
            "external_uncertain_90d_minor": uncertain_subscription_90 if include_external else 0,
            "expense_90d_minor": projected_expense, "net_90d_minor": net,
            "monthly_net_minor": net // 3, "unreserved_cash_end_90d_minor": ending,
            "emergency_floor_minor": emergency, "emergency_gap_90d_minor": max(0, emergency - ending),
            "income_multiplier_percent": income_percent, "variable_expense_multiplier_percent": expense_percent,
            "guaranteed": False}
    return {"forecast_version": "cashflow-scenarios-v2", "horizon_days": 90,
        "history_window": {"start": start.isoformat(), "end": end.isoformat(), "covered_days": covered_days,
                           "transaction_count": len(operational), "time_zone": "Asia/Shanghai"},
        "sample_status": sample_status, "scenario_confidence": "LIMITED" if not sufficient else "MODERATE",
        "observed_income_minor": analysis.income_minor, "observed_refund_minor": analysis.refund_minor,
        "observed_recurring_category_income_minor": recurring_observed,
        "observed_one_off_income_minor": analysis.income_minor - recurring_observed,
        "refund_as_future_income_minor": 0, "income_sources": income_sources,
        "known_future_debits": known_debits, "uncertain_future_debits": uncertain_debits,
        "scheduled_outflows": [{"schedule_id": row.id, "amount_minor": row.amount_minor, "due_at": as_utc(row.due_at).isoformat()} for row in scheduled],
        "scenarios": scenarios, "net_90d_range_minor": [scenarios["conservative"]["net_90d_minor"], scenarios["less_conservative"]["net_90d_minor"]],
        "data_months": len(analysis.monthly), "monthly_income_minor": scenarios["baseline"]["income_90d_minor"] // 3,
        "monthly_spending_minor": scenarios["baseline"]["expense_90d_minor"] // 3,
        "monthly_refund_minor": analysis.refund_minor * 30 // covered_days if covered_days else 0,
        "estimated_surplus_minor": scenarios["baseline"]["monthly_net_minor"],
        "source": "SYNTHETIC_TRANSACTION_AGGREGATE_AND_SCHEDULES", "projection_nonbinding": True,
        "assumptions": [
            "90天情景基于最近90天已发生的现金流水，按可观测天数估算可变消费；不是统计置信区间。",
            "仅工资、养老金等类别作继续发放假设；少样本时基准也只是待本人核实的假设，保守情景不假定收入。",
            "历史退款、红包及其他一次性收入不会保证再次出现，未来情景计入为0。",
            "已匹配订阅从历史可变消费中剔除，再按有效协议的未来扣费日单列；未接入商户仅列不确定，保守情景计入。",
            "保守/基准/较宽松分别使用收入85%/100%/105%、可变消费115%/100%/90%的显式压力假设。",
            "应急金底线独立检查；多个目标共用同一现金和结余，不能重复承诺相同额度。",
        ]}


def goals_view(session, sandbox_id, user_id) -> dict:
    account = _account(session, sandbox_id, user_id)
    clock = business_now(session, sandbox_id)
    goal_rows = list(session.scalars(select(CashflowGoal).where(CashflowGoal.sandbox_id == sandbox_id, CashflowGoal.user_id == user_id)))
    reserved = _all_reserved_cash_minor(session, sandbox_id, user_id, account.id)
    emergency = session.scalar(select(BudgetEnvelope.reserved_minor).where(BudgetEnvelope.sandbox_id == sandbox_id,
                    BudgetEnvelope.user_id == user_id, BudgetEnvelope.label == "应急金")) or 0
    forecast = _cashflow_projection(session, sandbox_id, user_id, account, clock, reserved, emergency)
    free_after_emergency = max(0, account.balance_minor - reserved - emergency)
    monthly_capacity = max(0, forecast["scenarios"]["baseline"]["monthly_net_minor"])
    rows = []
    for row in goal_rows:
        remaining = max(0, row.target_minor - row.reserved_minor)
        months_left = max(1, ((as_utc(row.due_at) - clock).days + 29) // 30) if row.due_at else None
        net = session.scalar(select(func.coalesce(func.sum(GoalLedgerEntry.delta_minor), 0)).where(
            GoalLedgerEntry.goal_id == row.id, GoalLedgerEntry.account_ref == f"GOAL:{row.id}"))
        required = (remaining + months_left - 1) // months_left if months_left else None
        overdue = bool(row.due_at and as_utc(row.due_at) < clock)
        if remaining == 0:
            feasibility = "FUNDED_NOT_SPENT"
        elif overdue:
            feasibility = "OVERDUE_REVIEW_REQUIRED"
        elif remaining <= free_after_emergency:
            feasibility = "FEASIBLE_FROM_CURRENT_CASH"
        elif forecast["sample_status"] != "SUFFICIENT_FOR_SCENARIO":
            feasibility = "SAMPLE_INSUFFICIENT"
        elif required is None:
            feasibility = "DUE_DATE_REQUIRED"
        else:
            feasibility = "FEASIBLE_BASELINE" if required <= monthly_capacity else "MONTHLY_GAP"
        gap = max(0, required - monthly_capacity) if required is not None else None
        options = [
            {"kind": "VOLUNTARY_RESERVE", "read_only": True, "amount_minor": min(remaining, free_after_emergency),
             "message": "可自愿使用当前应急金之外的闲置现金预留；须另发请求并确认，不自动划拨。"},
            {"kind": "EXTEND_OR_REDUCE_GOAL", "read_only": True, "message": "可延长目标日期或降低目标金额，重新查看月需存和多个目标之间的冲突。"},
            {"kind": "REVIEW_DISCRETIONARY_SPENDING", "read_only": True,
             "message": "可查看订阅及可选消费，评估是否自愿调整；本方案不会自动退订、转账或买基金。"},
        ]
        rows.append({"goal_id": row.id, "goal_name": row.name, "target_minor": row.target_minor,
            "due_date": as_utc(row.due_at).isoformat() if row.due_at else None, "reserved_minor": row.reserved_minor,
            "remaining_minor": remaining, "progress": round(row.reserved_minor / row.target_minor, 4),
            "version": row.version, "status": row.status, "monthly_suggested_minor": required,
            "overdue": overdue, "feasibility": feasibility, "monthly_capacity_baseline_minor": monthly_capacity,
            "monthly_gap_minor": gap, "current_cash_gap_minor": max(0, remaining - free_after_emergency),
            "planning_horizon_months": months_left, "options": options,
            "ledger_reserved_minor": net, "reservation_balanced": net == row.reserved_minor})
    net_all = session.scalar(select(func.coalesce(func.sum(GoalLedgerEntry.delta_minor), 0)).where(
        GoalLedgerEntry.sandbox_id == sandbox_id, GoalLedgerEntry.user_id == user_id))
    rewards = [{"redemption_id": row.id, "reward_name": session.get(CarbonReward, row.reward_id).name,
                "eligibility": "UNUSED_SIMULATION_REWARD", "status": row.status, "used": False,
                "cash_value_minor": 0, "realized_saving_minor": 0, "counted_as_income": False,
                "message": "已兑换的本活动权益尚未使用，不是现金、工资或已实现节省。"}
               for row in session.scalars(select(CarbonRedemption).where(CarbonRedemption.sandbox_id == sandbox_id,
                                                                        CarbonRedemption.user_id == user_id))]
    required_total = sum(row["monthly_suggested_minor"] or 0 for row in rows)
    remaining_total = sum(row["remaining_minor"] for row in rows)
    return {"goals": rows, "forecast": forecast, "budget_reward_feedback": rewards,
            "portfolio": {"required_monthly_minor": required_total, "monthly_capacity_baseline_minor": monthly_capacity,
                          "monthly_gap_minor": max(0, required_total - monthly_capacity),
                          "current_cash_gap_minor": max(0, remaining_total - free_after_emergency)},
            "account_balance_minor": account.balance_minor,
            "goal_reserved_minor": reserved, "available_cash_minor": account.balance_minor - reserved,
            "emergency_reserved_minor": emergency, "spendable_after_emergency_minor": max(0, account.balance_minor - reserved - emergency),
            "reservation_ledger_net_minor": net_all, "synthetic": True,
            "explanation": "目标预留以额外分账记录，占用可支出额度但不伪造现金扣款；现金总额=未预留金额+目标预留。预测来自历史账务流水，不保证未来收入。"}


def _estimate_behavior(behavior):
    factor = CARBON_METHOD["avoided_grams_per_km"].get(behavior.mode)
    if factor is None:
        raise DomainError("CARBON_METHOD_UNSUPPORTED", "方法版本不支持该行为类型", 409)
    grams = behavior.distance_m * factor // 1000
    return grams, grams // CARBON_METHOD["grams_per_point"]


def carbon_view(session, sandbox_id, user_id, *, reward_name=None, behavior_name=None) -> dict:
    from .carbon_settlement import reward_settlement_contract
    _user(session, sandbox_id, user_id)
    wallet = session.scalar(select(CarbonWallet).where(CarbonWallet.sandbox_id == sandbox_id, CarbonWallet.user_id == user_id))
    credits = {row.behavior_id: row for row in session.scalars(select(CarbonCredit).where(CarbonCredit.user_id == user_id, CarbonCredit.sandbox_id == sandbox_id))}
    events = []
    clock = business_now(session, sandbox_id)
    for row in session.scalars(select(CarbonBehavior).where(CarbonBehavior.sandbox_id == sandbox_id, CarbonBehavior.user_id == user_id)):
        try:
            grams, points = _estimate_behavior(row)
            supported = True
        except DomainError:
            grams, points, supported = None, 0, False
        events.append({"behavior_id": row.id, "behavior_name": row.name, "mode": row.mode, "distance_m": row.distance_m,
            "source": row.source, "status": row.status, "occurred_at": as_utc(row.occurred_at).isoformat(),
            "estimated_avoided_grams": grams, "estimated_points": points, "claimed": row.id in credits,
            "method_supported": supported,
            "claimable": row.id not in credits and row.source == "SYNTHETIC_BANK_FEED"
                         and row.status == "VERIFIED_SYNTHETIC" and as_utc(row.occurred_at) <= clock and points > 0})
    rewards = [{"reward_id": row.id, "reward_name": row.name, "cost_points": row.cost_points,
                "stock": row.stock, "version": row.version,
                "can_redeem": bool(wallet and wallet.points >= row.cost_points and row.stock > 0), "synthetic": True,
                "settlement":reward_settlement_contract()}
               for row in session.scalars(select(CarbonReward).where(CarbonReward.sandbox_id == sandbox_id))]
    redemptions = [{"redemption_id": row.id, "reward_name": session.get(CarbonReward, row.reward_id).name,
                   "cost_points": row.cost_points, "status": row.status} for row in session.scalars(select(CarbonRedemption).where(
                    CarbonRedemption.sandbox_id == sandbox_id, CarbonRedemption.user_id == user_id))]
    ledger_net = session.scalar(select(func.coalesce(func.sum(CarbonPointLedger.delta_points), 0)).where(CarbonPointLedger.sandbox_id == sandbox_id))
    result = {"methodology": CARBON_METHOD, "events": events, "wallet": {"points": wallet.points if wallet else 0,
            "version": wallet.version if wallet else None}, "rewards": rewards, "redemptions": redemptions,
            "point_ledger_net": ledger_net, "synthetic": True,
            "explanation": "行为凭据来自本活动已记录的数据源；估算和积分仅适用于本活动，非核证碳资产，不构成外部权益或实际行为证明。"}
    selectors = {key: value for key, value in (("reward_name", reward_name), ("behavior_name", behavior_name)) if value is not None}
    if not selectors:
        return result
    from .read_selection import validate_selectors, make_read_selection
    validate_selectors("CARBON_VIEW", selectors)
    selected_records = []
    selected_rewards, selected_events = [], []
    for field, collection, id_key in (("reward_name", rewards, "reward_id"), ("behavior_name", events, "behavior_id")):
        if field not in selectors:
            continue
        values = [value for value in collection if value[field] == selectors[field]]
        if len(values) != 1:
            raise DomainError("CARBON_READ_TARGET_REQUIRED", "没有找到唯一的已登记权益或本人行为，请核对名称。", 422)
        value = values[0]
        selected_records.append({"field": field, "record_id": value[id_key],
            "record_version": value.get("version") if field == "reward_name" else None,
            "record_hash": canonical_hash(value)})
        (selected_rewards if field == "reward_name" else selected_events).append(value)
    # A narrow owner read has no global tenant diagnostic or unrequested rows.
    # Empty secondary dimensions mean not requested, never account-wide absence.
    result = {key: value for key, value in result.items() if key != "point_ledger_net"}
    result.update(rewards=selected_rewards, events=selected_events, redemptions=[])
    selection = make_read_selection(session, sandbox_id, user_id, "CARBON_VIEW", selectors, selected_records, result)
    return {**result, "read_selection": selection}


def _resolve_arguments(session, task, kind, slots, context):
    sid, uid = task.sandbox_id, task.user_id
    if kind in {"FAMILY_GRANT", "FAMILY_REVOKE"}:
        _account(session, sid, uid)
        members = list(session.scalars(select(FamilyMember).where(FamilyMember.sandbox_id == sid,
            FamilyMember.owner_user_id == uid, FamilyMember.helper_user_id.is_not(None))))
        if slots.get("grantee_name"):
            members = [row for row in members if row.display_name == slots["grantee_name"]]
        if len(members) != 1:
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请先创建独立协助身份，再明确唯一协助人称呼", 422)
        member = members[0]
        existing = session.scalar(select(FamilyDelegation).where(FamilyDelegation.owner_user_id == uid,
                                                                 FamilyDelegation.grantee_user_id == member.helper_user_id))
        args = {"owner_user_id": uid, "grantee_user_id": member.helper_user_id, "grantee_name": member.display_name,
                "delegation_id": existing.id if existing else None, "expected_version": existing.version if existing else None}
        if kind == "FAMILY_REVOKE":
            if existing is None or not _delegation_valid(existing):
                raise DomainError("DELEGATION_NOT_ACTIVE", "没有可撤销的有效委托", 409)
            return args
        scopes = slots.get("scopes")
        if not isinstance(scopes, list) or not scopes or any(scope not in FAMILY_SCOPES for scope in scopes):
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请明确允许查看余额、提出转账建议或提出锁卡建议的细粒度范围", 422)
        maximum = _integer(slots.get("maximum_amount_minor"), "转账建议单笔上限", 5_000_000) if "PROPOSE_TRANSFER" in scopes else 0
        period = slots.get("limit_period") or "PER_PROPOSAL"
        if period not in {"PER_PROPOSAL", "MONTH"}:
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请明确每笔或每月累计的授权额度范围", 422)
        return {**args, "scopes": sorted(set(scopes)), "maximum_amount_minor": maximum,
                "limit_period": period, "expires_days": _integer(slots.get("expires_days"), "明确的授权有效天数", 180)}
    if kind == "FAMILY_PROPOSE":
        proposed_kind = slots.get("proposal_kind")
        if proposed_kind not in {"TRANSFER", "CARD_LOCK"}:
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请明确为本人提出转账建议还是锁卡建议", 422)
        scope = "PROPOSE_TRANSFER" if proposed_kind == "TRANSFER" else "PROPOSE_CARD_LOCK"
        delegations = [row for row in session.scalars(select(FamilyDelegation).where(
            FamilyDelegation.sandbox_id == sid, FamilyDelegation.grantee_user_id == uid))
            if _delegation_valid(row) and scope in row.scopes_json]
        if slots.get("owner_name"):
            delegations = [row for row in delegations if session.get(User, row.owner_user_id).display_name == slots["owner_name"]]
        if len(delegations) != 1:
            raise DomainError("FAMILY_SCOPE_REQUIRED", "没有唯一、有效且允许此类建议的本人委托", 403)
        delegation = delegations[0]
        args = {"delegation_id": delegation.id, "delegation_version": delegation.version,
                "owner_user_id": delegation.owner_user_id, "grantee_user_id": uid, "proposal_kind": proposed_kind}
        from .runtime_models import RuntimeRecord
        record = session.get(RuntimeRecord, task.id)
        source_id = record.root_task_id if record and record.root_task_id else (context or {}).get("source_root_task_id", task.id)
        source = session.get(Task, source_id)
        if source is None or source.sandbox_id != sid or source.user_id != uid:
            raise DomainError("FAMILY_SOURCE_CHANGED", "协助建议来源不属于当前主体", 403)
        args["source_helper_task_id"] = source_id
        args["source_helper_context_hash"] = hashlib.sha256(_root_message(session, task, context).encode()).hexdigest()
        if proposed_kind == "TRANSFER":
            amount = _integer(slots.get("amount_minor"), "转账建议金额", delegation.maximum_amount_minor)
            month = business_now(session, sid).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m")
            if delegation.limit_period == "MONTH" and family_month_used_minor(session, delegation, month) + amount > delegation.maximum_amount_minor:
                raise DomainError("FAMILY_MONTH_LIMIT_EXCEEDED", "本协助委托的业务月累计建议额度不足", 403)
            name = _name(slots.get("payee_name"), "本人已登记收款人")
            from .internal_banking import resolve_transfer_payees, recipient_scope
            matches = resolve_transfer_payees(session, sid, delegation.owner_user_id, name)
            if len(matches) != 1:
                raise DomainError("EXTENSION_INPUT_REQUIRED", "收款人未唯一登记在本人账套，请让本人核验", 422)
            risk = evaluate_transfer_risk(session, context={"sandbox_id": sid, "user_id": delegation.owner_user_id,
                    "payee_id": matches[0].id, "message": _root_message(session, task, context)})
            if risk["decision"] != "ALLOW":
                raise DomainError("FAMILY_PROPOSAL_RISK_BLOCKED", "风险尚未允许转账建议，不能靠协助人或本人确认跳过", 403)
            destination = recipient_scope(session, matches[0])
            target_context = None
            if destination["transfer_channel"] == "INTERNAL_BANK":
                from .contacts import recipient_context
                target_context = recipient_context(session, sid, delegation.owner_user_id, name)
            return {**args, "amount_minor": amount, "payee_id": matches[0].id, "payee_name": matches[0].display_name,
                    **destination, "risk_context_hash": risk["context_hash"],
                    **({"requested_payee_reference": name, "recipient_context": target_context} if target_context else {})}
        from .card_identifiers import resolve_owned_cards, card_identifier_view
        refs = [value for value in (slots.get("card_number"), slots.get("card_last4")) if value]
        selected = None
        for reference in refs:
            current = {row.id: row for row in resolve_owned_cards(session, sid, delegation.owner_user_id, reference)}
            selected = current if selected is None else {key: row for key, row in selected.items() if key in current}
        cards = [row for row in (selected or {}).values() if row.status == "ACTIVE"]
        if len(cards) != 1:
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请明确委托本人可锁定卡片的完整平台卡号或唯一四位尾码", 422)
        identity = card_identifier_view(session, cards[0]) or {}
        return {**args, "card_id": cards[0].id, "card_last4": cards[0].last4,
                "card_version": cards[0].version, "requested_card_reference": slots.get("card_number") or slots.get("card_last4"),
                "card_display_last4": identity.get("display_last4") or cards[0].last4,
                **({"card_number": identity.get("card_number")} if slots.get("card_number") else {})}
    if kind.startswith("GOAL_"):
        account = _account(session, sid, uid)
        name = _name(slots.get("goal_name"), "目标名称")
        goal = session.scalar(select(CashflowGoal).where(CashflowGoal.sandbox_id == sid, CashflowGoal.user_id == uid, CashflowGoal.name == name))
        if kind == "GOAL_CREATE":
            if goal:
                raise DomainError("GOAL_ALREADY_EXISTS", "同名目标已经存在", 409)
            due = None
            if slots.get("due_date"):
                try:
                    due = datetime.combine(date.fromisoformat(slots["due_date"]), time(23, 59, 59),
                                           timezone(timedelta(hours=8))).astimezone(timezone.utc)
                except (ValueError, TypeError):
                    raise DomainError("EXTENSION_INPUT_REQUIRED", "请用YYYY-MM-DD指定目标日期", 422) from None
                clock = business_now(session, sid)
                if not clock < due <= clock + timedelta(days=1827):
                    raise DomainError("EXTENSION_INPUT_REQUIRED", "目标日期必须位于业务时钟之后且五年以内", 422)
            return {"goal_name": name, "target_minor": _integer(slots.get("target_minor"), "目标金额"),
                    "due_at": due.isoformat() if due else None, "account_id": account.id}
        if goal is None or goal.status != "ACTIVE":
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请先创建并明确有效目标", 422)
        amount = _integer(slots.get("amount_minor"), "预留或释放金额")
        if kind == "GOAL_RELEASE" and amount > goal.reserved_minor:
            raise DomainError("GOAL_RELEASE_EXCEEDED", "释放金额超过当前目标预留", 409)
        if kind == "GOAL_RESERVE":
            if goal.reserved_minor + amount > goal.target_minor:
                raise DomainError("GOAL_TARGET_EXCEEDED", "预留金额超过目标", 409)
            emergency = session.scalar(select(BudgetEnvelope.reserved_minor).where(BudgetEnvelope.sandbox_id == sid,
                                        BudgetEnvelope.user_id == uid, BudgetEnvelope.label == "应急金")) or 0
            if account.balance_minor - _all_reserved_cash_minor(session, sid, uid, account.id) - emergency < amount:
                raise DomainError("GOAL_AVAILABLE_CASH_INSUFFICIENT", "可预留资金不足或会侵占应急金", 409)
        return {"goal_id": goal.id, "goal_name": name, "amount_minor": amount,
                "expected_goal_version": goal.version, "account_id": account.id, "expected_account_version": account.version}
    if kind in {"CARBON_CLAIM", "CARBON_REDEEM"}:
        wallet = session.scalar(select(CarbonWallet).where(CarbonWallet.sandbox_id == sid, CarbonWallet.user_id == uid))
        if wallet is None:
            raise DomainError("EXTENSION_FIXTURE_REQUIRED", "当前会话尚未初始化低碳活动", 409)
        if kind == "CARBON_CLAIM":
            behavior = session.scalar(select(CarbonBehavior).where(CarbonBehavior.sandbox_id == sid, CarbonBehavior.user_id == uid,
                                                  CarbonBehavior.name == slots.get("behavior_name")))
            if behavior is None:
                raise DomainError("EXTENSION_INPUT_REQUIRED", "请选择本活动数据源已提供的行为，用户自报不生成凭证", 422)
            if behavior.source != "SYNTHETIC_BANK_FEED" or behavior.status != "VERIFIED_SYNTHETIC" or as_utc(behavior.occurred_at) > business_now(session, sid):
                raise DomainError("CARBON_BEHAVIOR_NOT_VERIFIED", "该行为记录尚未核验或尚未发生，不能发放积分", 409)
            if session.scalar(select(CarbonCredit.id).where(CarbonCredit.behavior_id == behavior.id)):
                raise DomainError("CARBON_ALREADY_CLAIMED", "该行为已经记入积分，方法版本更新也不能重复领取", 409)
            grams, points = _estimate_behavior(behavior)
            if points <= 0:
                raise DomainError("CARBON_NO_POINTS", "该行为估算未达到一个活动积分", 409)
            return {"behavior_id": behavior.id, "behavior_name": behavior.name, "methodology_version": CARBON_METHOD_VERSION,
                    "estimated_avoided_grams": grams, "points": points, "wallet_id": wallet.id, "expected_wallet_version": wallet.version}
        reward = session.scalar(select(CarbonReward).where(CarbonReward.sandbox_id == sid, CarbonReward.name == slots.get("reward_name")))
        if reward is None:
            raise DomainError("EXTENSION_INPUT_REQUIRED", "请选择本活动已提供的权益", 422)
        if reward.stock <= 0 or wallet.points < reward.cost_points:
            raise DomainError("CARBON_REDEMPTION_UNAVAILABLE", "积分不足或权益库存不足", 409)
        return {"reward_id": reward.id, "reward_name": reward.name, "cost_points": reward.cost_points,
                "settlement":reward_settlement_contract(),
                "expected_reward_version": reward.version, "wallet_id": wallet.id, "expected_wallet_version": wallet.version}
    raise DomainError("EXTENSION_ACTION_UNKNOWN", "扩展动作不支持", 422)


def _preview_message(kind, args):
    if kind == "FAMILY_GRANT":
        labels = {"READ_BALANCE": "查看余额", "PROPOSE_TRANSFER": "提出转账建议", "PROPOSE_CARD_LOCK": "提出锁卡建议"}
        scope = "、".join(labels[value] for value in args["scopes"])
        unit = "每月累计" if args["limit_period"] == "MONTH" else "单笔"
        limit = f"，转账建议{unit}最多{args['maximum_amount_minor'] / 100:.2f}元" if args["maximum_amount_minor"] else ""
        return f"准备允许{args['grantee_name']}{scope}，有效{args['expires_days']}天{limit}。协助人不能代替本人执行银行操作，请确认委托。"
    if kind == "FAMILY_REVOKE":
        return f"准备撤销{args['grantee_name']}的协助委托，请本人确认。"
    if kind == "FAMILY_PROPOSE":
        reference = args.get("requested_payee_reference") or args.get("payee_name")
        card_ref = args.get("requested_card_reference") or args.get("card_last4")
        detail = f"给{reference}转{args['amount_minor'] / 100:.2f}元" if args["proposal_kind"] == "TRANSFER" else f"临时锁定卡片{card_ref}"
        return f"准备提出建议：{detail}。当前确认仅提交建议，银行操作仍需本人受理后单独确认。"
    if kind == "GOAL_CREATE":
        return f"准备建立{args['goal_name']}，目标{args['target_minor'] / 100:.2f}元。建立目标不会自动扣款或预留资金，请确认。"
    if kind in {"GOAL_RESERVE", "GOAL_RELEASE"}:
        verb = "预留" if kind == "GOAL_RESERVE" else "释放"
        return f"准备为{args['goal_name']}{verb}{args['amount_minor'] / 100:.2f}元。调整的是目标分账与可支出额度，不产生现金支付，请确认。"
    if kind == "CARBON_CLAIM":
        return f"准备为{args['behavior_name']}领取{args['points']}本活动积分（版本方法估算{args['estimated_avoided_grams']}克）。来源为本活动行为记录，并非核证碳资产，请确认。"
    return f"准备使用{args['cost_points']}本活动积分兑换{args['reward_name']}，将扣减积分和本活动库存，请确认。"


def prepare_extension_task(session: Session, task: Task, action: dict, context: dict | None = None) -> Task:
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind, slots = action.get("kind"), action.get("slots", {})
    tool = EXTENSION_ACTION_TOOLS.get(kind)
    if tool is None:
        raise DomainError("EXTENSION_ACTION_UNKNOWN", "扩展动作不支持", 422)
    try:
        from .family_account_context import validate_family_context_actor
        family_context = None
        if validate_family_context_actor(session, task.sandbox_id, task.user_id) is not None:
            from .runtime import validate_runtime_family_context
            source = session.get(Task, (context or {}).get("source_root_task_id")) if (context or {}).get("source_root_task_id") else task
            if source is None or (source.sandbox_id, source.user_id) != (task.sandbox_id, task.user_id):
                raise DomainError("FAMILY_CONTEXT_INVALID", "家庭协助任务身份来源已失效", 401)
            family_context = validate_runtime_family_context(session, source)
        if kind in READ_ACTIONS:
            if kind == "FRAUD_ANALYZE":
                trusted_context = {**(context or {}), "payee_name": slots.get("payee_name"),
                                   "sandbox_id": task.sandbox_id, "user_id": task.user_id}
                from .semantic_materials import semantic_analysis_message
                material=semantic_analysis_message(session,task,action,context)
                if material:
                    # The quote is recovered from the validated parent source,
                    # not from a free model slot or assistant paraphrase.
                    risk=evaluate_transfer_risk(session,None,{**trusted_context,'message':material['message']})
                    risk={**risk,'analysis_kind':material['analysis_kind'],
                        'material_source_count':material['material_source_count']}
                    if risk['reason_codes']==['RECIPIENT_CONTEXT_NOT_RESOLVED']:
                        risk['reason_codes']=['NO_CONTEXTUAL_SIGNAL_DETECTED']
                        risk['explanation']='这些材料暂未匹配明确的危险信号，但不能据此判断对方身份或认定安全。'
                else:
                    risk=evaluate_transfer_risk(session, task, trusted_context)
                result = {"risk": risk, "message": "已根据当前证据完成风险分析。"}
            elif kind == "FAMILY_VIEW":
                from .family_read_contract import family_read_arguments
                from .services import Principal
                result = {**family_view(session, task.sandbox_id, task.user_id, **family_read_arguments(slots),
                    _principal=Principal(task.user_id, task.sandbox_id, family_context=family_context)),
                          "message": "已核对当前身份与细粒度委托。"}
            elif kind == "GOAL_VIEW":
                result = {**goals_view(session, task.sandbox_id, task.user_id), "message": "已核对目标进度、分账预留与现金流。"}
                from .goal_what_if import calculate_goal_what_if, goal_what_if_requested
                requested = goal_what_if_requested(_root_message(session, task, context)) or slots.get("monthly_saving_delta_minor") is not None or slots.get("monthly_saving_total_minor") is not None
                if requested:
                    candidates = [row for row in result["goals"] if row["status"] == "ACTIVE" and
                                  (not slots.get("goal_name") or row["goal_name"] == slots["goal_name"])]
                    if len(candidates) != 1:
                        names = "、".join(row["goal_name"] for row in result["goals"] if row["status"] == "ACTIVE")
                        raise DomainError("GOAL_WHAT_IF_TARGET_REQUIRED", f"您想测算哪个储蓄目标？目前有{names}。" if names else "您想测算哪个储蓄目标？目前还没有已建立的目标。", 422)
                    if slots.get("monthly_saving_minor") is None:
                        raise DomainError("GOAL_WHAT_IF_BASELINE_REQUIRED", f"为了算出{candidates[0]['goal_name']}能提前多久，您原本每月为这个目标存多少元？", 422)
                    if slots.get("monthly_saving_delta_minor") is None and slots.get("monthly_saving_total_minor") is None:
                        raise DomainError("GOAL_WHAT_IF_DELTA_REQUIRED", "您准备每月额外存多少元？例如每月少花500元，并全部存入这个目标。", 422)
                    result["what_if"] = calculate_goal_what_if(candidates[0], monthly_saving_minor=slots["monthly_saving_minor"],
                        monthly_saving_delta_minor=slots.get("monthly_saving_delta_minor"), monthly_saving_total_minor=slots.get("monthly_saving_total_minor"))
                    result["message"] = ("新的每月总储蓄额为0元，目标暂无可预计的完成月份，不能比较提前多久；未调整资金或目标。" if not result["what_if"]["proposed_plan_reachable"] else
                        f"原本每月为{candidates[0]['goal_name']}存0元，原计划无法在有限月份内完成，因此不能比较提前多久；若每月增加储蓄，约{result['what_if']['improved_months']}个月完成。未调整资金或目标。"
                        if not result["what_if"]["baseline_reachable"] else
                        f"按您给定的每月储蓄金额测算，{candidates[0]['goal_name']}预计{'晚' if result['what_if']['months_saved'] < 0 else '提前'}约{abs(result['what_if']['months_saved'])}个月完成；未调整资金或目标。")
            else:
                result = {**carbon_view(session, task.sandbox_id, task.user_id,
                    reward_name=slots.get("reward_name"), behavior_name=slots.get("behavior_name")),
                    "message": "已核对活动行为记录、方法版本、积分和权益库存。"}
            args = {key: slots[key] for key in ("goal_name", "monthly_saving_minor", "monthly_saving_delta_minor", "monthly_saving_total_minor")
                    if kind == "GOAL_VIEW" and slots.get(key) is not None}
            if kind == "CARBON_VIEW":
                args = {key: slots[key] for key in ("reward_name", "behavior_name") if slots.get(key) is not None}
            elif kind == "FAMILY_VIEW":
                args = family_read_arguments(slots)
        else:
            if is_family_helper(session, task.sandbox_id, task.user_id) and kind != "FAMILY_PROPOSE":
                raise DomainError("FAMILY_HELPER_WRITE_FORBIDDEN", "协助身份只可在有效委托范围内提出建议", 403)
            args = _resolve_arguments(session, task, kind, slots, context)
            result = {"message": _preview_message(kind, args),
                      "preview": args, "synthetic": True}
    except DomainError as error:
        task.result_json = {"message": error.message, "error": error.code, "synthetic": True}
        if error.status_code != 422:
            transition_task(task, "FAILED")
        return task
    node_key = kind.lower()
    node = {"node_id": node_key, "tool_name": tool, "read_only": kind in READ_ACTIONS,
            "status": "SUCCEEDED" if kind in READ_ACTIONS else "WAITING_CONFIRMATION",
            "requires_confirmation": kind not in READ_ACTIONS, "action_kind": kind, "args": args}
    if kind in {"CARBON_VIEW", "FAMILY_VIEW"} and result.get("read_selection"):
        from .read_selection import VERSION as READ_SELECTION_VERSION
        node.update(read_selection_version=READ_SELECTION_VERSION,
                    read_selection_hash=result["read_selection"]["scope_hash"])
    plan = {"task_id": task.id, "sandbox_id": task.sandbox_id, "plan_version": 1,
            "policy_version": "extensions-v1", "nodes": [node]}
    task.plan_json, task.plan_hash, task.result_json = plan, canonical_hash(plan), result
    user_fields = {"goal_name", "target_minor", "due_at", "amount_minor", "monthly_saving_minor", "monthly_saving_delta_minor", "grantee_name", "scopes", "maximum_amount_minor",
                   "expires_days", "limit_period", "proposal_kind", "payee_name", "card_last4", "behavior_name", "reward_name"}
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY",
                     "slots": {key: {"value": value, "source": "DERIVED_VERSIONED_METHOD" if key in {"points", "estimated_avoided_grams"}
                                      else ("USER_TEXT" if key in user_fields else "AUTHORITATIVE")}
                               for key, value in args.items()}}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
                  decision="ALLOW" if kind in READ_ACTIONS else "REQUIRE_CONFIRMATION",
                  reason_codes=["USER_SCOPED_SYNTHETIC_EXTENSION", "OWNER_OR_DELEGATION_RECHECK"], policy_version="extensions-v1")
    snapshot = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="SYNTHETIC_EXTENSION_FACTS",
                                source_version=READ_SELECTION_VERSION if kind in {"CARBON_VIEW", "FAMILY_VIEW"} and result.get("read_selection") else "extensions-v1",
                                payload_json=result if kind in READ_ACTIONS else args)
    session.add(snapshot)
    if kind in READ_ACTIONS:
        transition_task(task, "SUCCEEDED")
    else:
        transition_task(task, "AWAITING_CONFIRMATION")
        target = next((args.get(key) for key in ("grantee_user_id", "goal_id", "behavior_id", "reward_id") if args.get(key)), None)
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id, node_key=node_key,
            action_type=tool, target_id=target, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}),
            expires_at=utcnow() + timedelta(minutes=5)))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="EXTENSION_ACTION_PREPARED",
                 payload={"kind": kind, "tool": tool, "read_only": kind in READ_ACTIONS, "plan_hash": task.plan_hash})
    return task


def _assert_delegation(session, sid, args):
    delegation = session.scalar(select(FamilyDelegation).where(FamilyDelegation.id == args["delegation_id"],
        FamilyDelegation.sandbox_id == sid, FamilyDelegation.owner_user_id == args["owner_user_id"],
        FamilyDelegation.grantee_user_id == args["grantee_user_id"]).with_for_update())
    scope = "PROPOSE_TRANSFER" if args["proposal_kind"] == "TRANSFER" else "PROPOSE_CARD_LOCK"
    if delegation is None or not _delegation_valid(delegation) or scope not in delegation.scopes_json \
            or delegation.version != args["delegation_version"]:
        raise DomainError("FAMILY_DELEGATION_CHANGED", "委托已失效或范围变化，请本人重新核验", 403)
    if args["proposal_kind"] == "TRANSFER" and args["amount_minor"] > delegation.maximum_amount_minor:
        raise DomainError("FAMILY_AMOUNT_SCOPE_EXCEEDED", "建议金额超出本人委托上限", 403)
    return delegation


def _validate_family_recipient(session, sid, args):
    """Do not wash an owner's exact card/personal alias into a bare username.

    Runs before delegation/funds locks so named registry locks have the same
    global ordering as immediate CNY/FX settlement. Legacy signed bank targets
    remain account-only; legacy identifiable lost references require replan.
    No historical proposal JSON or hash is silently backfilled here.
    """
    if args.get("proposal_kind") != "TRANSFER":
        return
    context = args.get("recipient_context")
    if context:
        if context.get("requested_reference") != args.get("requested_payee_reference") \
                or context.get("recipient_username") != args.get("recipient_username") \
                or context.get("recipient_bank_account_id") != args.get("recipient_account_id"):
            raise DomainError("FAMILY_RECIPIENT_SCOPE_CHANGED", "协助提案的原始收款范围不一致，请重新提出", 409)
        from .contacts import validate_recipient_context
        validate_recipient_context(session, sid, args["owner_user_id"], context)
        return
    from .contacts import find_alias, recipient_context, validate_recipient_context
    # An old externally named contact cannot be remapped by a newly saved alias
    # during owner-task compilation, even though its old carrier is unchanged.
    if args.get("transfer_channel") != "INTERNAL_BANK":
        if find_alias(session, sid, args["owner_user_id"], args.get("payee_name", "")):
            raise DomainError("FAMILY_RECIPIENT_REPLAN_REQUIRED", "原外部联系人已有新的个人身份映射，请重新核对提案，不能按新称呼替换旧目标", 409)
        return
    source = session.get(Task, args.get("source_helper_task_id"))
    if source:
        from .intelligence import rule_proposal
        refs = [a.slots.payee_name for a in rule_proposal(source.message).actions
                if a.kind == "FAMILY_PROPOSE" and a.slots.proposal_kind == "TRANSFER"]
        if any(ref and (ref.lower().startswith(("zhenhang", "xiaozhao"))
                       or find_alias(session, sid, args["owner_user_id"], ref)) for ref in refs):
            raise DomainError("FAMILY_RECIPIENT_REPLAN_REQUIRED", "旧提案没有保存明确卡号或个人身份快照，请重新提出，不补造历史确认范围", 409)
    # Lock and verify the originally signed username/account, without treating
    # this transient compatibility check as a historical alias/card snapshot.
    legacy = recipient_context(session, sid, args["owner_user_id"], args["payee_name"])
    if legacy["recipient_bank_account_id"] != args.get("recipient_account_id"):
        raise DomainError("FAMILY_RECIPIENT_SCOPE_CHANGED", "原实名收款银行账户已变化", 409)
    validate_recipient_context(session, sid, args["owner_user_id"], legacy)


def _family_request_settled(session, proposal):
    if not proposal.owner_task_id:
        return False
    from .runtime_models import RuntimeOperation
    children = select(RuntimeOperation.child_task_id).where(RuntimeOperation.parent_task_id == proposal.owner_task_id)
    tools = ["transfer.commit.v1"] if proposal.proposal_kind == "TRANSFER" else ["card.manage.v1", "card.lock.v1"]
    return session.scalar(select(ToolCall.id).where(ToolCall.task_id.in_(children),
        ToolCall.sandbox_id == proposal.sandbox_id, ToolCall.tool_name.in_(tools), ToolCall.status == "SUCCESS")) is not None


def family_month_used_minor(session, delegation, quota_month):
    used = 0
    for proposal in session.scalars(select(FamilyProposal).where(FamilyProposal.delegation_id == delegation.id,
        FamilyProposal.sandbox_id == delegation.sandbox_id,
        or_(FamilyProposal.quota_month == quota_month, FamilyProposal.quota_month.is_(None)),
        FamilyProposal.proposal_kind == "TRANSFER")):
        if proposal.status == "PENDING_OWNER_APPROVAL":
            used += proposal.args_json["amount_minor"]
        elif proposal.status == "OWNER_TASK_CREATED":
            owner_task = session.get(Task, proposal.owner_task_id)
            if _family_request_settled(session, proposal) or (owner_task and owner_task.state not in {"FAILED", "CLOSED"}):
                used += proposal.args_json["amount_minor"]
    return used


def validate_family_origin(session, owner_parent_task):
    """Recheck a trusted family request at prepare, confirmation and execution.

    Settled receipts remain authoritative after delegation expiry or revocation;
    this function never reverses bank postings or changes a prior receipt.
    """
    from .runtime_models import RuntimeRecord
    record = session.get(RuntimeRecord, owner_parent_task.id)
    if record and record.root_task_id:
        owner_parent_task = session.get(Task, record.root_task_id)
        record = session.get(RuntimeRecord, owner_parent_task.id)
    origin = record.metadata_json.get("family_origin") if record else None
    if not origin:
        legacy = session.scalar(select(FamilyProposal).where(FamilyProposal.owner_task_id == owner_parent_task.id,
            FamilyProposal.owner_user_id == owner_parent_task.user_id, FamilyProposal.sandbox_id == owner_parent_task.sandbox_id))
        if legacy and not _family_request_settled(session, legacy):
            raise DomainError("FAMILY_ORIGIN_UNPROVEN", "旧协助请求缺乏可信来源绑定，请重新提出并受理", 403)
        return {"valid": True, "family_origin": False, "already_settled": False}
    proposal = session.scalar(select(FamilyProposal).where(FamilyProposal.id == origin.get("proposal_id"),
        FamilyProposal.sandbox_id == owner_parent_task.sandbox_id,
        FamilyProposal.owner_user_id == owner_parent_task.user_id).with_for_update())
    if proposal is None or proposal.owner_task_id != owner_parent_task.id:
        raise DomainError("FAMILY_ORIGIN_INVALID", "本人请求与可信协助提案不一致", 403)
    if _family_request_settled(session, proposal):
        return {"valid": True, "family_origin": True, "already_settled": True}
    args = proposal.args_json
    _validate_family_recipient(session, proposal.sandbox_id, args)
    if args.get("recipient_context") and origin.get("approved_recipient_context_hash") != canonical_hash(args["recipient_context"]):
        raise DomainError("FAMILY_APPROVED_REQUEST_CHANGED", "本人受理的明确卡号或身份范围已变化", 403)
    if proposal.proposal_kind == "TRANSFER" and "transfer_channel" in args:
        from .internal_banking import recipient_scope
        payee = session.get(Payee, args.get("payee_id"))
        approved_scope = origin.get("approved_recipient_scope")
        expected_scope = {key: args.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}
        if payee is None or approved_scope != expected_scope or recipient_scope(session, payee) != expected_scope:
            raise DomainError("FAMILY_APPROVED_REQUEST_CHANGED", "家庭提案的收款渠道或实名账户已经变化", 403)
    if not args.get("source_helper_task_id") or not args.get("source_helper_context_hash"):
        raise DomainError("FAMILY_ORIGIN_UNPROVEN", "旧协助提案没有可核验来源，请重新提出", 403)
    target_id = args.get("payee_id") if proposal.proposal_kind == "TRANSFER" else args.get("card_id")
    target_name = args.get("payee_name") if proposal.proposal_kind == "TRANSFER" else args.get("card_last4")
    if origin.get("delegation_id") != proposal.delegation_id or origin.get("delegation_version") != args["delegation_version"] \
            or origin.get("approved_kind") != proposal.proposal_kind or origin.get("approved_target_id") != target_id \
            or origin.get("approved_name") != target_name or origin.get("approved_amount_minor") != args.get("amount_minor") \
            or origin.get("helper_source_task_id") != args.get("source_helper_task_id") \
            or origin.get("source_context_hash") != args.get("source_helper_context_hash") \
            or origin.get("approved_message_hash") != hashlib.sha256(owner_parent_task.message.encode()).hexdigest():
        raise DomainError("FAMILY_APPROVED_REQUEST_CHANGED", "本人已受理的目标、金额或来源发生变化，请重新受理", 403)
    source = session.get(Task, origin.get("helper_source_task_id"))
    if source is None or source.sandbox_id != proposal.sandbox_id or source.user_id != proposal.grantee_user_id \
            or hashlib.sha256(source.message.encode()).hexdigest() != origin.get("source_context_hash"):
        raise DomainError("FAMILY_SOURCE_CHANGED", "协助来源已经变化，不能继续使用旧确认", 403)
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, source)
    delegation = _assert_delegation(session, proposal.sandbox_id, args)
    if delegation.limit_period == "MONTH" and proposal.proposal_kind == "TRANSFER":
        month = business_now(session, proposal.sandbox_id).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m")
        if month != proposal.quota_month:
            raise DomainError("FAMILY_MONTH_SCOPE_CHANGED", "业务月已变化，请在新月份重新提出并受理建议", 403)
        if family_month_used_minor(session, delegation, month) > delegation.maximum_amount_minor:
            raise DomainError("FAMILY_MONTH_LIMIT_EXCEEDED", "本协助委托的业务月累计额度已变化", 403)
    if proposal.proposal_kind == "TRANSFER":
        target = session.scalar(select(Payee).where(Payee.id == target_id, Payee.sandbox_id == proposal.sandbox_id,
            Payee.user_id == proposal.owner_user_id, Payee.status == "ACTIVE"))
        if target is None or target.display_name != target_name:
            raise DomainError("FAMILY_TARGET_CHANGED", "本人已批准收款人事实变化", 403)
    else:
        target = session.scalar(select(Card).where(Card.id == target_id, Card.sandbox_id == proposal.sandbox_id,
            Card.user_id == proposal.owner_user_id, Card.status == "ACTIVE").with_for_update())
        if target is None or target.last4 != target_name or (args.get("card_version") is not None and target.version != args["card_version"]):
            raise DomainError("FAMILY_TARGET_CHANGED", "本人已批准卡片事实变化", 403)
        if args.get("card_version") is not None and (origin.get("approved_card_version") != args["card_version"]
                or origin.get("approved_card_reference") != args.get("requested_card_reference")):
            raise DomainError("FAMILY_APPROVED_REQUEST_CHANGED", "本人受理的卡片引用或版本变化", 403)
    return {"valid": True, "family_origin": True, "already_settled": False}


def _post_goal_entries(session, goal, operation_key, delta):
    session.add_all([
        GoalLedgerEntry(sandbox_id=goal.sandbox_id, user_id=goal.user_id, goal_id=goal.id,
                        operation_key=operation_key, account_ref=f"AVAILABLE:{goal.account_id}", delta_minor=-delta),
        GoalLedgerEntry(sandbox_id=goal.sandbox_id, user_id=goal.user_id, goal_id=goal.id,
                        operation_key=operation_key, account_ref=f"GOAL:{goal.id}", delta_minor=delta),
    ])


def _post_points(session, sid, uid, operation_key, delta):
    session.add_all([
        CarbonPointLedger(sandbox_id=sid, user_id=uid, operation_key=operation_key, account_ref=f"WALLET:{uid}", delta_points=delta),
        CarbonPointLedger(sandbox_id=sid, user_id=uid, operation_key=operation_key, account_ref=f"POOL:{sid}", delta_points=-delta),
    ])


def _commit_business(session, task, kind, args, operation_key):
    sid, uid = task.sandbox_id, task.user_id
    if kind in {"FAMILY_GRANT", "FAMILY_REVOKE"}:
        # Identity eligibility is a read, not a cash mutation. Taking the bank
        # row before the delegation reverses family Transfer's D -> account
        # order. Serialize new/existing grants with the member row instead.
        _account(session, sid, uid)
        member = session.scalar(select(FamilyMember).where(FamilyMember.sandbox_id == sid,
                    FamilyMember.owner_user_id == uid, FamilyMember.helper_user_id == args["grantee_user_id"]).with_for_update())
        if member is None:
            raise DomainError("FAMILY_MEMBER_CHANGED", "协助身份已变化", 409)
        delegation = session.scalar(select(FamilyDelegation).where(FamilyDelegation.owner_user_id == uid,
                                      FamilyDelegation.grantee_user_id == member.helper_user_id).with_for_update())
        if (delegation.version if delegation else None) != args["expected_version"]:
            raise DomainError("FAMILY_DELEGATION_CHANGED", "授权事实已变化", 409)
        if kind == "FAMILY_REVOKE":
            if delegation is None or not _delegation_valid(delegation):
                raise DomainError("FAMILY_DELEGATION_CHANGED", "委托已不再有效", 409)
            delegation.status = "REVOKED"
            delegation.version += 1
        elif delegation:
            delegation.scopes_json = args["scopes"]
            delegation.maximum_amount_minor = args["maximum_amount_minor"]
            delegation.limit_period = args["limit_period"]
            delegation.expires_at = utcnow() + timedelta(days=args["expires_days"])
            delegation.status = "ACTIVE"
            delegation.version += 1
        else:
            delegation = FamilyDelegation(sandbox_id=sid, owner_user_id=uid, grantee_user_id=member.helper_user_id,
                scopes_json=args["scopes"], maximum_amount_minor=args["maximum_amount_minor"],
                limit_period=args["limit_period"],
                expires_at=utcnow() + timedelta(days=args["expires_days"]))
            session.add(delegation)
        session.flush()
        return {"delegation_id": delegation.id, "status": delegation.status, "scopes": delegation.scopes_json,
                "message": "本人细粒度委托已保存。" if kind == "FAMILY_GRANT" else "本人委托已撤销。", "synthetic": True}
    if kind == "FAMILY_PROPOSE":
        _validate_family_recipient(session, sid, args)
        delegation = _assert_delegation(session, sid, args)
        if uid != args["grantee_user_id"]:
            raise DomainError("FAMILY_ACTOR_MISMATCH", "提案主体不一致", 403)
        from .runtime_models import RuntimeRecord
        record = session.get(RuntimeRecord, task.id)
        source_id = record.root_task_id if record and record.root_task_id else args["source_helper_task_id"]
        source = session.get(Task, source_id)
        if source is None or source.sandbox_id != sid or source.user_id != uid \
                or hashlib.sha256(source.message.encode()).hexdigest() != args["source_helper_context_hash"]:
            raise DomainError("FAMILY_SOURCE_CHANGED", "协助建议来源已经变化，请重新提出", 403)
        args = {**args, "source_helper_task_id": source_id}
        if args["proposal_kind"] == "TRANSFER":
            risk = evaluate_transfer_risk(session, context={"sandbox_id": sid, "user_id": args["owner_user_id"],
                    "payee_id": args["payee_id"], "message": _root_message(session, task, {}), "for_commit": True})
            if risk["decision"] != "ALLOW":
                raise DomainError("FAMILY_PROPOSAL_RISK_BLOCKED", "转账风险不允许提案", 403)
        elif args.get("card_version") is not None:
            target = session.scalar(select(Card).where(Card.id == args["card_id"], Card.sandbox_id == sid,
                Card.user_id == args["owner_user_id"]).with_for_update())
            if target is None or target.status != "ACTIVE" or target.version != args["card_version"]:
                raise DomainError("FAMILY_TARGET_CHANGED", "建议明确的本人卡片已变化，请重新提出", 409)
        clock = business_now(session, sid)
        quota_month = clock.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m")
        if args["proposal_kind"] == "TRANSFER" and delegation.limit_period == "MONTH" \
                and family_month_used_minor(session, delegation, quota_month) + args["amount_minor"] > delegation.maximum_amount_minor:
            raise DomainError("FAMILY_MONTH_LIMIT_EXCEEDED", "本协助委托的业务月累计建议额度不足", 403)
        proposal = FamilyProposal(sandbox_id=sid, owner_user_id=args["owner_user_id"], grantee_user_id=uid,
            delegation_id=args["delegation_id"], proposal_kind=args["proposal_kind"], args_json=args, operation_key=operation_key,
            business_created_at=clock, quota_month=quota_month)
        session.add(proposal)
        session.flush()
        return {"proposal_id": proposal.id, "redacted_request_id": proposal.id,
                "status": proposal.status, "bank_action_executed": False, "message": "协助提案已提交，必须由本人受理并单独确认银行操作。", "synthetic": True}
    if kind.startswith("GOAL_"):
        account = _account(session, sid, uid, lock=True)
        if kind == "GOAL_CREATE":
            if session.scalar(select(CashflowGoal.id).where(CashflowGoal.user_id == uid, CashflowGoal.name == args["goal_name"])):
                raise DomainError("GOAL_ALREADY_EXISTS", "同名目标已经存在", 409)
            goal = CashflowGoal(sandbox_id=sid, user_id=uid, account_id=account.id, name=args["goal_name"],
                                target_minor=args["target_minor"], due_at=datetime.fromisoformat(args["due_at"]) if args["due_at"] else None)
            session.add(goal)
            session.flush()
            return {"goal_id": goal.id, "goal_name": goal.name, "target_minor": goal.target_minor,
                    "reserved_minor": 0, "cash_debit_minor": 0, "message": "目标已建立，尚未预留任何资金。", "synthetic": True}
        goal = session.scalar(select(CashflowGoal).where(CashflowGoal.id == args["goal_id"], CashflowGoal.sandbox_id == sid,
                                                       CashflowGoal.user_id == uid, CashflowGoal.account_id == account.id).with_for_update())
        if goal is None or goal.status != "ACTIVE" or goal.version != args["expected_goal_version"] or account.version != args["expected_account_version"]:
            raise DomainError("GOAL_FACTS_CHANGED", "目标或账户事实已变化", 409)
        amount = args["amount_minor"]
        if kind == "GOAL_RESERVE":
            if goal.reserved_minor + amount > goal.target_minor:
                raise DomainError("GOAL_TARGET_EXCEEDED", "预留金额超过目标", 409)
            emergency = session.scalar(select(BudgetEnvelope.reserved_minor).where(BudgetEnvelope.sandbox_id == sid,
                                        BudgetEnvelope.user_id == uid, BudgetEnvelope.label == "应急金")) or 0
            free = account.balance_minor - _all_reserved_cash_minor(session, sid, uid, account.id) - emergency
            if free < amount:
                raise DomainError("GOAL_AVAILABLE_CASH_INSUFFICIENT", "可预留资金不足或会侵占应急金", 409)
            delta = amount
        else:
            if amount > goal.reserved_minor:
                raise DomainError("GOAL_RELEASE_EXCEEDED", "释放金额超过当前目标预留", 409)
            delta = -amount
        goal.reserved_minor += delta
        goal.version += 1
        account.version += 1
        _post_goal_entries(session, goal, operation_key, delta)
        return {"goal_id": goal.id, "goal_name": goal.name, "reserved_minor": goal.reserved_minor,
                "cash_debit_minor": 0, "reserved_goal_delta_minor": delta,
                "message": "目标分账预留已更新，现金总余额没有被伪造扣减。", "synthetic": True}
    if kind in {"CARBON_CLAIM", "CARBON_REDEEM"}:
        wallet = session.scalar(select(CarbonWallet).where(CarbonWallet.id == args["wallet_id"],
            CarbonWallet.sandbox_id == sid, CarbonWallet.user_id == uid).with_for_update())
        pool = session.scalar(select(CarbonPool).where(CarbonPool.sandbox_id == sid).with_for_update())
        if wallet is None or pool is None or wallet.version != args["expected_wallet_version"]:
            raise DomainError("CARBON_WALLET_CHANGED", "积分事实已变化，请重新核对", 409)
        if kind == "CARBON_CLAIM":
            behavior = session.scalar(select(CarbonBehavior).where(CarbonBehavior.id == args["behavior_id"],
                CarbonBehavior.sandbox_id == sid, CarbonBehavior.user_id == uid).with_for_update())
            if behavior is None or behavior.source != "SYNTHETIC_BANK_FEED" or behavior.status != "VERIFIED_SYNTHETIC" \
                    or as_utc(behavior.occurred_at) > business_now(session, sid):
                raise DomainError("CARBON_BEHAVIOR_NOT_VERIFIED", "行为凭据不可用于积分发放", 409)
            if session.scalar(select(CarbonCredit.id).where(CarbonCredit.behavior_id == behavior.id)):
                raise DomainError("CARBON_ALREADY_CLAIMED", "该行为记录已经领取积分", 409)
            if args["methodology_version"] != CARBON_METHOD_VERSION:
                raise DomainError("CARBON_METHOD_CHANGED", "估算方法版本变化，请重新确认", 409)
            grams, points = _estimate_behavior(behavior)
            if grams != args["estimated_avoided_grams"] or points != args["points"] or pool.remaining_points < points:
                raise DomainError("CARBON_ESTIMATE_CHANGED", "行为估算或活动积分池已变化", 409)
            session.add(CarbonCredit(sandbox_id=sid, user_id=uid, behavior_id=behavior.id,
                methodology_version=CARBON_METHOD_VERSION, methodology_json=CARBON_METHOD,
                estimated_avoided_grams=grams, points=points, operation_key=operation_key))
            wallet.points += points
            pool.remaining_points -= points
            _post_points(session, sid, uid, operation_key, points)
            result = {"behavior_id": behavior.id, "estimated_avoided_grams": grams, "credited_points": points,
                      "message": "活动行为记录已按指定版本方法估算并记入本活动积分。"}
        else:
            from .carbon_settlement import reward_settlement_contract,valid_reward_settlement
            if 'settlement' in args and not valid_reward_settlement(args['settlement']):
                raise DomainError('CARBON_SETTLEMENT_CONTRACT_CHANGED','兑换结算规则已变化，请重新核对。',409)
            reward = session.scalar(select(CarbonReward).where(CarbonReward.id == args["reward_id"], CarbonReward.sandbox_id == sid).with_for_update())
            if reward is None or reward.version != args["expected_reward_version"] or reward.cost_points != args["cost_points"]:
                raise DomainError("CARBON_REWARD_CHANGED", "权益事实已变化", 409)
            if reward.stock <= 0 or wallet.points < reward.cost_points:
                raise DomainError("CARBON_REDEMPTION_UNAVAILABLE", "积分或库存不足", 409)
            wallet.points -= reward.cost_points
            pool.remaining_points += reward.cost_points
            reward.stock -= 1
            reward.version += 1
            redemption = CarbonRedemption(sandbox_id=sid, user_id=uid, reward_id=reward.id,
                                            cost_points=reward.cost_points, operation_key=operation_key)
            session.add(redemption)
            session.flush()
            _post_points(session, sid, uid, operation_key, -reward.cost_points)
            result = {"redemption_id": redemption.id, "status": redemption.status,
                      "settlement":reward_settlement_contract(),
                      "reward_name": reward.name, "message": "权益已从本活动库存兑换，仅用于本活动，不代表现金或外部商户已受理。"}
        wallet.version += 1
        return {**result, "wallet_points": wallet.points, "synthetic": True, "certified_carbon_asset": False}
    raise DomainError("EXTENSION_ACTION_UNKNOWN", "扩展动作不支持", 422)


def commit_extension_task(session: Session, task: Task, principal, confirmation: Confirmation,
                          operation_key: str, *, request_hash: str | None = None, node=None) -> Task:
    if task.sandbox_id != principal.sandbox_id or task.user_id != principal.user_id or confirmation.user_id != principal.user_id \
            or confirmation.task_id != task.id or confirmation.sandbox_id != principal.sandbox_id:
        raise DomainError("EXTENSION_ACTOR_MISMATCH", "扩展确认不属于当前主体", 403)
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, task)
    plan_node = task.plan_json["nodes"][0]
    kind, args, tool = plan_node["action_kind"], plan_node["args"], plan_node["tool_name"]
    expected_target = next((args.get(key) for key in ("grantee_user_id", "goal_id", "behavior_id", "reward_id") if args.get(key)), None)
    if is_family_helper(session, task.sandbox_id, task.user_id) and kind != "FAMILY_PROPOSE":
        raise DomainError("FAMILY_HELPER_WRITE_FORBIDDEN", "协助身份不能执行本人资金、卡片或权益写操作", 403)
    if confirmation.status != "PENDING" or as_utc(confirmation.expires_at) <= utcnow() \
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation.plan_hash != task.plan_hash \
            or confirmation.action_type != tool or confirmation.node_key != plan_node["node_id"] \
            or confirmation.target_id != expected_target \
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}):
        raise DomainError("EXTENSION_CONFIRMATION_INVALID", "扩展确认已失效或内容不一致", 409)
    if task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}:
        raise DomainError("TASK_NOT_CONFIRMABLE", "当前扩展任务不能确认", 409)
    result = _commit_business(session, task, kind, args, operation_key)
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == plan_node["node_id"]))
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    task.result_json = result
    finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool, idempotency_key=operation_key,
                        request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}),
                        status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="EXTENSION_ACTION_COMMITTED",
                 payload={"kind": kind, "operation_key": operation_key, "synthetic": True})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
                             payload_json={"task_id": task.id, "action": kind}))
    return task


def dispatch_extension_task(session: Session, *, task: Task, principal, request, idempotency_key: str) -> Task:
    from .tools import ToolGateway
    if task.user_id != principal.user_id or task.sandbox_id != principal.sandbox_id:
        raise DomainError("EXTENSION_ACTOR_MISMATCH", "扩展任务不属于当前主体", 403)
    if not task.plan_json.get("nodes"):
        raise DomainError("NO_CONFIRMABLE_ACTION", "当前扩展任务没有可执行确认动作", 409)
    plan_node = task.plan_json["nodes"][0]
    request_hash, node, confirmation, duplicate = ToolGateway().authorize_write(
        session, task=task, sandbox_id=principal.sandbox_id, user_id=principal.user_id, request=request,
        idempotency_key=idempotency_key, tool_name=plan_node["tool_name"], node_key=plan_node["node_id"])
    if duplicate:
        return task
    return commit_extension_task(session, task, principal, confirmation, idempotency_key, request_hash=request_hash, node=node)


def owner_review_family_proposal(session, principal, proposal_id, decision, expected_version):
    from .runtime import accept_task
    proposal = session.scalar(select(FamilyProposal).where(FamilyProposal.id == proposal_id,
                                      FamilyProposal.sandbox_id == principal.sandbox_id).with_for_update())
    if proposal is None:
        raise DomainError("FAMILY_PROPOSAL_NOT_FOUND", "协助提案不存在", 404)
    if proposal.owner_user_id != principal.user_id:
        raise DomainError("FAMILY_OWNER_REVIEW_REQUIRED", "只有本人可以受理协助提案", 403)
    # Review only creates an owner task; it never moves cash. Do not acquire a
    # funds lock before the delegation used by an already executing proposal.
    _account(session, principal.sandbox_id, principal.user_id)
    if decision not in {"APPROVE", "REJECT"}:
        raise DomainError("FAMILY_REVIEW_INVALID", "请明确接受或拒绝提案", 422)
    if proposal.status == "OWNER_TASK_CREATED" and decision == "APPROVE":
        existing = session.get(Task, proposal.owner_task_id)
        return {"proposal_id": proposal.id, "owner_task_id": proposal.owner_task_id, "status": proposal.status,
                "owner_task_state": existing.state if existing else None,
                "requires_owner_confirmation": bool(existing and existing.state not in {"SUCCEEDED", "FAILED", "CLOSED"}),
                "bank_action_executed": None if existing and existing.state == "UNKNOWN" else bool(existing and existing.state == "SUCCEEDED")}
    if proposal.version != expected_version or proposal.status != "PENDING_OWNER_APPROVAL":
        raise DomainError("FAMILY_PROPOSAL_CHANGED", "提案已经处理或版本变化", 409)
    if decision == "REJECT":
        proposal.status = "REJECTED"
        proposal.version += 1
        return {"proposal_id": proposal.id, "status": proposal.status, "bank_action_executed": False}
    args = proposal.args_json
    if not args.get("source_helper_task_id") or not args.get("source_helper_context_hash"):
        raise DomainError("FAMILY_ORIGIN_UNPROVEN", "旧协助提案缺乏可核验来源，请重新提出", 403)
    source = session.get(Task, args["source_helper_task_id"])
    if source is None or (source.sandbox_id, source.user_id) != (proposal.sandbox_id, proposal.grantee_user_id) \
            or hashlib.sha256(source.message.encode()).hexdigest() != args["source_helper_context_hash"]:
        raise DomainError("FAMILY_SOURCE_CHANGED", "协助来源已经变化，不能受理旧提案", 403)
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, source)
    _validate_family_recipient(session, principal.sandbox_id, args)
    _assert_delegation(session, principal.sandbox_id, args)
    if proposal.proposal_kind == "TRANSFER":
        payee = session.scalar(select(Payee).where(Payee.id == args["payee_id"], Payee.sandbox_id == principal.sandbox_id,
            Payee.user_id == principal.user_id, Payee.status == "ACTIVE"))
        if payee is None or payee.display_name != args["payee_name"]:
            raise DomainError("FAMILY_TARGET_CHANGED", "本人收款人事实变化", 409)
        if "transfer_channel" in args:
            from .internal_banking import recipient_scope
            if recipient_scope(session, payee) != {key: args.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}:
                raise DomainError("FAMILY_TARGET_CHANGED", "家庭提案收款渠道已经变化", 409)
        risk = evaluate_transfer_risk(session, context={"sandbox_id": principal.sandbox_id, "user_id": principal.user_id,
                                                       "payee_id": payee.id, "for_commit": True})
        if risk["decision"] != "ALLOW":
            raise DomainError("FAMILY_PROPOSAL_RISK_BLOCKED", "收款风险不允许受理转账", 403)
        amount = args["amount_minor"]
        reference = args.get("requested_payee_reference") or payee.display_name
        message = f"给{reference}转{amount // 100}.{amount % 100:02d}元"
    else:
        card = session.scalar(select(Card).where(Card.id == args["card_id"], Card.sandbox_id == principal.sandbox_id,
                                                 Card.user_id == principal.user_id, Card.status == "ACTIVE"))
        if card is None or card.last4 != args["card_last4"] or (args.get("card_version") is not None and card.version != args["card_version"]):
            raise DomainError("FAMILY_TARGET_CHANGED", "本人卡片事实变化", 409)
        reference = args.get("requested_card_reference") or card.last4
        label = "卡号" if reference.lower().startswith(("zhenhang", "xiaozhao")) else "尾号"
        message = f"临时锁卡，{label}{reference}"
    task = accept_task(session, principal, message, idempotency_key=f"family-owner:{proposal.id}")
    proposal.status, proposal.owner_task_id = "OWNER_TASK_CREATED", task.id
    proposal.version += 1
    from .runtime_models import RuntimeRecord
    record = session.get(RuntimeRecord, task.id)
    record.metadata_json = {**record.metadata_json, "family_origin": {
        "proposal_id": proposal.id, "delegation_id": proposal.delegation_id, "delegation_version": args["delegation_version"],
        "approved_kind": proposal.proposal_kind, "approved_target_id": args.get("payee_id") or args.get("card_id"),
        "approved_name": args.get("payee_name") or args.get("card_last4"), "approved_amount_minor": args.get("amount_minor"),
        "approved_recipient_scope": {key: args.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")} if "transfer_channel" in args else None,
        **({"approved_recipient_context_hash": canonical_hash(args["recipient_context"])} if args.get("recipient_context") else {}),
        **({"approved_card_version": args["card_version"], "approved_card_reference": args.get("requested_card_reference")}
           if args.get("card_version") is not None else {}),
        "helper_source_task_id": args["source_helper_task_id"], "source_context_hash": args["source_helper_context_hash"],
        "approved_message_hash": hashlib.sha256(message.encode()).hexdigest(), "quota_month": proposal.quota_month,
    }}
    validate_family_origin(session, task)
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=task.id, event_type="FAMILY_OWNER_REQUEST_ACCEPTED",
                 payload={"proposal_id": proposal.id, "requires_owner_confirmation": True})
    return {"proposal_id": proposal.id, "owner_task_id": task.id, "status": proposal.status,
            "requires_owner_confirmation": True, "bank_action_executed": False}
