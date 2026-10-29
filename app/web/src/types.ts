export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
export interface Profile {
  id?: string;
  profile?: string;
  key?: string;
  name?: string;
  label?: string;
  description?: string;
}
export interface DemoSession {
  token: string;
  sandbox_id: string;
  user_id: string;
  expires_at: string;
  profile?: string | null;
  actor_role?: string;
  display_name?: string;
  username?: string;
  account_id?: string | null;
  registered?: boolean;
  main_account_id?: string;
}
export interface Account {
  account_id: string;
  name: string;
  balance_minor: number;
  available_balance_minor?: number;
  goal_reserved_minor?: number;
  version: number;
}
export interface Card {
  card_id: string;
  last4: string;
  card_number?: string | null;
  display_last4?: string | null;
  legacy_last4?: string | null;
  issuer_prefix?: "zhenhang" | "xiaozhao" | null;
  status: string;
  limit_minor: number;
  version: number;
  currency_mode?: "CNY_USD" | "CNY_ONLY";
  supported_currencies?: string[];
  balances_by_currency?: Record<string, number>;
  fx_card?: boolean;
  limit_currency?: string;
  card_tier_code?: string | null;
  card_tier_name?: string | null;
  card_tier_version?: number | null;
  instrument_type?: "DEBIT";
  instrument_id?: string;
  available_by_currency?: Record<string, number>;
}
export interface CreditCurrencyDebt {
  currency: "CNY" | "USD"; principal_minor: number; fees_minor: number; interest_minor: number;
  credit_balance_minor: number; outstanding_minor: number;
}
export interface CreditCard {
  card_id: string; card_number: string; display_last4: string; instrument_id: string; instrument_type: "CREDIT";
  status: string; version: number; base_currency: "CNY"; shared_credit_limit: true; credit_is_cash: false;
  supported_currencies: ("CNY" | "USD")[]; base_credit_limit_minor: number; used_base_exposure_minor: number;
  authorization_base_exposure_minor: number; available_base_credit_minor: number; balances: CreditCurrencyDebt[];
}
export interface CreditApplication {
  application_id: string; kind: "CARD" | "LIMIT"; status: string; requested_limit_minor: number | null;
  card_id: string | null; user_id?: string; version: number;
}
export interface Subscription {
  auto_renew?: boolean;
  entitlement_end_at?: string | null;
  entitled?: boolean;
  can_renew_here?: boolean;
  can_start_here?: boolean;
  currency?: string;
  monthly_price_minor?: number;
  catalog_monthly_price_minor?: number | null;
  billing_months?: number;
  due_status?: "PAST_RECORDED_DATE" | "UPCOMING" | "UNKNOWN" | "RECORDED_INACTIVE";
  calendar_time?: string;
  data_through?: string | null;
  subscription_id: string;
  merchant: string;
  amount_minor: number;
  source_type: string;
  status: string;
  next_due_at: string;
}
export interface Investment {
  product_id: string;
  name: string;
  risk_level: number;
  price_minor: number;
  holding_units: number;
  status: string;
}
export interface Notification {
  notification_id: string;
  task_id: string;
  root_task_id?: string;
  event_type: string;
  payload: Record<string, JsonValue>;
  created_at: string;
}
export interface TaskListItem {
  task_id: string;
  state: string;
  version?: number;
  message: string;
  created_at?: string;
  summary?: string;
}
export interface TraceNode {
  display_label?: string;
  display_error_message?: string | null;
  display_wait_reason?: string | null;
  node_id: string;
  operation_id?: string;
  action: string;
  label: string;
  status: string;
  depends_on: string[];
  child_task_id?: string;
  result?: Record<string, JsonValue> | null;
  wait_reason?: string | null;
  error?: string | null;
  can_compensate?: boolean;
  compensation_task_id?: string | null;
  evidence?: Record<string, JsonValue> | null;
}
export interface Confirmation {
  display_label?: string;
  display_summary?: JsonValue;
  task_id: string;
  confirmation_id: string;
  plan_hash: string;
  expected_task_version: number;
  node_key: string;
  label: string;
  summary: JsonValue;
  requires_step_up: boolean;
  expires_at?: string;
  scope?: Record<string, JsonValue> | null;
}
export interface RegisteredRecipient {
  username: string;
  display_name?: string | null;
  account_reference: string;
  account_last4?: string | null;
  transfer_channel?: "INTERNAL_BANK";
  accepted_currencies?: string[];
  primary_card_number?: string | null;
  available_receiving_cards?: {
    card_number: string;
    display_last4: string;
    accepted_currencies: string[];
  }[];
}
export interface OwnTransferReceipt {
  transfer_id: string;
  direction: "INCOMING" | "OUTGOING" | string;
  transfer_channel: "INTERNAL_BANK" | "EXTERNAL_SIM" | string;
  status: string;
  amount_minor: number;
  currency: string;
  memo: string;
  counterparty: string;
  created_at: string;
  settlement_verified: boolean;
  requested_currency?: string;
  requested_amount_minor?: number;
  fx_quote?: Record<string, JsonValue> | null;
}
export interface BillAnalysis {
  category_filter?: {version:"bill-category-filter-v1";basis:"CATEGORY_EXACT_MATCH";category:string;currency:"CNY";read_only:true;bank_state_changed:false} | null;
  statement_transactions?: { transaction_id: string; amount_minor: number; currency: "CNY"; merchant: string; category: string; occurred_at: string; cash_flow_type: "MOVEMENT" | "REFUND" | "EXPENSE" | "INCOME" }[];
  statement_transactions_truncated?: boolean;
  period_start?: string | null;
  period_end?: string | null;
  period_scope?: { start?: string; end?: string; label?: string; kind?: string; time_zone?: string } | null;
  period_fallback?: {
    version: "bill-period-fallback-v1";
    status: "APPLIED";
    reason: "REQUESTED_PERIOD_NOT_COVERED" | "REQUESTED_PERIOD_ONLY_MOVEMENTS";
    requested: { start: string; end: string; kind: "MONTH"; time_zone: "Asia/Shanghai" };
    displayed: { start: string; end: string; kind: "MONTH"; time_zone: "Asia/Shanghai" };
    requested_coverage: BillAnalysis["coverage"];
    available_transaction_count: number;
    unfiltered_period_transaction_count?: number;
    requested_activity?: {
      currency:"CNY"; transaction_count:number; period_start:string; period_end:string;
      spending_minor:0; income_minor:0; refund_minor:0; movement_in_minor:number; movement_out_minor:number;
    };
    read_only: true;
    bank_state_changed: false;
  } | null;
  reference_display?: { status: string; value_minor?: number | null; currency?: string; original_minor?: number | null; original_currency?: string;
    rate?: string | null; provider?: string | null; market_date?: string | null; fetched_at?: string | null; expires_at?: string | null; reference_only?: boolean } | null;
  coverage?: {
    status: "COMPLETE" | "PARTIAL" | "INSUFFICIENT" | "UNKNOWN" | "OBSERVED_HISTORY";
    basis?: "LIVE_ACTIVITY_ONLY" | "HISTORY_AND_LIVE_ACTIVITY" | string;
    historical_data_through?: string | null; historical_data_start?: string | null; live_activity_through?: string | null; live_activity_is_full_import?: boolean;
    calendar_time?: string; calendar_date?: string; time_zone?: string;
    data_start?: string | null; data_through?: string | null;
    requested_start?: string | null; requested_end?: string | null;
    effective_start?: string | null; effective_end?: string | null;
    zero_is_full_period_total?: boolean; notice?: string;
  } | null;
  financial_insights?: {
    category_filter?: BillAnalysis["category_filter"];
    read_only?: boolean;bank_state_changed?: boolean;model_calculated_money?: boolean;
    version?: string; question?: string; currency?: string; unit?: string;
    facts?: { field: string; value: number; currency: string; unit: string; label?: string; label_en?: string }[];
    insights?: { code: string; severity?: string }[];
    category_labels_en?: Record<string, string>;
    review_candidates?: { category: string; amount_minor: number; currency: string; unit: string; action: string; guaranteed_saving_minor: number | null }[];
    comparison?: { status: string; spending_change_minor?: number | null; growth_claim_allowed?: boolean; zero_baseline?: boolean;
      current?: { start: string; end: string }; previous?: { start: string; end: string };
      category_changes?: { category: string; change_minor: number }[] };
  } | null;
  transaction_count: number;
  spending_minor: number;
  refund_minor: number;
  category_spending_minor: Record<string, number>;
  anomalies: Record<string, JsonValue>[];
  explanation: string;
  income_minor?: number;
  net_minor?: number;
  movement_in_minor?: number;
  movement_out_minor?: number;
  monthly?: {
    month: string;
    income_minor: number;
    spending_minor: number;
    refund_minor: number;
    net_minor?: number;
  }[];
}
export interface BillReport {
  coverage?: BillAnalysis["coverage"];
  report_version: string;
  business_time: string;
  synthetic: boolean;
  period: {
    kind: string;
    year: number;
    month: number | null;
    label: string;
    status: string;
    partial: boolean;
    data_through: string | null;
  };
  summary: BillAnalysis;
  transactions: {
    transaction_id: string;
    occurred_at: string;
    amount_minor: number;
    merchant: string;
    category: string;
    flag: string | null;
  }[];
  reconciliation: { balanced: boolean; explanation: string };
}
export interface ScheduledTransfer {
  schedule_id: string;
  task_id: string;
  amount_minor: number;
  payee_name: string;
  due_at: string;
  status: string;
  error?: string | null;
}
export interface AARequest {
  request_id: string;
  task_id: string;
  total_minor: number;
  received_minor: number;
  status: string;
  participants: {
    participant_id: string;
    name: string;
    amount_minor: number;
    status: string;
    paid_at?: string | null;
  }[];
}
export interface TaskTrace {
  paused_plan?: {
    version: "customer-paused-plan-v1";
    task_id: string;
    task_version: number;
    paused: true;
    confirmation_available: false;
  };
  reply_language?: "zh-CN" | "en";
  display_status_message?: string | null;
  created_at?: string;
  updated_at?: string;
  result_observed_at?: string | null;
  schema_version: string;
  task_id: string;
  version: number;
  state: string;
  message: string;
  summary: string;
  model_label: string;
  plan_version: number;
  event_cursor?: number;
  nodes: TraceNode[];
  pending_confirmation: Confirmation | null;
  allowed_actions: string[];
  clarification_questions?: string[];
  customer_reply_status?: "QUEUED" | "PROCESSING" | "READY" | "UNAVAILABLE" | "STALE" | null;
  reviewed_material_reply?: {schema_version:"customer-reviewed-bill-reply-v1";text:string;status:"READY"|"UNAVAILABLE";
    read_only:true;bank_data_changed:false;screenshot_proves_posting:false};
  assistant_response?: {
    reply_language?: "zh-CN" | "en";
    intro: string;
    detail: string | null;
    card_detail?: string | null;
    card_intro?: string | null;
    query_layout_version?: "customer-query-layout-v1" | null;
    query_focus?: "CARD_LIMIT" | "CARD_BALANCE_LIMIT" | "CARBON_CLAIMABLE_RECORDS" | "CARBON_ELIGIBLE_REWARDS" | "FAMILY_AUTHORIZED_BALANCES" | "FAMILY_DELEGATIONS" | null;
    query_focus_by_node?: Record<string, "CARD_LIMIT" | "CARD_BALANCE_LIMIT" | "CARBON_CLAIMABLE_RECORDS" | "CARBON_ELIGIBLE_REWARDS" | "FAMILY_AUTHORIZED_BALANCES" | "FAMILY_DELEGATIONS">;
    detail_kind: string;
    state_kind: string;
    read_only: boolean;
  } | null;
  analysis?: BillAnalysis | null;
  supplementary_bill?: BillAnalysis | null;
  supplementary_bill_observed_at?: string | null;
  receipt?: Record<string, JsonValue> | null;
  status_message?: string | null;
  outcome?: {
    kind:
      | "PROCESS"
      | "QUERY_RESULT"
      | "BUSINESS_RECEIPT"
      | "PROPOSAL_SAVED"
      | "PARTIAL_RESULT"
      | "UNKNOWN"
      | "FAILURE";
    state: string;
    verified: boolean;
    completed_operation_ids: string[];
  } | null;
  model_decisions?:
    | Record<string, JsonValue>[]
    | Record<string, JsonValue>
    | null;
  compensation_source?: {
    source_task_id: string;
    source_operation_id: string;
  } | null;
  laya_advice?: LayaAdvice | null;
  planning_diagnostics?: Record<string, JsonValue> | null;
  domains?: string[];
  multi_step?: boolean;
  cross_domain?: boolean;
}
export interface LayaAdvice {
  mode: "OFF" | "SHADOW";
  status: string;
  backend: string;
  laya_used: boolean;
  answers: {
    question_id: string;
    raw_p_true: number;
    probabilities: Record<string, number>;
    distribution_source: string;
  }[];
  question_definitions?: Record<string, JsonValue>;
  candidate_domains: string[];
  primary_domains: string[];
  unmapped_actions?: string[];
  multi_step_advice?: boolean | null;
  cross_domain_advice?: boolean | null;
  coverage_difference?: {
    agreement: string;
    interpretation?: string;
    candidate_domains_missing_from_proposal?: string[];
    proposal_domains_not_suggested?: string[];
  };
  calibration?: { status: string };
  identity?: Record<string, JsonValue> | null;
  latency_ms?: number | null;
  usage?: Record<string, JsonValue>;
  error_code?: string | null;
  readonly_candidate?: unknown;
  readonly_candidate_error?: string | null;
}
export interface LayaReadonlyCandidatePin {
  identity_sha256: string;
  questions_sha256: string;
  policy_sha256: string;
  manifest_sha256: string;
}
export interface LayaReadonlyCandidateObservation {
  status: "ABSTAIN" | "EXPERIMENTAL_CANDIDATE";
  reason: string;
  candidate_kind: "BILL_ANALYSIS" | "CARD_STATUS" | "SUBSCRIPTION_LIST" | "WEALTH_COMPARE" | null;
  calibrated_score: number | null;
  score_is_correctness_probability: false;
  sdk_inference_used: boolean;
  latency_ms: number | null;
  mode: "SHADOW";
  authority: "OBSERVATION_ONLY";
  adopted: false;
}
export interface LayaReadonlyCandidateEnvelope {
  schema_version: "laya-readonly-candidate-v1";
  pin: LayaReadonlyCandidatePin;
  source_sha256: string;
  inference_input_sha256: string;
  quality_gate_passed: false;
  adoption_enabled: false;
  observation: LayaReadonlyCandidateObservation;
}
export interface Challenge {
  challenge_id: string;
  demo_code?: string;
  expires_at: string;
  provider: string;
}
/** Finite, authenticated BFF rendering scope; not a bank authorization. */
export type CustomerReadCoverage = { status: "UNFILTERED" | "UNAVAILABLE_SCOPE" } | {
  status: "FILTERED";
  read_kind: "CARBON_VIEW" | "WEALTH_COMPARE" | "FAMILY_VIEW";
  selected_dimensions: ("REWARDS" | "ACTIVITIES" | "PRODUCTS" | "FAMILY_OWNERS" | "FAMILY_GRANTEES")[];
  selected_counts: Partial<Record<"REWARDS" | "ACTIVITIES" | "PRODUCTS" | "FAMILY_OWNERS" | "FAMILY_GRANTEES", number>>;
  balance_authorization?: {schema_version:"family-balance-negative-authorization-v1";status:"NOT_AUTHORIZED";owner_name:string};
};
