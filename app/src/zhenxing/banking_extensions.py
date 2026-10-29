"""Scoped scheduled transfers and stateful AA collection in the synthetic bank."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from sqlalchemy import or_, and_, select
from sqlalchemy.orm import Session

from .audit import append_audit
from .banking_extension_models import AACollectionRequest, AALedgerEntry, AAParticipant, ScopedScheduledTransfer
from .contracts import DomainError, FinancialIntentContract
from .intelligence import _chinese_integer, amounts_in_text
from .models import Account, Confirmation, LedgerEntry, OutboxMessage, Payee, Task, TaskNode, ToolCall, Transfer, utcnow
from .planning import canonical_hash
from .posting import record_cash_transaction
from .platform import add_task_nodes, append_message, record_policy
from .simulation import as_utc, business_now
from .simulation_models import SimulationState
from .tools import ToolGateway
from .transfers import parse_transfer_request
from .workflow import finish_node, transition_task


LOCAL_TZ = timezone(timedelta(hours=8))
TRANSFER_LIMIT_MINOR = 5_000_000
SCHEDULE_TOOL = "transfer.schedule.v1"
AA_TOOL = "transfer.aa.v1"


def parse_schedule_date(message: str, clock: datetime) -> datetime | None:
    """Resolve a bounded date against the caller's trusted China-time clock."""
    now = as_utc(clock).astimezone(LOCAL_TZ)
    # Unsupported foreign offsets must never be silently interpreted as China
    # time. The original English evidence layer also refuses these expressions.
    timezone_check = re.sub(r"\b(?:UTC|GMT)\s*\+0?8(?::?00)?\b", "", message, flags=re.I)
    if re.search(r"\b(?:PST|PDT|EST|EDT|CST|MST|GMT|UTC|timezone)\b|\b(?:New\s+York|London|Los\s+Angeles|my\s+local\s+time)\b|\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})", timezone_check, re.I):
        return None
    match = re.search(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", message)
    if match:
        try:
            day = now.replace(year=int(match.group(1)), month=int(match.group(2)), day=int(match.group(3)))
        except ValueError:
            return None
    elif (match := re.search(r"(?<!\d)(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", message)):
        try:
            day = now.replace(year=int(match.group(1)) if match.group(1) else now.year,
                              month=int(match.group(2)), day=int(match.group(3)))
        except ValueError:
            return None
    elif "后天" in message:
        day = now + timedelta(days=2)
    elif "明天" in message:
        day = now + timedelta(days=1)
    elif "今天" in message or re.search(r"\btoday\b", message, re.I):
        day = now
    elif re.search(r"\btomorrow\b", message, re.I):
        day = now + timedelta(days=1)
    elif (match := re.search(r"\bnext\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", message, re.I)):
        weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(match[1].lower())
        day = now + timedelta(days=7 - now.weekday() + weekday)
    elif (match := re.search(r"下周([一二三四五六日天])?", message)):
        weekday = "一二三四五六日".find(match.group(1) or "一")
        if match.group(1) == "天":
            weekday = 6
        day = now + timedelta(days=7 - now.weekday() + weekday)
    else:
        return None
    time = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", message)
    if not time:
        time = re.search(r"(?<!\d)(\d{1,2}|[零一二三四五六七八九十]{1,3})点(?:(\d{1,2}|[零一二三四五六七八九十]{1,3})分)?", message)
    if not time:
        time = re.search(r"(?<![\d.])(\d{1,2})\s*(AM|PM)\b", message, re.I)
        if time:
            hour = int(time[1])
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if time[2].lower() == "pm" else 0)
            return day.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if time:
        number = lambda token: int(token) if token.isdigit() else _chinese_integer(token)
        hour, minute = number(time.group(1)), number(time.group(2)) if time.group(2) else 0
    else:
        hour, minute = 9, 0
    if ("下午" in message or "晚上" in message) and hour < 12:
        hour += 12
    marker = re.search(r"\d{1,2}:\d{2}\s*(AM|PM)\b", message, re.I)
    if marker:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if marker[1].lower() == "pm" else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def _schedule_clock(session, task, *, basis=None, calendar_time=None):
    from .operational_clock import registered_customer
    from .reporting_clock import calendar_now
    if basis is None:
        basis = "CALENDAR_REAL" if registered_customer(session, task.sandbox_id, task.user_id) else "BANK_CONTROLLED"
    if basis not in {"CALENDAR_REAL", "BANK_CONTROLLED"}:
        raise DomainError("SCHEDULE_CLOCK_CHANGED", "预约时钟范围无法核验，请重新安排", 409)
    clock = as_utc(calendar_time) if calendar_time is not None and basis == "CALENDAR_REAL" else calendar_now() if basis == "CALENDAR_REAL" else business_now(session, task.sandbox_id)
    return basis, clock


def _account(session, task):
    return session.scalar(select(Account).where(Account.sandbox_id == task.sandbox_id, Account.user_id == task.user_id))


