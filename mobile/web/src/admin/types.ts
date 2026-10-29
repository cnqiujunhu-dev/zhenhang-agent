import type { JsonValue, TaskTrace } from "../types";
import type { PurposeObservationLedger } from "./PurposeObservationMetrics";
export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface ManagedAccount {
  account_id: string;
  username: string;
  role: string;
  status: string;
  version: number;
  profile: string | null;
  user_id: string;
  sandbox_id: string;
  created_at: string;
  last_login_at: string | null;
}
export interface AdminTask {
  task_id: string;
  username: string | null;
  user_id: string;
  sandbox_id: string;
  intent_type: string;
  state: string;
  version: number;
  created_at: string;
  updated_at: string;
}
export interface AdminOverview {
  scope: string;
  runtime_health: {
    status: string;
    worker: string;
    database: string;
    online_workers: number;
  };
  accounts: {
    total: number;
    by_role: Record<string, number>;
    by_status: Record<string, number>;
  };
  tasks: { total: number; by_state: Record<string, number> };
  jobs: {
    total: number;
    by_status: Record<string, number>;
    active_unknown_operations?: number;
  };
  policies?: { total: number; by_decision: Record<string, number> };
  models?: AdminModelObservations;
  bank_confirmation_permitted: boolean;
  customer_data_edit_permitted: boolean;
}
export interface AdminProvenanceEvent {
  event_seq: number;
  event_type: "ACTION_AUTHORIZED" | "ACTION_STARTED" | "WORKFLOW_JOB_FAILED";
  recorded_at: string;
  settlement_asserted: false;
  schema_version?: "execution-provenance-v1";
  operation_id?: string;
  child_task_id?: string;
  action_kind?: string;
  plan_version?: number;
}
export interface AdminAuthorizationEvidence extends AdminProvenanceEvent {
  scope_status: "RECORDED" | "NOT_RECORDED";
  stage?: "ACCEPTED_USER_CONFIRMATION";
  scope_coverage?: "ALLOWLIST_ONLY";
  node_key?: string;
  target_id?: string;
  expected_task_version?: number;
  plan_hash?: string;
  view_hash?: string;
  scope: JsonValue | null;
  scope_hash: string | null;
}
export interface AdminExecutionSoftware {
  package: "zhenxing-agent";
  release: string | null;
  release_source: "INSTALLED_DISTRIBUTION_METADATA";
  module_hash_algorithm: "SHA256_MARSHALLED_LOADED_CODE_V1";
  module_hashes: Record<string, string>;
  module_hash_scope: "LISTED_LOADED_MODULE_CODE_ONLY";
  runtime_fingerprint: string;
}
export interface AdminExecutionEvidence extends AdminProvenanceEvent {
  software_status: "RECORDED" | "NOT_RECORDED";
  stage?: "STARTED" | "FAILED_ATTEMPT";
  job_id?: string;
  attempt?: number;
  dispatch_started?: boolean;
  software: AdminExecutionSoftware | null;
}
export interface AdminTrace extends AdminTask {
  runtime_trace?: TaskTrace | null;
  authorization_evidence?: AdminAuthorizationEvidence[];
  authorization_evidence_total?: number;
  authorization_evidence_returned?: number;
  authorization_evidence_truncated?: boolean;
  execution_evidence?: AdminExecutionEvidence[];
  execution_evidence_total?: number;
  execution_evidence_returned?: number;
  execution_evidence_truncated?: boolean;
  tool_calls?: {
    tool_call_id: string;
    task_id: string;
    tool_name: string;
    status: string;
    idempotency_key: string;
    request_hash: string;
    result: JsonValue;
    created_at: string;
  }[];
  tool_calls_total?: number;
  tool_calls_truncated?: boolean;
  plan_snapshots?: {
    snapshot_id: string;
    version: number;
    plan_hash: string;
    proposal: JsonValue;
    plan: JsonValue;
    created_at: string;
  }[];
  plan_snapshots_total?: number;
  plan_snapshots_truncated?: boolean;
  operations: {
    operation_id: string;
    action_kind: string;
    status: string;
    active: number;
    plan_version: number;
  }[];
  events: { seq: number; event_type: string; created_at: string }[];
  read_only: boolean;
  bank_confirmation_permitted: boolean;
}
export interface AdminModelObservations {
  purpose_observations?: PurposeObservationLedger;
  scope: string;
  planning_invocations: number;
  by_outcome: Record<string, number>;
  by_provider: Record<string, number>;
  diagnostics_record_count: number;
  fallback_count: number;
  fallback_rate: number | null;
  observed_api_calls: number | null;
  api_call_counts_unknown: number;
  route_observations: number;
  route_fallback_count: number;
  route_fallback_rate: number | null;
  by_actual_provider: Record<string, number>;
  token_usage: Record<
    string,
    {
      observed_sum: number | null;
      complete_total: number | null;
      known_observations: number;
      unknown_observations: number;
    }
  >;
}
export interface RoutingPolicy {
  schema_version: string;
  version: number;
  mode: "AUTO" | "PREFER_CLOUD" | "LOCAL_ONLY" | "CLOUD_ONLY";
  local_model: string;
  cloud_provider: string;
  cloud_model: string;
  cloud_enabled: boolean;
  max_http_attempts: number;
  max_elapsed_seconds: number;
  cloud_secondary_model?: string | null;
  standard_read_fastpath?: boolean;
}
export interface RoutingRegistry {
  providers: {
    provider_id: string;
    configured: boolean;
    credential_configured: boolean;
    is_cloud: boolean;
    circuit_status: string;
    models: {
      model: string;
      last_http_available: boolean | null;
      circuit_status?: "OPEN" | "CLOSED";
      availability_failures?: number;
      retry_after_seconds?: number;
      scope?: string;
    }[];
  }[];
}
export interface ModelRoutingSnapshot {
  policy: RoutingPolicy;
  registry: RoutingRegistry;
}
export interface TestDataRow {
  row_id: string;
  version: number;
  [field: string]: JsonValue;
}
export interface TestDataSnapshot {
  scope: string;
  role: string;
  editable_fields: Record<string, string[]>;
  account: TestDataRow;
  history: TestDataRow[];
  history_page: number;
  history_page_size: number;
  history_total: number;
  cards: TestDataRow[];
  subscriptions: TestDataRow[];
  risk_profile: TestDataRow;
  business_time: string;
  recent_edits: Record<string, JsonValue>[];
}
