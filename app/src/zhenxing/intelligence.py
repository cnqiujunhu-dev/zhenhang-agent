"""Typed, untrusted model proposals and deterministic user-text grounding.

This module does not read a bank database or execute a capability. Even a
grounded action is only a proposal: entity resolution, policy and confirmation
belong to the runtime and domain compilers.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from .financial_intent import CN_FINANCIAL_INQUIRY, foreign_financial_request, personal_financial_inquiry
from .language import (EN_ACTION_PATTERNS, EN_CONDITION, EN_DATE_HINT, EN_IMPRECISE, EN_NO_WRITE, EN_PRIVATE_READ_VETO, EN_SCHEDULE_DATE, EN_UNMET_CONDITION, detect_reply_language, localize_clarification,
                       english_allowed, english_money_evidence, english_payee, english_receiving_currencies, english_slots, english_spending_reference)


ActionKind = Literal[
    "BILL_ANALYSIS", "CARD_STATUS", "CARD_LOCK", "CARD_UNLOCK",
    "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT", "TRANSFER",
    "WEALTH_COMPARE", "WEALTH_BUY", "WEALTH_REDEEM", "ASSET_POSITIONS_READ", "SUBSCRIPTION_LIST",
    "MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW", "PREFERENCE_SET", "PREFERENCE_REMOVE",
    "SUBSCRIPTION_CANCEL", "SUBSCRIPTION_START", "SUBSCRIPTION_RENEW", "TRANSFER_SCHEDULE", "AA_REQUEST",
    "FRAUD_ANALYZE", "FAMILY_VIEW", "FAMILY_GRANT", "FAMILY_REVOKE", "FAMILY_PROPOSE",
    "GOAL_VIEW", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE",
    "CARBON_VIEW", "CARBON_CLAIM", "CARBON_REDEEM",
    "FX_QUOTE", "FX_TRANSFER", "FX_EXCHANGE",
    "CONTACT_BIND",
    "OWN_ACCOUNT_TRANSFER",
    "AA_VIEW", "AA_PAY", "TRANSFER_SCHEDULE_VIEW", "CREDIT_VIEW", "CREDIT_STATEMENT", "CREDIT_REPAY", "CREDIT_INSTALLMENT",
    "CREDIT_SETTLE_INSTALLMENT", "CREDIT_APPLY", "CREDIT_LIMIT_REQUEST", "CREDIT_ACTIVATE",
    "OCCASION_VIEW", "OCCASION_SET", "LIFE_PLAN_VIEW", "LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL",
    "OCCASION_MEMBER_VIEW", "OCCASION_MEMBER_SET",
    "MERCHANT_CATALOG", "MERCHANT_ORDERS", "MERCHANT_PURCHASE",
]


class ActionSlots(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    payee_name: str | None = Field(default=None, min_length=1, max_length=80)
    card_last4: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{4}$")
    card_number: str | None = Field(default=None, pattern=r"^(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}$")
    card_type: Literal["DEBIT", "ALL"] | None = None
    card_balance_currencies: list[Literal['CNY', 'USD']] | None = Field(default=None, min_length=1, max_length=2,
        exclude_if=lambda value: value is None)

    @field_validator('card_balance_currencies')
    @classmethod
    def canonical_card_balance_currencies(cls, value):
        if value is None: return value
        from .card_balance_scope import canonical_currencies
        return canonical_currencies(value)
    recipient_alias: str | None = Field(default=None, min_length=1, max_length=32)
    replace_alias: bool | None = None
    product_name: str | None = Field(default=None, min_length=1, max_length=100)
    merchant_categories: list[Literal['CAKE', 'FLOWER', 'RESTAURANT']] | None = Field(default=None, min_length=1, max_length=3,
        exclude_if=lambda value: value is None)

    @field_validator('merchant_categories')
    @classmethod
    def canonical_merchant_categories(cls, value):
        if value is None: return value
        from .semantic_merchant_categories import canonical_categories
        return canonical_categories(value)
    asset_class: Literal["WEALTH", "STOCK", "PRECIOUS_METAL", "ALL"] | None = None
    benefit_name: str | None = Field(default=None, min_length=1, max_length=100)
    preference_kind: Literal["CONSUMPTION", "TRAVEL"] | None = None
    preference_value: str | None = Field(default=None, min_length=1, max_length=80)
    merchant: str | None = Field(default=None, min_length=1, max_length=100)
    subscription_months: int | None = Field(default=None, ge=1, le=12, strict=True)
    subscription_auto_renew: bool | None = None
    amount_minor: int | None = Field(default=None, gt=0, le=10**13)
    units: int | None = Field(default=None, gt=0, le=10**9, strict=True)
    memo: str | None = Field(default=None, max_length=80)
    scheduled_at: str | None = Field(default=None, min_length=1, max_length=80)
    participant_names: list[str] | None = Field(default=None, max_length=20)
    participant_count: int | None = Field(default=None, ge=2, le=20)
    context_message: str | None = Field(default=None, max_length=2000)
    grantee_name: str | None = Field(default=None, min_length=1, max_length=80)
    owner_name: str | None = Field(default=None, min_length=1, max_length=80)
    scopes: list[Literal["READ_BALANCE", "PROPOSE_TRANSFER", "PROPOSE_CARD_LOCK"]] | None = Field(default=None, max_length=3)
    maximum_amount_minor: int | None = Field(default=None, gt=0, le=5_000_000)
    expires_days: int | None = Field(default=None, ge=1, le=180)
    proposal_kind: Literal["TRANSFER", "CARD_LOCK"] | None = None
    goal_name: str | None = Field(default=None, min_length=1, max_length=80)
    target_minor: int | None = Field(default=None, gt=0, le=10**13)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    monthly_saving_minor: int | None = Field(default=None, ge=0, le=10**13, strict=True)
    monthly_saving_delta_minor: int | None = Field(default=None, gt=0, le=10**13, strict=True)
    monthly_saving_total_minor: int | None = Field(default=None, ge=0, le=10**13, strict=True)
    category_filter: str | None = Field(default=None, min_length=1, max_length=32)
    behavior_name: str | None = Field(default=None, min_length=1, max_length=100)
    reward_name: str | None = Field(default=None, min_length=1, max_length=100)
    limit_period: Literal["PER_PROPOSAL", "MONTH"] | None = None
    currency: Literal["CNY", "USD"] | None = None
    source_currency: Literal["CNY", "USD"] | None = None
    target_currency: Literal["CNY", "USD"] | None = None
    recipient_card_reference: str | None = Field(default=None, min_length=4, max_length=80)
    destination_card_reference: str | None = Field(default=None, pattern=r'^(?:(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[0-9a-fA-F]{4})$')
    schedule_frequency: Literal['ONCE', 'DAILY', 'WEEKLY', 'MONTHLY', 'EXPLICIT'] | None = None
    schedule_occurrence_times: str | None = Field(default=None,min_length=1,max_length=1000)
    schedule_occurrence_count: int | None = Field(default=None, ge=1, le=366, strict=True)
    schedule_end_at: str | None = Field(default=None, max_length=80)
    schedule_reference: str | None = Field(default=None, min_length=1, max_length=100, exclude_if=lambda value: value is None)
    schedule_start_month: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$', exclude_if=lambda value: value is None)
    schedule_day_of_month: int | None = Field(default=None, ge=1, le=31, strict=True, exclude_if=lambda value: value is None)
    aa_scope: Literal['ALL', 'PAYABLE', 'COLLECTIBLE', 'PENDING'] | None = None
    aa_collection_ref: str | None = Field(default=None,min_length=1,max_length=100)
    merchant_order_scope: Literal['ALL','LIFE_PLANS'] | None = Field(default=None,exclude_if=lambda value:value is None)
    aa_note: str | None = Field(default=None, min_length=1, max_length=160, exclude_if=lambda value: value is None)
    aa_obligation_ref: str | None = Field(default=None, max_length=100)
    aa_expires_at: str | None = Field(default=None, max_length=80)
    card_reference: str | None = Field(default=None, max_length=100)
    source_card: str | None = Field(default=None, max_length=100)
    repayment_mode: Literal['FULL', 'MINIMUM', 'AMOUNT'] | None = None
    payment_id: str | None = Field(default=None, max_length=100)
    installment_id: str | None = Field(default=None, max_length=100)
    application_id: str | None = Field(default=None, max_length=100)
    periods: int | None = Field(default=None, ge=1, le=60, strict=True)
    requested_limit_minor: int | None = Field(default=None, gt=0, le=10_000_000, strict=True)
    family_member_name: str | None = Field(default=None, max_length=100)
    family_member_id: str | None = Field(default=None, max_length=100)
    family_member_new_name: str | None = Field(default=None, min_length=1, max_length=40)
    family_relationship: str | None = Field(default=None, min_length=1, max_length=40)
    occasion_id: str | None = Field(default=None, max_length=100)
    birthday_month: int | None = Field(default=None, ge=1, le=12, strict=True)
    birthday_day: int | None = Field(default=None, ge=1, le=31, strict=True)
    birthday_calendar: Literal['GREGORIAN', 'LUNAR'] | None = None
    birthday_timezone: str | None = Field(default=None, max_length=80)
    leap_day_policy: Literal['FEB28', 'MAR1', 'LEAP_YEARS_ONLY'] | None = None
    lunar_leap_month: bool | None = None
    confirmed_solar_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    life_plan_id: str | None = Field(default=None, max_length=100)
    life_plan_name: str | None = Field(default=None, max_length=100)
    life_plan_items: str | None = Field(default=None, min_length=1, max_length=500)
    life_plan_reserve_at: str | None = Field(default=None, min_length=1, max_length=80)
    life_plan_order_at: str | None = Field(default=None, min_length=1, max_length=80)
    budget_minor: int | None = Field(default=None, gt=0, le=10**13, strict=True)
    plan_year: int | None = Field(default=None, ge=2000, le=2200, strict=True)
    recipient_name: str | None = Field(default=None, max_length=100)
    recipient_phone: str | None = Field(default=None, max_length=40)
    recipient_address: str | None = Field(default=None, max_length=500)
    delivery_start: str | None = Field(default=None, max_length=80)
    delivery_end: str | None = Field(default=None, max_length=80)
    merchant_product_id: str | None = Field(default=None, max_length=100)
    quantity: int | None = Field(default=None, ge=1, le=100, strict=True)


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: ActionKind
    slots: ActionSlots = Field(default_factory=ActionSlots)
    depends_on: list[int] = Field(default_factory=list, max_length=8)
    label: str = Field(default="", max_length=160)


class IntentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(default="", max_length=500)
    actions: list[ProposedAction] = Field(default_factory=list, max_length=8)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)
    model_label: str = Field(default="", max_length=160)
    fallback: bool = False
    failure_reasons: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def prior_dependencies_only(self):
        for index, action in enumerate(self.actions):
            if any(isinstance(dep, bool) or dep < 0 or dep >= index for dep in action.depends_on):
                raise ValueError("Dependencies must reference earlier actions")
            if len(set(action.depends_on)) != len(action.depends_on):
                raise ValueError("Duplicate dependency")
        return self


ACTION_LABELS = {
    "BILL_ANALYSIS": "分析账单", "CARD_STATUS": "查询卡片状态",
    "CARD_LOCK": "临时锁卡", "CARD_UNLOCK": "解除临时锁定",
    "CARD_REPORT_LOSS": "挂失卡片", "CARD_REPLACEMENT": "申请补卡",
    "CARD_SET_LIMIT": "调整卡片限额", "TRANSFER": "转账",
    "WEALTH_COMPARE": "比较理财产品", "WEALTH_BUY": "申购理财",
    "ASSET_POSITIONS_READ": "查询本人资产持仓", "MEMBERSHIP_VIEW": "查询客户等级与权益",
    "BENEFITS_VIEW": "查询优惠活动", "PREFERENCE_VIEW": "查看本人偏好",
    "PREFERENCE_SET": "保存本人明确偏好", "PREFERENCE_REMOVE": "撤回本人偏好",
    "WEALTH_REDEEM": "赎回理财", "SUBSCRIPTION_LIST": "查询订阅代扣",
    "SUBSCRIPTION_CANCEL": "取消订阅代扣",
    "SUBSCRIPTION_START": "开通会员订阅", "SUBSCRIPTION_RENEW": "续期会员订阅",
    "TRANSFER_SCHEDULE": "定时转账", "AA_REQUEST": "发起 AA 收款",
    "FRAUD_ANALYZE": "分析转账风险", "FAMILY_VIEW": "查看家庭协助",
    "FAMILY_GRANT": "建立家庭委托", "FAMILY_REVOKE": "撤销家庭委托", "FAMILY_PROPOSE": "为家人提交协助提案",
    "GOAL_VIEW": "分析目标现金流", "GOAL_CREATE": "创建储蓄目标",
    "GOAL_RESERVE": "预留目标资金", "GOAL_RELEASE": "释放目标预留",
    "CARBON_VIEW": "查看低碳权益", "CARBON_CLAIM": "核算碳积分", "CARBON_REDEEM": "兑换低碳权益",
    "FX_QUOTE": "查询外汇报价", "FX_TRANSFER": "美元计价转账", "FX_EXCHANGE": "兑换人民币与美元",
    "CONTACT_BIND": "设置本人收款人角色别名",
}
READ_KINDS = {"BILL_ANALYSIS", "CARD_STATUS", "WEALTH_COMPARE", "ASSET_POSITIONS_READ", "MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW", "SUBSCRIPTION_LIST", "FRAUD_ANALYZE", "FAMILY_VIEW", "GOAL_VIEW", "CARBON_VIEW", "FX_QUOTE"}
from .upgraded_capabilities import CAPABILITIES as UPGRADED_CAPABILITIES
ACTION_LABELS.update({kind: value[0] for kind, value in UPGRADED_CAPABILITIES.items()})
READ_KINDS.update(kind for kind, value in UPGRADED_CAPABILITIES.items() if value[4])


def domain_for_action(kind: str) -> str:
    if kind in UPGRADED_CAPABILITIES:
        return UPGRADED_CAPABILITIES[kind][1]
    if kind == "ASSET_POSITIONS_READ":
        return "ASSETS"
    if kind == "MEMBERSHIP_VIEW":
        return "MEMBERSHIP"
    if kind == "BENEFITS_VIEW":
        return "BENEFITS"
    if kind.startswith("PREFERENCE_"):
        return "PREFERENCES"
    if kind in {"TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "FX_TRANSFER"}:
        return "TRANSFER"
    if kind in {"FX_QUOTE", "FX_EXCHANGE"}:
        return "FX"
    if kind == "CONTACT_BIND":
        return "CONTACTS"
    if kind == "BILL_ANALYSIS":
        return "BILLS"
    if kind.startswith("CARD_"):
        return "CARDS"
    if kind.startswith("WEALTH_"):
        return "WEALTH"
    if kind.startswith("SUBSCRIPTION_"):
        return "SUBSCRIPTIONS"
    return {"FRAUD": "FRAUD", "FAMILY": "FAMILY", "GOAL": "GOALS", "CARBON": "CARBON"}.get(kind.split("_")[0], "UNSUPPORTED")


def action_signature(action: dict) -> dict:
    # Optional schema growth must not cause already-completed actions to repeat.
    return {"kind": action["kind"], "slots": {key: value for key, value in action.get("slots", {}).items() if value is not None}}
_FX_PRICE_QUERY = re.compile(r"(?:要|需要|应|需|得)(?:支付|付款|付出|花|付|扣)?多少|多少钱|(?:能|可以|可)(?:换|兑)(?:成|到|得)?多少|(?:换|兑)(?:成|到|得)?多少|(?:查询|查看|看看|查一下).{0,24}(?:报价|价格|汇率|换汇)|(?:报价|价格)(?:多少|如何|怎样)")
# A complete single-clause request may state an exact CNY amount before the
# payee. It is a pending transfer intent, never authority to send the money.
_BARE_CNY_TRANSFER = r"^(?:(?:我想|我要|请帮我|帮我|请)\s*)?转\s*(?:人民币\s*)?\d+(?:\.\d{1,2})?\s*(?:元|人民币|块|CNY|RMB)[。！!？?\s]*$"
_AMOUNT_FIRST_COMMAND = r"(?:转|汇)\s*(?:人民币|美元|美金|USD|CNY|RMB)?\s*\d+(?:,\d{3})*(?:\.\d{1,6})?\s*(?:[wW](?:\s*(?:元|人民币|块|美元|美金|USD|CNY|RMB))?|万(?:元|人民币|块|美元|美金|USD|CNY|RMB)?|元|人民币|块|美元|美金|USD|CNY|RMB)\s*(?:给|向|到)(?!\s*[\d零〇一二两三四五六七八九十百千万])"
_OWNED_CARD_QUESTION = (
    r"(?:(?:我的|本人的|我自己的)(?:银行卡|卡片|卡)(?:(?:都有|有)?哪些|(?:的)?余额(?:是多少|有多少|还剩多少|多少)?)|"
    r"(?:我|本人)(?:现在|一共|总共)?(?:有|持有)(?:几|多少)张(?:银行卡|卡片|卡))(?=$|[，,。；;！!？?\s])"
)
_CARBON_REDEMPTION_INQUIRY = (
    r"(?:能不能|能否|可不可以|是否可以|是否能|可以|能)\s*"
    r"(?:用(?:这些|我的)?(?:低碳)?积分\s*)?(?:兑换|换)\s*"
    r"(?:什么|哪些(?:权益)?|哪种(?:权益)?|啥|公交券|乘车券|费率券|低碳权益)"
)
# A parent's balance is a delegated family projection, never the child's own
# bill/account tool. Require an actual read request or balance question: merely
# mentioning a parent's funds must not authorize either a read or a write.
_PARENT_BALANCE_QUERY = re.compile(
    r"(?:查询|查看|查一下|看一下|查查|看看|查下|看下|查|看)\s*(?:一下\s*)?(?:我的?\s*)?"
    r"(?:爸爸|妈妈|父亲|母亲)(?:的)?(?:银行卡|银行账户|银行|账户|帐户|卡)?余额|"
    r"(?:爸爸|妈妈|父亲|母亲)(?:的)?(?:银行卡|银行账户|银行|账户|帐户|卡)?余额"
    r"(?:是多少|还有多少|还剩多少|有多少|多少)"
)
_ACTION_PATTERNS: dict[str, re.Pattern] = {
    "BILL_ANALYSIS": re.compile(r"账单|帐单|账目|消费(?:记录|情况|明细|报告)|(?:分析|看看|查).{0,24}(?:扣款|交易|花费|支出)|(?:看看|看一下|分析).{0,16}(?:花(?:了|在|多少)|收支)|(?:生成|总结|查看|分析).{0,12}(?:收支|收入|支出).{0,6}(?:报告|总结)|(?:收支|收入|支出).{0,12}(?:做|整理|制作).{0,6}(?:报告|总结)|^(?:请|帮我)?(?:只看|仅看|只查|只统计|筛选|过滤)[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z _-]{0,31}[。！？!?\s]*$"),
    "CARD_STATUS": re.compile(r"卡(?:片)?(?:状态|限额)|(?:查看|查询|看看|查一下|看一下|查查|查下|看下|查).{0,12}卡|卡.{0,8}(?:能不能用|还能用|怎么样)|" + _OWNED_CARD_QUESTION),
    "CARD_LOCK": re.compile(r"(?<!解)锁卡|(?<!解)锁(?:住|上).{0,8}卡|卡.{0,8}(?:(?<!解)锁(?:住|上|了|一下|起来|定)?|冻结)|(?:临时锁定|冻结卡)|保护.{0,8}卡|(?:暂停|停止|禁用).{0,8}卡.{0,8}(?:支付|付款|刷卡)"),
    "CARD_UNLOCK": re.compile(r"解锁|解除.{0,24}(?:锁定|冻结)|恢复.{0,8}卡.{0,6}(?:支付|使用)"),
    "CARD_REPORT_LOSS": re.compile(r"挂失"),
    "CARD_REPLACEMENT": re.compile(r"补卡|换卡|补办.{0,5}卡"),
    "CARD_SET_LIMIT": re.compile(r"(?:调整|设置|设为|调到|改成|提高|降低|降到|升到).{0,10}限额|限额.{0,10}(?:调整|设置|设为|调到|改成|提高|降低|降到|升到)"),
    "TRANSFER": re.compile(r"转[账帐]|汇款|(?:转|汇|打)给|(?:给|向).{1,84}(?:转|汇|打)(?:[账帐])?|把.{0,22}(?:转给|汇给|打给)|打款|" + _BARE_CNY_TRANSFER + "|" + _AMOUNT_FIRST_COMMAND, re.I),
    "TRANSFER_SCHEDULE": re.compile(r"转[账帐]|汇款|转给|(?:给|向).{1,84}(?:转|汇|打)(?:[账帐])?|定时转|预约转|" + _AMOUNT_FIRST_COMMAND, re.I),
    "AA_REQUEST": re.compile(r"(?i)(?<![A-Za-z0-9_])AA(?![A-Za-z_])|平摊|分摊|均摊|分账"),
    "WEALTH_COMPARE": re.compile(r"理财|基金|(?:比较|对比|看看).{0,12}(?:产品|现金管理)"),
    # Legacy proposal grounding only; the production semantic gateway still
    # chooses an offered action and proves each supplied slot independently.
    "ASSET_POSITIONS_READ": re.compile(r"持仓|持有.{0,16}(?:份|股|克)|股票|贵金属|黄金|\b(?:positions?|holdings?|stocks?|gold)\b", re.I),
    "MEMBERSHIP_VIEW": re.compile(r"客户等级|客户权益|卡片等级|卡等级|\b(?:customer\s+tier|card\s+tier|membership\s+level)\b", re.I),
    "BENEFITS_VIEW": re.compile(r"优惠活动|活动权益|优惠资格|\b(?:benefits?|promotions?)\b", re.I),
    "PREFERENCE_VIEW": re.compile(r"偏好|\bpreferences?\b", re.I),
    "PREFERENCE_SET": re.compile(r"(?:保存|记住|设置|修改|更新).{0,40}偏好|偏好.{0,20}(?:设为|改为)|\b(?:save|remember|set|update)\b.{0,50}\bpreference\b", re.I),
    "PREFERENCE_REMOVE": re.compile(r"(?:撤回|删除|移除|忘记|取消).{0,25}偏好|\b(?:remove|delete|forget|withdraw)\b.{0,40}\bpreference\b", re.I),
    "WEALTH_BUY": re.compile(r"申购|买入|购买.{0,20}(?:理财|基金|产品|现金管理)|(?:把|用).{0,20}买(?:成|入)"),
    "WEALTH_REDEEM": re.compile(r"赎回|卖出"),
    "SUBSCRIPTION_LIST": re.compile(r"订阅|代扣|自动续费|会员|续费"),
    "SUBSCRIPTION_START": re.compile(r"开通|恢复.{0,12}(?:会员|订阅)|重新订阅|订阅|(?:购买|买)(?=.{0,30}(?:会员|订阅|云存储))|\b(?:subscribe|activate|reactivate|restore)\b",re.I),
    "SUBSCRIPTION_RENEW": re.compile(r"续订|续期|续费|\b(?:renew|extend)\b",re.I),
    "SUBSCRIPTION_CANCEL": re.compile(r"(?:取消|关闭|停用|退订|关掉)(?!转账|汇款|申购|赎回|锁卡|挂失|补卡|限额|所有|刚才|之前|原来).{1,40}|停止.{0,30}(?:订阅|代扣|自动续费|会员|续费)|(?:订阅|代扣|自动续费|会员|续费).{0,15}(?:取消|关闭|停用|停止|退订|关掉)|^[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 _-]{0,70}?(?:我)?(?:不用了|不续了|不再续费了|不续费了)[。！!\s]*$"),
    "FRAUD_ANALYZE": re.compile(r"反诈|诈骗|转账.{0,12}风险|风险.{0,12}转账|安全账户|屏幕共享|退款先转账"),
    "FAMILY_VIEW": re.compile(r"家庭|家人|委托|协助|授权.{0,6}(?:女儿|儿子|妈妈|爸爸|家人)|" + _PARENT_BALANCE_QUERY.pattern),
    "FAMILY_GRANT": re.compile(r"(?:授权|委托)(?:给)?\s*[“「\"‘']?(?:女儿|儿子|妈妈|爸爸|家人|[\u4e00-\u9fffA-Za-z0-9_]{1,80}).{0,84}(?:协助|帮我|查看|提案|提议|代办|转账|锁卡)"),
    "FAMILY_REVOKE": re.compile(r"(?:撤销|取消|收回|停止).{0,20}(?:委托|授权|家庭协助)"),
    "FAMILY_PROPOSE": re.compile(r"(?:为|帮)\s*[\u4e00-\u9fffA-Za-z0-9_· .-]{1,80}?(?:提交|发起|准备).{0,15}(?:提案|转账|锁卡)|(?:为|帮)\s*[\u4e00-\u9fffA-Za-z0-9_· .-]{1,80}?提出.{0,12}(?:锁卡|临时锁定).{0,8}(?:建议|提案)|协助提案|代办提案"),
    "GOAL_VIEW": re.compile(r"现金流|储蓄目标|(?:目标|旅行|养老|教育).{0,12}(?:进度|预算|分析|规划|提前)|(?:查看|查询|分析).{0,8}目标|(?:每月|每个月).{0,10}(?:少花|多存|多攒|减少支出|减少消费).{0,80}提前(?:多久|多少|几个月|几月)|(?:查看|查询|看看).{0,8}(?:能花|可花|可支出|可以花)的钱"),
    "GOAL_CREATE": re.compile(r"(?:创建|建立|设立|新建).{0,20}(?:目标|储蓄)|(?:目标|储蓄).{0,10}(?:创建|建立|设立)"),
    "GOAL_RESERVE": re.compile(r"(?:预留|划拨|保留).{0,20}(?:目标|旅行|教育|养老)|(?:目标|旅行|教育|养老).{0,20}(?:预留|划拨|保留)"),
    "GOAL_RELEASE": re.compile(r"(?:释放|取回|解除).{0,20}(?:目标|预留)|(?:目标|预留).{0,20}(?:释放|取回|解除)"),
    "CARBON_VIEW": re.compile(r"低碳|碳积分|碳权益|碳循环|(?:公交券|地铁通勤).{0,8}(?:权益|积分)|查看.{0,8}权益|(?:查(?:询|一下|查|下)?|看(?:一下|看)?)(?:我的)?积分|" + _CARBON_REDEMPTION_INQUIRY),
    "CARBON_CLAIM": re.compile(r"(?:领取|核算|申领|计入|记录).{0,25}(?:碳积分|低碳积分|低碳行为)|(?:碳积分|低碳积分).{0,12}(?:领取|核算|申领)"),
    "CARBON_REDEEM": re.compile(r"兑换.{0,20}(?:权益|公交券|乘车券|费率券|低碳)|(?:低碳|碳积分).{0,12}兑换"),
    "FX_QUOTE": re.compile(r"汇率|外汇报价|换汇报价|(?:查|查看|查询|看看).{0,18}(?:外汇|购汇|结汇)|" + _FX_PRICE_QUERY.pattern),
    "FX_TRANSFER": re.compile(r"转[账帐]|汇款|(?:转|汇|打)给|(?:给|向).{1,84}(?:转|汇|打)(?:[账帐])?|打款|" + _AMOUNT_FIRST_COMMAND, re.I),
    "FX_EXCHANGE": re.compile(r"换成|换为|兑成|兑为|兑换|换汇|购汇|结汇|(?:人民币|美元|美金|(?i:USD|CNY)).{0,20}换"),
    "CONTACT_BIND": re.compile(r"(?:设置|设定|设为|绑定|标记|记为|保存).{0,24}(?:我的|本人|收款|联系人|角色|别名)|(?:我的|本人|房东|收款|联系人|角色|别名).{0,24}(?:改为|改成|改绑|换成|替换为)"),
}
for _kind, _pattern in EN_ACTION_PATTERNS.items():
    _ACTION_PATTERNS[_kind] = re.compile("(?:" + _ACTION_PATTERNS[_kind].pattern + ")|(?i:" + _pattern + ")")
_BASE_BILL_PATTERN = _ACTION_PATTERNS["BILL_ANALYSIS"]
_ACTION_PATTERNS["BILL_ANALYSIS"] = re.compile("(?:" + _BASE_BILL_PATTERN.pattern + ")|(?:" + CN_FINANCIAL_INQUIRY + ")")
_NEGATION = re.compile(r"不要|不想|不需要|无需|先别|暂时不|暂不|不能|不得|不许|不准|禁止|别|不(?:要)?再|撤销|撤回|取消(?:刚才|之前|这次|原来)?.{0,5}(?:转账|汇款|申购|赎回|锁卡|挂失|补卡|限额)")
# A standalone hypothetical receiving-currency note is unproven FX metadata,
# not a condition on the independently stated transfer. Its target_currency
# remains unset by _receiving_currency_signals; a new bank quote is still checked.
_HYPOTHESIS_CONDITION = r"假设(?!\s*(?:收款方|收方|对方|收款人)\s*(?:到账|收款|收到|接收)\s*(?:人民币|美元|美金|(?i:CNY|USD))\s*(?=[，,。；;\n]|$))"
_CONDITIONAL = re.compile(r"如果|若(?:是|发现|有|没有|余额|异常)|假如|" + _HYPOTHESIS_CONDITION + r"|一旦|等.{0,18}(?:后|再)|只有.{0,18}(?:才|时)|(?:完成|成功|确认).{0,8}(?:之后|以后|后再)|明天|后天|下(?:周|个月)|月底|定时|预约")
_UNMET_CONDITION = re.compile(r"如果|若(?:是|发现|有|没有|余额|异常)|假如|" + _HYPOTHESIS_CONDITION + r"|一旦|等.{0,18}(?:后|再)|只有.{0,18}(?:才|时)|(?:完成|成功|确认).{0,8}(?:之后|以后|后再)")
_DATE_EXPRESSION = re.compile(r"(?:明天|后天|下周(?:[一二三四五六日天])?|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日)(?:\s*(?:上午|下午|晚上)?(?:\d{1,2}:\d{2}|(?:\d{1,2}|[零一二三四五六七八九十]{1,3})点(?:(?:\d{1,2}|[零一二三四五六七八九十]{1,3})分)?))?")
_QUESTION = re.compile(r"(?:怎么|如何|能否|可否|是不是|是否可以|能不能|可以吗|吗[？?]?$)")
_READ_OR_EXPLANATION = re.compile(r"查询|查一下|查看|看看|解释|介绍|说明|方法|教程|流程|记录|历史|明细|结果|失败原因")
_GLOBAL_NO_WRITE = re.compile(r"不要(?:真的)?执行|不执行|别执行|先别操作|不要操作|只(?:做)?(?:演示|查询|分析)|取消所有(?:操作|任务|步骤)|撤销刚才的(?:请求|操作)")
_PRIVATE_READ_VETO = re.compile(r"(?:不要|不想|拒绝|不)(?:查询|读取|访问|查看)(?:本人的|本人|我的|我)(?:银行|账户|个人)?(?:数据|资料|流水|账单|信息|余额)")


def wealth_units_in_text(text: str) -> set[int]:
    """Only exact whole units are supported by the current holding ledger."""
    from .wealth_quantity_polarity import mask_denied_wealth_units
    text=mask_denied_wealth_units(text)
    number = r"[\d零〇一二两三四五六七八九十百千万]+"
    unit = r"(?:份|units?\b|shares?\b)"
    if re.search(r"(?:\d+\s*/\s*\d+|\d+\s*\.\s+\d+|" + number + r"(?:又|分之)" + number + r"|" + number + r"又半)\s*" + unit, text, re.I):
        return set()
    if re.search(number + r"\s*" + unit + r"\s*(?:半|\d+\s*/\s*\d+|三分之一|二分之一)", text, re.I):
        return set()
    units = set()
    for match in re.finditer(r"(?<![\d./])(?P<number>\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:份(?![额\u4e00-\u9fff])|(?:units?|shares?)\b)", text, re.I):
        before = text[max(0, match.start()-32):match.start()]
        if re.search(r"[-－负]|约|大概|大约|至少|最多|不超过|不少于|(?:至|到|~|～|-)\s*$|\b(?:about|around|approximately|at\s+most|at\s+least|up\s+to|to|through)\s*$", before, re.I):
            continue
        try:
            value = int(match.group("number")) if match.group("number").isdigit() else _chinese_integer(match.group("number"))
        except (ValueError, TypeError):
            continue
        if 0 < value <= 10**9:
            units.add(value)
    return units


def _subscription_stop_merchant(text: str) -> str | None:
    match = re.fullmatch(r"\s*(?:我(?:的)?\s*)?(?P<merchant>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 _-]{0,70}?)(?:我)?(?:不用了|不续了|不再续费了|不续费了)[。！!\s]*", text)
    if not match:
        return None
    name = match.group("merchant").strip().removesuffix("的自动续费").removesuffix("的订阅").removesuffix("的续费")
    return name if name and not re.search(r"不要|如果|假如|先别|取消|查询|怎么|如何|能不能", name) else None


def _private_read_veto(message: str) -> bool:
    text = _intent_text(message)
    return bool(_PRIVATE_READ_VETO.search(text) or EN_PRIVATE_READ_VETO.search(text))


_SOURCE_PREFIX = re.compile(r"(?:^|[，,:：;；。\n])\s*(?P<label>商户(?:备注|文案|消息)|(?:短信|邮件|网页|截图|聊天记录|合同|示例|引用)(?:内容|中|里)?|对方(?:说|要求|让我)|(?:朋友|同事|客服|他|她|他们|她们)(?:说|要求|让我)|备注)(?:写着|写了|写的是|内容是|说|是|为)?[：:]?\s*")
_OWN_REQUEST_BOUNDARY = re.compile(r"[，,]\s*(?:另外|但(?:现在)?|我(?:现在|仍然)?(?:要|请|要求)|请(?:你)?|帮我)(?=.{0,20}(?:转|汇|锁|解|挂|申|赎|取消|查看|分析|授权))")
_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_MONEY = re.compile(r"(?<![\d.])(?P<cny>人民币\s*)?(?P<number>\d+(?:,\d{3})*(?:\.\d{1,2})?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]{1,2})?)\s*(?P<unit>万(?:元|块)?(?:\s*人民币)?|元(?:\s*人民币)?|块(?:钱)?(?:\s*人民币)?|人民币|分|角)?")
_FOREIGN_CURRENCY = r"(?:USD|US\$|美元|美金|EUR|欧元|HKD|港元|港币|JPY|日元|日币|GBP|英镑|澳元|加元|澳币|加币|[$€£])"
_NUMBER_TOKEN = r"\d+(?:,\d{3})*(?:\.\d{1,2})?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]{1,2})?"
_CARD_NUMBER = r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}"
_RECIPIENT_REFERENCE = "(?:" + _CARD_NUMBER + r"|[A-Za-z][A-Za-z0-9_]{2,31})"
_USD_MONEY = re.compile(r"(?<![\d.])(?P<usd>(?:USD|US\$|美元|美金)\s*)?(?P<number>" + _NUMBER_TOKEN + r")\s*(?P<unit>万(?:美元|美金|USD|US\$)|美元|美金|USD|US\$|美分|cents)?", re.IGNORECASE)
_USD_CURRENCY = r"(?:美元|美金|(?<![A-Za-z_])USD(?![A-Za-z_])|US\$|\bUS\s+dollars?\b|\bU\.S\.\s+dollars?\b)"
_CNY_CURRENCY = r"(?:人民币|(?<![A-Za-z_])(?:CNY|RMB)(?![A-Za-z_])|\b(?:yuan|renminbi)\b)"
_OTHER_CURRENCY = r"(?:(?<![A-Za-z_])(?:EUR|HKD|JPY|GBP|AUD|CAD|NZD|CHF|SGD)(?![A-Za-z_])|欧元|港元|港币|日元|日币|英镑|澳元|澳币|加元|加币|新西兰元|瑞士法郎|新加坡元|€|£)"

# Exact colloquial magnitude, not a rewritten utterance. Source offsets remain
# those of the user's original text; 'w' inside an identifier is never a unit.
_WAN_CURRENCY = r"(?:人民币|美元|美金|US\$|USD|CNY|RMB|元|块)"
_WAN_LITERAL = (r"(?P<prefix>" + _WAN_CURRENCY + r")?\s*"
                r"(?P<number>\d+(?:,\d{3})*(?:\.\d{1,6})?)\s*[wW]"
                r"\s*(?P<suffix>" + _WAN_CURRENCY + r")?")
_WAN_MONEY = re.compile(r"(?<![A-Za-z0-9_.])" + _WAN_LITERAL + r"(?![A-Za-z0-9_.])", re.I)
_AMOUNT_FIRST_MONEY = (r"(?:" + _WAN_CURRENCY + r")?\s*(?:" + _NUMBER_TOKEN
                       + r")\s*(?:[wW](?:\s*" + _WAN_CURRENCY + r")?|万(?:元|块|人民币|美元|美金|USD|US\$)?|元|块|人民币|美元|美金|USD|US\$)")
_AMOUNT_FIRST_TRANSFER = re.compile(
    r"(?:转[账帐]|转|汇款|汇|打款)\s*(?P<money>" + _AMOUNT_FIRST_MONEY + r")\s*(?:给|向|到)\s*"
    r'(?:[“「"](?P<quoted>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79})[”」"]|'
    r"(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_.-]{0,79}))(?=$|[，,。；;！!？?\s])", re.I)


def _wan_money_evidence(text: str, currency: str, *, allow_limits: bool = False):
    """Integer Fen/cents proved by an exact w/W magnitude in this source."""
    text = _mask_card_numbers(text)
    # A known direct object can contain Chinese numerals, currency words and w.
    # Mask only its span; do not alter amounts or any following instruction.
    object_spans = [match.span("name") for match in _direct_transfer_match(text, all_matches=True)]
    object_spans += [match.span("quoted" if match["quoted"] else "name")
                     for match in _AMOUNT_FIRST_TRANSFER.finditer(text)]
    for start, end in sorted(set(object_spans), reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    for match in _WAN_MONEY.finditer(text):
        tokens = [token for token in (match["prefix"], match["suffix"]) if token]
        codes = {"USD" if re.fullmatch(r"美元|美金|USD|US\$", token, re.I) else "CNY" for token in tokens}
        # No currency specified follows the existing domestic-transfer default.
        if len(codes) > 1 or (next(iter(codes), "CNY") != currency):
            continue
        prefix, suffix = text[:match.start()], text[match.end():]
        if re.search(r"(?:[-−﹣－±负]\s*|\d\s*[,，]\s*|(?:大概|大约|约|左右|上下|不到|剩余|余额|全部)\s*)$", prefix):
            continue
        if not allow_limits and re.search(r"(?:超过|最多|至少|以内|不超过|上限|限额)\s*$", prefix):
            continue
        if EN_IMPRECISE.search(prefix[-35:]) or re.search(r"\b(?:minus|negative|plus)\s*$", prefix, re.I):
            continue
        if re.search(r"(?:" + _FOREIGN_CURRENCY + r"|人民币|CNY|RMB)\s*$", prefix, re.I):
            continue  # A conflicting/unsupported currency outside this token.
        if re.search(r"[\d.]+\s*(?:[wW]|万元?|元|人民币|美元|美金|USD)?\s*(?:到|至|-|~|～|—|–|or|to|through)\s*$", prefix, re.I):
            continue
        if re.match(r"\s*(?:[\d零〇一二两三四五六七八九十百千万亿]|" + _FOREIGN_CURRENCY + r"|人民币|CNY|RMB|元|块|角|分|左右|上下|以内|以上|以下|多|余|出头|±|(?:到|至|-|~|～|—|–)\s*\d|(?:or|to|through)\s+\d)", suffix, re.I) or EN_IMPRECISE.match(suffix.strip()):
            continue
        try:
            minor = Decimal(match["number"].replace(",", "")) * 1_000_000
            if minor == int(minor) and 0 < minor <= 10**13:
                yield match.start(), match.end(), int(minor)
        except InvalidOperation:
            continue


def _partial_wan_token(text: str, start: int, end: int) -> bool:
    """A malformed or compound w value cannot fall back to a smaller amount."""
    return bool(re.match(r"\s*(?:[wW]|[eE][+-]?\d+\s*[wW]|_\s*[wW])", text[end:])
                or re.search(r"(?<![A-Za-z0-9_.])(?:(?:USD|CNY|RMB|US\$)\s*)?\d+(?:,\d{3})*(?:\.\d+)?\s*[wW]\s*$", text[:start], re.I))


def _chinese_integer(text: str) -> int:
    if not text:
        return 0
    if not any(char in text for char in "十百千万亿"):
        return int("".join(str(_DIGITS[char]) for char in text))
    if re.search(r"[一二两三四五六七八九][零〇一二两三四五六七八九]|[零〇][十百千万亿]", text):
        raise ValueError("Invalid adjacent Chinese digits before a magnitude")
    total = section = digit = 0
    last_magnitude = 10_000
    for char in text:
        if char in _DIGITS:
            digit = _DIGITS[char]
        elif char in "十百千":
            magnitude = {"十": 10, "百": 100, "千": 1000}[char]
            if magnitude >= last_magnitude:
                raise ValueError("Chinese magnitudes must descend within a section")
            last_magnitude = magnitude
            section += (digit or 1) * magnitude
            digit = 0
        elif char == "万":
            if section + digit == 0:
                raise ValueError("A large Chinese magnitude needs an explicit count")
            total += (section + digit) * 10_000
            section = digit = 0
            last_magnitude = 10_000
        elif char == "亿":
            if total + section + digit == 0:
                raise ValueError("A large Chinese magnitude needs an explicit count")
            total = (total + section + digit) * 100_000_000
            section = digit = 0
            last_magnitude = 10_000
        else:
            raise ValueError("Unknown Chinese numeral")
    # Colloquial 一万五 / 三百二 is ambiguous; require 五千 / 二十.
    if digit and text[-2:-1] in "百千万亿" and len(text) > 2:
        raise ValueError("Ambiguous abbreviated Chinese amount")
    return total + section + digit


def _cny_money_evidence(text: str, *, allow_limits: bool = False):
    yield from sorted(set(_wan_money_evidence(text, "CNY", allow_limits=allow_limits))
                      | set(_base_cny_money_evidence(text, allow_limits=allow_limits)))


def _base_cny_money_evidence(text: str, *, allow_limits: bool = False):
    """Yield exact source spans and Fen amounts, not approximate/foreign values."""
    text = _mask_card_numbers(text)
    for start, end, minor in english_money_evidence(text, "CNY", allow_limits=allow_limits):
        if not _partial_wan_token(text, start, end):
            yield start, end, minor
    for match in _MONEY.finditer(text):
        if any(match.start() < short.end() and short.start() < match.end() for short in _WAN_MONEY.finditer(text)):
            continue  # 人民币1w is 10000 yuan, never the partial prefix 人民币1.
        if _partial_wan_token(text, match.start(), match.end()):
            continue  # Malformed shorthand also cannot downgrade to 人民币1.
        unit = match.group("unit") or ""
        if not unit and not match.group("cny"):
            continue
        if unit in {"分", "角"} and not match.group("cny"):
            continue  # Standalone scores/minutes are not currency evidence.
        prefix = text[max(0, match.start() - 12):match.start()]
        if EN_IMPRECISE.search(text[max(0, match.start()-35):match.start()]) or EN_IMPRECISE.match(text[match.end():].strip()) or re.search(r"\b(?:minus|negative|plus)\s*$", prefix, re.I) or re.match(r"\s*(?:or|to|through)\s+\d|\s*(?:approximately|about|roughly)\b", text[match.end():], re.I):
            continue
        if re.search(r"(?:[-−﹣－±负]\s*|\d\s*[,，]\s*)$", prefix):
            continue
        if re.search(_FOREIGN_CURRENCY + r"\s*$", prefix, re.IGNORECASE):
            continue
        if re.search(r"(?:大概|大约|左右|上下|约|不到|剩余|余额|全部)\s*$", prefix) or (
                not allow_limits and re.search(r"(?:超过|最多|至少|以内)\s*$", prefix)):
            continue
        suffix = text[match.end():]
        if suffix.startswith(".") or (not unit and re.match(r"\s*[,，]\s*\d", suffix)) or re.match(r"\s*" + _FOREIGN_CURRENCY, suffix, re.IGNORECASE):
            continue
        if re.match(r"\s*(?:左右|上下|以内|以上|以下|余|多|出头|±|(?:到|至|-|~|～|—|–)\s*[\d零〇一二两三四五六七八九十百千万])", suffix):
            continue
        if _range_amount_prefix(prefix):
            continue
        if unit in {"分", "角"} and re.match(r"钟|数|度", suffix):
            continue
        try:
            token = match.group("number")
            if token[0].isdigit():
                amount = Decimal(token.replace(",", ""))
            else:
                integral, _, fraction = token.partition("点")
                amount = Decimal(_chinese_integer(integral))
                if fraction:
                    amount += Decimal("0." + "".join(str(_DIGITS[char]) for char in fraction))
            if unit.startswith("万"):
                amount *= 10_000
            minor = int(amount * (1 if unit == "分" else 10 if unit == "角" else 100))
            if amount * (1 if unit == "分" else 10 if unit == "角" else 100) != minor:
                continue
            # 元/角/分 is exact arithmetic; a dangling colloquial number (一百
            # 元五) is not silently treated as 一百元 and needs clarification.
            if re.match(r"[\d零〇一二两三四五六七八九]", suffix):
                fraction = re.match(r"(?:(?P<jiao>[\d零〇一二两三四五六七八九])(?:角|毛))?(?:(?P<fen>[\d零〇一二两三四五六七八九])分)?", suffix)
                if not fraction or fraction.end() == 0 or re.match(r"[\d零〇一二两三四五六七八九]", suffix[fraction.end():]):
                    continue
                for field, multiplier in (("jiao", 10), ("fen", 1)):
                    numeral = fraction.group(field)
                    if numeral:
                        minor += (int(numeral) if numeral.isdigit() else _DIGITS[numeral]) * multiplier
            if 0 < minor <= 10**13:
                yield match.start(), match.end(), minor
        except (ValueError, KeyError, InvalidOperation):
            continue


def amounts_in_text(text: str, *, allow_limits: bool = False) -> set[int]:
    """Exact, positive CNY amounts explicitly expressed by the user."""
    values = {minor for _start, _end, minor in _cny_money_evidence(text, allow_limits=allow_limits)}
    # Preserve legacy precise transfer syntax with no 元 suffix.
    for match in re.finditer(r"(?:转[账帐]|转|汇|打款)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d.])(?:$|[，,。；;\s])", text):
        if re.match(r"\s*(?:左右|上下|以内|以上|以下|多|余|出头|±|USD|美元|美金|EUR|欧元|HKD|港币|港元|JPY|日元)", text[match.end():], re.IGNORECASE):
            continue
        if re.match(r"\d", text[match.end():]) and text[match.end()-1:match.end()] == ",":
            continue
        value = int(Decimal(match.group(1).replace(",", "")) * 100)
        if 0 < value <= 10**13:
            values.add(value)
    return values


def _range_amount_prefix(prefix: str) -> bool:
    return bool(re.search(r"[\d零〇一二两三四五六七八九十百千万亿.]+\s*(?:[wW]\s*)?(?:美元|美金|USD|US\$|人民币|元|块)?\s*(?:到|至|-|~|～|—|–)\s*$", prefix, re.IGNORECASE))


def _extension_object_matches(kind: str, source: str) -> list[tuple[str, int, int]]:
    """Names end at an actual predicate, never an internal topic keyword.

    Quotes delimit an opaque argument only after the outer business verb. This
    function does not decide permission and never unmasks a quoted instruction.
    """
    if kind == "FAMILY_GRANT":
        prefix = r"(?:授权|委托)(?:给)?\s*"
        read = r"(?:(?:协助|帮我)\s*)?(?:查看|查询|看)\s*余额"
        write = (r"(?:转账|锁卡)(?:提案|建议|提议)|(?:(?:协助|帮我)\s*(?:(?:提出|提案|提议|建议|代办)\s*)?"
                 r"|(?:提出|提案|提议|建议|代办)\s*)(?:转账|锁卡)(?:提案|建议|提议)?")
        permission = "(?:" + read + "|" + write + ")"
        duration = r"(?:有效(?:期)?|期限|最长)?\s*\d+\s*天"
        limit = (r"(?:单笔|每笔|每月(?:累计)?|上限|限额|最多|至多|不超过){1,3}\s*(?:人民币\s*)?"
                 + "(?:" + _NUMBER_TOKEN + r")\s*(?:人民币|元|块|分|角)")
        field = "(?:" + duration + "|" + limit + ")"
        # Consume complete permission phrases before checking their boundary.
        # A phrase followed by 工作室/小助手 (or a field-like name fragment)
        # is still inside the object, not a shorter name plus extra authority.
        boundary = r"(?=\s*(?:$|[，,。；;]|(?:" + field + r"\s*){1,3}(?:$|[，,。；;])))"
        predicate = permission + r"(?:\s*(?:并且|并|和|及|、)\s*" + permission + "){0,2}" + boundary
    elif kind == "FAMILY_REVOKE":
        prefix, predicate = r"(?:撤销|取消|收回|停止)\s*", r"的?(?:委托|授权|家庭协助)(?=$|[，,。；;\s])"
    elif kind in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW"}:
        prefix = (r"(?:创建|建立|设立|新建)\s*" if kind == "GOAL_CREATE" else
                  r"(?:为|给|释放|取回|解除|查看|分析)\s*")
        boundary = r"(?=$|[，,。；;\s]|(?:\d+(?:\.\d{1,2})?|[零〇一二两三四五六七八九十百千万]+)(?:元|块|人民币)|(?:先|再)?(?:预留|划拨|保留|释放|取回|解除)|资金(?=\s*(?:\d|[零〇一二两三四五六七八九十百千万]))|截止|日期|有效)"
        predicate = r"(?:储蓄目标|目标)" + boundary
        alternate_predicate = r"(?:预留|划拨|保留)" + boundary if kind in {"GOAL_RESERVE", "GOAL_RELEASE"} else None
    else:
        return []
    names = []
    quotes = {"“": "”", "「": "」", '"': '"', "‘": "’", "'": "'"}
    for verb in re.finditer(prefix, source):
        tail = source[verb.end():]
        if tail[:1] in quotes:
            close = tail.find(quotes[tail[0]], 1)
            suffix_pattern = predicate + ("|" + alternate_predicate if kind.startswith("GOAL_") and alternate_predicate else "")
            if close < 0 or not re.match(r"\s*(?:的)?(?:" + suffix_pattern + ")", tail[close+1:]):
                continue
            value, start, end = tail[1:close], verb.end()+1, verb.end()+close
            if not value or len(value) > 80:
                continue
        else:
            # The closing predicate must have a real field/amount boundary.
            # For goal labels prefer the last eligible marker, allowing names
            # such as 云验收目标abcd1234 before the outer 储蓄目标.
            name = r"[\u4e00-\u9fffA-Za-z0-9_· .-]{1,80}" + ("?" if kind.startswith("FAMILY_") else "")
            matched = re.match(r"(?P<name>" + name + r")\s*(?P<predicate>" + predicate + ")", tail)
            if matched is None and kind.startswith("GOAL_") and alternate_predicate:
                matched = re.match(r"(?P<name>" + name + r")\s*(?P<predicate>" + alternate_predicate + ")", tail)
            if matched is None:
                continue
            value = matched.group("name").strip()
            start, end = verb.end()+matched.start("name"), verb.end()+matched.end("name")
            if kind.startswith("GOAL_") and matched.group("predicate").startswith("目标") and value.endswith("储蓄"):
                value = value[:-2]
                end -= 2
            if not value:
                continue
        names.append((value, start, end))
    names = list(dict.fromkeys(names))
    return [item for item in names if not any(other != item and other[1] <= item[1] and other[2] >= item[2]
                                             for other in names)]


def _extension_object_text(source: str, *, routing_kind: str | None = None) -> str:
    spans = {}
    for object_kind in ("FAMILY_GRANT", "FAMILY_REVOKE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW"):
        for _name, start, end in _extension_object_matches(object_kind, source):
            spans.setdefault((start, end), set()).add(object_kind)
    text = source
    for (start, end), object_kinds in sorted(spans.items(), reverse=True):
        replacement = " " * (end-start)
        if routing_kind == "FAMILY_GRANT" and "FAMILY_GRANT" in object_kinds:
            replacement = "某" + replacement[1:]
        elif routing_kind in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW"} and routing_kind in object_kinds:
            replacement = source[start:end]  # Own outer goal syntax retains legacy noun routing; policy checks use the full mask.
        text = text[:start] + replacement + text[end:]
    return text


def _opaque_object_text(source: str, kind: str) -> str:
    """Equal-length routing mask for explicit object labels, never source data.

    '家庭' as a payee/product name does not ask for family data. Slot extraction,
    currency proof and risk evaluation still receive the untouched user text.
    Object spans stop at their own verb/amount/boundary so another explicit
    query, including one in the same clause, retains its independent evidence.
    """
    spans = [match.span("name") for match in _direct_transfer_match(source, all_matches=True)]
    spans += [match.span("quoted" if match["quoted"] else "name") for match in _AMOUNT_FIRST_TRANSFER.finditer(source)]
    english_recipient = english_payee(source)
    if english_recipient:
        spans += [match.span() for match in re.finditer(re.escape(english_recipient), source)]
    if kind != "SUBSCRIPTION_CANCEL":
        merchant = english_slots("SUBSCRIPTION_CANCEL", source).get("merchant")
        if merchant:
            spans += [match.span() for match in re.finditer(re.escape(merchant), source)]
    if kind not in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        product = (english_slots("WEALTH_BUY", source) or english_slots("WEALTH_REDEEM", source)).get("product_name")
        if product:
            spans += [match.span() for match in re.finditer(re.escape(product), source)]
    name = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79}?"
    for reverse in re.finditer(r"(?:转[账帐]给|转给|汇给|打给)\s*", source):
        tail = source[reverse.end():]
        quoted = re.match(r'[“「"](?P<object>'+name+r')[”」"]', tail)
        if quoted:
            spans.append((reverse.end() + quoted.start("object"), reverse.end() + quoted.end("object")))
            continue
        monies = [start for start, _end, _minor in _cny_money_evidence(tail)]
        monies += [match.start() for match in _USD_MONEY.finditer(tail) if match.group("usd") or match.group("unit")]
        end = min(monies) if monies else len(tail)
        boundary = re.search(r"[，,。；;\s]", tail[:end])
        end = boundary.start() if boundary else end
        if end and re.fullmatch(name, tail[:end].strip()):
            spans.append((reverse.end(), reverse.end() + end))
    # Bracketed personal roles are a recipient object even where the direct
    # transfer matcher cannot include the parentheses around the account.
    role_patterns = (
        r"(?:向|给)(?:我的|本人(?:的)?)(?P<object>[\u4e00-\u9fff]{1,32}?)\s*[“「(（]\s*" + _RECIPIENT_REFERENCE + r"\s*[”」)）]\s*(?=转[账帐]|转|汇款|汇|打款|打)",
        r"把\s*" + _RECIPIENT_REFERENCE + r"\s*(?:账户|账号|银行卡|卡)?(?:设置为|设为|绑定为|标记为|记为|保存为)(?:我的|本人(?:的)?)(?P<object>[\u4e00-\u9fff]{1,32})(?=[，。；;\s]|$)",
        r"(?:把|将)(?:我的|本人(?:的)?)?(?P<object>[\u4e00-\u9fff]{1,32}?)(?:改为|改成|改绑为|换成|替换为)\s*" + _RECIPIENT_REFERENCE + r"(?:账户|账号|银行卡|卡)?(?=[，。；;\s]|$)",
    )
    patterns = [] if kind == "CONTACT_BIND" else list(role_patterns)
    if kind not in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        patterns.append(r"(?:申购|买入|赎回|卖出)\s*(?P<object>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9（）()\-]{1,70}?)(?=\d|[零〇一二两三四五六七八九十百千万]+(?:元|块)|[，。；\s]|$)")
        patterns.append(r'(?:申购|买入|赎回|卖出)\s*[“「"](?P<object>[^”」"\r\n]{1,80})[”」"]')
    if kind != "SUBSCRIPTION_CANCEL":
        patterns.append(r'(?:取消|关闭|停用|停止|退订|关掉)\s*[“「"](?P<object>[^”」"\r\n]{1,80})[”」"]\s*的?(?:订阅|代扣|自动续费|会员|续费)')
        for merchant in re.finditer(r"(?:取消|关闭|停用|停止|退订|关掉)\s*(?P<object>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]{0,40})(?=[，。；;\s]|$)", source):
            category = re.search(r"的?(?:订阅|代扣|自动续费|会员|续费)", merchant.group("object"))
            # Service-type nouns remain real routing evidence, e.g. a declined
            # cancellation may still yield a safe subscription overview.
            end = merchant.start("object") + category.start() if category else merchant.end("object")
            if end > merchant.start("object"):
                spans.append((merchant.start("object"), end))
    if kind not in {"GOAL_VIEW", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}:
        patterns += [r"(?:创建|建立|设立|新建)\s*(?P<object>[\u4e00-\u9fffA-Za-z0-9]{1,30}?)\s*(?:储蓄)?目标",
                     r"(?:为|给|查看|分析|释放|取回)\s*(?P<object>[\u4e00-\u9fffA-Za-z0-9]{1,30}?)目标",
                     r"(?:为|给|释放|取回)\s*(?P<object>[\u4e00-\u9fffA-Za-z0-9]{1,30}?)\s*(?:预留|划拨|保留)"]
        patterns += [r'(?:创建|建立|设立|新建|为|给|释放|取回|查看|分析)\s*[“「"](?P<object>[^”」"\r\n]{1,80})[”」"]\s*(?:储蓄)?目标',
                     r'(?:为|给|释放|取回)\s*[“「"](?P<object>[^”」"\r\n]{1,80})[”」"]\s*(?:预留|划拨|保留)']
    for pattern in patterns:
        spans += [match.span("object") for match in re.finditer(pattern, source)]
    text = _extension_object_text(source, routing_kind=kind)
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + " " * (end-start) + text[end:]
    return text


def _action_routing_text(kind: str, clause: str) -> str:
    # Fraud analysis explicitly inspects source material. Other private reads
    # need the customer's request outside that material, just like writes.
    source = clause if kind == "FRAUD_ANALYZE" else _intent_text(clause)
    if kind == "AA_REQUEST":
        source = _aa_intent_text(source)
    return _opaque_object_text(_mask_card_numbers(source), kind)


def _currency_text(message: str) -> str:
    """Currency words in a direct recipient username are not money instructions."""
    text = _extension_object_text(_intent_text(message))
    spans = [match.span("name") for match in _direct_transfer_match(text, all_matches=True)]
    spans += [match.span("quoted" if match["quoted"] else "name") for match in _AMOUNT_FIRST_TRANSFER.finditer(text)]
    recipient_en = english_payee(text)
    if recipient_en:
        spans.extend(match.span() for match in re.finditer(re.escape(recipient_en), text))
    recipient = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79}?"
    for match in re.finditer(r'(?:转账给|转给|汇给|打给)\s*[“「"](?P<name>'+recipient+r')[”」"]', text):
        spans.append(match.span("name"))
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + " " * (end-start) + text[end:]
    return _mask_card_numbers(text)


def _mask_card_numbers(text: str) -> str:
    return re.sub(_CARD_NUMBER, lambda match: " " * len(match.group(0)), text)


def _aa_intent_text(source: str) -> str:
    """Opaque named recipients cannot supply an AA keyword.

    This mask is only for AA routing, never for amount or identity extraction.
    Compact actual instructions such as AA100元 stay intact outside a recipient.
    """
    text = _mask_card_numbers(source)
    spans = [match.span("name") for match in _direct_transfer_match(source, all_matches=True)]
    spans += [match.span("quoted" if match["quoted"] else "name") for match in _AMOUNT_FIRST_TRANSFER.finditer(source)]
    for match in re.finditer(r'(?:转[账帐]给|转给|汇给|打给)\s*[“「"]?(?P<name>[A-Za-z][A-Za-z0-9_]{2,79})', source):
        spans.append(match.span("name"))
    reference = _recipient_reference_info(source).get("payee_name")
    if reference:
        spans.extend(match.span() for match in re.finditer(re.escape(reference), source))
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + " " * (end-start) + text[end:]
    return text


def contact_memory_declined(message: str) -> bool:
    """Explicit trusted user veto on saving a recipient relationship.

    This is evaluated on the whole request, not just the transfer clause.
    Quoted/source material and hypothetical examples are not user commands.
    """
    text = "；".join(_intent_text(segment) for segment in re.split(r"[;；。\n]", _intent_text(message)))
    text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"',
                  lambda match: " " * len(match.group(0)), text)
    denial = r"(?:不要|不需要|不用|不能|不许|不准|不必|无需|禁止|不得|别|勿|不允许|不希望|不打算|不愿(?:意)?|不想|不)"
    memory = r"(?:保存|记住|记忆|记录|绑定|改绑|记下|存储|添加|新增|更新|覆盖|登记|创建|建立)"
    subject = r"(?:联系人|身份|关系|称呼|别名|角色|收款人|联系信息|联系记录|房东)"
    patterns = (
        denial + r"(?:再|自动|额外|顺便|永久|长期)?\s*" + memory
        + r"(?:[^，,。；;！!？?\n]{0,16}" + subject + r"|\s*(?=[，,。；;！!？?\n]|$))",
        subject + r"[^，,。；;！!？?\n]{0,8}" + denial + r"(?:再|自动)?\s*" + memory,
    )
    for clause in re.split(r"[，,。；;！!？?\n]", text):
        for pattern in patterns:
            match = re.search(pattern, clause)
            if match and not re.search(r"如果|假如|假设|例如|示例|引用|(?:怎么|如何|是否|能否).{0,10}$",
                                       clause[:match.start()]):
                return True
    return False


def _recipient_reference_info(message: str) -> dict:
    """User-declared per-payer alias, never a relation inferred from history."""
    text = _intent_text(message)
    reference, alias = None, None
    patterns = (
        r"(?:向|给)(?:账户|账号|卡号)?\s*(?P<ref>" + _RECIPIENT_REFERENCE + r")\s*(?:账户|账号|银行卡|卡)?的(?:我的|本人(?:的)?)?(?P<alias>[\u4e00-\u9fff]{1,32}?)(?=转[账帐]|转|汇款|汇|打款|打)",
        r"(?:向|给)(?:我的|本人(?:的)?)(?P<alias>[\u4e00-\u9fff]{1,32}?)\s*[“「(（]?\s*(?P<ref>" + _RECIPIENT_REFERENCE + r")\s*[”」)）]?\s*(?=转[账帐]|转|汇款|汇|打款|打)",
        r"把\s*(?P<ref>" + _RECIPIENT_REFERENCE + r")\s*(?:账户|账号|银行卡|卡)?(?:设置为|设为|绑定为|标记为|记为|保存为)(?:我的|本人(?:的)?)(?P<alias>[\u4e00-\u9fff]{1,32})(?=[，。；;\s]|$)",
        r"(?:把|将)(?:我的|本人(?:的)?)?(?P<alias>[\u4e00-\u9fff]{1,32}?)(?:改为|改成|改绑为|换成|替换为)\s*(?P<ref>" + _RECIPIENT_REFERENCE + r")(?:账户|账号|银行卡|卡)?(?=[，。；;\s]|$)",
    )
    matched = None
    for pattern in patterns:
        matched = re.search(pattern, text)
        if matched:
            reference, alias = matched.group("ref"), matched.group("alias")
            break
    if not matched:
        return {}
    if _NEGATION.search(text[max(0, matched.start()-10):matched.end()]) or _UNMET_CONDITION.search(text) \
            or re.search(r"假设|可能|也许|不确定|不是.{0,8}(?:房东|角色|别名)|报价|价格|参考|估算", text):
        return {}
    values = {"payee_name": reference, "recipient_alias": alias,
              "replace_alias": True if re.search(r"改为|改成|改绑|换成|替换为", matched.group(0)) else None}
    if contact_memory_declined(message):
        values.pop("recipient_alias")
        values.pop("replace_alias")
    return values


def _owned_card_reference(kind: str, source: str) -> str | None:
    if kind.startswith("CARD_") or (kind == "FAMILY_PROPOSE" and re.search(r"锁卡|临时锁定", _intent_text(source))):
        pattern = r"(?:卡号|银行卡号|卡号码)\s*(?P<number>" + _CARD_NUMBER + r")(?![A-Za-z0-9_])"
    else:
        pattern = r"(?:使用|用|从)\s*(?:我的|本人|付款)?(?:银行卡|卡片|卡|卡号)?\s*(?:卡号)?\s*(?P<number>" + _CARD_NUMBER + r")(?![A-Za-z0-9_])|(?:付款|支付|来源)卡(?:号)?\s*(?P<other>" + _CARD_NUMBER + r")(?![A-Za-z0-9_])"
    match = re.search(pattern, _intent_text(source))
    if not match or re.search(r"(?:对方|收方|收款方|收款人|接收方|不要|不用|别).{0,6}$", source[max(0, match.start()-12):match.start()]):
        return None
    return match.groupdict().get("number") or match.groupdict().get("other")


def _usd_money_evidence(text: str):
    yield from sorted(set(_wan_money_evidence(_currency_text(text), "USD"))
                      | set(_base_usd_money_evidence(text)))


def _base_usd_money_evidence(text: str):
    proof_text = _currency_text(text)
    for start, end, minor in english_money_evidence(proof_text, "USD"):
        if not _partial_wan_token(proof_text, start, end):
            yield start, end, minor
    for match in _USD_MONEY.finditer(proof_text):
        if any(match.start() < short.end() and short.start() < match.end() for short in _WAN_MONEY.finditer(proof_text)):
            continue
        if _partial_wan_token(proof_text, match.start(), match.end()):
            continue
        unit = match.group("unit") or ""
        if not unit and not match.group("usd"):
            continue
        if unit.lower() == "cents" and not match.group("usd"):
            continue
        prefix, suffix = proof_text[max(0, match.start()-14):match.start()], proof_text[match.end():]
        if EN_IMPRECISE.search(proof_text[max(0, match.start()-35):match.start()]) or EN_IMPRECISE.match(suffix.strip()) or re.search(r"\b(?:minus|negative|plus)\s*$", prefix, re.I) or re.match(r"\s*(?:or|to|through)\s+\d|\s*(?:approximately|about|roughly)\b", suffix, re.I):
            continue
        if re.search(r"(?:[-−﹣－±负]\s*|\d\s*[,，]\s*|(?:大概|大约|约|左右|上下|不到|剩余|余额|全部|超过|最多|至少)\s*)$", prefix):
            continue
        if _range_amount_prefix(prefix):
            continue
        if re.search(r"(?:" + _CNY_CURRENCY + "|" + _OTHER_CURRENCY + r")\s*$", prefix, re.IGNORECASE):
            continue
        if re.match(r"\s*(?:" + _CNY_CURRENCY + "|" + _OTHER_CURRENCY + r"|元|块|角|分|cent(?!s)|左右|上下|以内|以上|以下|多|余|出头|±|(?:到|至|-|~|～|—|–)\s*[\d零〇一二两三四五六七八九十百千万])", suffix, re.IGNORECASE):
            continue
        if re.match(r"[\d.]|\s*[,，]\s*\d|[零〇一二两三四五六七八九]", suffix):
            continue
        try:
            token = match.group("number")
            if token[0].isdigit():
                major = Decimal(token.replace(",", ""))
            else:
                integral, _, fraction = token.partition("点")
                major = Decimal(_chinese_integer(integral))
                if fraction:
                    major += Decimal("0." + "".join(str(_DIGITS[char]) for char in fraction))
            if unit.startswith("万"):
                major *= 10_000
            exact_minor = major * (1 if unit.lower() in {"美分", "cents"} else 100)
            minor = int(exact_minor)
            if exact_minor == minor and 0 < minor <= 10**13:
                yield match.start(), match.end(), minor
        except (ValueError, KeyError, InvalidOperation):
            continue


def usd_amounts_in_text(text: str) -> set[int]:
    """Positive, exact USD cents; bare $, CNY/foreign mixtures stay unproven."""
    return {minor for _start, _end, minor in _usd_money_evidence(text)}


def money_amounts_by_currency(text: str) -> dict[str, set[int]]:
    source = _currency_text(text)
    return {"CNY": amounts_in_text(source), "USD": usd_amounts_in_text(source)}


def _currency_code(token: str) -> str:
    return "CNY" if re.fullmatch(_CNY_CURRENCY, token.strip(), re.IGNORECASE) else "USD"


def _has_usd(source: str) -> bool:
    return bool(re.search(_USD_CURRENCY, _currency_text(source), re.IGNORECASE))


def _has_unsupported_currency(source: str) -> bool:
    text = _currency_text(source)
    return bool(re.search(_OTHER_CURRENCY + r"|(?<![Uu][Ss])\$|(?<!US )(?<!U.S. )\bdollars?\b", text, re.IGNORECASE))


def _receiving_currency_signals(source: str) -> set[str]:
    """Positive recipient settlement instructions, never the payer's currency."""
    text = _currency_text(source)
    currency = "(?:" + _USD_CURRENCY + "|" + _CNY_CURRENCY + ")"
    subject = r"(?:收款方|收款人|收方|对方|接收方)"
    verb = r"(?:到账|入账|接收|收取|收款|收到)"
    modifiers = r"\s*(?:明确|指定|要求|需要|希望|必须|应|要)?\s*"
    patterns = (
        subject + modifiers + verb + r"\s*(?:的)?(?:币种)?\s*(?:明确)?(?:为|是|以|用|[：:])?\s*(?P<currency>" + currency + ")",
        r"(?:到账|入账|收款|接收)币种" + modifiers + r"(?:为|是|[：:])?\s*(?P<currency>" + currency + ")",
        r"(?P<currency>" + currency + r")\s*" + verb,
        r"(?:以|用)\s*(?P<currency>" + currency + r")\s*" + verb,
        subject + modifiers + r"(?:以|用)\s*(?P<currency>" + currency + r")\s*" + verb,
        r"(?:" + subject + "|" + verb + r")[^，。；;\n]{0,8}?(?P<currency>" + currency + ")",
        r"(?P<currency>" + currency + r")[^，。；;\n]{0,8}?(?:" + subject + "|" + verb + ")",
    )
    proven = english_receiving_currencies(text)
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            prefix = text[max(0, match.start()-16):match.start()]
            if re.search(r"(?:不要|不用|不能|不许|不准|不得|禁止|不|未|别|假设|假如|如果|可能|也许|不确定|未确定).{0,6}$", prefix):
                continue
            span = match.group(0)
            if re.search(r"不要|不用|不能|不许|不准|不得|禁止|不接|不收|未确定|不确定|假设|假如|如果|可能|也许|报价|价格|估算|估计|预估|折算|参考|等值|付款|支付|付出|扣款|资金来源", span):
                continue
            if re.match(r"\s*(?:付款|支付|付出|扣款|出资|资金来源)", text[match.end("currency"):]):
                continue
            proven.add(_currency_code(match.group("currency")))
    return proven