def _plan(session, task, conversation_id, tool, node_key, args, target_id, message):
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.requested_capabilities = [tool]
    fic.slots = {key: {"value": value, "source": (
        ("DERIVED_FROM_CALENDAR_CLOCK" if args.get("clock_basis") == "CALENDAR_REAL" else "DERIVED_FROM_BUSINESS_CLOCK") if key == "due_at" else
        "POLICY" if key in {"max_amount_minor", "remainder_rule", "clock_basis", "time_zone"} else
        "AUTHORITATIVE" if key.endswith("_id") or key in {"payee_last4", "participants", "recipient_context"} else "USER_TEXT")}
        for key, value in args.items()}
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    plan = {"plan_version": 1, "policy_version": "banking-extra-v1", "task_id": task.id,
            "sandbox_id": task.sandbox_id, "nodes": [{"node_id": node_key, "tool_name": tool,
                "read_only": False, "requires_confirmation": True, **args}]}
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
                  decision="REQUIRE_CONFIRMATION", reason_codes=["USER_REQUESTED_SCOPED_ACTION"], policy_version="banking-extra-v1")
    transition_task(task, "AWAITING_CONFIRMATION")
    session.add(Confirmation(sandbox_id=task.sandbox_id, user_id=task.user_id, task_id=task.id,
        node_key=node_key, action_type=tool, target_id=target_id, plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash": task.plan_hash, "scope": args}), expires_at=utcnow()+timedelta(minutes=5)))
    task.result_json = {"message": message, "simulated": True}
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="BANKING_EXTENSION_PROPOSED",
                 payload={"tool": tool, "plan_hash": task.plan_hash})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=message)
    session.flush()
    return task


