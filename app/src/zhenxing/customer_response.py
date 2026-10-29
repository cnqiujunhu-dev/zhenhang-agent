"""Deterministic customer narration from recorded business results, never a grant.

This layer has no database, network, model or text-intent parser. Subscription
amounts without a currency use the existing CNY-only subscription capability's
bank contract, not a currency inferred from user wording. Different billing
cycles/currencies are never summed as a monthly fee.
"""
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field

from .contracts import StrictModel
from .intelligence import READ_KINDS
from .redaction import redact_credentials


DetailKind = Literal["BILLS", "CARDS", "SUBSCRIPTIONS", "WEALTH", "RISK", "FAMILY", "GOALS",
                     "CARBON", "FX", "TRANSFER", "CONTACTS", "ASSETS", "BENEFITS", "PREFERENCES", "CREDIT", "OCCASIONS", "LIFE_PLANS", "MERCHANTS", "MIXED", "NONE"]
StateKind = Literal["PROGRESS", "PENDING", "EXECUTING", "UNKNOWN", "FAILED", "PARTIAL", "CLOSED",
                    "SCHEDULED", "PROPOSAL_SAVED", "COLLECTION_CREATED", "CONTACT_SAVED",
                    "QUERY_RESULT", "COMMITTED", "UNVERIFIED", "GUIDANCE"]


class AssistantResponse(StrictModel):
    intro: str = Field(min_length=1, max_length=500)
    detail: str | None = Field(default=None, max_length=2400)
    card_detail: str | None = Field(default=None, max_length=2400)
    card_intro: str | None = Field(default=None,max_length=500)
    query_layout_version: Literal['customer-query-layout-v1'] | None = None
    query_focus: Literal['CARD_LIMIT','CARD_BALANCE_LIMIT','CARBON_CLAIMABLE_RECORDS','CARBON_ELIGIBLE_REWARDS','FAMILY_AUTHORIZED_BALANCES','FAMILY_DELEGATIONS'] | None = None
    query_focus_by_node: dict[str,Literal['CARD_LIMIT','CARD_BALANCE_LIMIT','CARBON_CLAIMABLE_RECORDS','CARBON_ELIGIBLE_REWARDS','FAMILY_AUTHORIZED_BALANCES','FAMILY_DELEGATIONS']] = Field(default_factory=dict,max_length=30)
    detail_kind: DetailKind
    state_kind: StateKind
    read_only: bool
    reply_language: Literal["zh-CN", "en"] = "zh-CN"


ACTION_PRESENTATIONS = {
    "AA_VIEW": ("TRANSFER", "本人AA应付与收款"), "AA_PAY": ("TRANSFER", "支付本人AA份额"),
    "TRANSFER_SCHEDULE_VIEW": ("TRANSFER", "定时转账安排"),
    'OWN_ACCOUNT_TRANSFER': ('TRANSFER','本人账户转存'),
    "CREDIT_VIEW": ("CREDIT", "信用卡与共享授信"), "CREDIT_STATEMENT": ("CREDIT", "信用卡账单"),
    "CREDIT_REPAY": ("CREDIT", "信用卡还款"), "CREDIT_INSTALLMENT": ("CREDIT", "信用卡分期"),
    "CREDIT_SETTLE_INSTALLMENT": ("CREDIT", "分期提前结清"), "CREDIT_APPLY": ("CREDIT", "信用卡申请"),
    "CREDIT_LIMIT_REQUEST": ("CREDIT", "授信调额申请"), "CREDIT_ACTIVATE": ("CREDIT", "信用卡激活"),
    "ASSET_POSITIONS_READ": ("ASSETS", "资产持仓"),
    "MEMBERSHIP_VIEW": ("BENEFITS", "客户与卡片等级"), "BENEFITS_VIEW": ("BENEFITS", "优惠活动与权益"),
    "PREFERENCE_VIEW": ("PREFERENCES", "个人偏好"), "PREFERENCE_SET": ("PREFERENCES", "更新偏好"),
    "PREFERENCE_REMOVE": ("PREFERENCES", "撤回偏好"),
    "BILL_ANALYSIS": ("BILLS", "账单收支"),
    "CARD_STATUS": ("CARDS", "卡片状态"), "CARD_LOCK": ("CARDS", "锁卡"),
    "CARD_UNLOCK": ("CARDS", "解锁"), "CARD_REPORT_LOSS": ("CARDS", "挂失"),
    "CARD_REPLACEMENT": ("CARDS", "补卡申请"), "CARD_SET_LIMIT": ("CARDS", "卡片限额"),
    "TRANSFER": ("TRANSFER", "转账"), "TRANSFER_SCHEDULE": ("TRANSFER", "预约转账"),
    "AA_REQUEST": ("TRANSFER", "AA收款"),
    "WEALTH_COMPARE": ("WEALTH", "理财产品与风险适配"), "WEALTH_BUY": ("WEALTH", "理财申购"),
    "WEALTH_REDEEM": ("WEALTH", "理财赎回"),
    "SUBSCRIPTION_LIST": ("SUBSCRIPTIONS", "订阅和自动续费"), "SUBSCRIPTION_CANCEL": ("SUBSCRIPTIONS", "关闭自动续费"),
    "SUBSCRIPTION_START": ("SUBSCRIPTIONS", "订阅开通或恢复"), "SUBSCRIPTION_RENEW": ("SUBSCRIPTIONS", "订阅续期"),
    "FRAUD_ANALYZE": ("RISK", "转账风险"),
    "FAMILY_VIEW": ("FAMILY", "家庭协助与授权"), "FAMILY_GRANT": ("FAMILY", "家庭授权"),
    "FAMILY_REVOKE": ("FAMILY", "家庭授权撤销"), "FAMILY_PROPOSE": ("FAMILY", "家庭协助建议"),
    "GOAL_VIEW": ("GOALS", "储蓄目标和现金流"), "GOAL_CREATE": ("GOALS", "储蓄目标"),
    "GOAL_RESERVE": ("GOALS", "目标资金预留"), "GOAL_RELEASE": ("GOALS", "预留资金释放"),
    "CARBON_VIEW": ("CARBON", "低碳积分与权益"), "CARBON_CLAIM": ("CARBON", "活动积分领取"),
    "CARBON_REDEEM": ("CARBON", "活动权益兑换"),
    "FX_QUOTE": ("FX", "参考汇率"), "FX_TRANSFER": ("TRANSFER", "美元计价转账"),
    "FX_EXCHANGE": ("FX", "本人换汇"), "CONTACT_BIND": ("CONTACTS", "本人收款人称呼"),
}
from .life_plan_presentation import ACTION_PRESENTATIONS as _LIFE_ACTION_PRESENTATIONS
ACTION_PRESENTATIONS.update(_LIFE_ACTION_PRESENTATIONS)


def _int(value, minimum=0):
    return type(value) is int and value >= minimum


def _text(value):
    from .customer_query_layout import customer_display_name
    return customer_display_name(value)


def _money(value, currency):
    if type(value) is not int or currency not in {"CNY", "USD"}:
        return None
    return f"{Decimal(value)/100:.2f}" + ("人民币元" if currency == "CNY" else "美元")