def _received_currency(source: str) -> str | None:
    proven = _receiving_currency_signals(source)
    return next(iter(proven)) if len(proven) == 1 else None


def _fx_slots(kind: str, source: str) -> dict:
    text = _currency_text(source)
    if kind == "FX_QUOTE" and english_spending_reference(text):
        return {}  # Display reference only; bank bill facts are not a user-entered FX amount.
    currency_word = "(?:" + _USD_CURRENCY + "|" + _CNY_CURRENCY + ")"
    values = english_slots(kind, text)
    values.update(_recipient_reference_info(source) if kind == "FX_TRANSFER" else {})
    number = _owned_card_reference(kind, source)
    if number:
        values["card_number"] = number
    source_card = re.search(r"(?:使用|用|从)\s*(?:我的|本人|付款)?(?:银行卡|卡片|卡)?\s*(?:尾号|末四位)\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])(?!\s*(?:的)?(?:收方|收款|接收|对方))|(?:付款|支付|出资|来源|转出)卡(?:片)?\s*(?:尾号|末四位)\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])", source)
    if source_card and not re.search(r"(?:对方|收方|收款方|收款人|接收方).{0,4}$", source[max(0, source_card.start()-9):source_card.start()]):
        values["card_last4"] = source_card.group(1) or source_card.group(2)
    if kind == "FX_TRANSFER":
        values["currency"] = "USD" if _has_usd(source) else None
        amounts = usd_amounts_in_text(source)
        if len(amounts) == 1:
            values["amount_minor"] = next(iter(amounts))
        values["payee_name"] = _transfer_payee(source, currency="USD")
        payment = re.search(r"(" + currency_word + r")\s*(?:付款|支付|付出|出资)|(?:付款|支付|来源)币种[：:]?\s*(" + currency_word + ")", text, re.IGNORECASE)
        if payment:
            values["source_currency"] = _currency_code(payment.group(1) or payment.group(2))
        receiving = _received_currency(source)
        if receiving:
            values["target_currency"] = receiving
    else:
        direction = re.search(r"(" + currency_word + r")[^，。；;]{0,24}?(?:换成|换为|兑成|兑为|兑换为|兑换成|兑|换)\s*(?:多少)?(?:[\d零〇一二两三四五六七八九十百千万亿.,]+\s*)?(" + currency_word + ")", text, re.IGNORECASE)
        if direction:
            values["source_currency"], values["target_currency"] = _currency_code(direction.group(1)), _currency_code(direction.group(2))
        amounts = money_amounts_by_currency(source)
        present = [currency for currency, monies in amounts.items() if monies]
        if kind in {"FX_EXCHANGE", "FX_QUOTE"} and len(present) == 1 and len(amounts[present[0]]) == 1:
            values["currency"], values["amount_minor"] = present[0], next(iter(amounts[present[0]]))
    return {key: value for key, value in values.items() if value is not None}