def prepare_extra_task(session: Session, *, task: Task, conversation_id: str,
                       proposed_action: dict | None = None, source_context: dict | None = None) -> Task:
    from .compiled_actions import KEY
    # A verified semantic proposal has already bound each requested change to
    # its literal/identity source. A negated unrelated clause ("其它不要动")
    # must not veto that validated edit through this legacy whole-text guard.
    if KEY not in (source_context or {}) and re.search(r"如果|假如|一旦|只有|不要|先别|撤销|撤回", task.message):
        task.result_json = {"message": "请先明确本次操作请求与前置条件；未确认条件、否定或撤销不会登记转账/收款。"}
        return task
    values = (proposed_action or {}).get("slots") or {}
    account = _account(session, task)
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "未找到付款账户", 404)
    if task.intent_type == "TRANSFER_SCHEDULE":
        if values.get('schedule_reference'):
            from .schedule_amendments import prepare_amendment
            from .services import Principal
            return prepare_amendment(session, task=task, principal=Principal(task.user_id, task.sandbox_id),
                conversation_id=conversation_id, values=values, source_context=source_context)
        parsed = ((values["payee_name"], values["amount_minor"], values.get("memo") or "")
                  if values.get("payee_name") and values.get("amount_minor") else parse_transfer_request(task.message))
        clock_basis, clock = _schedule_clock(session, task)
        from .temporal_fields import temporal_field_clock
        date_clock=temporal_field_clock(session,task,source_context,'scheduled_at',values['scheduled_at']) if values.get('scheduled_at') else None
        from .transfer_authorization_dates import scheduled_instant
        due = scheduled_instant(values['scheduled_at'],date_clock or clock) if values.get('scheduled_at') else parse_schedule_date(task.message,clock)
        if values.get('schedule_start_month') is not None or values.get('schedule_day_of_month') is not None:
            from .schedule_calendar_fields import monthly_first
            if values.get('schedule_frequency') != 'MONTHLY' or values.get('schedule_start_month') is None or values.get('schedule_day_of_month') is None:
                task.result_json={'error':'SCHEDULE_MONTHLY_ANCHOR_REQUIRED','message':'请明确从哪年哪月开始，以及每月几日执行。','missing_fields':['schedule_start_month','schedule_day_of_month']}
                return task
            selected_first=monthly_first(values['schedule_start_month'],values['schedule_day_of_month'])
            if due is not None and values.get('scheduled_at') and (due.astimezone(LOCAL_TZ).year,due.astimezone(LOCAL_TZ).month,due.astimezone(LOCAL_TZ).day)!=(selected_first.year,selected_first.month,selected_first.day):
                raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','首次日期和月度安排不一致，请核对。',422)
            if not values.get('scheduled_at'):due=selected_first.astimezone(timezone.utc)
        if values.get('schedule_occurrence_times'):
            from .transfer_recurrence import explicit_occurrence_dates
            temporal_field_clock(session,task,source_context,'schedule_occurrence_times',values['schedule_occurrence_times'])
            explicit_dates=explicit_occurrence_dates(values['schedule_occurrence_times'],allow_single=
                values.get('schedule_frequency') in {None,'ONCE'} and values.get('schedule_occurrence_count') in {None,1})
            if values.get('scheduled_at') and (due is None or as_utc(due)!=explicit_dates[0]):
                raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','首次日期与指定的全部执行时点冲突。',422)
            due=explicit_dates[0]
        if values.get("scheduled_at") and re.fullmatch(r'\d{4}-\d{2}-\d{2}T.+',values["scheduled_at"].strip()):
            try:
                exact = datetime.fromisoformat(values["scheduled_at"])
                due = as_utc(exact) if exact.tzinfo is not None else None
            except ValueError:
                due = None
        if parsed is None or due is None:
            task.result_json = ({'error':'SCHEDULE_DATE_REQUIRED','message':'请明确唯一的预约执行日期和时间；收款人及金额已保留，不会自动选择多个日期中的一个。','missing_fields':['scheduled_at']}
                if parsed is not None else {"message": "请明确转账收款人、金额和日期，例如：明天给房东转100元。"})
            return task
        name, amount, memo = parsed
        if due <= clock:
            task.result_json = {'error':'SCHEDULE_DATE_PAST','message':"定时日期须晚于当前预约日历；过去的安排不会立即扣款，请明确新的日期。",'missing_fields':['scheduled_at']}
            return task
        from .internal_banking import resolve_transfer_payees, recipient_scope
        recipient_selection = values.get("recipient_card_reference")
        recipient_reference = recipient_selection
        if recipient_reference:
            from .services import Principal
            from .transfer_payment_scope import selected_recipient_reference
            recipient_reference = selected_recipient_reference(session, Principal(task.user_id, task.sandbox_id), name, recipient_reference)
        payees = resolve_transfer_payees(session, task.sandbox_id, task.user_id, recipient_reference or name)
        if len(payees) != 1:
            task.result_json = {"message": "请明确唯一且有效的收款人。",
                                "payee_candidates": [{"payee_id": p.id, "name": p.display_name, "last4": p.account_last4} for p in payees]}
            return task
        if amount > TRANSFER_LIMIT_MINOR:
            task.result_json = {"message": "金额超过当前转账限额，未创建定时安排。"}
            transition_task(task, "FAILED")
            return task
        payee = payees[0]
        destination = recipient_scope(session, payee)
        payment_scope = None
        from .operational_clock import registered_customer
        if registered_customer(session, task.sandbox_id, task.user_id):
            from .services import Principal
            from .transfer_payment_scope import resolve_transfer_payment_scope, selected_source_reference
            payment_scope = resolve_transfer_payment_scope(session, Principal(task.user_id, task.sandbox_id), payee=payee,
                payee_reference=recipient_reference or name,
                source_card_reference=selected_source_reference(session, Principal(task.user_id, task.sandbox_id), values),
                recipient_card_reference=(recipient_selection if recipient_selection == "REGISTERED_DEFAULT" else recipient_reference),
                amount_minor=amount, require_available=False)
            account = session.get(Account, payment_scope["source"]["account_id"])
        from .transfer_recurrence import recurrence_scope
        end_clock=temporal_field_clock(session,task,source_context,'schedule_end_at',values['schedule_end_at']) if values.get('schedule_end_at') else None
        try:recurrence = recurrence_scope(values, due, clock=end_clock or clock)
        except DomainError as error:
            from .compiled_actions import KEY
            if error.status_code!=422 or KEY not in (source_context or {}):raise
            task.result_json={'error':error.code,'message':error.message,'missing_fields':['schedule_end_at'] if values.get('schedule_end_at') else ['schedule_occurrence_count']}
            return task
        args = {"source_account_id": account.id, "payee_id": payee.id, "payee_name": payee.display_name,
                **destination,
                "payee_last4": payee.account_last4, "amount_minor": amount, "memo": memo,
                "due_at": due.isoformat(), "max_amount_minor": TRANSFER_LIMIT_MINOR,
                "clock_basis": clock_basis, "time_zone": "Asia/Shanghai"}
        from .schedule_presentation import timing_policy
        args['timing_policy'] = timing_policy(values.get('scheduled_at') or task.message)
        if payment_scope:
            args["transfer_payment_scope"] = payment_scope
        if recurrence:
            args["recurrence"] = recurrence
        if destination["transfer_channel"] == "INTERNAL_BANK":
            from .contacts import recipient_context
            # Keep the user's exact reference, not merely its resolved Payee.
            # A saved personal role or explicitly chosen card remains part of
            # this authorization when the bank business clock later reaches due.
            args["recipient_context"] = recipient_context(session, task.sandbox_id, task.user_id, recipient_reference or name)
        return _plan(session, task, conversation_id, SCHEDULE_TOOL, "schedule-transfer", args, payee.id,
                     f"将在银行业务时间 {due.astimezone(LOCAL_TZ).isoformat()} 向 {name} 转账 {amount/100:.2f} 元；"
                     + (f"频率{recurrence['frequency']}，共{recurrence['occurrence_count']}次，单次{amount/100:.2f}元；任一期失败停止后续。" if recurrence else "确认仅授权本次。")
                     + "到期再次核验收付款卡、身份、限额和当前可支出余额。")
    if task.intent_type != "AA_REQUEST":
        raise DomainError("INTENT_NOT_ALLOWED", "不支持的转账扩展类型", 403)
    amounts = {values["amount_minor"]} if values.get("amount_minor") else amounts_in_text(task.message)
    named = re.search(r"(?:参与者|名单|联系人)[：:]?\s*([^。；;]+)", task.message)
    names = values.get("participant_names") or ([name.strip() for name in re.split(r"[、,，和]", named.group(1)) if name.strip()] if named else [])
    count = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*人", task.message)
    requested_count = values.get("participant_count") or ((int(count.group(1)) if count.group(1).isdigit() else _chinese_integer(count.group(1))) if count else None)
    if len(amounts) != 1 or not names or len(set(names)) != len(names) or not 2 <= len(names) <= 20 \
            or (requested_count is not None and requested_count != len(names)):
        task.result_json = {"message": "请明确 AA 总额与完整联系人名单；人数须与名单一致，例如：AA总额100元，2人，参与者：妈妈、房东。"}
        return task
    total = next(iter(amounts))
    if total < len(names) or total > TRANSFER_LIMIT_MINOR:
        task.result_json = {"message": "AA 总额须保证每人至少一分，且不超过当前限额。"}
        return task
    collector_scope = None
    from .operational_clock import registered_customer
    if registered_customer(session, task.sandbox_id, task.user_id):
        from .access_models import LoginAccount
        from .payment_instruments import resolve_recipient_instrument
        from .services import Principal
        login = session.scalar(select(LoginAccount).where(LoginAccount.user_id == task.user_id, LoginAccount.sandbox_id == task.sandbox_id))
        collector_scope = resolve_recipient_instrument(session, login.username, values.get("recipient_card_reference"),
            "CNY", caller_principal=Principal(task.user_id, task.sandbox_id))
        account = session.get(Account, collector_scope["account_id"])
    participants = []
    participant_identities = set()
    for name in names:
        # The same scoped identity resolver used by ordinary transfers accepts
        # an exact active bank username without requiring a pre-created alias.
        from .internal_banking import resolve_transfer_payees,recipient_scope
        try:
            from .aa_participant_reference import verified_aa_participant_reference
            participant_reference = verified_aa_participant_reference(session, name)
            payees = resolve_transfer_payees(session,task.sandbox_id,task.user_id,participant_reference)
        except DomainError as error:
            task.result_json={"message":error.message,"error":error.code}
            return task
        if len(payees) != 1:
            task.result_json = {"message": f"联系人 {name} 不存在或不唯一，请补充完整名单。"}
            return task
        if collector_scope and recipient_scope(session, payees[0])["transfer_channel"] != "INTERNAL_BANK":
            task.result_json = {"message": "AA参与者须为可各自登录确认付款的平台账号；外部联系人暂不支持此收款流程。",
                                "error": "AA_REGISTERED_PARTICIPANTS_REQUIRED"}
            return task
        identity = ("BANK", recipient_scope(session, payees[0])["recipient_username"]) if collector_scope else ("PAYEE", payees[0].id)
        if identity in participant_identities:
            task.result_json = {"message": "名单中有多项实际指向同一参与人，请按真实参与人去重后重新核对。", "error": "AA_PARTICIPANT_DUPLICATE"}
            return task
        participant_identities.add(identity)
        participants.append(payees[0])
    base, remainder = divmod(total, len(participants))
    shares = [{"payee_id": p.id, "name": p.display_name, "last4": p.account_last4,
               "amount_minor": base + (index < remainder),"recipient_scope":recipient_scope(session,p)} for index, p in enumerate(participants)]
    args = {"account_id": account.id, "total_minor": total, "participant_count": len(shares),
            "participants": shares, "remainder_rule": "名单前序参与者各多一分"}
    if collector_scope:
        args["aa_collector_scope"] = collector_scope
        from .transfer_authorization_dates import authorization_deadline
        current = utcnow()
        from .temporal_fields import temporal_field_clock
        expiry_clock=temporal_field_clock(session,task,source_context,'aa_expires_at',values['aa_expires_at']) if values.get('aa_expires_at') else None
        expiry = authorization_deadline(values["aa_expires_at"], expiry_clock or current) if values.get("aa_expires_at") else current + timedelta(hours=24)
        if expiry is None or not current < as_utc(expiry) <= current + timedelta(days=30):
            from .compiled_actions import KEY
            if KEY in (source_context or {}):
                task.result_json={'error':'AA_EXPIRY_INVALID','message':'请明确未来30天内唯一的AA截止日期；不会自动延长原截止时间。','missing_fields':['aa_expires_at']}
                return task
            raise DomainError("AA_EXPIRY_INVALID", "AA有效期须为未来30天内的明确日期。", 422)
        args["aa_expires_at"] = as_utc(expiry).isoformat()
    if values.get("aa_note"):
        args["aa_note"] = values["aa_note"]
    return _plan(session, task, conversation_id, AA_TOOL, "create-aa-request", args, account.id,
                 f"准备向 {len(shares)} 名联系人发起合计 {total/100:.2f} 元 AA 收款请求；余数按名单顺序各多一分，确认不会代参与者付款。"
                 + (f"收款卡尾号{collector_scope['display_last4']}，CNY；有效期至{args['aa_expires_at']}，各参与者须自行登录选卡并确认。" if collector_scope else ""))


