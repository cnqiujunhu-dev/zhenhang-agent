from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


IntentType = Literal[
    "TRANSFER_SCHEDULE",
    "AA_REQUEST",
    "BILL_ANALYSIS",
    "BILL_ANOMALY_LOCK_CARD",
    "TRANSFER",
    "WEALTH",
    "CARD_MANAGEMENT",
    "SUBSCRIPTION_MANAGEMENT",
    "CROSS_SCENARIO",
    "FRAUD_ANALYSIS",
    "FAMILY_ASSISTANCE",
    "GOAL_CASHFLOW",
    "CARBON_BENEFITS",
    "FOREX_QUERY",
    "FOREX_TRANSFER",
    "FOREIGN_EXCHANGE",
    "CONTACT_MANAGEMENT",
    "CUSTOMER_BENEFITS",
    "CUSTOMER_PREFERENCES",
    "CREDIT_MANAGEMENT", "AA_VIEW", "FAMILY_OCCASIONS", "LIFE_PLAN", "MERCHANT_SERVICE", "OWN_ACCOUNT_TRANSFER",
    "UNSUPPORTED",
]
TaskState = Literal[
    "QUEUED",
    "UNDERSTANDING",
    "PLANNING",
    "WAITING",
    "NEED_CLARIFICATION",
    "PLANNED",
    "POLICY_CHECKED",
    "AWAITING_CONFIRMATION",
    "AUTHORIZED",
    "EXECUTING",
    "PARTIAL",
    "UNKNOWN",
    "COMPENSATING",
    "SUCCEEDED",
    "FAILED",
    "CLOSED",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTaskRequest(StrictModel):
    message: str = Field(min_length=2, max_length=2000)


class ConfirmTaskRequest(StrictModel):
    confirmation_id: str
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_task_version: int = Field(ge=1, strict=True)
    auth_challenge_id: str | None = None


class CancelTaskRequest(StrictModel):
    expected_task_version: int = Field(ge=1, strict=True)


class ClarifyPayeeRequest(StrictModel):
    payee_id: str
    expected_task_version: int = Field(ge=1, strict=True)


class ClarifyTaskRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    expected_task_version: int = Field(ge=1, strict=True)


class DemoSessionResponse(StrictModel):
    token: str
    sandbox_id: str
    user_id: str
    expires_at: str


class BillAnalysis(StrictModel):
    transaction_count: int
    spending_minor: int
    refund_minor: int
    category_spending_minor: dict[str, int]
    anomalies: list[dict]
    explanation: str
    income_minor: int = 0
    net_minor: int = 0
    movement_in_minor: int = 0
    movement_out_minor: int = 0
    category_income_minor: dict[str, int] = Field(default_factory=dict)
    monthly: list[dict] = Field(default_factory=list)
    daily: list[dict] = Field(default_factory=list)
    period_start: str | None = None
    period_end: str | None = None
    time_zone: str = "Asia/Shanghai"
    coverage: dict | None = None
    financial_insights: dict | None = None
    # A read-only alternative period is disclosed separately from the original
    # user request; actual totals always belong to period_start/period_end.
    period_fallback: dict | None = None
    statement_transactions: list[dict] = Field(default_factory=list)
    statement_transactions_truncated: bool = False
    category_filter: dict | None = None
    billing_schema_version: Literal["owned-bill-analysis-v2"] | None = None
    currency: Literal["CNY"] | None = None
    totals_by_currency: list[dict] = Field(default_factory=list)
    billing_scope: dict | None = None

    @model_serializer(mode="wrap")
    def _preserve_legacy_bill_contract(self, handler):
        value = handler(self)
        # Adding capabilities must not alter already recorded v1 JSON/hashes.
        if self.billing_schema_version is None:
            for key in ("billing_schema_version", "currency", "totals_by_currency", "billing_scope"):
                value.pop(key, None)
        return value


class FinancialIntentContract(StrictModel):
    intent_id: str
    intent_version: int
    intent_type: IntentType
    actor_user_id: str
    sandbox_id: str
    source_channel: str
    slots: dict[str, dict]
    requested_capabilities: list[str]
    status: Literal["PROPOSED", "READY", "NEED_CLARIFICATION"]


class TaskView(StrictModel):
    task_id: str
    state: TaskState
    version: int
    intent_type: IntentType
    model_label: str
    message: str
    fic: FinancialIntentContract | None
    analysis: BillAnalysis | None
    plan: dict
    plan_hash: str | None
    confirmation_id: str | None
    confirmation_node_key: str | None = None
    result: dict


class CardView(StrictModel):
    card_id: str
    last4: str
    status: str
    limit_minor: int
    version: int
    card_number: str | None = None
    display_last4: str | None = None
    legacy_last4: str | None = None
    issuer_prefix: str | None = None
    currency_mode: Literal["CNY_ONLY", "CNY_USD"] = "CNY_ONLY"
    supported_currencies: list[Literal["CNY", "USD"]] = Field(default_factory=lambda: ["CNY"])
    balances_by_currency: dict[str, int] = Field(default_factory=dict)
    fx_card: bool = False
    fx_profile_version: int | None = None
    usd_wallet_version: int | None = None
    limit_currency: Literal["CNY"] = "CNY"
    card_tier_code: str | None = None
    card_tier_name: str | None = None
    card_tier_version: int | None = None


class ApiError(StrictModel):
    code: str
    message: str
    retryable: bool = False


class PreferenceInput(StrictModel):
    key: Literal["display_language", "notification_level"]
    value: str = Field(min_length=1, max_length=40)


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