_MEMO_EDIT_PREFIX = re.compile(r"(?:改成|改为|修改为|更改为|调整为|换成|设置为)\s*[:：]?\s*")


def _source_spans(message: str) -> list[tuple[int, int]]:
    spans = []
    object_spans = [(start, end) for object_kind in ("FAMILY_GRANT", "FAMILY_REVOKE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW")
                    for _name, start, end in _extension_object_matches(object_kind, message)]
    english_marker = re.compile(r"(?:^|[;；。\n,.:])\s*(?P<label>(?:(?:(?:the|a|my|this)\s+)?(?:email|message|website|screenshot|receipt|example|quote|memo|note|sample\s+(?:note|message|notification|email)|merchant\s+(?:note|message))|(?:he|she|they)\s+(?:said|says|told\s+me|asked\s+me)))\s*(?:says?|reads?|contains?|is|states?)?\s*[:]?\s*", re.I)
    markers = sorted([*_SOURCE_PREFIX.finditer(message), *english_marker.finditer(message)], key=lambda marker: marker.start())
    for marker in markers:
        start, payload_start = marker.start("label"), marker.end()
        if any(object_start <= start and marker.end("label") <= object_end for object_start, object_end in object_spans):
            continue  # A declared object's source-label word is still just its name.
        if any(start < end for _prior, end in spans):
            continue
        tail = message[payload_start:]
        if marker.group('label') in {'备注','商户备注'}:
            setter = _MEMO_EDIT_PREFIX.match(tail)
            if setter:
                payload_start += setter.end()
                tail = message[payload_start:]
        quotes = {"“": "”", "「": "」", '"': '"', "‘": "’", "'": "'"}
        if tail[:1] in quotes:
            closing = tail.find(quotes[tail[0]], 1)
            while closing >= 0 and quotes[tail[0]] in {"'", "’"}:
                after = tail[closing+1:]
                inside_word = closing + 1 < len(tail) and tail[closing-1].isalnum() and tail[closing+1].isalnum()
                boundary = not after.strip() or bool(re.match(r"\s*[.,;:!?，。；：！？\n]", after))
                own_boundary = bool(re.match(r"\s+(?:(?:and|but)\s+)?(?:now|separately|instead)\b|\s+(?:另外|但现在|我现在|请你|帮我)", after, re.I))
                if not inside_word and (boundary or own_boundary):
                    break
                # Ambiguous apostrophes/possessives do not release the rest of
                # a declared source as a fresh customer banking command.
                closing = tail.find(quotes[tail[0]], closing + 1)
            end = payload_start + closing + 1 if closing >= 0 else len(message)
        else:
            stop = re.search(r"[。；;\n]", tail)
            own = _OWN_REQUEST_BOUNDARY.search(tail)
            english_own = re.search(r"[,;]\s*(?:but\s+)?(?:now|separately|instead)\s+(?:please\s+)?(?:transfer|send|lock|freeze|cancel|show|check)\b", tail, re.I)
            end = payload_start + min([match.start() for match in (stop, own, english_own) if match] or [len(tail)])
        spans.append((start, end))
    # A bare quoted instruction is evidence, not an imperative. Quoted object
    # identifiers without an action remain available for exact grounding.
    for quote in re.finditer(r'''"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘(?:[^’\n]|(?<=\w)’(?=\w))*’(?!\w)|`[^`]*`|(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))*'(?!\w)''', message):
        inner, prefix, suffix = quote.group(0)[1:-1], message[:quote.start()], message[quote.end():]
        # Quotes around an explicitly named argument remain identity/source
        # data. Only the outer verb authorizes a request; words in the name
        # must not turn an opaque payee/product/goal into a quoted command.
        direct_payee = any(match.span("name") == (quote.start()+1, quote.end()-1)
                           for match in _direct_transfer_match(message, all_matches=True))
        amount_first_payee = any(match.span("quoted") == (quote.start()+1, quote.end()-1)
                                for match in _AMOUNT_FIRST_TRANSFER.finditer(message) if match["quoted"])
        reverse_payee = bool(re.search(r"(?:转[账帐]给|转给|汇给|打给)\s*$", prefix)
                             and re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79}", inner))
        named_argument = bool(
            re.search(r"(?:申购|买入|赎回|卖出)\s*$", prefix)
            or re.search(r"(?:创建|建立|设立|新建|为|给|释放|取回|查看|分析)\s*$", prefix)
               and re.match(r"\s*(?:储蓄)?目标|\s*(?:预留|划拨|保留)", suffix)
            or re.search(r"(?:取消|关闭|停用|停止|退订|关掉)\s*$", prefix)
               and re.match(r"\s*的?(?:订阅|代扣|自动续费|会员|续费)", suffix)
            or any(start == quote.start()+1 and end == quote.end()-1
                   for object_kind in ("FAMILY_GRANT", "FAMILY_REVOKE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW")
                   for _name, start, end in _extension_object_matches(object_kind, message)))
        if (direct_payee or amount_first_payee or reverse_payee or named_argument) and not any(
                quote.start() < end and quote.end() > start for start, end in spans):
            continue
        named_product = (english_slots("WEALTH_BUY", message) or english_slots("WEALTH_REDEEM", message)).get("product_name")
        if named_product == quote.group(0)[1:-1] and re.search(r"\b(?:buy|purchase|redeem|sell|invest\s+in)\s*$", message[:quote.start()], re.I):
            continue  # An explicitly named product is an opaque argument, not another instruction.
        read_instruction = re.search(r"\b(?:show|check|list|view|analy[sz]e|compare|pull\s+up|bring\s+up)\b.{0,80}\b(?:my|bank|cards?|balance|statements?|bills?|subscriptions?|investments?|funds?)\b", quote.group(0), re.I)
        owned_info_question = re.match(
            r"^\s*(?:what|which)\b(?=[^;\r\n]{0,160}\b(?:my|I)\b)"
            r"(?=[^;\r\n]{0,160}\b(?:cards?|subscriptions?|accounts?|balances?|statements?|bills?|wallets?)\b)"
            r"(?=[^;\r\n]{0,160}\b(?:am|are|is|do|does|did|have|has|can|could|should|would)\b)",
            quote.group(0)[1:-1], re.I)
        chinese_instruction = re.search(
            r"查询|查看|查一下|看看|分析|比较|创建|建立|设立|新建|授权|撤销|"
            r"锁卡|解锁|挂失|补卡|(?:设置|调整|修改|更改|提高|降低).{0,12}限额|申购|买入|赎回|卖出|取消|关闭|停用|停止|退订|关掉|"
            r"兑换|换汇|购汇|结汇|预留|划拨|释放|领取|核算|申领|"
            r"转[账帐]|转给|汇款|汇给|打款|(?:转|汇|打)\s*(?:人民币|美元|美金|[\d零〇一二两三四五六七八九十百千万])", inner)
        # An unlabelled quoted owner-information question remains a source,
        # including for consultation's private-fact eligibility, not just PLAN.
        if (re.search(r"\b(?:transfer|send|remit|pay|freeze|lock|unlock|cancel|stop|exchange|convert|buy|purchase|redeem)\b", quote.group(0), re.I) or read_instruction or owned_info_question or chinese_instruction or re.search(_OWNED_CARD_QUESTION, inner) or re.search(_CARBON_REDEMPTION_INQUIRY, inner) or re.search(CN_FINANCIAL_INQUIRY, quote.group(0))) and not any(quote.start() < end and quote.end() > start for start, end in spans):
            spans.append(quote.span())
    spans.sort()
    return spans