def _validate_schedule_recipient_context(session, sandbox_id, user_id, scope):
    # Historical schedules have no identity snapshot. Preserve their signed
    # scope rather than backfilling it from a possibly changed alias today.
    if "recipient_context" not in scope:
        return
    snapshot = scope["recipient_context"]
    required = {"requested_reference", "recipient_login_account_id", "recipient_bank_account_id", "recipient_username"}
    if scope.get("transfer_channel") != "INTERNAL_BANK" or not isinstance(snapshot, dict) \
            or not required.issubset(snapshot) \
            or (snapshot["recipient_username"], snapshot["recipient_bank_account_id"]) != (
                scope.get("recipient_username"), scope.get("recipient_account_id")):
        raise DomainError("RECIPIENT_SCOPE_CHANGED", "定时收款身份快照与确认的账户范围不一致", 409)
    from .contacts import validate_recipient_context
    # The shared validator takes all participating LoginAccount locks sorted
    # before alias/card locks, and before the common bank Account locks.
    validate_recipient_context(session, sandbox_id, user_id, snapshot)


def dispatch_extra_task(session: Session, *, task: Task, principal, request, idempotency_key: str) -> Task:
    if principal.user_id != task.user_id or principal.sandbox_id != task.sandbox_id:
        raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
    schedule = task.intent_type == "TRANSFER_SCHEDULE"
    if not schedule and task.intent_type != "AA_REQUEST":
        raise DomainError("INTENT_NOT_ALLOWED", "不支持的转账扩展类型", 403)
    tool, key = (SCHEDULE_TOOL, "schedule-transfer") if schedule else (AA_TOOL, "create-aa-request")
    request_hash, node, confirmation, duplicate = ToolGateway().authorize_write(session, task=task,
        sandbox_id=task.sandbox_id, user_id=task.user_id, request=request, idempotency_key=idempotency_key,
        tool_name=tool, node_key=key)
    if duplicate:
        return task
    args = next((item for item in task.plan_json.get("nodes", []) if item.get("node_id") == key), None)
    if args is None or node is None or confirmation is None:
        raise DomainError("PLAN_NODE_MISSING", "执行节点缺失", 409)
    scope = {name: value for name, value in args.items() if name not in {"node_id", "tool_name", "read_only", "requires_confirmation"}}
    if confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "scope": scope}):
        raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认范围发生变化", 409)
    if schedule:
        if scope.get('replaces_schedule'):
            from .schedule_amendments import validate_replacement
            validate_replacement(session, principal, scope['replaces_schedule'], lock=True)
        payee = session.scalar(select(Payee).where(Payee.id == scope["payee_id"], Payee.sandbox_id == task.sandbox_id,
                                                Payee.user_id == task.user_id, Payee.status == "ACTIVE"))
        if payee is None or confirmation.target_id != payee.id or payee.account_last4 != scope["payee_last4"]:
            raise DomainError("TRANSFER_TARGET_CHANGED", "收款人已发生变化", 409)
        _validate_schedule_recipient_context(session, task.sandbox_id, task.user_id, scope)
        if scope.get("transfer_payment_scope"):
            from .internal_banking import lock_transfer_accounts
            from .services import Principal
            from .transfer_payment_scope import validate_transfer_payment_scope
            payment=scope["transfer_payment_scope"]
            if (payment.get("amount_minor"),payment.get("currency"),
                    (payment.get("source") or {}).get("account_id"),(payment.get("recipient") or {}).get("account_id")) != (
                    scope["amount_minor"],"CNY",scope["source_account_id"],scope.get("recipient_account_id")):
                raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "确认收付款卡与预约范围不一致，未登记。", 409)
            # Same sorted owner/account locks as actual posting. Future funds
            # are not reserved here, but a changed/locked/default-switched card
            # cannot be accepted as a newly confirmed authorization.
            lock_transfer_accounts(session,task.sandbox_id,task.user_id,scope["source_account_id"],payee)
            validate_transfer_payment_scope(session,Principal(task.user_id,task.sandbox_id),payment,
                scheduled=True,require_funds=False)
        due = datetime.fromisoformat(scope["due_at"])
        _, clock = _schedule_clock(session, task, basis=scope.get("clock_basis", "BANK_CONTROLLED"))
        if due <= clock:
            raise DomainError("SCHEDULE_DATE_PASSED", "定时日期已过，请重新安排", 409)
        from .schedule_amendments import complete_replacement
        complete_replacement(session, principal, scope)
        row = ScopedScheduledTransfer(sandbox_id=task.sandbox_id, user_id=task.user_id, task_id=task.id,
            confirmation_id=confirmation.id, source_account_id=scope["source_account_id"], payee_id=payee.id,
            amount_minor=scope["amount_minor"], memo=scope["memo"], due_at=due,
            business_key=f"schedule:{task.id}", plan_hash=task.plan_hash, scope_json=scope)
        session.add(row)
        session.flush()
        from .transfer_recurrence import register_series
        register_series(session, row)
        node.status = "WAITING_TIME"
        node.version += 1
        node.result_json = {"schedule_id": row.id, "due_at": scope["due_at"], "status": "WAITING",
                            "clock_basis": scope.get("clock_basis", "BANK_CONTROLLED"), "time_zone": "Asia/Shanghai"}
        transition_task(task, "WAITING")
        task.result_json = {"message": "定时转账已登记，等待约定日期到期；当前未扣款。", **node.result_json, "simulated": True}
    else:
        if scope.get("aa_collector_scope"):
            from .payment_instruments import validate_recipient_snapshot
            validate_recipient_snapshot(session, scope["aa_collector_scope"], lock=True)
        if confirmation.target_id != scope["account_id"] or session.scalar(select(Account).where(
            Account.id == scope["account_id"], Account.sandbox_id == task.sandbox_id,
            Account.user_id == task.user_id)) is None:
            raise DomainError("AA_ACCOUNT_CHANGED", "AA 收款账户发生变化", 409)
        row = AACollectionRequest(sandbox_id=task.sandbox_id, user_id=task.user_id, task_id=task.id,
            account_id=scope["account_id"], total_minor=scope["total_minor"],
            participant_count=scope["participant_count"], idempotency_key=f"aa:{task.id}")
        session.add(row)
        session.flush()
        real_participants = []
        for index, participant in enumerate(scope["participants"]):
            payee = session.scalar(select(Payee).where(Payee.id == participant["payee_id"],
                Payee.sandbox_id == task.sandbox_id, Payee.user_id == task.user_id, Payee.status == "ACTIVE"))
            if payee is None:
                raise DomainError("AA_CONTACT_CHANGED", "参与者已发生变化", 409)
            if participant.get("recipient_scope"):
                from .internal_banking import _validate_expected_scope,_bound_entities
                _validate_expected_scope(session,payee,participant["recipient_scope"])
                binding,login,bank=_bound_entities(session,payee)
                if binding and (login.status!="ACTIVE" or login.role not in {"CUSTOMER","DEV_TEST"}):
                    raise DomainError("AA_CONTACT_CHANGED","参与账号当前不可使用，请重新核对名单。",409)
            added = AAParticipant(request_id=row.id, sandbox_id=task.sandbox_id, payee_id=payee.id,
                display_name=payee.display_name, ordinal=index, amount_minor=participant["amount_minor"],
                payment_key=f"aa:{row.id}:{index}")
            session.add(added); session.flush()
            if scope.get("aa_collector_scope"):
                if not binding or login is None:
                    raise DomainError("AA_REGISTERED_PARTICIPANTS_REQUIRED", "参与者必须本人登录付款。", 409)
                real_participants.append((added, login))
        if scope.get("aa_collector_scope"):
            from .aa_participant_payments import register_real_collection
            register_real_collection(session, row, scope["aa_collector_scope"], real_participants,
                                     expires_at=datetime.fromisoformat(scope["aa_expires_at"]))
        task.result_json = {"message": "AA 收款请求已创建，各参与者尚未付款。", "aa_request_id": row.id,
                            "total_minor": row.total_minor, "status": "COLLECTING", "simulated": True}
        finish_node(session, task, node, task.result_json)
    confirmation.status = "CONSUMED"
    confirmation.consumed_at = utcnow()
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool,
        idempotency_key=idempotency_key, request_hash=request_hash, status="SUCCESS", result_json=task.result_json))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                 event_type="SCHEDULE_REGISTERED" if schedule else "AA_REQUEST_CREATED", payload=task.result_json)
    session.flush()
    return task