def _rows(value):
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def read_query_coverage(kind, result):
    """Finite rendering scope, not source ownership or banking authorization.

    Runtime facts retain the internal proof; the authenticated BFF replaces it
    with a checked, dimension/count-only projection. Missing dimensions in a
    filtered read were not queried, and must never become zero-result claims.
    """
    if not isinstance(result, dict):
        return {"status": "UNAVAILABLE_SCOPE"}
    if "read_selection" in result:
        from .read_selection import safe_selection_summary
        return safe_selection_summary(kind, result)
    if "read_coverage" not in result:
        return {"status": "UNFILTERED"}
    value = result["read_coverage"]
    unavailable = {"status": "UNAVAILABLE_SCOPE"}
    if value == unavailable:
        return unavailable
    dimensions = {"CARBON_VIEW": {"REWARDS": "rewards", "ACTIVITIES": "events"},
                  "WEALTH_COMPARE": {"PRODUCTS": "products"},
                  "FAMILY_VIEW": {"FAMILY_OWNERS": "actors", "FAMILY_GRANTEES": "actors"}}.get(kind)
    from .family_balance_access_display import valid_negative_balance_authorization
    negative_balance = kind == "FAMILY_VIEW" and valid_negative_balance_authorization(result, value)
    expected_fields = {"status", "read_kind", "selected_dimensions", "selected_counts"}
    if negative_balance: expected_fields.add("balance_authorization")
    if not isinstance(value, dict) or set(value) != expected_fields \
            or value.get("status") != "FILTERED" or value.get("read_kind") != kind or not dimensions:
        return unavailable
    selected, counts = value.get("selected_dimensions"), value.get("selected_counts")
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected) \
            or len(set(selected)) != len(selected) or not set(selected) <= set(dimensions) \
            or not isinstance(counts, dict) or set(counts) != set(selected):
        return unavailable
    if any(type(counts[item]) is not int or counts[item] != 1
           or _rows(result.get(dimensions[item])) is None
           or kind != "FAMILY_VIEW" and len(result[dimensions[item]]) != counts[item]
           for item in selected):
        return unavailable
    if kind == "FAMILY_VIEW":
        # actors includes the current identity and the relationship's other
        # side. One selected owner is not one actor/one delegation in total.
        # Dimensions select different roles; never count an owner as a helper
        # or reinterpret malformed selected scope as an unfiltered family read.
        actors, current = result["actors"], result.get("current_actor")
        required_collections = ("delegations", "proposals") if negative_balance else ("delegations", "proposals", "authorized_balances")
        if not actors or not isinstance(current, dict) or any(_rows(result.get(key)) is None
                for key in required_collections):
            return unavailable
        roles = {"FAMILY_OWNERS": "OWNER", "FAMILY_GRANTEES": "FAMILY_HELPER"}
        if any(actor.get("role") not in roles.values() or not isinstance(actor.get("user_id"), str) or not actor["user_id"]
                or not isinstance(actor.get("display_name"), str) or not actor["display_name"].strip() for actor in actors) \
                or len({actor["user_id"] for actor in actors}) != len(actors) \
                or sum(actor == current for actor in actors) != 1 \
                or any(sum(actor["role"] == roles[dimension] for actor in actors) != counts[dimension] for dimension in selected):
            return unavailable
    if kind == "CARBON_VIEW" and ("point_ledger_net" in result or result.get("redemptions") != []
            or any(result.get(collection) != [] for dimension, collection in dimensions.items() if dimension not in selected)):
        return unavailable
    return {"status": "FILTERED", "read_kind": kind, "selected_dimensions": list(selected),
            "selected_counts": dict(counts), **({"balance_authorization": deepcopy(value["balance_authorization"])} if negative_balance else {})}


def carbon_query_summary(result, *, english=False):
    coverage = read_query_coverage("CARBON_VIEW", result)
    if coverage["status"] == "UNAVAILABLE_SCOPE":
        return ("The original query scope could not be verified." if english else "原查询范围暂时无法核对。"), (
            "Please query again; unavailable records are not treated as zero." if english else "请重新查询，未核验的记录不会当作零记录。")
    selected = set(coverage.get("selected_dimensions", ()))
    full = coverage["status"] == "UNFILTERED"
    detail = []
    if full or "ACTIVITIES" in selected:
        events = _rows(result.get("events"))
        if events is None:
            detail.append("Activity records have not been returned." if english else "行为记录尚未完整返回。")
        else:
            detail.append(f"{len(events)} activity records are available." if english else f"有{len(events)}条活动行为记录。")
            if all(type(row.get("claimable")) is bool for row in events):
                count = sum(row["claimable"] is True for row in events)
                detail.append(f"{count} currently meet the recorded claim conditions." if english else f"其中{count}条目前可申请积分。")
            else:
                detail.append("Some claim conditions remain unverified." if english else "部分领取条件尚未完整返回。")
    if full or "REWARDS" in selected:
        rewards = _rows(result.get("rewards"))
        detail.append((f"{len(rewards)} benefits are available." if english else f"可查看{len(rewards)}项权益。") if rewards is not None
                      else "The benefit list has not been fully returned." if english else "权益清单尚未完整返回。")
    wallet = result.get("wallet") if isinstance(result.get("wallet"), dict) else {}
    if _int(wallet.get("points")) and wallet["points"] <= 2**53-1:
        detail.append(f"Recorded activity points: {wallet['points']}." if english else f"当前积分为{wallet['points']}。")
    detail.append("Points are not cash or certified carbon assets." if english else "积分仅适用于本活动，不是现金或核证碳资产。")
    return ("Activity points and benefits are ready." if english else "低碳积分与活动权益已查到。"), (" ".join(detail) if english else "".join(detail))


def _result(node):
    return node.get("result") if isinstance(node.get("result"), dict) else {}


def _identifier(result, key):
    return isinstance(result.get(key), str) and bool(result[key])


def _subscription_datetime(value):
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _subscription_paid_record(result):
    """Only a typed posted receipt, never a quote, proves a subscription debit."""
    start, end = _subscription_datetime(result.get('effective_from')), _subscription_datetime(result.get('entitlement_end_at'))
    return bool(_identifier(result, 'subscription_id') and _text(result.get('merchant'))
        and result.get('status') == 'ACTIVE' and result.get('currency') == 'CNY' and result.get('already_active') is not True
        and result.get('effect_kind') is None and result.get('mandate_restored') is not True
        and _identifier(result, 'payment_id') and _identifier(result, 'transaction_id')
        and _int(result.get('months'), 1) and result['months'] <= 12 and _int(result.get('monthly_price_minor'), 1)
        and _int(result.get('total_amount_minor'), 1) and _int(result.get('charged_minor'), 1)
        and all(result[key] <= 2**53-1 for key in ('monthly_price_minor', 'total_amount_minor', 'charged_minor'))
        and result['charged_minor'] == result['total_amount_minor'] == result['monthly_price_minor'] * result['months']
        and type(result.get('auto_renew')) is bool and start and end and end > start)


def _subscription_already_active(result):
    return bool(result.get('already_active') is True and type(result.get('charged_minor')) is int and result['charged_minor'] == 0
        and _identifier(result, 'subscription_id') and _text(result.get('merchant')) and (result.get('status') == 'ACTIVE'
            or result.get('status') == 'CANCELLED' and result.get('no_effect_kind') == 'START_PAID_RIGHTS_ACTIVE'
            and result.get('entitled') is True and result.get('auto_renew') is False and _subscription_datetime(result.get('entitlement_end_at')))
        and not result.get('payment_id') and not result.get('transaction_id'))


def _subscription_restore_record(result):
    """Typed confirmed mandate receipt, distinct from payment and access grant."""
    end = _subscription_datetime(result.get('entitlement_end_at'))
    effective = _subscription_datetime(result.get('effective_from'))
    return bool(result.get('effect_kind') == 'RESTORE_AUTORENEW' and result.get('action') == 'START'
        and result.get('mandate_restored') is True and result.get('status') == 'ACTIVE' and result.get('currency') == 'CNY'
        and _identifier(result, 'subscription_id') and _text(result.get('merchant')) and _int(result.get('version'), 1)
        and type(result.get('charged_minor')) is int and result['charged_minor'] == 0
        and type(result.get('total_amount_minor')) is int and result['total_amount_minor'] == 0
        and result.get('auto_renew') is True and result.get('auto_renew_setting_source') == 'CURRENT_USER'
        and _int(result.get('months'), 1) and result['months'] <= 12
        and _int(result.get('monthly_price_minor'), 1) and _int(result.get('mandate_amount_minor'), 1)
        and result['mandate_amount_minor'] <= 2**53-1
        and result.get('mandate_billing_months') == result['months']
        and type(result.get('mandate_billing_months')) is int
        and result['mandate_amount_minor'] == result['monthly_price_minor'] * result['months']
        and end and effective == end and _subscription_datetime(result.get('expires_at')) == end
        and result.get('effective_policy') == 'RESTORE_AT_EXISTING_PAID_EXPIRY' and result.get('entitled') is True
        and not result.get('payment_id') and not result.get('transaction_id'))