def _intent_text(message: str) -> str:
    text = message
    for start, end in reversed(_source_spans(message)):
        text = text[:start] + " " * (end-start) + text[end:]
    return text


def financial_inquiry(message: str) -> bool:
    return personal_financial_inquiry(_intent_text(message))


def _transfer_memo_data(source: str) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """Read an opaque memo verbatim, without treating its punctuation as clauses."""
    spans = _source_spans(source)
    marker_source = _opaque_object_text(source, "TRANSFER")
    for marker in re.finditer(r"(?P<cn>备注)\s*|(?P<en>\b(?:memo|note))\s*[:：]\s*", source, re.I):
        english = marker.group('en') is not None
        label = marker.group('en') if english else '备注'
        containing = next(((start, end) for start, end in spans
                           if start <= marker.start() < end), None)
        if marker_source[marker.start():marker.start() + len(label)].casefold() != label.casefold() and (
                containing is None or containing[0] != marker.start()):
            continue  # An entity's keyword is its name, not a memo field.
        if containing and (containing[0] != marker.start() or not source[containing[0]:].casefold().startswith(label.casefold())):
            continue  # A merchant/source's note is not the customer's memo.
        value_start = marker.end()
        tail = source[value_start:]
        # An explicit field-edit operator is syntax, not part of the memo.
        # This only reads the selected field's exact payload; it does not
        # choose an action, change its recipient/amount, or grant confirmation.
        # Quoted payloads and existing label prefixes retain their old bytes.
        setter = None if english else _MEMO_EDIT_PREFIX.match(tail)
        if setter:
            value_start += setter.end()
            tail = source[value_start:]
        end = containing[1] if containing else len(source)
        quotes = {"“": "”", "「": "」", '"': '"', "‘": "’", "'": "'"}
        quoted = re.match(r"(?:写着|写了|写的是|内容是|说|是|为)?[：:]?\s*(?P<opening>[“「\"‘'])", tail)
        if quoted:
            opening = quoted.group("opening")
            if containing:
                value = source[value_start:containing[1]]
                closed = len(value) > quoted.end() and value[-1] == quotes[opening]
            else:
                closing = tail.find(quotes[opening], quoted.end())
                closed = closing >= 0
                value = tail[:closing + 1] if closed else ""
                end = value_start + closing + 1 if closed else len(source)
            if not closed:
                return None, "转账备注的引号尚未闭合，请补充完整备注后再核对方案。", (marker.start(), end)
        else:
            raw = re.split(r"[，。；;\n\r]", tail, maxsplit=1)[0]
            value = raw.strip()
            end = value_start + len(raw)
            if english and value.endswith('.'):
                # A final sentence stop is outside an unquoted English memo;
                # internal dots (e.g. invoice 1.23) and quoted text stay exact.
                value=value[:-1].rstrip()
                end=value_start+len(raw.rstrip())-1
            if re.fullmatch(r"(?:写着|写了|写的是|内容是|说|是|为)?[：:]?\s*", raw):
                value = ""
        if not value:
            return None, "请补充转账备注；如无需备注，请明确说明。", (marker.start(), end)
        if len(value) > 80:
            return None, "转账备注超过80字（包括原文引号），请提供80字以内的完整备注后再核对方案。", (marker.start(), end)
        return value, None, (marker.start(), end)
    return None, None, None