def _fail_schedule(session, row, task, code):
    row.status, row.last_error, row.completed_at = "FAILED", code, utcnow()
    node = session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == "schedule-transfer"))
    if node:
        node.status, node.result_json = "FAILED", {"error": code}
        node.version += 1
    task.result_json = {"message": "定时转账到期核验未通过，未扣款。", "schedule_id": row.id, "error": code, "simulated": True}
    transition_task(task, "FAILED")
    append_audit(session, sandbox_id=row.sandbox_id, task_id=task.id,
                 event_type="SCHEDULE_FAILED", payload=task.result_json)


def execute_due_schedules(session: Session, *, calendar_time=None) -> int:
    """Caller commits. Row locks and stable bank keys make restarts single-shot."""
    from .transfer_recurrence import execute_due_series
    from .aa_participant_payments import expire_collections
    expire_collections(session)
    from .banking_extension_models import ScheduledTransferSeries
    count = execute_due_series(session, calendar_time=calendar_time)
    from .reporting_clock import calendar_now
    current = as_utc(calendar_time) if calendar_time is not None else calendar_now()
    basis = ScopedScheduledTransfer.scope_json["clock_basis"].as_string()
    rows = list(session.scalars(select(ScopedScheduledTransfer).join(SimulationState,
        SimulationState.sandbox_id == ScopedScheduledTransfer.sandbox_id).where(
        ScopedScheduledTransfer.status == "WAITING",
        ScopedScheduledTransfer.id.not_in(select(ScheduledTransferSeries.schedule_id)), or_(
            and_(basis == "CALENDAR_REAL", ScopedScheduledTransfer.due_at <= current),
            and_(or_(basis.is_(None), basis == "BANK_CONTROLLED"), ScopedScheduledTransfer.due_at <= SimulationState.business_time),
            and_(basis.is_not(None), basis.not_in({"CALENDAR_REAL", "BANK_CONTROLLED"})))
    ).order_by(ScopedScheduledTransfer.due_at, ScopedScheduledTransfer.id).limit(1).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)))
    for row in rows:
        task = session.scalar(select(Task).where(Task.id == row.task_id).with_for_update().execution_options(populate_existing=True))
        if task is None or task.state != "WAITING":
            continue
        count += 1
        if row.scope_json.get("clock_basis", "BANK_CONTROLLED") not in {"CALENDAR_REAL", "BANK_CONTROLLED"}:
            _fail_schedule(session, row, task, "SCHEDULE_CLOCK_CHANGED")
            continue
        account = None
        payee = session.scalar(select(Payee).where(Payee.id == row.payee_id, Payee.sandbox_id == row.sandbox_id,
            Payee.user_id == row.user_id, Payee.status == "ACTIVE"))
        confirmation = session.get(Confirmation, row.confirmation_id)
        if task.plan_hash != row.plan_hash or confirmation is None or confirmation.status != "CONSUMED" \
                or confirmation.plan_hash != row.plan_hash or canonical_hash(task.plan_json) != row.plan_hash \
                or confirmation.view_hash != canonical_hash({"plan_hash": row.plan_hash, "scope": row.scope_json}) \
                or row.source_account_id != row.scope_json["source_account_id"] \
                or row.payee_id != row.scope_json["payee_id"] or row.amount_minor != row.scope_json["amount_minor"] \
                or as_utc(row.due_at) != as_utc(datetime.fromisoformat(row.scope_json["due_at"])):
            _fail_schedule(session, row, task, "SCHEDULE_SCOPE_CHANGED")
            continue
        if payee is None or payee.account_last4 != row.scope_json["payee_last4"]:
            _fail_schedule(session, row, task, "TRANSFER_TARGET_CHANGED")
            continue
        from .internal_banking import lock_transfer_accounts, recipient_scope, commit_transfer_money
        destination = {key: row.scope_json.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}
        if "transfer_channel" not in row.scope_json:
            destination = {"transfer_channel": "EXTERNAL_SIM", "recipient_username": None, "recipient_account_id": None}
        try:
            _validate_schedule_recipient_context(session, row.sandbox_id, row.user_id, row.scope_json)
            account = lock_transfer_accounts(session, row.sandbox_id, row.user_id, row.source_account_id, payee)
            if row.scope_json.get("transfer_payment_scope"):
                from .services import Principal
                from .transfer_payment_scope import validate_transfer_payment_scope
                validate_transfer_payment_scope(session, Principal(row.user_id, row.sandbox_id),
                    row.scope_json["transfer_payment_scope"], scheduled=True)
            if recipient_scope(session, payee) != destination:
                raise DomainError("RECIPIENT_SCOPE_CHANGED", "定时收款账号或渠道变化", 409)
        except DomainError as error:
            _fail_schedule(session, row, task, error.code)
            continue
        if row.amount_minor <= 0 or row.amount_minor > min(TRANSFER_LIMIT_MINOR, row.scope_json["max_amount_minor"]):
            _fail_schedule(session, row, task, "TRANSFER_LIMIT_CHANGED")
            continue
        from .extensions import evaluate_transfer_risk, reserved_goal_cash_minor
        risk = evaluate_transfer_risk(session, task=task, context={"payee_id": row.payee_id, "for_commit": True})
        if risk["decision"] != "ALLOW":
            _fail_schedule(session, row, task, "FRAUD_TRANSFER_BLOCKED")
            continue
        if account.balance_minor - reserved_goal_cash_minor(session, row.sandbox_id, row.user_id, account.id) < row.amount_minor:
            _fail_schedule(session, row, task, "INSUFFICIENT_FUNDS")
            continue
        try:
            payment_scope = row.scope_json.get("transfer_payment_scope")
            existing = commit_transfer_money(session, account, payee, row.amount_minor, row.memo, row.business_key, destination,
                source_card_id=(payment_scope["source"].get("card_id") if payment_scope else None),
                recipient_card_id=((payment_scope.get("recipient") or {}).get("card_id") if payment_scope else None))
        except DomainError as error:
            _fail_schedule(session, row, task, error.code)
            continue
        row.status, row.transfer_id, row.completed_at = "POSTED", existing.id, utcnow()
        task.result_json = {"message": "定时转账已完成。" if destination["transfer_channel"] == "INTERNAL_BANK" else "定时转账已转出，到账结果待核实。", "schedule_id": row.id,
                            **destination,
                            "debit_status":"POSTED", "settlement_status":"SETTLED" if destination['transfer_channel']=='INTERNAL_BANK' else 'UNVERIFIED',
                            "transfer_id": existing.id, "amount_minor": row.amount_minor, "simulated": True}
        node = session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == "schedule-transfer"))
        finish_node(session, task, node, task.result_json)
        append_audit(session, sandbox_id=row.sandbox_id, task_id=task.id,
                     event_type="SCHEDULE_TRANSFER_POSTED", payload=task.result_json)
        session.add(OutboxMessage(sandbox_id=row.sandbox_id, task_id=task.id,
                                  event_type="TASK_COMPLETED", payload_json=task.result_json))
    session.flush()
    return count