def _subscription_write_summary(kind, result, *, english=False):
    name = _text(result.get('merchant')) or ('This subscription' if english else '这项订阅')
    if kind == 'SUBSCRIPTION_START' and _subscription_restore_record(result):
        end = _subscription_datetime(result['entitlement_end_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        future = f"CNY {Decimal(result['mandate_amount_minor'])/100:.2f}" if english else _money(result['mandate_amount_minor'], 'CNY')
        return ((f"Automatic renewal for {name} has been restored.",
            f"No charge was made and existing paid access was not extended. Its expiry remains {end} (Asia/Shanghai); subsequent renewals cost {future} every {result['mandate_billing_months']} month(s).") if english else
            (f"{name}自动续费已恢复。", f"本次没有扣款，也没有延长已付权益；原到期时间仍为{end}，之后每{result['mandate_billing_months']}个月扣款{future}。"))
    value = f"CNY {Decimal(result['charged_minor'])/100:.2f}" if english else _money(result['charged_minor'], 'CNY')
    end = _subscription_datetime(result.get('entitlement_end_at')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    renewal = ('Automatic renewal is enabled.' if result['auto_renew'] else 'Automatic renewal is not enabled.') if english else ('自动续费已开启。' if result['auto_renew'] else '自动续费未开启。')
    if result['auto_renew'] and _int(result.get('mandate_amount_minor'),1) and _int(result.get('mandate_billing_months'),1) and result['mandate_billing_months']<=12:
        future = f"CNY {Decimal(result['mandate_amount_minor'])/100:.2f}" if english else _money(result['mandate_amount_minor'],'CNY')
        renewal = (f"Automatic renewal is enabled: {future} per {result['mandate_billing_months']} month(s), under the confirmed renewal plan." if english else
            f"自动续费已开启，按已确认计划每{result['mandate_billing_months']}个月扣款{future}。")
    intro = (f"{name} {'was renewed' if kind == 'SUBSCRIPTION_RENEW' else 'is now active'}." if english else
        f"{name}{'已续期' if kind == 'SUBSCRIPTION_RENEW' else '已开通'}。")
    detail = (f"Charged {value} for {result['months']} paid month(s). Paid access ends {end} (Asia/Shanghai). {renewal}" if english else
        f"本次已扣款{value}，付费周期{result['months']}个月；权益有效至{end}。{renewal}")
    return intro, detail


def deferred_security_write_notice(trace,*,english=False):
    """Explain an explicitly separate request held by the security-only path.

    This is narration of non-execution, never a write/permission proposal.
    Third-party reported commands alone are not the user's banking request.
    """
    from .fraud_security_dialogue import reported_security_question
    from .intelligence import rule_proposal
    import re
    message=trace.get('message','')
    if not reported_security_question(message) or not trace.get('nodes') or any(node.get('action')!='FRAUD_ANALYZE' for node in trace['nodes']): return ''
    extra=re.split(r'另外|同时|还有|\b(?:also|additionally)\b',message,flags=re.I)
    if len(extra)<2: return ''
    if not any(action.kind not in READ_KINDS for clause in extra[1:] for action in rule_proposal(clause).actions): return ''
    return ('Your additional banking request has not been carried out. Check safety first, then review it independently.' if english else
        '你另外提出的银行操作尚未办理；核实安全后，仍需独立核对。')


def _read_recorded(kind, result, trace):
    if kind == "TRANSFER_SCHEDULE_VIEW":
        return result.get('schema_version') == 'scheduled-transfers-read-v1' and result.get('bank_state_changed') is False and _rows(result.get('schedules')) is not None
    if kind in {"OCCASION_MEMBER_VIEW","OCCASION_VIEW","LIFE_PLAN_VIEW","MERCHANT_CATALOG","MERCHANT_ORDERS"}:
        from .life_plan_presentation import life_display_read_recorded
        return life_display_read_recorded(kind,result)
    if kind == "AA_VIEW":
        return result.get("schema_version")=="aa-obligations-read-v1" and result.get("bank_state_changed") is False \
            and _rows(result.get("payable_obligations")) is not None and _rows(result.get("collections")) is not None
    if kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_presentation import credit_read_recorded
        return credit_read_recorded(kind, result)
    if kind in {"CARBON_VIEW", "WEALTH_COMPARE", "FAMILY_VIEW"} and read_query_coverage(kind, result)["status"] == "UNAVAILABLE_SCOPE":
        return False
    if kind == "BILL_ANALYSIS":
        analysis = trace.get("analysis")
        return isinstance(analysis, dict) and _int(analysis.get("transaction_count"))
    key = {"CARD_STATUS": "cards", "SUBSCRIPTION_LIST": "subscriptions", "WEALTH_COMPARE": "products",
           "FAMILY_VIEW": "delegations", "GOAL_VIEW": "goals", "CARBON_VIEW": "events",
           "ASSET_POSITIONS_READ": "positions", "BENEFITS_VIEW": "activities", "PREFERENCE_VIEW": "preferences"}.get(kind)
    if kind == "MEMBERSHIP_VIEW":
        return isinstance(result.get("membership"), dict) and _rows(result.get("cards")) is not None
    if key:
        return _rows(result.get(key)) is not None
    if kind == "FRAUD_ANALYZE":
        return isinstance(result.get("risk"), dict) and result["risk"].get("risk_level") in {"LOW", "HIGH", "UNKNOWN"}
    if kind == "FX_QUOTE":
        return isinstance(result.get("market_rate"), dict)
    return False


def _aa_payment_recorded(node, trace):
    """Presentation only, after the server's actual AA receipt integrity gate.

    Native AA pays through transfer.commit.v1. Its top-level result predates
    currency, but its registered payment_confirmation names the currency on
    both real instruments. Do not default arbitrary transfers to CNY, mutate
    that receipt, or treat this shape adapter as new payment authority.
    """
    result = _result(node)
    if node.get('action') != 'AA_PAY' or node.get('status') != 'SUCCEEDED' \
            or not _identifier(result, 'transfer_id') or not _int(result.get('amount_minor'), 1):
        return False
    if 'currency' in result:
        return result['currency'] == 'CNY'  # Preserve the explicit receipt contract.
    outcome = trace.get('outcome') or {}
    completed = outcome.get('completed_operation_ids')
    operation = node.get('operation_id')
    if trace.get('state') != 'SUCCEEDED' or trace.get('pending_confirmation') or result.get('error') is not None \
            or outcome.get('verified') is not True or outcome.get('kind') != 'BUSINESS_RECEIPT' \
            or outcome.get('state') != 'SUCCEEDED' or not isinstance(completed, list) \
            or not isinstance(operation, str) or not operation or completed.count(operation) != 1 \
            or [row for row in trace.get('nodes', []) if row.get('operation_id') == operation] != [node] \
            or result.get('transfer_channel') != 'INTERNAL_BANK':
        return False
    payment = result.get('payment_confirmation')
    source = result.get('source_card_context')
    target = result.get('recipient_context')
    if not all(isinstance(row, dict) for row in (payment, source, target)):
        return False
    pay_source, pay_target = payment.get('source'), payment.get('recipient')
    if not isinstance(pay_source, dict) or not isinstance(pay_target, dict) \
            or type(payment.get('amount_minor')) is not int or payment['amount_minor'] != result['amount_minor'] \
            or payment.get('currency') != 'CNY':
        return False
    valid_tail = lambda value: isinstance(value, str) and len(value) == 4 and all(c in '0123456789abcdefABCDEF' for c in value)
    if any(row.get('instrument_type') != 'DEBIT' or row.get('currency') != 'CNY'
            or not valid_tail(row.get('display_last4')) for row in (pay_source, pay_target)):
        return False
    return all(_identifier(source, key) for key in ('card_id', 'account_id')) \
        and all(_identifier(target, key) for key in ('recipient_card_id', 'recipient_bank_account_id', 'recipient_username')) \
        and source['account_id'] != target['recipient_bank_account_id'] \
        and source.get('display_last4') == pay_source['display_last4'] \
        and target.get('recipient_card_display_last4') == pay_target['display_last4'] \
        and target['recipient_bank_account_id'] == result.get('recipient_account_id') \
        and target['recipient_username'] == pay_target.get('username') == result.get('recipient_username')


def _recorded(node, trace):
    result, kind = _result(node), node.get("action")
    if node.get("status") != "SUCCEEDED" or result.get("error"):
        return False
    if kind in READ_KINDS:
        if kind == 'BILL_ANALYSIS':
            from .customer_bill_read_projection import bill_node_trace
            trace = bill_node_trace(trace, node)
            if trace is None:
                return False
        return _read_recorded(kind, result, trace)
    if kind=='OWN_ACCOUNT_TRANSFER':
        from .own_transfer_presentation import own_transfer_write_recorded
        return own_transfer_write_recorded(kind,result)
    if str(kind).startswith("CREDIT_") or result.get("schema_version") == "credit-card-control-v1":
        from .credit_presentation import credit_write_recorded
        return credit_write_recorded(kind, result)
    if kind in {"OCCASION_MEMBER_SET","OCCASION_SET","LIFE_PLAN_AUTHORIZE","LIFE_PLAN_CANCEL","MERCHANT_PURCHASE"}:
        from .life_plan_presentation import life_write_recorded
        return life_write_recorded(kind,result)
    if kind == "AA_PAY":
        return _aa_payment_recorded(node, trace)
    if kind in {"TRANSFER", "TRANSFER_SCHEDULE"}:
        return _identifier(result, "transfer_id") and _int(result.get("amount_minor"), 1)
    if kind in {"FX_TRANSFER", "FX_EXCHANGE"}:
        return (_identifier(result, "fx_operation_id") and _int(result.get("debit_minor"), 1)
                and _int(result.get("credit_minor"), 1) and result.get("source_currency") in {"USD", "CNY"}
                and result.get("target_currency") in {"USD", "CNY"})
    if kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        return _identifier(result, "order_id") and _int(result.get("amount_minor"), 1)
    if kind in {"CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT"}:
        return _identifier(result, "card_id") and _identifier(result, "card_status") and _int(result.get("card_version"), 1)
    if kind == "SUBSCRIPTION_CANCEL":
        return _identifier(result, "subscription_id") and result.get("status") == "CANCELLED"
    if kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
        return _subscription_paid_record(result) or (kind == 'SUBSCRIPTION_START' and _subscription_restore_record(result)) or (kind == 'SUBSCRIPTION_START' and _subscription_already_active(result)
            and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT')
    if kind in {"FAMILY_GRANT", "FAMILY_REVOKE"}:
        return _identifier(result, "delegation_id") and result.get("status") == ("ACTIVE" if kind == "FAMILY_GRANT" else "REVOKED")
    if kind == "FAMILY_PROPOSE":
        return _identifier(result, "proposal_id") and result.get("bank_action_executed") is False
    if kind in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}:
        return _identifier(result, "goal_id") and _int(result.get("reserved_minor")) and result.get("cash_debit_minor") == 0
    if kind == "CARBON_CLAIM":
        return _identifier(result, "behavior_id") and _int(result.get("credited_points"), 1) and _int(result.get("wallet_points"))
    if kind == "CARBON_REDEEM":
        return _identifier(result, "redemption_id") and result.get("status") == "ISSUED_SIMULATION"
    if kind == "AA_REQUEST":
        return _identifier(result, "aa_request_id") and result.get("status") in {"COLLECTING", "COMPLETED"}
    if kind == "CONTACT_BIND":
        return isinstance(result.get("contact_alias"), dict) and result["contact_alias"].get("personal_only") is True
    if kind in {"PREFERENCE_SET", "PREFERENCE_REMOVE"}:
        return result.get("schema_version") == "customer-preference-change-v1" \
            and result.get("status") == ("SAVED" if kind == "PREFERENCE_SET" else "RETRACTED") \
            and result.get("bank_state_changed") is False and type(result.get("preference_state_changed")) is bool
    return False


def _subscription_summary(result, *, english=False, compact=False):
    """Describe recorded fees and cancellation scope without changing bank facts."""
    rows = _rows(result.get("subscriptions")) or []
    active = sum(row.get('entitled') if type(row.get('entitled')) is bool else row.get("status") == "ACTIVE" for row in rows)
    known_statuses = all(row.get("status") in ("ACTIVE", "CANCELLED") for row in rows)
    if english:
        detail = (f"{len(rows)} subscriptions are recorded; {active} are currently active. " if known_statuses
                  else f"{len(rows)} records are available, but some subscription statuses are incomplete or unrecognized. ")
    else:
        detail = (f"共{len(rows)}项，其中{active}项目前启用。" if known_statuses
                  else f"共{len(rows)}项，部分活动状态尚未完整返回，请查看单项说明。")
    fees = []
    for row in ([] if compact else rows[:3]):
        # Only the existing CNY-only legacy DTO may omit currency. Unknown
        # amounts/currencies stay unknown; a cycle is never inferred as monthly.
        value, currency = row.get("amount_minor"), row.get("currency", "CNY")
        if not _int(value) or currency not in ("CNY", "USD"):
            continue
        major = f"{Decimal(value)/100:.2f}"
        merchant = _text(row.get("merchant")) or ("This service" if english else "该服务")
        amount = f"{currency} {major}" if english else major + ("元（人民币）" if currency == "CNY" else "美元（USD）")
        fees.append(f"{merchant}: {amount} per cycle" if english else merchant + "每期" + amount)
    if fees:
        detail += ("Recorded fees: " + "; ".join(fees) + ". Each service's billing cycle is shown separately. " if english
                   else "记录费用：" + "；".join(fees) + "。费用按各服务的扣款周期分别列示。")
    if len(rows) == 1:
        from .subscription_answer_facts import renewal_answer, access_answer
        renewal = renewal_answer(rows[0], 'en' if english else 'zh-CN')
        if renewal:
            detail = renewal + ' ' + access_answer(rows[0], 'en' if english else 'zh-CN') + ' ' + detail
    if compact and rows:
        detail += ("See each service's fees and recorded dates below. " if english
                   else "各项费用和扣款信息可展开记录查看。")
    cancellable = [row for row in rows if row.get("status") == "ACTIVE"
                   and row.get('auto_renew') is not False
                   and row.get("source_type") in ("BANK_MANDATE", "MERCHANT_SIM")
                   and row.get("can_cancel_here") is True]
    external = [row for row in rows if row.get("status") == "ACTIVE"
                and row.get("source_type") == "EXTERNAL_UNCONNECTED" and row.get("can_cancel_here") is False]
    if cancellable:
        detail += (f"You can prepare cancellation here for {len(cancellable)} connected services; review and confirm before it is carried out. " if english
                   else f"其中{len(cancellable)}项支持在此准备取消，核对并确认后办理。")
    if external:
        merchants = [(_text(row.get("merchant")) or ("this service" if english else "该服务")) for row in external[:3]]
        names = ", ".join(merchants) if english else "、".join(merchants)
        detail += (f"For {names}, check and cancel with the service provider; cancellation is not connected here. " if english
                   else f"{names}尚未接入取消办理，需到服务商核实并关闭。")
    if any(row.get("status") != "CANCELLED" and row.get('auto_renew') is not False and row not in cancellable and row not in external for row in rows):
        detail += ("Some cancellation methods need verification; check each service's details. " if english
                   else "部分服务的取消方式尚未核实，请查看单项说明。")
    review = result.get("renewal_review")
    if isinstance(review, dict) and review.get("version") == "visitor-renewal-review-v1" and review.get("read_only") is True and review.get("bank_state_changed") is False:
        if english:
            detail += f"For departure on {review.get('departure_date')}, {len(review.get('recorded_after_departure', []))} recorded dates fall later; {len(review.get('active_dates_need_verification', []))} active services need date verification. " + review["notice_en"]
        else:
            detail += f"针对{review.get('departure_date')}离境，记录中有{len(review.get('recorded_after_departure', []))}项计划日期在其后，另有{len(review.get('active_dates_need_verification', []))}项活跃服务日期待核实。" + review["notice"]
    return ("Your subscription list is ready." if english else "你的订阅清单已整理好。"), detail.strip()


def _card_balances_complete(rows):
    if not rows:
        return False
    for row in rows:
        currencies = row.get("supported_currencies", ["CNY"])
        balances = row.get("balances_by_currency")
        if 'requested_balance_currencies' in row:
            from .card_balance_scope import canonical_currencies
            try: requested = canonical_currencies(row['requested_balance_currencies'])
            except ValueError: return False
            unsupported = [key for key in requested if key not in currencies]
            if row.get('unsupported_balance_currencies') != unsupported or not isinstance(balances, dict) \
                    or set(balances) != set(requested) - set(unsupported) \
                    or any(type(amount) is not int or abs(amount) > 2**53 - 1 for amount in balances.values()): return False
            continue
        if not isinstance(currencies, list) or not currencies or "CNY" not in currencies or any(
                currency not in {"CNY", "USD"} for currency in currencies) or not isinstance(balances, dict) or any(
                type(balances.get(currency)) is not int or abs(balances[currency]) > 2**53 - 1 for currency in currencies):
            return False
    return True


def _card_balance_scope_detail(result, *, english=False):
    scope = result.get('card_balance_scope')
    if not isinstance(scope, dict) or scope.get('schema_version') != 'card-balance-scope-v1': return ''
    from .card_balance_scope import canonical_currencies
    try: requested = canonical_currencies(scope.get('requested_currencies'))
    except ValueError: return ''
    unsupported = sorted({currency for row in _rows(result.get('cards')) or []
        for currency in row.get('unsupported_balance_currencies', []) if currency in requested})
    detail = (' Requested ' + ' / '.join(requested) + ' balances are separate, not summed.' if english else
        '本次按' + ' / '.join(requested) + '分别查询，不相加。')
    if unsupported:
        detail += (' Some selected cards do not support ' + ' / '.join(unsupported) + '; unsupported is not a zero balance.' if english else
            '部分所选卡不支持' + ' / '.join(unsupported) + '；不支持不表示余额为零。')
    return detail


def _goal_what_if_summary(result, *, english=False):
    """Render only a verified read-only server calculation, never model math."""
    scenario = result.get("what_if")
    goals = _rows(result.get("goals")) or []
    if not isinstance(scenario, dict) or scenario.get("calculation_version") != "goal-monthly-saving-what-if-v1" \
            or scenario.get("currency") != "CNY" or scenario.get("basis") != "USER_STATED_MONTHLY_SAVING" \
            or scenario.get("rounding") != "CEILING_TO_WHOLE_MONTH" or scenario.get("read_only") is not True \
            or scenario.get("changes_applied") is not False or scenario.get("guaranteed") is not False \
            or not _int(scenario.get("goal_version"),1) or not _identifier(scenario,"goal_id"):
        return None
    goal = next((row for row in goals if row.get("goal_id") == scenario.get("goal_id")
        and row.get("goal_name") == scenario.get("goal_name") and row.get("version") == scenario.get("goal_version")
        and row.get("remaining_minor") == scenario.get("remaining_minor")), None)
    if not goal or not isinstance(scenario.get("goal_name"), str):
        return None
    from .contracts import DomainError
    try:
        from .goal_what_if import calculate_goal_what_if
        expected = calculate_goal_what_if(goal, monthly_saving_minor=scenario.get("monthly_saving_minor"),
            monthly_saving_delta_minor=scenario.get("monthly_saving_delta_minor"),
            monthly_saving_total_minor=scenario.get('monthly_saving_total_minor'))
    except (ValueError, TypeError, KeyError, DomainError):
        return None
    numeric = ("remaining_minor", "monthly_saving_minor", "improved_monthly_saving_minor")
    if any(not _int(scenario.get(key)) or scenario[key] > 2**53 - 1 or scenario[key] != expected[key] for key in numeric):
        return None
    if type(scenario.get("baseline_reachable")) is not bool or scenario['baseline_reachable'] != expected['baseline_reachable']:
        return None
    delta=scenario.get('monthly_saving_delta_minor')
    if type(delta) is not int or abs(delta)>2**53-1 or delta!=expected['monthly_saving_delta_minor']: return None
    if scenario.get('monthly_saving_total_minor') is not None and (not _int(scenario['monthly_saving_total_minor'])
        or scenario['monthly_saving_total_minor']!=expected['monthly_saving_total_minor']): return None
    if 'proposed_plan_reachable' in scenario and (type(scenario['proposed_plan_reachable']) is not bool
        or scenario['proposed_plan_reachable']!=expected['proposed_plan_reachable']): return None
    for key in ('baseline_months','improved_months','months_saved'):
        if expected[key] is None:
            if scenario.get(key) is not None: return None
        elif type(scenario.get(key)) is not int or abs(scenario[key])>2**53-1 or scenario[key]!=expected[key]: return None
    goal_name = _text(scenario["goal_name"])
    total_plan=scenario.get('monthly_saving_total_minor') is not None
    assumption=('The new total monthly saving is used for this goal, with other conditions unchanged.' if english else '每月按新的总储蓄额用于这个目标，其他条件不变。') if total_plan else (
        'All extra monthly saving goes to this goal, with other conditions unchanged.' if english else '每月新增储蓄全部用于这个目标，其他条件不变。')
    baseline, improved, saved = (scenario[key] for key in ("baseline_months", "improved_months", "months_saved"))
    if improved is None:
        return ((f"The proposed plan saves nothing each month for {goal_name}, so no completion month can currently be estimated." if english else
            f"新计划每月不再为{goal_name}储蓄，暂时无法预计完成月份。"),
            "This is a read-only comparison; the original goal and funds have not changed." if english else "这是只读方案对比，原目标和资金安排没有改变。")
    if baseline is None:
        return ((f"For {goal_name}, the original plan saves nothing each month, so an earlier completion cannot be compared. The extra saving plan estimates {improved} months."
            if english else f"原计划每月没有为{goal_name}存钱，无法比较能提前多久；按新的月储蓄计划，约需{improved}个月。"),
            assumption+(' This is an estimate; no money was reserved or moved.' if english else '这只是估算，没有调整资金预留或转账。'))
    if english:
        unit='month' if abs(saved)==1 else 'months'
        movement=f'{saved} {unit} sooner' if saved>0 else f'{-saved} {unit} later' if saved<0 else 'no change in the estimated months'
        return (f"For {goal_name}, the stated savings plan changes the estimate from {baseline} to {improved} months, {movement}.",
            assumption+" It is not a guaranteed date; no money was reserved or moved.")
    movement=f'可提前{saved}个月' if saved>0 else f'约晚{-saved}个月' if saved<0 else '预计月份与原计划相同'
    return (f"按你给出的月储蓄计划，{goal_name}预计由{baseline}个月到{improved}个月，{movement}。",
        assumption+"只是估算，没有调整资金预留或转账，也不保证实际完成日期。")


def _read_summary(kind, result, trace):
    if kind in {"OCCASION_MEMBER_VIEW","OCCASION_VIEW","LIFE_PLAN_VIEW","MERCHANT_CATALOG","MERCHANT_ORDERS"}:
        from .life_plan_presentation import life_action_summary
        return life_action_summary(kind,result)
    if kind == "TRANSFER_SCHEDULE_VIEW":
        from .scheduled_transfer_reads import summary
        return summary(result)
    if kind == "AA_VIEW":
        payable=result.get('payable_obligations',[]);collections=result.get('collections',[])
        if len(collections)==1 and not payable:
            row=collections[0];participants=row.get('participants',[])
            total,received=row.get('total_minor'),row.get('received_minor')
            paid=[p for p in participants if p.get('status')=='PAID' and p.get('receipt_verified') is True]
            pending=[p for p in participants if p.get('status')=='PENDING']
            if type(total) is int and type(received) is int and 0<=received<=total:
                if participants and len(paid)==len(participants) and received==total:
                    return f"这笔AA已经收齐，共收到{_money(received,'CNY')}。", "所有参与人都已付款，没有未付的人。"
                names='、'.join(str(p.get('name')or'参与人') for p in pending)
                return f"这笔AA已收到{_money(received,'CNY')}，总额{_money(total,'CNY')}。", (f"还没付款的是：{names}。" if names else "部分参与人的付款状态还需要核对。")
        if not collections and not payable:return "目前没有符合这次查询范围的AA记录。", ""
        return "你的AA记录在下面。", f"待查看的本人应付记录{len(payable)}项，发起收款{len(collections)}项。"
    if kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_presentation import credit_summary
        return credit_summary(kind, result)
    if kind == "ASSET_POSITIONS_READ":
        positions = _rows(result.get("positions")) or []
        if not positions:
            return "目前没有符合条件的持仓。", "可以继续查询其他资产类型。"
        units = {"UNIT": "份", "SHARE": "股", "GRAM": "克"}
        amounts = [f"{_text(row.get('product_name'))}：{row['total_quantity']}{units.get(row.get('quantity_unit'), '')}"
            for row in positions[:4] if isinstance(row.get('total_quantity'), str)]
        return "已查到你的当前持仓。", "；".join(amounts) + ("。" if amounts else "")
    if kind == "MEMBERSHIP_VIEW":
        membership = result.get("membership") or {}
        return "客户等级和卡片等级已查到。", f"当前客户等级为{_text(membership.get('tier_name'))}，各张卡的等级分别列出。"
    if kind == "BENEFITS_VIEW":
        activities = _rows(result.get("activities")) or []
        count = sum((row.get("eligibility") or {}).get("eligible") is True for row in activities)
        return "已按当前资料核对活动条件。", f"查到{len(activities)}项活动，其中{count}项目前符合参与条件。"
    if kind == "PREFERENCE_VIEW":
        preferences = _rows(result.get("preferences")) or []
        return ("这是你目前保存的偏好。" if preferences else "你还没有保存消费或出行偏好。"), "可以直接告诉我需要记住、修改或撤回的内容。"
    if kind == "SUBSCRIPTION_LIST":
        return _subscription_summary(result)
    if kind == "CARD_STATUS":
        rows = _rows(result.get("cards")) or []
        active, locked = sum(row.get("status") == "ACTIVE" for row in rows), sum(row.get("status") == "LOCKED" for row in rows)
        detail = (f"共{len(rows)}张卡，其中{active}张状态正常、{locked}张已临时锁定。" if all(isinstance(row.get("status"), str) and row["status"] in {"ACTIVE", "LOCKED", "LOST", "CLOSED"} for row in rows)
                  else f"共{len(rows)}张卡，部分状态尚未完整返回。")
        credit_rows=_rows(result.get("credit_cards"))
        if credit_rows:
            detail="借记卡"+detail+f"另列{len(credit_rows)}张信用卡；授信不是现金，不与存款余额相加。"
        balances_complete = _card_balances_complete(rows)
        detail += _card_balance_scope_detail(result)
        return ("银行卡资料与关联账户余额已查到。" if balances_complete else "银行卡资料已返回，部分余额尚未核实。"), detail
    if kind == "WEALTH_COMPARE":
        rows = _rows(result.get("products")) or []
        suitable = sum(row.get("suitable") is True for row in rows)
        detail = (f"共{len(rows)}项产品，其中{suitable}项符合当前适配条件。" if all(type(row.get("suitable")) is bool for row in rows)
                  else f"共{len(rows)}项产品，部分适配结果尚未完整返回。")
        from .wealth_risk_state import risk_status_summary
        risk_status = risk_status_summary(result.get('risk_profile'))
        if risk_status:
            detail = risk_status + detail
        return "理财产品和当前风险适配结果已整理好。", detail + "交易前仍会复查，风险匹配不代表收益预测或本金保证。"
    if kind == "BILL_ANALYSIS":
        analysis = trace["analysis"]
        from .customer_bill_fallback import checked_bill_fallback
        if checked_bill_fallback(trace.get("supplementary_bill")):
            analysis = trace["supplementary_bill"]
        from .customer_financial_narrative import financial_summary
        specific = financial_summary(analysis)
        if specific is not None:
            from .customer_reference_views import reference_summary
            selected=analysis.get('category_filter')
            scoped=isinstance(selected,dict) and selected.get('version')=='bill-category-filter-v1' and selected.get('basis')=='CATEGORY_EXACT_MATCH' \
                and selected.get('currency')=='CNY' and selected.get('read_only') is True and selected.get('bank_state_changed') is False \
                and (analysis.get('financial_insights') or {}).get('category_filter')==selected and isinstance(selected.get('category'),str)
            category_intro=f"本次只看{_text(selected['category'])}分类，以下不是整期账单总额。" if scoped else ''
            return (specific[0]+category_intro), specific[1] + reference_summary(analysis)
        anomalies = _rows(analysis.get("anomalies"))
        detail = f"这次统计了{analysis['transaction_count']}笔流水。"
        if anomalies is not None:
            detail += f"有{len(anomalies)}条待核验线索，不代表已确认欺诈。"
        return "账单收支已整理好，你可以先看主要分类和待核验项目。", detail
    if kind == "FRAUD_ANALYZE":
        level = result["risk"]["risk_level"]
        text = {"HIGH": "先停止联系与转账，不要屏幕共享或提供验证码。自行通过银行或平台官方渠道核实；若资金已受损，保留证据并联系银行与警方。普通确认不能绕过风险拦截。",
                "UNKNOWN": "目前信息不足，风险尚未确定，请先核实收款对象和来意。",
                "LOW": "当前已登记信息没有命中高风险信号，但这不等于安全保证，请继续核对对象和用途。"}[level]
        if result['risk'].get('analysis_kind')=='USER_MESSAGE_ANALYSIS' and level=='UNKNOWN':
            text='这些话暂未匹配明确的危险信号，但不能据此认定对方身份或判断安全。可以继续提供对方具体要求，我会结合前文帮你核实。'
        return "风险检查结果已整理好。", text+deferred_security_write_notice(trace)
    if kind == "FAMILY_VIEW":
        rows = _rows(result.get("delegations")) or []
        valid = sum(row.get("valid") is True for row in rows)
        proposals = _rows(result.get("proposals"))
        detail = (f"当前有{valid}项有效委托。" if all(type(row.get("valid")) is bool for row in rows)
                  else f"当前可查看{len(rows)}项委托记录，有效范围请以逐项说明为准。")
        if proposals is not None:
            detail += f"另有{len(proposals)}项协助建议可查看。"
        return "家庭协助范围已查到。", detail + "协助人只能在授权范围内查看或提建议，银行操作仍须本人受理并独立确认。"
    if kind == "GOAL_VIEW":
        what_if = _goal_what_if_summary(result)
        if what_if:
            return what_if
        rows = _rows(result.get("goals")) or []
        forecast = result.get("forecast") if isinstance(result.get("forecast"), dict) else {}
        detail = f"共{len(rows)}个目标，可查看进度和资金缺口；缺期限时仍需补充，不能直接计算月需存。"
        if forecast.get("sample_status") not in {None, "SUFFICIENT_FOR_SCENARIO"}:
            detail += "可用样本不足，预测需要谨慎参考。"
        return "你的储蓄目标和现金流已整理好。", detail + "情景预测不保证未来收入，也不会自动调整目标资金或消费安排。"
    if kind == "CARBON_VIEW":
        return carbon_query_summary(result)
    if kind == "FX_QUOTE":
        market = result.get("market_rate", {})
        detail = "参考汇率与换算条件请看下方。"
        try:
            rate = Decimal(market.get("rate", ""))
            if rate.is_finite() and rate > 0 and market.get("base_currency") == "USD" and market.get("quote_currency") == "CNY":
                detail = f"USD/CNY参考汇率为{rate}。"
        except (InvalidOperation, TypeError):
            pass
        return "参考汇率已查到。", detail + "这是日频中间参考价，不是银行实际成交价格，不会自动兑换。"
    return "查询结果已整理好。", "请查看下方业务明细，本轮没有执行新的银行操作。"


def _write_summary(kind, result):
    if kind=='OWN_ACCOUNT_TRANSFER':
        from .own_transfer_presentation import own_transfer_summary
        return own_transfer_summary(kind,result)
    if kind in {"OCCASION_MEMBER_SET","OCCASION_SET","LIFE_PLAN_AUTHORIZE","LIFE_PLAN_CANCEL","MERCHANT_PURCHASE"}:
        from .life_plan_presentation import life_action_summary
        return life_action_summary(kind,result)
    if kind == "AA_PAY": return "本人AA份额已支付。", "支付金额：" + (_money(result.get("amount_minor"),"CNY") or "") + "。"
    if str(kind).startswith("CREDIT_") or result.get("schema_version") == "credit-card-control-v1":
        from .credit_presentation import credit_summary
        summary = credit_summary(kind, result)
        if summary is not None: return summary
    if kind in {"PREFERENCE_SET", "PREFERENCE_REMOVE"}:
        preference = result.get("preference") or {}
        return ((f"已记住你的{_text(result.get('kind_label'))}。", _text(preference.get("value")))
            if kind == "PREFERENCE_SET" else (f"已撤回你的{_text(result.get('kind_label'))}。", "后续不会再按这项偏好提供建议。"))
    if kind in {'SUBSCRIPTION_START', 'SUBSCRIPTION_RENEW'}:
        return _subscription_write_summary(kind, result)
    if kind == 'SUBSCRIPTION_CANCEL' and result.get('auto_renew') is False and result.get('entitlement_preserved') is True and type(result.get('refunded_minor')) is int and result.get('refunded_minor') == 0:
        return '自动续费已关闭。', '原付费权益的有效期保留，本次没有退款；其他订阅不受影响。'
    if kind in {"TRANSFER", "TRANSFER_SCHEDULE"}:
        amount = _money(result.get("amount_minor"), result.get("currency", "CNY"))
        if result.get("transfer_channel") == "EXTERNAL_SIM":
            return "外部转出已完成清算记账。", "外部到账尚未核验，不能据此认定收款人已收到。"
        return "这笔转账已记账。", ("转账金额为" + amount + "。" if amount else "金额请看业务明细。") + "请以对应的收付款流水和结果为准。"
    if kind in {"FX_TRANSFER", "FX_EXCHANGE"}:
        debit = _money(result.get("debit_minor"), result.get("source_currency"))
        credit = _money(result.get("credit_minor"), result.get("target_currency"))
        return ("美元计价转账已按确认路线记账。" if kind == "FX_TRANSFER" else "本人换汇已记账。"), (
            f"付款或换出{debit}，收款或换入{credit}。" if debit and credit else "各币种的扣入金额请看明细。") + "人民币和美元分别记账，不合并余额。"
    texts = {
        "CARD_LOCK": ("卡片已临时锁定。", "临时锁定不同于挂失，需要恢复时须另行核对并确认。"),
        "CARD_UNLOCK": ("卡片解除临时锁定的结果已记录。", "请以当前卡片状态为准。"),
        "CARD_REPORT_LOSS": ("卡片挂失已登记。", "挂失不是普通临时锁定，请查看当前状态和后续处理。"),
        "CARD_REPLACEMENT": ("补卡申请已提交。", "这里只是申请受理，不代表已实际制卡或寄送。"),
        "CARD_SET_LIMIT": ("卡片限额已更新。", "新的额度请看卡片明细，其他交易仍须按原规则核验。"),
        "WEALTH_BUY": ("理财申购已记账。", "持仓和资金变化请看明细，风险适配不代表保本或收益保证。"),
        "WEALTH_REDEEM": ("理财赎回已记账。", "赎回份额和资金变化请看明细，不将本次结果当作未来收益承诺。"),
        "SUBSCRIPTION_CANCEL": ("这项已接入订阅的取消结果已记录。", "不会因此取消其他服务，外部未接入服务仍需到服务商核实。"),
        "FAMILY_GRANT": ("家庭协助授权已保存。", "协助人仍不能代本人执行银行操作，额度、期限和范围请看授权内容。"),
        "FAMILY_REVOKE": ("这项家庭协助授权已撤销。", "不能继续使用旧授权，已经完成的业务记录仍保留。"),
        "FAMILY_PROPOSE": ("协助建议已提交，等待本人审阅。", "提交建议没有执行转账或锁卡，银行操作仍需本人单独确认。"),
        "GOAL_CREATE": ("储蓄目标已建立。", "建立目标没有自动扣款或预留资金。"),
        "GOAL_RESERVE": ("目标预留已更新。", "预留会占用可支出额度，但现金总余额不减少，不是一次付款。"),
        "GOAL_RELEASE": ("目标预留已释放。", "释放恢复相应可支出额度，没有产生对外付款。"),
        "CARBON_CLAIM": ("本活动积分已记入账户。", "积分不是现金或核证碳资产，同一行为来源不能重复领取。"),
        "CARBON_REDEEM": ("本活动权益已兑换。", "权益不是现金或已实现节省，不代表外部商户已受理或已核证减排。"),
        "AA_REQUEST": ("AA收款请求已创建。", "仍需参与者自行付款，发起请求不等于已收到全部资金。"),
        "CONTACT_BIND": ("你的收款人称呼已保存。", "称呼只在本人范围内生效，绑定本身没有付款。"),
    }
    return texts.get(kind, ("业务结果已记录。", "请查看下方业务明细。"))


def card_owned_question_overview(trace, question_reply):
    """Only a revalidated sealed overview may delegate its facts to cards.

    Concrete requested facets and mixed domains keep mandatory narration.
    This is presentation ownership, never interpretation or bank authority.
    """
    from .customer_read_question_facts import KEY, read_question_response
    if not isinstance(question_reply, dict) or question_reply.get('reply_language') not in {'zh-CN', 'en'} \
            or read_question_response(trace, question_reply['reply_language']) != question_reply:
        return False
    kinds = {node['action'] for node in trace['nodes']}
    expected = {"SUBSCRIPTION_LIST": {'SUMMARY'}, "ASSET_POSITIONS_READ": {'HOLDINGS'}}
    return len(kinds) == 1 and (trace.get(KEY) or {}).get('question_aspects') is not None \
        and set(trace[KEY]['question_aspects']) == expected.get(next(iter(kinds)))


def build_assistant_response(trace, reply_language=None):
    from .language import detect_reply_language
    language = detect_reply_language(trace.get("message", ""), preferred=reply_language or trace.get("reply_language"))
    from .credit_repayment_closure import repayment_closure_response
    closure = repayment_closure_response(trace, language)
    if closure:
        return AssistantResponse(**closure).model_dump()
    if trace.get("state") == "AWAITING_CONFIRMATION" and trace.get("pending_confirmation") is None \
            and trace.get("confirmation_unavailable_reason") == "EXPIRED":
        return AssistantResponse(intro="This confirmation expired. Please edit the request and prepare a new confirmation."
            if language == "en" else "本次确认已过期，请修改需求并重新准备确认。",
            detail="The action has not been executed; the previous confirmation will not be reused."
            if language == "en" else "这一步尚未执行，原确认不会复用。",
            detail_kind="NONE", state_kind="PENDING", read_only=False, reply_language=language).model_dump()
    from .planning_failure_facts import controlled_failure_response
    failure=controlled_failure_response(trace,language)
    if failure: return AssistantResponse(**failure).model_dump()
    from .customer_presentation import completed_reads_for_pending
    completed_reads = completed_reads_for_pending(trace)
    if completed_reads:
        pending = trace["pending_confirmation"]
        pending_node = next(node for node in trace["nodes"] if isinstance(node, dict)
                            and node.get("child_task_id") == pending["task_id"] and node.get("status") == "WAITING_CONFIRMATION")
        transfer = pending_node.get("action") in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}
        all_reads = len(completed_reads) == sum(isinstance(node, dict) and node.get("action") in READ_KINDS for node in trace["nodes"])
        if language == "en":
            prefix = "The query is complete; " if all_reads else "Query results are available; "
            intro = prefix + ("the transfer still needs your review." if transfer else "the requested action still needs your review.")
        else:
            intro = ("查询已完成，" if all_reads else "已有查询结果，") + ("转账还待核对。" if transfer else "办理事项还待核对。")
        return AssistantResponse(intro=intro, detail=None, detail_kind="MIXED", state_kind="PENDING",
                                 read_only=False, reply_language=language).model_dump()
    from .customer_read_question_facts import read_question_response
    question_reply = read_question_response(trace, language)
    if question_reply:
        from .subscription_question_display import subscription_question_display
        display_reply = subscription_question_display(trace, question_reply) or question_reply
        response = AssistantResponse(**display_reply).model_dump()
        if card_owned_question_overview(trace, question_reply):
            from .customer_query_layout import apply_query_layout
            return apply_query_layout(response, trace)
        return response
    from .credit_status_display import credit_status_response
    credit_reply = credit_status_response(trace, language)
    if credit_reply:
        return AssistantResponse(**credit_reply).model_dump()
    if language == "en":
        from .customer_english_response import english_assistant_response
        from .customer_query_layout import apply_query_layout
        english_response = english_assistant_response(trace)
        aa_nodes = trace.get('nodes') or []
        if english_response.get('state_kind') == 'COMMITTED' and len(aa_nodes) == 1 \
                and 'currency' not in _result(aa_nodes[0]) and _aa_payment_recorded(aa_nodes[0], trace):
            amount = Decimal(_result(aa_nodes[0])['amount_minor']) / 100
            english_response = {**english_response, 'detail':f'CNY {amount:.2f} was posted for your verified AA share; this does not establish that other participants paid.'}
        return apply_query_layout(english_response,trace)
    nodes = [node for node in trace.get("nodes", []) if isinstance(node, dict)]
    kinds = [node.get("action") for node in nodes]
    known = [kind for kind in kinds if kind in ACTION_PRESENTATIONS]
    detail_kinds = list(dict.fromkeys(ACTION_PRESENTATIONS[kind][0] for kind in known))
    detail_kind = detail_kinds[0] if len(detail_kinds) == 1 else "MIXED" if detail_kinds else "NONE"
    read_only = bool(kinds) and all(kind in READ_KINDS for kind in kinds)
    state = trace.get("state")

    def response(intro, detail, state_kind, readonly=read_only, card_detail=None):
        return AssistantResponse(intro=redact_credentials(intro), detail=redact_credentials(detail) if detail else None,
            card_detail=redact_credentials(card_detail) if card_detail else None,
            detail_kind=detail_kind, state_kind=state_kind, read_only=readonly).model_dump()

    if state in {"QUEUED", "UNDERSTANDING", "PLANNING"}:
        subject = "、".join(dict.fromkeys(ACTION_PRESENTATIONS[kind][1] for kind in known))
        return response("正在帮你查询" + subject + "，请稍候。" if read_only else "好的，正在处理你的需求。",
                        "查到后会整理结果，本轮查询不会改变银行数据。" if read_only else "稍后会给你查询结果或需要核对的办理方案。", "PROGRESS")
    if state == "UNKNOWN":
        return response("这次办理的结果还未确定。", "请先查询原请求，不要根据超时重复付款，也不要把结果未知当成失败。", "UNKNOWN")
    if state == "EXECUTING":
        return response("正在办理你已确认的内容，请稍候。", "完成后会展示结果；请勿重复提交相同付款。", "EXECUTING")
    if state == "FAILED":
        return response("这次请求没有完成。", "请查看原因和已有结果；不要仅凭失败提示重发付款或假定历史业务已撤回。", "FAILED")
    if state == "CLOSED":
        return response("这次请求已关闭。", "尚未执行的步骤不会继续，已完成的业务与记录仍保留。", "CLOSED")
    if state == "PARTIAL":
        recorded = [node for node in nodes if _recorded(node, trace)]
        return response("部分内容已返回结果，剩余项目还需要处理。", f"有{len(recorded)}项记录可查看；请逐项核对，不将其余步骤当成已完成。", "PARTIAL")
    if state == "NEED_CLARIFICATION":
        from .customer_reply_gateway import naturalize_needed_questions, needed_question_sources
        raw = needed_question_sources(trace)
        questions = naturalize_needed_questions(raw,trace.get("customer_needed_fields"),user_message=trace.get('customer_user_message',trace.get('message','')))
        return response(questions[0] if questions else "你想查询什么，或办理哪项服务？", "\n".join(questions[1:])[:1800] or None, "PENDING")
    if state == "AWAITING_CONFIRMATION" or trace.get("pending_confirmation"):
        from .customer_deferred_preparation import deferred_preparation_notice
        later=deferred_preparation_notice(trace)
        detail="下方这一步尚未执行，是否办理仍由你决定；聊天中的确认不会代替确认流程。"
        return response("办理内容已整理好，请先核对下面的确认卡。", detail+(' '+later if later else ''), "PENDING")
    if state == "WAITING":
        scheduled = any(node.get("action") == "TRANSFER_SCHEDULE" and _identifier(_result(node), "schedule_id") for node in nodes)
        return response("预约转账已登记，正在等待约定日期。" if scheduled else "正在等待约定条件或后续处理。",
                        "登记不等于付款，到期会再次核验对象和资金；当前不能认定已转出。", "SCHEDULED")
    recorded = [node for node in nodes if _recorded(node, trace)]
    if state != "SUCCEEDED":
        return response("正在等待最新业务状态。", "请以实际返回的业务结果为准。", "PROGRESS")
    # A successful external-guidance/status read is not a cancelled service.
    if len(nodes) == 1 and kinds[0] in {'SUBSCRIPTION_START','SUBSCRIPTION_RENEW'} and (trace.get('outcome') or {}).get('verified') is True \
        and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT':
        result=_result(nodes[0])
        if result.get('no_effect_kind') == 'REGISTERED_UNAVAILABLE_GUIDANCE' and result.get('can_start_here') is False \
            and result.get('can_renew_here') is False and type(result.get('charged_minor')) is int and result['charged_minor']==0 \
            and not result.get('payment_id') and not result.get('transaction_id'):
            return response(f"目前不能在这里开通或续期{_text(result.get('merchant')) or '这项服务'}。",'需到服务商核对服务状态和办理方式，本次没有开通或扣款。','GUIDANCE',readonly=True)
    if len(nodes) == 1 and kinds[0] == 'SUBSCRIPTION_START' and (trace.get('outcome') or {}).get('verified') is True \
        and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT' and _subscription_already_active(_result(nodes[0])):
        if _result(nodes[0]).get('no_effect_kind') == 'START_PAID_RIGHTS_ACTIVE':
            return response(f"{_text(_result(nodes[0]).get('merchant'))}的已付权益仍可使用。", '本次没有重复收费或延长权益，自动续费仍关闭。', 'QUERY_RESULT', readonly=True)
        return response(f"{_text(_result(nodes[0]).get('merchant'))}已经启用。", '本次只查询了当前状态，没有重复开通或扣款。', 'QUERY_RESULT', readonly=True)
    if len(nodes) == 1 and kinds[0] == "SUBSCRIPTION_CANCEL":
        result = _result(nodes[0])
        if (trace.get('outcome') or {}).get('verified') is True and (trace.get('outcome') or {}).get('kind') == 'QUERY_RESULT' \
            and result.get('already_cancelled') is True and result.get('auto_renew') is False and result.get('status') == 'ACTIVE' \
            and result.get('entitlement_preserved') is True and _identifier(result,'subscription_id') and type(result.get('refunded_minor')) is int and result['refunded_minor']==0:
            return response('这项服务的自动续费原本就未开启。','本次只查询了状态，没有重复取消；原付费权益期限未改变，也没有退款。','QUERY_RESULT',readonly=True)
        if result.get("source_type") == "EXTERNAL_UNCONNECTED" and result.get("can_cancel_here") is False:
            return response("这项服务尚未接入取消办理，请按服务商指引处理。", "请到服务商设置中核实并关闭，本次没有取消外部服务。", "GUIDANCE")
        if (trace.get("outcome") or {}).get("verified") is True and result.get("already_cancelled") is True and result.get("status") == "CANCELLED" and _identifier(result, "subscription_id"):
            return response("这项订阅当前已取消，本次只查询了状态。", "没有重复执行取消，也没有影响其他服务。", "QUERY_RESULT")
    if len(recorded) != len(nodes) or not nodes:
        return response("已有返回信息，请查看具体结果。", "目前没有足够的业务结果依据确认全部内容已完成，不把准备方案当成完成回执。", "UNVERIFIED")
    if (trace.get("outcome") or {}).get("verified") is not True:
        return response("已看到业务记录，仍需核对最终状态。", "没有确认全部步骤的最终完成性，不据此重复付款。", "UNVERIFIED")
    from .customer_bill_read_projection import bill_node_trace
    summaries = [(_read_summary(node["action"], _result(node), bill_node_trace(trace, node) if node['action'] == 'BILL_ANALYSIS' else trace) if node["action"] in READ_KINDS else
                  _write_summary(node["action"], _result(node))) for node in recorded]
    if len(summaries) == 1:
        intro, detail = summaries[0]
    else:
        intro = "查询结果已整理好。" if read_only else "本次已完成的业务结果已整理好。"
        detail = "\n".join(intro + detail for intro, detail in summaries)[:2300]
    state_kind = ("QUERY_RESULT" if read_only else "PROPOSAL_SAVED" if all(kind == "FAMILY_PROPOSE" for kind in kinds)
                  else "COLLECTION_CREATED" if all(kind == "AA_REQUEST" for kind in kinds)
                  else "CONTACT_SAVED" if all(kind == "CONTACT_BIND" for kind in kinds) else "COMMITTED")
    card_detail = (_subscription_summary(_result(recorded[0]), compact=True)[1]
                   if len(recorded) == 1 and kinds == ["SUBSCRIPTION_LIST"] else None)
    from .customer_query_layout import apply_query_layout
    return apply_query_layout(response(intro, detail, state_kind, card_detail=card_detail),trace)