def _transfer_memo(source: str) -> tuple[str | None, str | None]:
    value, issue, _span = _transfer_memo_data(source)
    return value, issue


def _matching_transfer_clauses(action: ProposedAction, evidence: list[str]) -> list[str]:
    currency = "USD" if action.kind == "FX_TRANSFER" else "CNY"
    matched = [clause for clause in evidence if action.slots.payee_name
               and _transfer_payee(_intent_text(clause), currency=currency) == action.slots.payee_name]
    if len(matched) > 1 and action.slots.amount_minor is not None:
        exact = [clause for clause in matched if action.slots.amount_minor in
                 (usd_amounts_in_text(_intent_text(clause)) if currency == "USD" else amounts_in_text(_intent_text(clause)))]
        if exact:
            matched = exact
    if len(matched) > 1 and action.slots.memo is not None:
        exact = [clause for clause in matched if _transfer_memo(clause)[0] == action.slots.memo]
        if len(exact) == 1:
            matched = exact
    return matched


def transfer_memo_issue(action: ProposedAction, message: str) -> str | None:
    """Keep an incomplete explicit memo from becoming a complete bank request."""
    if action.kind not in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
        return None
    evidence = [clause for clause in _clauses(message) if _allowed_evidence(action.kind, clause)]
    matched = _matching_transfer_clauses(action, evidence)
    _value, issue, span = _transfer_memo_data("；".join(matched or evidence))
    if action.kind == "FX_TRANSFER" and span is not None:
        return "美元转账暂不支持备注，请明确选择无需备注后再核对原金额、收款人和币种。"
    if not issue and span is not None and action.slots.memo != _value:
        return "转账备注未完整保留原文，请核对完整备注后再生成确认方案。"
    return issue


def recompose_transfer_memo(message: str, supplement: str) -> str | None:
    """Replace only a unique user's memo; all other proven requests must survive."""
    text = supplement.strip()
    remove = bool(re.fullmatch(r"无需备注|不要备注|不加备注|无备注", text))
    value, issue, new_span = _transfer_memo_data(text)
    if not remove and (issue or value is None or new_span != (0, len(text))):
        return None  # A memo completion cannot smuggle in a separate instruction.
    before = rule_proposal(message)
    writes = [action for action in before.actions if action.kind not in READ_KINDS]
    if len(writes) != 1 or writes[0].kind not in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
        return None
    if writes[0].kind == "FX_TRANSFER" and not remove:
        return None
    _old, _issue, old_span = _transfer_memo_data(message)
    if old_span is None:
        return None
    start, end = old_span
    resolved = message[:start] + ("" if remove else "备注" + value) + message[end:]
    if len(resolved) > 2000:
        return None
    after = rule_proposal(resolved)
    if len(after.actions) != len(before.actions):
        return None
    for old_action, new_action in zip(before.actions, after.actions):
        if old_action.kind != new_action.kind or old_action.depends_on != new_action.depends_on or (
                old_action.slots.model_dump(exclude={"memo"}) != new_action.slots.model_dump(exclude={"memo"})):
            return None
    return resolved


def _clauses(message: str) -> list[str]:
    protected = {}
    text = message
    spans = _source_spans(message)
    # A renewal preference beside a current START/RENEW is an attribute,
    # not an independent cancelled service or a global no-write command.
    # Protect only a whole comma-delimited switch tied to a preceding raw
    # commerce clause; query/cancel-only messages keep their old boundaries.
    switch=re.compile(r"(?<=[，,])\s*(?:不(?:要|用|想|需要)?|不要|别|关闭|取消|停止|禁用|开启|启用|打开)\s*(?:自动续费|自动续订)(?=[，,。；;！？!?\n]|$)")
    for match in switch.finditer(message):
        prefix=re.split(r"[，,。；;！？!?\n]",message[:match.start()].rstrip('，,'))[-1]
        if any(_allowed_evidence(kind,prefix) for kind in {"SUBSCRIPTION_START","SUBSCRIPTION_RENEW"}) \
                and not any(match.start()<end and match.end()>start for start,end in spans):
            spans.append(match.span())
    for object_kind in ("FAMILY_GRANT", "FAMILY_REVOKE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "GOAL_VIEW"):
        for _name, start, end in _extension_object_matches(object_kind, message):
            if not any(start < prior_end and end > prior_start for prior_start, prior_end in spans):
                spans.append((start, end))
    spans.sort()
    for index, (start, end) in reversed(list(enumerate(spans))):
        key = f"⟦source{index}⟧"
        protected[key] = message[start:end]
        text = text[:start] + key + text[end:]
    # A funding-card clause is an attribute of the preceding FX action, not
    # another action. Only a 'use ... exchange' phrase opens a new action.
    action_start = r"(?:给|向|把|将|用(?=[^，。；;\n]{0,30}(?:换|兑))|锁|解|挂|申|赎|取消|关闭|开通|重新开通|重新订阅|订阅|恢复(?=[^，。；;\n]{0,30}(?:会员|订阅|云存储))|续订|续期|续费(?!设置)|查看|查询|查一下|看看|看一下|分析|创建|建立|预留|释放|核算|兑换|换|购汇|结汇|授权|撤销|不要|别|(?:无需|不必|不用|不能|勿)(?=[^，。；;\n]{0,12}(?:保存|记住|记录|绑定|改绑|新增|更新|覆盖)))"
    named_stop = r"[\u4e00-\u9fffA-Za-z0-9_· .-]{1,80}?(?:的)?(?:这|那)(?:一)?笔(?:转账|汇款)?(?:先)?(?:不要|别|不用|不需要)(?:转账|转了|汇款|汇了)"
    parts = re.split(r"[。；;！!？?\n]|[，,](?=(?:(?:先|再|请|帮我|不要|别|但|另外|此外|同时){0,2}" + action_start + r"|" + named_stop + r"))|然后|接着|随后|(?:再|和|并且|并)(?=" + action_start + r")", text)
    parts = [segment for part in parts for segment in re.split(r"(?i)(?:,\s*|\s+and\s+|\s+then\s+)(?=(?:please\s+)?(?:show|check|view|analyse|analyze|transfer|send|freeze|lock|unlock|cancel|subscribe|renew|activate|reactivate|restore|exchange|convert)\b)", part)]
    for key, value in protected.items():
        parts = [part.replace(key, value) for part in parts]
    return [part.strip() for part in parts if part.strip()]


def _direct_transfer_match(message: str, *, all_matches=False):
    name = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79}?"
    pattern = re.compile(r"(?:给|向)[“「\"]?(?P<name>"+name+r")[”」\"]?(?:转[账帐]|转|汇款|汇|打款|打)(?=\s*(?:人民币|美元|美金|USD|US\$|[\d零〇一二两三四五六七八九十百千万大约负+\-−]|[，。；;\s]|$))", re.IGNORECASE)
    return pattern.finditer(message) if all_matches else pattern.search(message)


def _transfer_payee(message: str, *, currency="CNY") -> str | None:
    english = english_payee(message)
    if english is not None:
        return english
    name = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·_ .-]{0,79}?"
    declared = _recipient_reference_info(message)
    if declared.get("payee_name"):
        return declared["payee_name"]
    explicit = re.search(r"(?:给|向)(?:账户|账号|卡号)\s*(?P<ref>" + _RECIPIENT_REFERENCE + r")\s*(?=转[账帐]|转|汇款|汇|打款|打)", message)
    if explicit:
        return explicit.group("ref")
    direct = _direct_transfer_match(message)
    if direct:
        return direct.group("name").strip()
    amount_first = _AMOUNT_FIRST_TRANSFER.search(message)
    if amount_first:
        # A syntactically plausible amount must also pass exact source/currency
        # checks. A range/approximation never becomes authority for this payee.
        evidence = list(_usd_money_evidence(message) if currency == "USD" else _cny_money_evidence(message))
        start, end = amount_first.span("money")
        if any(start <= a < b <= end for a, b, _minor in evidence):
            return (amount_first["quoted"] or amount_first["name"]).strip()
    reverse = re.search(r"(?:转[账帐]给|转给|汇给|打给)\s*", message)
    if reverse is None:
        return None
    tail = message[reverse.end():]
    card = re.match(_CARD_NUMBER, tail)
    if card:
        rest = tail[card.end():]
        if not rest or re.match(r"[，。；;\s]", rest) or (usd_amounts_in_text(rest) if currency == "USD" else amounts_in_text(rest)):
            return card.group(0)
    quoted = re.match(r'[“「"](?P<name>'+name+r')[”」"]', tail)
    if quoted:
        return quoted.group("name").strip()
    money = list(_usd_money_evidence(tail) if currency == "USD" else _cny_money_evidence(tail))
    candidate = re.split(r"[，。；;]", tail[:money[0][0]], maxsplit=1)[0].strip() if money else re.split(r"[，。；;\s]", tail, maxsplit=1)[0]
    # A trailing underscore is a likely incomplete numeric username, e.g.
    # user_2100 could mean user_2 + 100. Never bind user_ to that whole amount.
    if money and candidate.endswith(("_", ".", "-")):
        return None
    if not money and re.search(r"(?:\d|[零〇一二两三四五六七八九十百千万])\s*(?:人民币|元|块|美元|美金|USD|US\$)", candidate, re.IGNORECASE):
        return None
    return candidate if re.fullmatch(name, candidate) else None


def _ambiguous_compact_transfer(message: str, *, currency="CNY") -> bool:
    reverse = re.search(r"(?:转[账帐]给|转给|汇给|打给)\s*", message)
    if reverse is None or message[reverse.end():reverse.end()+1] in {"“", "「", '"'}:
        return False
    tail = message[reverse.end():]
    money = list(_usd_money_evidence(tail) if currency == "USD" else _cny_money_evidence(tail))
    return bool(money and tail[:money[0][0]].strip().endswith(("_", ".", "-")))


def _review_preparation_body(intent: str) -> str:
    """Only a neutral trailing review request, never approval or execution.

    Preserve the original utterance for slot/provenance checks. A request to
    see the confirmation plan is not the same as discussing an operation, but
    every normal negation, condition, foreign owner and write guard still
    applies to the remaining action. Never strip a stop or an unmet condition.
    """
    return re.sub(r"[，,]\s*(?:(?:先让我|让我先|请让我先|先给我)\s*(?:核对|看(?:一下)?(?:方案|办理信息))|"
        r"let\s+me\s+(?:review|check)\s+(?:the\s+)?(?:details|plan)\s+first)\s*[。.!！]?\s*$", "", intent, flags=re.I)


def _transfer_stop_resolution(message: str) -> tuple[list[dict], list[str]]:
    """Stops bind only raw user targets/amounts; sources and memos are opaque.

    Ambiguity withholds planning and asks which request, never claims that all
    payments were cancelled. A later independent own request remains separate.
    """
    clean = _intent_text(message)
    positives, cursor = [], 0
    for clause in _clauses(message):
        position = message.find(clause, cursor)
        if position >= 0:
            cursor = position + len(clause)
        intent = _intent_text(clause)
        kind = 'FX_TRANSFER' if _has_usd(intent) else 'TRANSFER_SCHEDULE' if _DATE_EXPRESSION.search(intent) else 'TRANSFER'
        if not _allowed_evidence(kind, clause):
            continue
        target = _transfer_payee(intent, currency='USD' if kind == 'FX_TRANSFER' else 'CNY')
        monies = usd_amounts_in_text(intent) if kind == 'FX_TRANSFER' else amounts_in_text(intent)
        if target:
            positives.append({'clause': clause, 'position': position, 'kind': kind, 'target': target,
                'amount': next(iter(monies)) if len(monies) == 1 else None})
    cancelled, questions = [], []
    negative = r'(?:先)?(?:不要|别|不用|不需要)'
    for match in re.finditer(r'(?:^|[，,。；;\n])\s*(?P<body>[^，,。；;\n]+)', clean):
        body = match.group('body').strip().rstrip('！!？?').strip()
        stop = None
        named = re.fullmatch(r'(?P<name>.+?)(?:的)?(?:这|那)(?:一)?笔(?:转账|汇款)?\s*' + negative + r'\s*(?:转账|转了|汇款|汇了)[吧啊呀]?', body)
        directed = re.fullmatch(negative + r'\s*(?:给|向|转给).+?(?:转账|汇款|转|汇)(?:[^，,。；;\n]*)', body)
        generic = re.fullmatch(negative + r'\s*(?:转账|转了|汇款|汇了)[吧啊呀]?', body)
        if named:
            stop = {'target': named.group('name').strip(), 'amount': None, 'kind': None}
        elif directed:
            target = _transfer_payee(body, currency='USD' if _has_usd(body) else 'CNY')
            # Parse the monetary tail after its verb, not the negated prefix.
            transfer = _direct_transfer_match(body)
            tail = body[transfer.end():] if transfer else ''
            monies = usd_amounts_in_text(tail) if _has_usd(tail) else amounts_in_text(tail)
            money_stated = bool(re.search(r'\d|[零〇一二两三四五六七八九十百千万]|元|人民币|美元|美金|USD|CNY', tail, re.I))
            if not target or money_stated and len(monies) != 1:
                questions.append('停止哪一笔转账还不明确，请说明完整收款人和准确金额；尚未办理或取消任何转账。')
                continue
            stop = {'target': target, 'amount': next(iter(monies)) if monies else None,
                'kind': 'FX_TRANSFER' if _has_usd(tail) else 'CNY' if money_stated else None}
        if stop:
            target = stop['target']
            matches = [row for row in positives if row['target'].casefold() == target.casefold()
                and (stop['amount'] is None or row['amount'] == stop['amount'])
                and (stop['kind'] is None or (row['kind'] == 'FX_TRANSFER') == (stop['kind'] == 'FX_TRANSFER'))]
            before = [row for row in matches if row['position'] < match.start('body')]
            future = [row for row in matches if row['position'] >= match.start('body') and not _explicit_independent_request(row['clause'], message)]
            uncertain = target in {'他', '她', '它', '他们', '她们', '那个', '这个', '上一'} or bool(re.search(r'或|或者|和|、|哪', target))
            related_prior = [row for row in positives if row['position'] < match.start('body')
                and (target in row['target'] or row['target'] in target)]
            if len(before) > 1 or future or uncertain or not matches and related_prior:
                questions.append(f'你想停止给{target}的哪一笔？请说明准确金额和币种；尚未办理或取消这些转账。')
            elif len(before) == 1:
                cancelled.append(before[0])
        elif generic:
            start = max([clean.rfind(mark, 0, match.start('body')) for mark in ('。', '；', ';', '\n')]) + 1
            same_sentence = [row for row in positives if start <= row['position'] < match.start('body')]
            earlier = [row for row in positives if row['position'] < match.start('body')]
            if same_sentence:
                cancelled.extend(same_sentence)
            elif len(earlier) == 1:
                cancelled.extend(earlier)
            elif earlier:
                questions.append('你想停止哪一笔转账？请说明收款人和金额；尚未办理或取消这些转账。')
    return cancelled, list(dict.fromkeys(questions))