def cancel_schedules_for_task(session: Session, task_id: str) -> int:
    from .transfer_recurrence import cancel_series
    cancel_series(session, task_id)
    rows = list(session.scalars(select(ScopedScheduledTransfer).where(
        ScopedScheduledTransfer.task_id == task_id, ScopedScheduledTransfer.status == "WAITING").with_for_update()
        .execution_options(populate_existing=True)))
    for row in rows:
        row.status, row.completed_at = "CANCELLED", utcnow()
        task = session.get(Task, task_id)
        node = session.scalar(select(TaskNode).where(TaskNode.task_id == task_id, TaskNode.node_key == "schedule-transfer"))
        if node:
            node.status = "CANCELLED"
            node.version += 1
        if task and task.state == "WAITING":
            transition_task(task, "CLOSED")
            task.result_json = {"message": "尚未执行的定时转账已取消。", "schedule_id": row.id, "simulated": True}
        append_audit(session, sandbox_id=row.sandbox_id, task_id=task_id,
                     event_type="SCHEDULE_CANCELLED", payload={"schedule_id": row.id})
    return len(rows)


def aa_request_view(session: Session, row: AACollectionRequest) -> dict:
    from .aa_note import collection_note
    participants = list(session.scalars(select(AAParticipant).where(AAParticipant.request_id == row.id).order_by(AAParticipant.ordinal)))
    result = {"request_id": row.id, "task_id": row.task_id, "total_minor": row.total_minor, "status": row.status,
            "memo": collection_note(session, row),
            "received_minor": sum(p.amount_minor for p in participants if p.status == "PAID"),
            "participants": [{"participant_id": p.id, "name": p.display_name, "amount_minor": p.amount_minor,
                              "status": p.status, "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in participants]}
    from .banking_extension_models import AARequestTerms, AAParticipantIdentity
    terms = session.get(AARequestTerms, row.id)
    result["payment_mode"] = "PARTICIPANT_BANK_TRANSFER" if terms else "LEGACY_SIMULATION"
    if terms:
        from .payment_instruments import recipient_instrument_view
        from .aa_participant_payments import aa_participant_receipt_verified
        known_received, unknown = 0, False
        for participant, published in zip(participants, result["participants"]):
            if participant.status == "PAID":
                identity = session.get(AAParticipantIdentity, participant.id)
                verified = aa_participant_receipt_verified(session, identity, participant, row)
                published["receipt_verified"] = verified
                if verified:
                    known_received += participant.amount_minor
                else:
                    published["status"], unknown = "UNVERIFIED", True
        result.update(received_minor=None if unknown else known_received,
            known_received_minor=known_received, receipt_status="UNVERIFIED" if unknown else "VERIFIED")
        if unknown:
            result["status"] = "UNVERIFIED"
        result.update(version=terms.version, request_status=terms.status,
            expires_at=as_utc(terms.expires_at).isoformat(), recipient=recipient_instrument_view(terms.collector_scope_json))
    return result


def simulate_aa_payment(session: Session, request_id: str, participant_id: str, sandbox_id: str) -> dict:
    """Bank simulation control: one participant pays their previously agreed share."""
    row = session.scalar(select(AACollectionRequest).where(AACollectionRequest.id == request_id,
        AACollectionRequest.sandbox_id == sandbox_id).with_for_update())
    participant = session.scalar(select(AAParticipant).where(AAParticipant.id == participant_id,
        AAParticipant.request_id == request_id, AAParticipant.sandbox_id == sandbox_id).with_for_update())
    if row is None or participant is None:
        raise DomainError("AA_REQUEST_NOT_FOUND", "AA 请求或参与者不存在", 404)
    from .access_models import LoginAccount
    owner = session.scalar(select(LoginAccount).where(LoginAccount.user_id == row.user_id, LoginAccount.sandbox_id == row.sandbox_id))
    if owner and owner.role != "DEV_TEST":
        raise DomainError("AA_SIMULATION_FORBIDDEN", "普通客户AA须由参与者本人确认付款，不能代填到账记录。", 403)
    if participant.status == "PAID":
        return aa_request_view(session, row)
    if row.status != "COLLECTING" or participant.status != "PENDING":
        raise DomainError("AA_PAYMENT_NOT_ALLOWED", "当前 AA 状态不允许支付", 409)
    account = session.scalar(select(Account).where(Account.id == row.account_id,
        Account.sandbox_id == sandbox_id, Account.user_id == row.user_id).with_for_update())
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "收款账户不存在", 404)
    session.add_all([
        AALedgerEntry(sandbox_id=sandbox_id, request_id=row.id, participant_id=participant.id,
                      account_ref=account.id, delta_minor=participant.amount_minor),
        AALedgerEntry(sandbox_id=sandbox_id, request_id=row.id, participant_id=participant.id,
                      account_ref=f"AA_CLEARING:{participant.payee_id}", delta_minor=-participant.amount_minor),
    ])
    account.balance_minor += participant.amount_minor
    account.version += 1
    participant.status, participant.paid_at = "PAID", utcnow()
    record_cash_transaction(session, sandbox_id=sandbox_id, account_id=account.id,
        delta_minor=participant.amount_minor, merchant="AA收款:"+participant.display_name,
        category="AA收款", source_kind="AA_COLLECTION", operation_ref="aa:"+participant.id)
    session.flush()
    unpaid = list(session.scalars(select(AAParticipant).where(AAParticipant.request_id == row.id, AAParticipant.status != "PAID")))
    if not unpaid:
        row.status = "COMPLETED"
    task = session.get(Task, row.task_id)
    if task:
        task.result_json = {**task.result_json, "status": row.status,
            "received_minor": sum(p.amount_minor for p in session.scalars(select(AAParticipant).where(
                AAParticipant.request_id == row.id, AAParticipant.status == "PAID"))),
            "message": "AA 收款已全部到账。" if row.status == "COMPLETED" else "AA 已有参与者付款，继续等待其他参与者。"}
        task.version += 1
        session.add(OutboxMessage(sandbox_id=sandbox_id, task_id=row.task_id,
            event_type="AA_PAYMENT_RECEIVED", payload_json={"request_id": row.id, "participant_id": participant.id,
                                                          "amount_minor": participant.amount_minor, "status": row.status}))
    append_audit(session, sandbox_id=sandbox_id, task_id=row.task_id,
                 event_type="AA_PAYMENT_POSTED", payload={"request_id": row.id, "participant_id": participant.id, "amount_minor": participant.amount_minor})
    return aa_request_view(session, row)


def install_extension_routes(app, get_session, get_principal):
    from .aa_payment_api import install_aa_payment_routes
    install_aa_payment_routes(app, get_session, get_principal)
    @app.get("/api/v2/transfers/schedules")
    def schedules(session=Depends(get_session), principal=Depends(get_principal)):
        rows = list(session.scalars(select(ScopedScheduledTransfer).where(
            ScopedScheduledTransfer.sandbox_id == principal.sandbox_id, ScopedScheduledTransfer.user_id == principal.user_id).order_by(ScopedScheduledTransfer.created_at.desc()))
        )
        from .banking_extension_models import ScheduledTransferSeries
        from .schedule_presentation import schedule_projection
        result = []
        for row in rows:
            series = session.scalar(select(ScheduledTransferSeries).where(ScheduledTransferSeries.schedule_id == row.id))
            result.append(schedule_projection(session, row, series))
        return result

    @app.post("/api/v2/transfers/schedules/{schedule_id}/cancel")
    def cancel_schedule(schedule_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        from .schedule_amendments import replacement_snapshot
        row, _ = replacement_snapshot(session, principal, schedule_id, lock=True)
        cancel_schedules_for_task(session, row.task_id)
        session.commit()
        return {"schedule_id": row.id, "status": row.status}

    @app.get("/api/v2/transfers/aa")
    def aa_requests(session=Depends(get_session), principal=Depends(get_principal)):
        from .customer_history_archive import visible_history
        rows = session.scalars(select(AACollectionRequest).where(AACollectionRequest.sandbox_id == principal.sandbox_id,
            AACollectionRequest.user_id == principal.user_id,
            visible_history(session, principal, AACollectionRequest.created_at)).order_by(AACollectionRequest.created_at.desc()))
        return [aa_request_view(session, row) for row in rows]

    @app.post("/api/v2/transfers/aa/{request_id}/participants/{participant_id}/simulate-payment")
    def aa_payment(request_id: str, participant_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        if principal.role not in {"ADMIN", "DEV_TEST"}:
            raise DomainError("SIMULATION_ADMIN_REQUIRED", "需要账套控制权限", 403)
        owned = session.scalar(select(AACollectionRequest).where(AACollectionRequest.id == request_id,
            AACollectionRequest.sandbox_id == principal.sandbox_id, AACollectionRequest.user_id == principal.user_id))
        if owned is None:
            raise DomainError("AA_REQUEST_NOT_FOUND", "AA 请求不存在", 404)
        result = simulate_aa_payment(session, request_id, participant_id, principal.sandbox_id)
        session.commit()
        return result