def _transfer_clause_retracted(clause: str, message: str) -> bool:
    cancelled, _questions = _transfer_stop_resolution(message)
    return any(row['clause'] == clause for row in cancelled)


def _independent_review_read(kind: str, message: str) -> bool:
    if kind not in READ_KINDS:
        return False
    clauses = _clauses(message)
    review = any(_review_preparation_body(_intent_text(clause)) != _intent_text(clause) for clause in clauses)
    return review and any(re.match(r'(?:另外|此外|同时)(?:请|帮我)?', _intent_text(clause))
        and _allowed_evidence(kind, clause) for clause in clauses)


def _allowed_evidence(kind: str, clause: str) -> bool:
    if kind == "SUBSCRIPTION_CANCEL":
        from .subscription_intent import subscription_request,COMMERCE_KINDS
        request=subscription_request(_intent_text(clause))
        if request and request["kind"] in COMMERCE_KINDS and request["mode"] == "COMMERCE":return False
    if kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW", "SUBSCRIPTION_LIST"}:
        from .subscription_intent import subscription_request
        request = subscription_request(_intent_text(clause))
        if kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
            return bool(request and request["kind"] == kind and request["mode"] == "COMMERCE")
        if request and (request["kind"] in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"} or request["mode"] in {"CONSULT", "DECLINED", "FIELDS", "UNSUPPORTED_PRICE"}):
            return False
    if kind == "FRAUD_ANALYZE":
        from .fraud_security_dialogue import reported_security_read_request
        if reported_security_read_request(clause):
            return True  # Reported material plus the user's risk question is read-only evidence, never credentials/approval.
    intent = _extension_object_text(_intent_text(clause))
    if kind == "CARD_STATUS" and _ACTION_PATTERNS["FAMILY_GRANT"].search(intent):
        scope_slots = _extension_slots("FAMILY_GRANT", clause)
        if scope_slots.get("grantee_name") and scope_slots.get("scopes"):
            # "查看余额并提出锁卡建议" describes the child's delegated
            # capabilities, not a request to read the parent's own card now.
            # A separately requested own-card clause is evaluated independently.
            return False
    if kind == "CARBON_VIEW" and not re.search(r"碳|积分|权益|公交券|乘车券|费率券", intent) \
            and (_has_usd(intent) or _has_unsupported_currency(intent) or amounts_in_text(intent)):
        return False  # A cash/FX exchange question is not a personal-points read.
    parent_balance_request = _PARENT_BALANCE_QUERY.search(_intent_text(clause))
    if parent_balance_request and kind in {"BILL_ANALYSIS", "CARD_STATUS"}:
        return False  # Parent balances cannot be read through the child's direct bank tools.
    if parent_balance_request and kind == "FAMILY_VIEW" and _UNMET_CONDITION.search(_intent_text(clause)):
        return False  # Conditional private reads stay unrequested, even beside an authorization noun.
    if kind not in READ_KINDS:
        intent = _review_preparation_body(intent)
    if kind == "CARBON_REDEEM" and re.search(_CARBON_REDEMPTION_INQUIRY, intent):
        return False  # Eligibility/available rewards is a read, never redemption authority.
    if kind in {'TRANSFER', 'TRANSFER_SCHEDULE', 'FX_TRANSFER'} and re.search(
        r'(?:先)?(?:不要|别|不用|不需要)\s*(?:转账|转了|汇款|汇了)\s*[吧啊呀]?\s*[。.!！]?$', intent):
        return False  # A trailing untargeted stop retracts this clause, not a quoted memo or another named recipient.
    if kind == "BILL_ANALYSIS":
        from .bill_category_filter import category_filter_in_text
        from .financial_diagnostics import CATEGORY_LABELS_EN
        category = category_filter_in_text(intent)
        if category in CATEGORY_LABELS_EN:
            intent = intent.replace(category + "的账单", category + "账单").replace(category + "的消费", category + "消费").replace(category + "的支出", category + "支出")
    if kind in {"BILL_ANALYSIS", "CARD_STATUS", "SUBSCRIPTION_LIST", "GOAL_VIEW", "CARBON_VIEW"} and foreign_financial_request(intent):
        return False  # Another owner's funds are not the current customer's facts.
    inquiry = personal_financial_inquiry(intent)
    if inquiry and kind not in READ_KINDS:
        return False  # 'Consumption or transfers?' is a question, not an instruction.
    if kind == "BILL_ANALYSIS" and re.search(CN_FINANCIAL_INQUIRY, clause) and not inquiry and not _BASE_BILL_PATTERN.search(intent):
        return False  # New implicit-owner questions cannot read other owners or quoted examples.
    if kind == "CARD_STATUS":
        from .direct_read_questions import model_owned_balance_evidence
        if model_owned_balance_evidence(clause):
            return True  # A positive own balance query need not say "bank card".
    english = english_allowed(kind, intent, READ_KINDS)
    if english is False:
        return False
    if kind in READ_KINDS and kind != "FRAUD_ANALYZE" and kind in EN_ACTION_PATTERNS and re.search(EN_ACTION_PATTERNS[kind], clause, re.I) and not re.search(EN_ACTION_PATTERNS[kind], intent, re.I):
        return False  # A quoted English private-read command is not the user's request.
    routing = _action_routing_text(kind, clause)
    found = _ACTION_PATTERNS[kind].search(routing)
    if found is None:
        return False
    if kind == "CONTACT_BIND" and not _recipient_reference_info(intent):
        return False
    if kind.startswith("FX_"):
        if _has_unsupported_currency(intent) or (kind != "FX_QUOTE" and not _has_usd(intent)):
            return False
        if kind == "FX_QUOTE" and not (_has_usd(intent) or re.search(r"汇率|外汇|换汇|购汇|结汇|exchange\s+rate|FX\s+(?:rate|quote)", intent, re.I)):
            return False
        if kind in {"FX_TRANSFER", "FX_EXCHANGE"} and _FX_PRICE_QUERY.search(intent):
            return False
        if kind == "FX_TRANSFER" and len(_receiving_currency_signals(intent)) > 1:
            return False
        if kind == "FX_EXCHANGE":
            direction = _fx_slots(kind, intent)
            if direction.get("source_currency") and direction.get("source_currency") == direction.get("target_currency"):
                return False
    cny_only = {"TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "WEALTH_BUY", "WEALTH_REDEEM", "CARD_SET_LIMIT",
                "FAMILY_GRANT", "FAMILY_PROPOSE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}
    if kind in cny_only and (_has_usd(intent) or _has_unsupported_currency(intent)):
        return False
    if kind == "WEALTH_COMPARE" and any(_ACTION_PATTERNS[item].search(clause)
            for item in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}) and not re.search(r"比较|对比.{0,12}(?:理财|基金产品)", clause):
        return False
    if kind == "GOAL_RESERVE" and _ACTION_PATTERNS["GOAL_RELEASE"].search(clause):
        return False
    if kind == "CARD_LOCK" and _ACTION_PATTERNS["CARD_UNLOCK"].search(intent):
        return False
    if kind in READ_KINDS:
        prefix = intent[max(0, found.start()-14):found.start()]
        prefix = re.split(r"[，,。；;]", prefix)[-1]
        denial = r"(?:不要|不需要|不用|不能|不得|不许|不准|禁止|不想|无需|别|先不|暂不|不)"
        if re.search(denial, prefix):
            read_declined = re.search(denial + r"(?:请|帮我|给我|再)?(?:分析|查询|查看|比较|读取|查|看)", prefix)
            # 'Do not cancel subscription' may still yield a safe subscription
            # overview; it is not the same as 'do not query subscriptions'.
            write_declined = re.search(denial + r".{0,8}(?:取消|锁|解锁|挂失|申购|赎回|转账|转帐|转给|汇|兑换|核算|领取|绑定|改绑|预留|释放|授权|补卡|创建)", prefix)
            return bool(write_declined) and not read_declined
        return True
    if kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"} and re.search(r"(?:分析|评估|核验|判断|查询|咨询).{0,20}(?:转账|汇款|(?:转|汇|打)(?=给|[\d零一二两三四五六七八九十])).{0,18}(?:风险|安全|合理|诈骗)|(?:转账|汇款).{0,12}(?:风险|是否安全|有没有问题)", intent):
        return False
    if kind in {"TRANSFER", "FX_TRANSFER", "FX_EXCHANGE", "CARD_LOCK", "SUBSCRIPTION_CANCEL"} and any(
            _ACTION_PATTERNS[item].search(intent) for item in {"FAMILY_GRANT", "FAMILY_REVOKE", "FAMILY_PROPOSE"}):
        return False
    if kind == "FX_TRANSFER" and _ACTION_PATTERNS["AA_REQUEST"].search(_aa_intent_text(intent)):
        return False
    prefix = intent[max(0, found.start() - 14):found.start()]
    if _NEGATION.search(prefix) or (kind != "FAMILY_REVOKE" and _NEGATION.search(intent[:found.end()])):
        return False
    # ProposedAction has no executable condition contract; defer conditions.
    if kind == "TRANSFER_SCHEDULE":
        if not (_DATE_EXPRESSION.search(intent) or (english is True and EN_SCHEDULE_DATE.search(intent))) or _UNMET_CONDITION.search(intent) or EN_UNMET_CONDITION.search(intent):
            return False
    elif _CONDITIONAL.search(intent):
        return False
    if kind == "TRANSFER" and _DATE_EXPRESSION.search(intent):
        return False
    question_intent = intent
    if kind == "CARBON_REDEEM" and re.match(r"^(?:(?:请帮我|帮我|请|麻烦你|麻烦|替我)\s*)?兑换", intent):
        question_intent = re.sub(r"(?:好吗|好不好)[？?。！!\s]*$", "", intent)
    if _QUESTION.search(question_intent):
        return False
    explanation_intent = re.sub(r"(?:查看|查询|看)\s*余额", "", intent) if kind == "FAMILY_GRANT" else intent
    if _READ_OR_EXPLANATION.search(explanation_intent) or re.search(r"检查|评估|核对", explanation_intent):
        return False
    return True


def _explicit_independent_request(clause: str, message: str) -> bool:
    """A new sentence explicitly requests an unconditional action now.

    Merely splitting a conditional instruction at a comma or saying 'then'
    never removes its condition. Only a distinct sentence introduced by an
    explicit current/independent request can stand apart from a prior condition.
    """
    for segment in re.split(r"[。；;\n]", _intent_text(message)):
        segment = segment.strip()
        if _UNMET_CONDITION.search(segment) or EN_UNMET_CONDITION.search(segment):
            continue
        if re.match(r"(?:现在|另外|此外)(?:请(?:你)?|帮我)?(?:给|向|把|将|锁|临时锁|解|挂|申|赎|取消|关闭|创建|建立|预留|释放|核算|兑换|授权|撤销)", segment) \
                and clause in segment:
            return True
        if re.match(r"(?:now|separately|instead)\s+(?:please\s+)?(?:transfer|send|remit|pay|freeze|lock|unlock|cancel|exchange|convert|buy|redeem)\b", segment, re.I) and clause in segment:
            return True
    return False


def _extension_slots(kind: str, source: str) -> dict:
    if kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW", "SUBSCRIPTION_LIST"}:
        from .subscription_intent import subscription_request
        request=subscription_request(_intent_text(source))
        return dict(request["slots"]) if request and request["kind"] == kind else {}
    if kind.startswith("FX_"):
        return _fx_slots(kind, source)
    values: dict = english_slots(kind, source)
    values.update(_recipient_reference_info(source) if kind in {"TRANSFER", "CONTACT_BIND"} else {})
    number = _owned_card_reference(kind, source)
    if number:
        values["card_number"] = number
    policy_source = _extension_object_text(source)
    amounts = amounts_in_text(policy_source)
    amount = next(iter(amounts)) if len(amounts) == 1 else None
    if kind.startswith("FAMILY_"):
        names = {name for name, _start, _end in _extension_object_matches(kind, source)}
        if kind in {"FAMILY_GRANT", "FAMILY_REVOKE"} and len(names) == 1:
            values["grantee_name"] = next(iter(names))
        if kind == "FAMILY_GRANT":
            scopes = []
            def requested(pattern):
                return any(not re.search(r"不要|不可以|不能|不许|不准|不得|禁止|无需|不让", policy_source[max(0, match.start()-10):match.end()])
                           for match in re.finditer(pattern, policy_source))
            if requested(r"(?:查看|查询|看).{0,8}余额"):
                scopes.append("READ_BALANCE")
            if requested(r"(?:提案|提出|提议|建议|代办|协助|帮我).{0,12}转账|转账(?:提案|建议|提议)"):
                scopes.append("PROPOSE_TRANSFER")
            if requested(r"(?:提案|提出|提议|建议|代办|协助|帮我).{0,12}锁卡|锁卡(?:提案|建议|提议)"):
                scopes.append("PROPOSE_CARD_LOCK")
            if scopes:
                values["scopes"] = scopes
            values["limit_period"] = "MONTH" if re.search(r"每月|月累计|月合计", policy_source) else "PER_PROPOSAL"
            limit_amounts = amounts_in_text(policy_source, allow_limits=True)
            if len(limit_amounts) == 1:
                values["maximum_amount_minor"] = next(iter(limit_amounts))
            days = re.search(r"(?:有效期?\s*)?(\d+)\s*天", policy_source)
            if days and 1 <= int(days.group(1)) <= 180:
                values["expires_days"] = int(days.group(1))
        if kind == "FAMILY_PROPOSE":
            values["proposal_kind"] = "CARD_LOCK" if re.search(r"锁卡|临时锁定", source) else ("TRANSFER" if _ACTION_PATTERNS["TRANSFER"].search(source) else None)
            owner = re.search(r"(?:为|帮)\s*([\u4e00-\u9fffA-Za-z0-9_· .-]{1,80}?)(?:提交|发起|准备|提出)", source)
            if owner:
                values["owner_name"] = owner.group(1).strip()
            if amount:
                values["amount_minor"] = amount
            payee = _transfer_payee(source)
            if payee:
                values["payee_name"] = payee
            card = re.search(r"(?:尾号|末四位)\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])", source)
            if card:
                values["card_last4"] = card.group(1)
    if kind.startswith("GOAL_"):
        names = {name for name, _start, _end in _extension_object_matches(kind, source)}
        if len(names) == 1:
            values["goal_name"] = next(iter(names))
        if amount:
            if kind == "GOAL_CREATE":
                values["target_minor"] = amount
            elif kind in {"GOAL_RESERVE", "GOAL_RELEASE"}:
                values["amount_minor"] = amount
        date = re.search(r"\d{4}-\d{2}-\d{2}", policy_source)
        if kind == "GOAL_CREATE" and date:
            values["due_date"] = date.group(0)
        if kind == "GOAL_VIEW":
            from .goal_what_if import goal_what_if_slots
            values.update(goal_what_if_slots(source))
    if kind == "CARBON_CLAIM":
        named = re.search(r"(?:核算|领取|申领)(?:模拟)?(?P<label>碳积分|低碳行为)?[：:]\s*([^，。；;]{1,80})", source)
        if named:
            value = named.group(2).strip()
            if named.group("label") == "低碳行为":
                value = re.sub(r"(?:低碳积分|碳积分)$", "", value).strip()
            values["behavior_name"] = value
        behavior = re.search(r"(?:领取|核算|申领|计入|记录)\s*(.{1,50}?)\s*(?:碳积分|低碳积分|低碳行为)", source)
        if behavior and "behavior_name" not in values:
            values["behavior_name"] = behavior.group(1).removesuffix("模拟").removesuffix("的")
    if kind == "CARBON_REDEEM":
        named = re.search(r"兑换(?:模拟)?(?:低碳)?权益[：:]\s*([^，。；;]{1,80})", source)
        if named:
            values["reward_name"] = named.group(1).strip()
        reward = re.search(r"兑换\s*([^，。；;]{1,80})", source)
        if reward and "reward_name" not in values:
            value = re.sub(r"(?:好吗|好不好)[？?。！!\s]*$", "", reward.group(1)).strip()
            # The current action exchanges one reward; do not silently shrink
            # multi-item or approximate requests into a single redemption.
            value = re.sub(r"^(?:一|1)(?:张|份|项)\s*", "", value)
            values["reward_name"] = value.removesuffix("模拟").removesuffix("的")
    return {key: value for key, value in values.items() if value is not None and (not isinstance(value, str) or value.strip())}


def _untrusted_only_transfer(action: ProposedAction, message: str, evidence: list[str], siblings: list[ProposedAction]) -> bool:
    """Drop a transfer anchored solely in labelled untrusted source material.

    An invented/mixed parameter for an actual user transfer is still subject
    to normal clearing and bounded quality repair, never reassigned here.
    """
    if action.kind not in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"} or not action.slots.payee_name:
        return False
    currency = "USD" if action.kind == "FX_TRANSFER" else "CNY"
    target = action.slots.payee_name
    if any(_transfer_payee(_intent_text(clause), currency=currency) == target for clause in evidence):
        return False
    # An extra quoted action is distinguishable from the sole legitimate
    # action with contaminated parameters only when a separate model action
    # already corresponds to the actual, source-proven request. Sole malformed
    # actions retain missing-slot diagnostics and bounded quality repair.
    actual_covered = any(
        other is not action and other.kind == action.kind and other.slots.payee_name
        and any(_transfer_payee(_intent_text(clause), currency=currency) == other.slots.payee_name
                and other.slots.amount_minor in (usd_amounts_in_text(_intent_text(clause)) if currency == "USD" else amounts_in_text(_intent_text(clause)))
                for clause in evidence)
        for other in siblings
    )
    if not actual_covered:
        return False
    for start, end in _source_spans(message):
        quoted = _SOURCE_PREFIX.sub("", message[start:end], count=1).strip()
        if _transfer_payee(quoted, currency=currency) != target:
            continue
        source_amounts = usd_amounts_in_text(quoted) if currency == "USD" else amounts_in_text(quoted)
        if action.slots.amount_minor is None or action.slots.amount_minor in source_amounts:
            return True
    return False


def ground_proposal(proposal: IntentProposal, message: str, *, _source_budget_checked: bool = False) -> IntentProposal:
    """Discard unrequested writes and remove invented or misattributed slots."""
    if not _source_budget_checked:
        budget = rule_proposal(message, _raw_proposal=True)
        if 'SOURCE_OPERATION_LIMIT_EXCEEDED' in budget.failure_reasons:
            # Only the private raw rule path is used here, so there is no
            # recursive Rule -> Ground -> Rule loop or public skip permission.
            return budget.model_copy(update={'fallback': proposal.fallback,
                'failure_reasons': list(dict.fromkeys(proposal.failure_reasons + budget.failure_reasons))})
    clauses = _clauses(message)
    user_intent = _extension_object_text(_intent_text(message))
    memory_declined = contact_memory_declined(message)
    actions: list[ProposedAction] = []
    questions = list(proposal.clarification_questions)
    cancelled_transfers, stop_questions = _transfer_stop_resolution(message)
    if stop_questions:
        from .fraud_security_dialogue import reported_security_read_request
        risk_read = ([ProposedAction(kind='FRAUD_ANALYZE', slots=ActionSlots(context_message=message),
            label=ACTION_LABELS['FRAUD_ANALYZE'])] if reported_security_read_request(message)
            and any(action.kind=='FRAUD_ANALYZE' for action in proposal.actions) else [])
        return proposal.model_copy(update={'actions': risk_read, 'clarification_questions': list(dict.fromkeys(questions + stop_questions))[:8]})
    indices: dict[int, int] = {}
    for original_index, action in enumerate(proposal.actions):
        if action.kind == "FAMILY_VIEW" and any(_PARENT_BALANCE_QUERY.search(segment)
                and _UNMET_CONDITION.search(segment)
                for segment in re.split(r"[。；;\n]", _intent_text(message))):
            continue  # Clause splitting at '再查看' must not erase a parent-read condition.
        if action.kind == "FRAUD_ANALYZE":
            from .fraud_security_dialogue import security_read_declined
            if security_read_declined(message):
                continue
        if action.kind in {"BILL_ANALYSIS", "CARD_STATUS", "WEALTH_COMPARE", "ASSET_POSITIONS_READ", "MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW", "SUBSCRIPTION_LIST", "FAMILY_VIEW", "GOAL_VIEW", "CARBON_VIEW"} and _private_read_veto(user_intent):
            continue  # Explicit no-private-data request dominates a topic mention.
        if action.kind == "CONTACT_BIND" and memory_declined:
            continue
        if action.kind == "FAMILY_PROPOSE" and (_has_usd(user_intent) or _has_unsupported_currency(user_intent)):
            questions.append("家庭协助暂不支持美元或其他外币建议，不会改成旧人民币转账提案。")
            continue
        if action.kind in {"TRANSFER", "FX_TRANSFER", "FX_EXCHANGE", "CARD_LOCK"} and _ACTION_PATTERNS["FAMILY_PROPOSE"].search(user_intent):
            continue  # A helper proposal never also becomes the helper's direct bank write.
        if action.kind not in READ_KINDS and (_GLOBAL_NO_WRITE.search(user_intent) or EN_NO_WRITE.search(user_intent)):
            continue
        if action.kind == "CARD_STATUS" and _UNMET_CONDITION.search(user_intent):
            from .direct_read_questions import model_owned_balance_evidence
            if not _ACTION_PATTERNS["CARD_STATUS"].search(_action_routing_text("CARD_STATUS", message)) \
                and any(model_owned_balance_evidence(clause) for clause in clauses):
                continue  # Clause splitting cannot erase a conditional default-balance query.
        evidence = [clause for clause in clauses if _allowed_evidence(action.kind, clause)]
        if action.kind in {'TRANSFER', 'TRANSFER_SCHEDULE', 'FX_TRANSFER'}:
            if action.slots.payee_name and any(row['target'].casefold() == action.slots.payee_name.casefold()
                    and (row['kind'] == 'FX_TRANSFER') == (action.kind == 'FX_TRANSFER')
                    and (action.slots.amount_minor == row['amount'] or action.slots.amount_minor is None
                        and not any(_transfer_payee(_intent_text(clause), currency='USD' if action.kind=='FX_TRANSFER' else 'CNY') == action.slots.payee_name
                            and not any(cancel['clause']==clause for cancel in cancelled_transfers) for clause in evidence))
                    for row in cancelled_transfers):
                continue
            evidence = [clause for clause in evidence if not any(row['clause'] == clause for row in cancelled_transfers)]
        if action.kind == "FRAUD_ANALYZE":
            from .fraud_security_dialogue import reported_security_read_request
            if reported_security_read_request(message):
                evidence = [message]  # The report and question can be in two sentences.
        if _untrusted_only_transfer(action, message, evidence, proposal.actions):
            continue
        if action.kind not in READ_KINDS and (_UNMET_CONDITION.search(user_intent) or EN_UNMET_CONDITION.search(user_intent)):
            evidence = [clause for clause in evidence if _explicit_independent_request(clause, message)]
            if not evidence:
                questions.append("请先确认前置条件满足，再明确请求对应操作。")
                continue
        if not evidence:
            if action.kind not in READ_KINDS:
                questions.append(f"请明确是否现在需要{ACTION_LABELS[action.kind]}；条件、否定和撤销表达不会生成执行动作。")
            continue
        # For repeated same-kind actions, the named target identifies the clause.
        target = next((getattr(action.slots, field) for field in ("grantee_name", "owner_name", "goal_name", "behavior_name", "reward_name", "payee_name", "product_name", "merchant", "card_last4") if getattr(action.slots, field)), None)
        entity_field = next((field for field in ("grantee_name", "owner_name", "goal_name", "behavior_name", "reward_name", "payee_name", "product_name", "merchant", "card_last4")
                             if getattr(action.slots, field)), None)
        if action.kind.startswith(("FAMILY_", "GOAL_", "CARBON_")) and entity_field:
            exact = [clause for clause in evidence if _extension_slots(action.kind, clause).get(entity_field) == target]
            candidates = [clause for clause in evidence if target and isinstance(_extension_slots(action.kind, clause).get(entity_field), str)
                          and target in _extension_slots(action.kind, clause)[entity_field]]
            matched = exact or (candidates if len(candidates) == 1 else [])
            if not matched and len(evidence) == 1 and _extension_slots(action.kind, evidence[0]).get(entity_field):
                matched = evidence  # A false field is still cleared below; never borrow another clause.
            if not matched and action.kind in {"FAMILY_GRANT", "FAMILY_REVOKE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}:
                questions.append("请明确完整且唯一的协助人或目标名称，不能用短名从多个请求合并权限或金额。")
                continue
        elif action.kind == "CONTACT_BIND":
            matched = [clause for clause in evidence if _recipient_reference_info(clause).get("payee_name") == target]
        elif action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
            matched = _matching_transfer_clauses(action, evidence)
        else:
            matched = [clause for clause in evidence if target and target in clause]
        if matched:
            evidence = matched
        if action.kind == "FAMILY_GRANT" and len(evidence) > 1:
            policies = [_extension_slots(action.kind, clause) for clause in evidence]
            grants = [row for row in policies if row.get("grantee_name") == target]
            days = {row.get("expires_days") for row in grants if row.get("expires_days") is not None}
            transfer_policies = [row for row in grants if "PROPOSE_TRANSFER" in row.get("scopes", [])]
            caps = {row.get("maximum_amount_minor") for row in transfer_policies if row.get("maximum_amount_minor") is not None}
            periods = {row.get("limit_period") for row in transfer_policies if row.get("limit_period") is not None}
            if len(days) > 1 or len(caps) > 1 or len(periods) > 1:
                questions.append("同一协助人的授权期限或额度存在冲突，请明确一份完整委托；不会合并范围并挑选其中一项限制。")
                continue
        source = "；".join(evidence)
        if action.kind == "FAMILY_PROPOSE":
            # Retain the declaration of assistance, but associate money with
            # the exact payee/card clause rather than the whole multi-proposal.
            requested = action.slots.payee_name or action.slots.card_last4
            intro = ""
            blocks = []
            for clause in clauses:
                if _ACTION_PATTERNS["FAMILY_PROPOSE"].search(clause):
                    intro = clause
                parsed = _extension_slots("FAMILY_PROPOSE", clause)
                if requested and requested in {parsed.get("payee_name"), parsed.get("card_last4")}:
                    blocks.append(clause if clause == intro else f"{intro}；{clause}")
            source = "；".join(blocks) if blocks else source
        elif action.kind in {"FAMILY_GRANT", "FAMILY_REVOKE"} and "补充：" in message \
                and len(list(_ACTION_PATTERNS[action.kind].finditer(message))) == 1:
            source = message
        elif "补充：" in message and domain_for_action(action.kind) in {"GOALS", "CARBON", "FRAUD"} \
                and len(list(_ACTION_PATTERNS[action.kind].finditer(message))) == 1:
            source = message  # Single retained intent, newly supplied slots are user text.
        if action.kind == "GOAL_VIEW":
            from .goal_what_if import goal_what_if_requested
            if goal_what_if_requested(_intent_text(message)) and len(re.findall(r"提前(?:多久|多少|几个月|几月)|how\s+(?:much\s+)?(?:sooner|earlier)", _intent_text(message),re.I)) == 1:
                source = _intent_text(message)  # One read-only hypothesis may place its assumption before a comma.
        memo_source = source
        if action.kind not in READ_KINDS:
            source = _intent_text(source)
        values = action.slots.model_dump()
        extracted = _extension_slots(action.kind, source)
        from .bill_category_filter import category_filter_in_text
        category_candidates = {value for clause in evidence if (value := category_filter_in_text(_intent_text(clause)))} if action.kind == "BILL_ANALYSIS" else set()
        category_evidence = next(iter(category_candidates)) if len(category_candidates) == 1 else None
        if action.kind != "BILL_ANALYSIS":
            values["category_filter"] = None
        elif values["category_filter"] is None:
            values["category_filter"] = category_evidence
        elif values["category_filter"] != category_evidence:
            values["category_filter"] = None
            questions.append("请明确要查看的消费类别，不使用模型推测的筛选条件。")
        permission_source = _extension_object_text(source) if action.kind.startswith(("FAMILY_", "GOAL_")) else source
        entity_key = "grantee_name" if action.kind in {"FAMILY_GRANT", "FAMILY_REVOKE"} else (
            "goal_name" if action.kind in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"} else None)
        if entity_key and not extracted.get(entity_key):
            questions.append("请用完整姓名或目标名明确唯一对象；有歧义时请为对象名称加配对引号。")
            continue
        if entity_key and re.search(r"[；;\n\r]", extracted[entity_key]):
            questions.append("协助人或目标名称含当前业务不支持的分隔符，请提供完整且受支持的对象名称；不会改成私有资料查询。")
            continue
        if entity_key == "grantee_name" and action.kind == "FAMILY_GRANT":
            quoted_name = any(value == extracted[entity_key] and start > 0 and source[start-1:start] in {"“", "「", '"', "‘", "'"}
                              for value, start, _end in _extension_object_matches(action.kind, source))
            if not quoted_name and re.search(r"(?:查看|查询|看)\s*余额.{0,6}(?:不要|不能|不得|禁止|不许|不准)", extracted[entity_key]):
                questions.append("姓名与权限否定没有清晰边界，请为完整协助人名称加配对引号，并明确允许的范围；不会将否定吞入姓名。")
                continue
        contradictory_currency = action.kind.startswith("FX_") and values["currency"] is not None and values["currency"] != extracted.get("currency")
        for field, value in extracted.items():
            if values.get(field) is None:
                if field in {"recipient_alias", "replace_alias", "card_number"}:
                    continue  # Identity/source omissions reach the quality gate.
                if action.kind in {"FX_TRANSFER", "FX_EXCHANGE"} and (field == "card_last4" or (action.kind == "FX_TRANSFER" and field == "target_currency")):
                    # An explicit funding-card omission must reach the quality
                    # gate; explicit settlement currency also cannot be defaulted.
                    continue
                values[field] = value
            elif field in {"grantee_name", "goal_name", "behavior_name", "reward_name"} and isinstance(value, str) \
                    and isinstance(values[field], str) and values[field] in value:
                values[field] = value  # Complete a shortened name from the same exact user clause.
        if action.kind == "FAMILY_PROPOSE" and values["owner_name"] != extracted.get("owner_name"):
            values["owner_name"] = None
        # Deterministic extraction may fill genuinely missing fields. Explicit
        # but false model values are still stripped below, never silently fixed.
        if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"} and values["payee_name"] is None:
            values["payee_name"] = _transfer_payee(source, currency="USD" if action.kind == "FX_TRANSFER" else "CNY")
        if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
            source_payees = {_transfer_payee(_intent_text(clause), currency="USD" if action.kind == "FX_TRANSFER" else "CNY") for clause in evidence}
            if values["payee_name"] is not None and values["payee_name"] not in source_payees:
                values["payee_name"] = None
                questions.append("请提供原文完整收款人；用户名中的数字与金额需用转账动词、空格或引号分隔。")
        if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "CARD_SET_LIMIT", "WEALTH_BUY", "WEALTH_REDEEM"} and values["amount_minor"] is None:
            amounts = amounts_in_text(source)
            if len(amounts) == 1:
                values["amount_minor"] = next(iter(amounts))
        if action.kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
            source_units = wealth_units_in_text(source)
            if values["units"] is None and len(source_units) == 1:
                values["units"] = next(iter(source_units))
            elif values["units"] is not None and values["units"] not in source_units:
                values["units"] = None
                questions.append("请明确准确的整份数量，不能由模型估算份额。")
            from .wealth_quantity_polarity import mask_denied_wealth_units
            positive_unit_source=mask_denied_wealth_units(source)
            if values["units"] is None and not source_units and re.search(r"(?:\d|[零〇一二两三四五六七八九十百千万半])\s*(?:份|units?\b|shares?\b)", positive_unit_source, re.I):
                questions.append("当前产品支持准确的正整数份额；小数、分数、范围和近似份数不会四舍五入办理。请给出整份数量或人民币金额。")
        else:
            values["units"] = None
        if action.kind == "TRANSFER_SCHEDULE" and values["scheduled_at"] is None:
            date = _DATE_EXPRESSION.search(source)
            if date:
                values["scheduled_at"] = date.group(0)
        if action.kind == "SUBSCRIPTION_CANCEL" and values["merchant"] is None:
            values["merchant"] = _subscription_stop_merchant(source)
            merchant = re.search(r"(?:取消|关闭|停用|停止|退订|关掉)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]{0,40})", source)
            if merchant:
                name = re.split(r"的?(?:订阅|代扣|自动续费|续费)", merchant.group(1), maxsplit=1)[0]
                if name:
                    values["merchant"] = name.removesuffix("的")
        if action.kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
            from .subscription_intent import subscription_request
            request=subscription_request(_intent_text(source))
            stated=request["slots"] if request and request["kind"] == action.kind else {}
            if request and request["mode"] == "CLARIFY":
                questions.append("你想开通或续期几个月？请明确1到12个月的月数，以及是否自动续费。")
            for field in ("subscription_months", "subscription_auto_renew"):
                if values[field] is not None and values[field] != stated.get(field):
                    values[field]=None
                    questions.append("请按你本次说的月数和自动续费选择核对，不会沿用旧设置。")
            # The service catalogue supplies the price, not the model or a
            # user-quoted sum. Money/rate/actor identifiers are never slots.
            for field in ("amount_minor", "units", "currency", "source_currency", "target_currency", "card_number", "card_last4"):
                values[field]=None
        elif not action.kind.startswith("SUBSCRIPTION_"):
            values["subscription_months"]=values["subscription_auto_renew"]=None
        if action.kind != "ASSET_POSITIONS_READ":
            values["asset_class"] = None
        if not action.kind.startswith("PREFERENCE_"):
            values["preference_kind"] = values["preference_value"] = None
        elif action.kind != "PREFERENCE_SET" or values.get("preference_value") is not None and values["preference_value"] not in source:
            values["preference_value"] = None
        if action.kind != "BENEFITS_VIEW":
            values["benefit_name"] = None
        for field in ("payee_name", "product_name", "merchant", "grantee_name", "owner_name", "goal_name", "behavior_name", "reward_name", "benefit_name"):
            quoted_object = field == entity_key and any(value == values[field] and start > 0
                and source[start-1:start] in {"“", "「", '"', "‘", "'"}
                for value, start, _end in _extension_object_matches(action.kind, source))
            forbidden = r"[；;\n\r]" if quoted_object else r"[，,。；;\n\r]"
            if values[field] and (values[field] not in source or re.search(forbidden, values[field])):
                values[field] = None
                questions.append(f"请补充{ACTION_LABELS[action.kind]}的{field}。")
        if values["card_last4"] and values["card_last4"] not in re.findall(r"(?:尾号|末四位|(?i:ending(?:\s+in)?|last\s+four(?:\s+(?:digits|characters))?|last\s+4(?:\s+(?:digits|characters))?))\s*[:#]?\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])", source):
            values["card_last4"] = None
            questions.append("请明确卡片尾号。")
        if values["card_number"] != extracted.get("card_number"):
            values["card_number"] = None
        identity = _recipient_reference_info(source) if action.kind in {"TRANSFER", "FX_TRANSFER", "CONTACT_BIND"} else {}
        if action.kind == "CONTACT_BIND" and values["payee_name"] != identity.get("payee_name"):
            values["payee_name"] = None
        for field in ("recipient_alias", "replace_alias"):
            if values[field] != identity.get(field):
                values[field] = None
        if memory_declined:
            values["recipient_alias"] = values["replace_alias"] = None
        amount_evidence = usd_amounts_in_text(source) if action.kind == "FX_TRANSFER" else (
            money_amounts_by_currency(source).get(values["currency"], set()) if action.kind in {"FX_EXCHANGE", "FX_QUOTE"} else amounts_in_text(source))
        if action.kind.startswith(("FAMILY_", "GOAL_")):
            amount_evidence = amounts_in_text(permission_source)
        if values["amount_minor"] and values["amount_minor"] not in amount_evidence:
            values["amount_minor"] = None
            questions.append(f"请明确{ACTION_LABELS[action.kind]}的准确金额。")
        if action.kind.startswith("FX_"):
            for field in ("currency", "source_currency", "target_currency"):
                if values[field] != extracted.get(field):
                    values[field] = None
            if values["card_last4"] != extracted.get("card_last4"):
                values["card_last4"] = None
            if contradictory_currency:
                values["currency"] = values["amount_minor"] = None
                questions.append("金额币种与用户原文不一致，请明确人民币分或美元美分，不能由模型选币种。")
        else:
            if values["currency"] == "USD":
                values["amount_minor"] = values["target_minor"] = values["maximum_amount_minor"] = None
                questions.append("此旧业务为人民币能力，不接受美元金额；请使用已开放的外汇动作。")
            values["currency"] = values["source_currency"] = values["target_currency"] = None
        if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"} and _ambiguous_compact_transfer(source, currency="USD" if action.kind == "FX_TRANSFER" else "CNY"):
            values["payee_name"] = values["amount_minor"] = None
            questions.append("用户名与金额边界不明确，请使用‘给用户名转2000人民币’或给收款人加引号。")
        memo, memo_issue = _transfer_memo(memo_source)
        if values["memo"] is not None and values["memo"] != memo:
            values["memo"] = None
        if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
            memo_issue = transfer_memo_issue(action, message)
            if memo_issue:
                questions.append(memo_issue)
        if values["scheduled_at"] and values["scheduled_at"] not in source:
            values["scheduled_at"] = None
            questions.append("请明确转账日期或时间。")
        if values["participant_names"] and any(name not in source for name in values["participant_names"]):
            values["participant_names"] = None
            questions.append("请明确 AA 参与者名单。")
        if values["participant_count"]:
            counts = []
            for token in re.findall(r"(\d+|[一二两三四五六七八九十]+)\s*(?:人|位)", source):
                try:
                    counts.append(int(token) if token.isdigit() else _chinese_integer(token))
                except ValueError:
                    pass
            if values["participant_count"] not in counts and values["participant_count"] != len(values["participant_names"] or []):
                values["participant_count"] = None
        for field in ("target_minor", "maximum_amount_minor"):
            if values[field] and values[field] not in amounts_in_text(permission_source, allow_limits=field == "maximum_amount_minor"):
                values[field] = None
        if values["due_date"] and values["due_date"] not in permission_source:
            values["due_date"] = None
        if values["expires_days"] and str(values["expires_days"]) not in re.findall(r"(\d+)\s*天", permission_source):
            values["expires_days"] = None
        derived = _extension_slots(action.kind, source)
        for field in ("monthly_saving_minor", "monthly_saving_delta_minor", "monthly_saving_total_minor"):
            if action.kind != "GOAL_VIEW":
                values[field] = None
            elif values[field] is not None and values[field] != derived.get(field):
                values[field] = None
                questions.append("请明确每月储蓄的原金额与调整金额，不使用模型推测值。")
        if action.kind.startswith("GOAL_") and values["goal_name"] and derived.get("goal_name"):
            proposed_name = values["goal_name"]
            quoted = any(quote in source for quote in (f"“{proposed_name}”", f"「{proposed_name}」", f'"{proposed_name}"'))
            if not quoted and (re.match(r"创建|建立|设立|新建|为|给|释放|取回", proposed_name)
                               or (action.kind == "GOAL_CREATE" and proposed_name == derived["goal_name"] + "目标")):
                values["goal_name"] = derived["goal_name"]
        if values["scopes"] is not None:
            values["scopes"] = [scope for scope in values["scopes"] if scope in derived.get("scopes", [])]
        if values["proposal_kind"] and values["proposal_kind"] != derived.get("proposal_kind"):
            values["proposal_kind"] = None
        if values["limit_period"] and values["limit_period"] != derived.get("limit_period"):
            values["limit_period"] = derived.get("limit_period")
        if action.kind == "FRAUD_ANALYZE":
            values["context_message"] = message  # Authoritative original wording, never a model-selected subset.
        elif values["context_message"]:
            values["context_message"] = None
        dependencies = [indices[dep] for dep in action.depends_on if dep in indices]
        if _independent_review_read(action.kind, message):
            dependencies = [indices[dep] for dep in action.depends_on if dep in indices
                and proposal.actions[dep].kind not in {'TRANSFER', 'TRANSFER_SCHEDULE', 'FX_TRANSFER'}]
        # Removing a dependency must not silently waive a required condition.
        if len(dependencies) != len(action.depends_on) and action.kind not in READ_KINDS:
            questions.append(f"{ACTION_LABELS[action.kind]}的前置动作需要重新确认。")
            continue
        dependencies = list(dict.fromkeys(dependencies))
        if action.kind in {"FRAUD_ANALYZE", "GOAL_VIEW"}:
            duplicate = next((index for index, previous in enumerate(actions)
                if previous.kind == action.kind and previous.slots.model_dump() == values
                and all(item.kind in READ_KINDS for item in actions[index + 1:])
                and (dependencies == previous.depends_on or dependencies == [index])), None)
            if duplicate is not None:
                indices[original_index] = duplicate
                continue  # Same original input, no intervening write, same obligation.
        indices[original_index] = len(actions)
        actions.append(ProposedAction(kind=action.kind, slots=ActionSlots(**values), depends_on=dependencies, label=ACTION_LABELS[action.kind]))
    return IntentProposal(summary=proposal.summary or message[:500], actions=actions,
                          clarification_questions=list(dict.fromkeys(localize_clarification(question, detect_reply_language(message)) for question in questions))[:8],
                          model_label=proposal.model_label, fallback=proposal.fallback,
                          failure_reasons=proposal.failure_reasons)


def rule_proposal(message: str, *, fallback: bool = True, reason: str = "rule", _raw_proposal: bool = False) -> IntentProposal:
    actions: list[ProposedAction] = []
    questions: list[str] = []
    overflow = False
    ordering = '；'.join(_review_preparation_body(_extension_object_text(_intent_text(clause))) for clause in _clauses(message))
    ordered = bool(re.search(r"先|然后|接着|随后|再|\b(?:first|then|afterwards)\b", ordering, re.I))
    # Clause splitting consumes the English separator. Preserve only a raw,
    # current then+registered-subscription verb, never source/memo ordering.
    ordered = ordered or bool(re.search(r"\bthen\s+(?:please\s+)?(?:subscribe|renew|activate|reactivate|restore)\b",_intent_text(message),re.I))
    for clause in _clauses(message):
        currency_source = _mask_card_numbers(_intent_text(clause))
        if _has_usd(currency_source) and len(_receiving_currency_signals(currency_source)) > 1:
            questions.append("收款币种存在互相冲突的明确指令，请确认唯一CNY或USD后再生成方案，不自动默认收币。")
        if _has_unsupported_currency(currency_source):
            questions.append("目前只支持人民币CNY与明确的美元USD；裸$或其他币种需先澄清，不自动折成人民币。")
        elif _has_usd(currency_source) and any(_ACTION_PATTERNS[item].search(_aa_intent_text(currency_source) if item == "AA_REQUEST" else currency_source) for item in {
                "TRANSFER_SCHEDULE", "AA_REQUEST", "FAMILY_GRANT", "FAMILY_PROPOSE", "WEALTH_BUY", "WEALTH_REDEEM", "CARD_SET_LIMIT", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}):
            if _CONDITIONAL.search(currency_source) or any(_ACTION_PATTERNS[item].search(_aa_intent_text(currency_source) if item == "AA_REQUEST" else currency_source) for item in {
                    "AA_REQUEST", "FAMILY_GRANT", "FAMILY_PROPOSE", "WEALTH_BUY", "WEALTH_REDEEM", "CARD_SET_LIMIT", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"}):
                questions.append("美元定时、AA、家庭授权、理财、限额与目标资金尚未开放；不能套用旧人民币写流程。")
        found: list[tuple[int, str]] = []
        for kind, pattern in _ACTION_PATTERNS.items():
            match = pattern.search(_action_routing_text(kind, clause))
            if match is not None and _allowed_evidence(kind, clause):
                found.append((match.start(), kind))
        kinds = {kind for _, kind in found}
        suppress = {
            "FAMILY_VIEW": {"FAMILY_GRANT", "FAMILY_REVOKE", "FAMILY_PROPOSE"},
            "GOAL_VIEW": {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"},
            "CARBON_VIEW": {"CARBON_CLAIM", "CARBON_REDEEM"},
        }
        for read_kind, writers in suppress.items():
            if kinds & writers:
                found = [(pos, kind) for pos, kind in found if kind != read_kind]
        if "TRANSFER_SCHEDULE" in kinds:
            found = [(pos, kind) for pos, kind in found if kind != "TRANSFER"]
        if "FX_TRANSFER" in kinds:
            found = [(pos, kind) for pos, kind in found if kind not in {"TRANSFER", "TRANSFER_SCHEDULE"}]
        if "FX_EXCHANGE" in kinds:
            found = [(pos, kind) for pos, kind in found if kind != "FX_QUOTE"]
        if kinds & {"CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT"}:
            found = [(pos, kind) for pos, kind in found if kind != "CARD_STATUS"]
        if kinds & {"WEALTH_BUY", "WEALTH_REDEEM"}:
            found = [(pos, kind) for pos, kind in found if kind != "WEALTH_COMPARE"]
        if "ASSET_POSITIONS_READ" in kinds and not kinds & {"WEALTH_BUY", "WEALTH_REDEEM"}:
            found = [(pos, kind) for pos, kind in found if kind != "WEALTH_COMPARE"]
        if kinds & {"PREFERENCE_SET", "PREFERENCE_REMOVE"}:
            found = [(pos, kind) for pos, kind in found if kind != "PREFERENCE_VIEW"]
        if "SUBSCRIPTION_CANCEL" in kinds:
            found = [(pos, kind) for pos, kind in found if kind != "SUBSCRIPTION_LIST"]
        for _, kind in sorted(found):
            source = message if kind == "FAMILY_PROPOSE" or ("补充：" in message and domain_for_action(kind) in {"GOALS", "CARBON", "FRAUD", "FAMILY"}
                    and len(list(_ACTION_PATTERNS[kind].finditer(message))) == 1) else clause
            if kind == "GOAL_VIEW":
                from .goal_what_if import goal_what_if_requested
                if goal_what_if_requested(_intent_text(message)) and len(re.findall(r"提前(?:多久|多少|几个月|几月)|how\s+(?:much\s+)?(?:sooner|earlier)", _intent_text(message),re.I)) == 1:
                    source = _intent_text(message)
            slots: dict = _extension_slots(kind, source)
            if kind == "BILL_ANALYSIS":
                from .bill_category_filter import category_filter_in_text
                if category := category_filter_in_text(_intent_text(clause)):
                    slots["category_filter"] = category
            if kind in {"TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "CARD_SET_LIMIT", "WEALTH_BUY", "WEALTH_REDEEM"}:
                amounts = amounts_in_text(_intent_text(clause))
                if len(amounts) == 1:
                    slots["amount_minor"] = next(iter(amounts))
            card = re.search(r"(?:尾号|末四位)\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])", clause)
            if card and kind.startswith("CARD_"):
                slots["card_last4"] = card.group(1)
            if kind in {"TRANSFER", "TRANSFER_SCHEDULE"}:
                payee = _transfer_payee(_intent_text(clause))
                if payee:
                    slots["payee_name"] = payee
                memo, memo_issue = _transfer_memo(clause)
                if memo is not None:
                    slots["memo"] = memo
                if memo_issue:
                    questions.append(memo_issue)
                date = _DATE_EXPRESSION.search(clause)
                if date and kind == "TRANSFER_SCHEDULE":
                    slots["scheduled_at"] = date.group(0)
            if kind == "AA_REQUEST":
                count = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*(?:人|位)", clause)
                if count:
                    value = int(count.group(1)) if count.group(1).isdigit() else _chinese_integer(count.group(1))
                    if 2 <= value <= 20:
                        slots["participant_count"] = value
                names = re.search(r"(?:参与者|名单|联系人)[：:]?\s*([^，。；;]+)", clause)
                if names:
                    slots["participant_names"] = [name.strip() for name in re.split(r"[、,，和]", names.group(1)) if name.strip()][:20]
            if kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
                source_units = wealth_units_in_text(_intent_text(clause))
                if len(source_units) == 1:
                    slots["units"] = next(iter(source_units))
                product = re.search(r"(?:申购|买入|赎回|卖出)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9（）()\-]{1,70}?)(?=\d|[零〇一二两三四五六七八九十百千万]+(?:元|块|份)|[，。；\s]|$)", clause)
                if product:
                    slots["product_name"] = product.group(1)
            if kind == "SUBSCRIPTION_CANCEL":
                if stop_merchant := _subscription_stop_merchant(_intent_text(clause)):
                    slots["merchant"] = stop_merchant
                merchant = re.search(r"(?:取消|关闭|停用|停止|退订|关掉)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]{0,40})", clause)
                if merchant:
                    name = re.split(r"的?(?:订阅|代扣|自动续费|续费)", merchant.group(1), maxsplit=1)[0]
                    if name:
                        slots["merchant"] = name.removesuffix("的")
            if len(actions) < 8:
                actions.append(ProposedAction(kind=kind, slots=ActionSlots(**slots),
                    depends_on=[len(actions)-1] if ordered and actions else [], label=ACTION_LABELS[kind]))
            else:
                overflow = True
        if (_UNMET_CONDITION.search(clause) or (_CONDITIONAL.search(clause) and "TRANSFER_SCHEDULE" not in kinds)) and any(_ACTION_PATTERNS[kind].search(clause) for kind in _ACTION_PATTERNS if kind not in READ_KINDS):
            questions.append("请确认前置条件满足后是否执行相应操作。")
        if (EN_UNMET_CONDITION.search(clause) or (EN_DATE_HINT.search(clause) and "TRANSFER_SCHEDULE" not in kinds)) and any(_ACTION_PATTERNS[kind].search(clause) for kind in _ACTION_PATTERNS if kind not in READ_KINDS):
            questions.append("Please state an exact supported date and time and confirm the intended request. Conditions and ambiguous dates will not become an immediate bank operation.")
    from .fraud_security_dialogue import reported_security_read_request
    if reported_security_read_request(message) and not any(action.kind == "FRAUD_ANALYZE" for action in actions):
        if len(actions) < 8:
            actions.append(ProposedAction(kind="FRAUD_ANALYZE",slots=ActionSlots(context_message=message),label=ACTION_LABELS["FRAUD_ANALYZE"]))
        else:
            overflow = True
    if overflow:
        questions.append('这次涉及超过8项事项，请分成两次请求，避免遗漏任何一步；这些事项尚未办理。')
        actions = ([ProposedAction(kind='FRAUD_ANALYZE', slots=ActionSlots(context_message=message),
            label=ACTION_LABELS['FRAUD_ANALYZE'])] if reported_security_read_request(message) else [])
    if not actions and not questions:
        questions.append("请说明需要查询的信息或办理的银行业务。")
    proposal = IntentProposal(summary=message[:500], actions=actions, clarification_questions=questions[:8], model_label=f"rule-proposal-v1 ({reason})", fallback=fallback,
        failure_reasons=['SOURCE_OPERATION_LIMIT_EXCEEDED'] if overflow else [])
    return proposal if _raw_proposal else ground_proposal(proposal, message, _source_budget_checked=True)


def canonical_action_message(action: ProposedAction) -> str:
    """Adapt a validated proposal to the existing domain compilers' syntax."""
    if action.kind in UPGRADED_CAPABILITIES:
        # New domains consume the verified typed action, never reparsed prose.
        return ACTION_LABELS[action.kind]
    slots = action.slots
    suffix = (f"，卡号{slots.card_number}" if slots.card_number else "") + (f"，尾号{slots.card_last4}" if slots.card_last4 else "")
    amount = f"{Decimal(slots.amount_minor) / 100:.2f}元" if slots.amount_minor else ""
    if action.kind == "CONTACT_BIND":
        verb = "改绑" if slots.replace_alias else "绑定"
        return f"将本人角色{slots.recipient_alias or '待明确'}{verb}为{slots.payee_name or '收款人待明确'}"
    if action.kind == "FX_TRANSFER":
        usd_amount = f"{Decimal(slots.amount_minor)/100:.2f}美元" if slots.amount_minor else "金额待补充"
        payment = f"，{slots.source_currency}付款" if slots.source_currency else ""
        receiving = f"，{slots.target_currency}收款" if slots.target_currency else ""
        return f"给{slots.payee_name or '收款人待补充'}转{usd_amount}{payment}{receiving}"
    if action.kind == "FX_EXCHANGE":
        exact = f"{Decimal(slots.amount_minor)/100:.2f}{slots.currency or '币种待补充'}" if slots.amount_minor else "金额待补充"
        return f"兑换{exact}，来源{slots.source_currency or '待补充'}，目标{slots.target_currency or '待补充'}"
    if action.kind == "FX_QUOTE":
        return f"查询{slots.source_currency or '来源币种'}兑{slots.target_currency or '目标币种'}汇率"
    card_messages = {
        "CARD_STATUS": "查询卡片状态", "CARD_LOCK": "临时锁卡",
        "CARD_UNLOCK": "解锁卡片", "CARD_REPORT_LOSS": "挂失卡片",
        "CARD_REPLACEMENT": "申请补卡", "CARD_SET_LIMIT": f"将卡片限额调整到{amount}",
    }
    if action.kind in card_messages:
        return card_messages[action.kind] + suffix
    if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE"}:
        date = f"{slots.scheduled_at} " if action.kind == "TRANSFER_SCHEDULE" and slots.scheduled_at else ""
        if not slots.payee_name or not slots.amount_minor:
            if slots.payee_name:
                return f"{date}给{slots.payee_name}转账"
            return f"{date}转账{amount}" if amount else f"{date}转账"
        memo = f"，备注{slots.memo}" if slots.memo else ""
        alias = f"，本人收款角色{slots.recipient_alias}" if slots.recipient_alias else ""
        funding = f"，使用卡号{slots.card_number}付款" if slots.card_number else ""
        return f"{date}给{slots.payee_name}转{amount}{alias}{funding}{memo}"
    if action.kind == "AA_REQUEST":
        count = f"，{slots.participant_count}人" if slots.participant_count else ""
        names = f"，参与者：{'、'.join(slots.participant_names)}" if slots.participant_names else ""
        return f"发起AA收款，总额{amount}{count}{names}"
    if action.kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        verb = "申购" if action.kind == "WEALTH_BUY" else "赎回"
        quantity = f"{slots.units}份" if slots.units is not None else ""
        if quantity and amount:
            quantity += f"，交易金额{amount}"
        return f"{verb}{slots.product_name or '理财'}{quantity or amount}"
    if action.kind == "WEALTH_COMPARE":
        return "比较理财产品"
    if action.kind == "ASSET_POSITIONS_READ":
        category = {"WEALTH": "理财", "STOCK": "股票", "PRECIOUS_METAL": "贵金属", "ALL": "全部资产"}.get(slots.asset_class, "全部资产")
        return f"查询本人{category}持仓" + (f"，产品{slots.product_name}" if slots.product_name else "")
    if action.kind == "MEMBERSHIP_VIEW":
        return "查询本人的客户等级与权益"
    if action.kind == "BENEFITS_VIEW":
        return "查询优惠活动" + (f"：{slots.benefit_name}" if slots.benefit_name else "")
    if action.kind.startswith("PREFERENCE_"):
        category = {"CONSUMPTION": "消费", "TRAVEL": "出行"}.get(slots.preference_kind, "本人")
        verb = {"PREFERENCE_VIEW": "查看", "PREFERENCE_SET": "保存", "PREFERENCE_REMOVE": "撤回"}[action.kind]
        return f"{verb}{category}偏好" + (f"：{slots.preference_value}" if slots.preference_value and action.kind == "PREFERENCE_SET" else "")
    if action.kind == "SUBSCRIPTION_CANCEL":
        return f"取消{slots.merchant or ''}的订阅代扣"
    if action.kind == "SUBSCRIPTION_LIST":
        return f"查看{slots.merchant or ''}订阅代扣"
    if action.kind in {"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"}:
        verb="开通" if action.kind == "SUBSCRIPTION_START" else "续期"
        months=f"{slots.subscription_months}个月" if slots.subscription_months is not None else ""
        renewal=("，开启自动续费" if slots.subscription_auto_renew else "，不自动续费") if slots.subscription_auto_renew is not None else ""
        return f"{verb}{slots.merchant or '会员待明确'}{months}{renewal}"
    if domain_for_action(action.kind) in {"FRAUD", "FAMILY", "GOALS", "CARBON"}:
        return ACTION_LABELS[action.kind]
    return "分析账单"
