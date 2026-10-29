import type { JsonValue } from "./types";

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  status: string;
  version: number;
  current_task_id: string | null;
  created_at: string;
  updated_at: string;
}
export interface ConversationSource {
  source_id?: string;
  title: string;
  kind?: string;
  reference?: string;
  observed_at?: string | null;
}
export interface ConversationSuggestion {
  id: string;
  label: string;
  prompt: string;
  behavior: "SEND_READ_ONLY" | "FILL_COMPOSER";
  capability_id: string;
  read_only: boolean;
}
export interface ConversationTurn {
  status_snapshot_status?: "RETAINED" | "NOT_APPLICABLE" | "NOT_RETAINED" | "UNAUTHORIZED" | "UNAVAILABLE" | "INVALID";
  status_snapshot?: {
    schema_version: "conversation-status-snapshot-v1";
    authority: "HISTORICAL_STATUS_NOT_CURRENT_AUTHORIZATION";
    conversation_id: string; turn_id: string; task_id: string; task_version: number; state: string;
    reply_language: "zh-CN" | "en"; text: string; observed_at: string; fingerprint: string;
    projection: { task_id: string; version: number; state: string; outcome_kind: string | null;
      verified: boolean; confirmation_status: "NONE" | "PENDING" | "EXPIRED" | "UNAVAILABLE" };
  } | null;
  /** Owned immutable READ evidence; never a retained bank authorization. */
  read_result_snapshot_status?: "RETAINED" | "NOT_RETAINED" | "UNAVAILABLE";
  read_result_snapshot_version?: number;
  read_result_snapshot_observed_at?: string;
  reply_snapshot?: {
    schema_version: "conversation-reply-snapshot-v1";
    conversation_id: string;
    turn_id: string;
    task_id: string;
    task_version: number;
    state: string;
    reply_language: "zh-CN" | "en";
    actor_role?: string;
    fingerprint: string;
    intro: string;
    detail: string | null;
    text: string;
    observed_at: string;
    authority: "HISTORICAL_NARRATION_NOT_CURRENT_STATUS_OR_AUTHORIZATION";
  } | null;
  reply_snapshot_status?: "AVAILABLE" | "RETAINED" | "NOT_APPLICABLE" | "NOT_RECORDED" | "NOT_RETAINED" | "INVALID_NOT_RETAINED" | "ROLE_CHANGED" | "CAPACITY_REACHED" | "HIDDEN" | "UNAUTHORIZED" | "UNAVAILABLE" | "INVALID";
  reply_language?: "zh-CN" | "en";
  turn_id: string;
  conversation_id: string;
  sequence: number;
  message: string;
  status: "QUEUED" | "LEASED" | "ANSWERED" | "FAILED";
  mode:
    | null
    | "CONSULT"
    | "NEW_TASK"
    | "CLARIFY"
    | "REVISE"
    | "STATUS"
    | "CONFIRM_HINT"
    | "CANCEL_HINT"
    | "NEED_CONTEXT"
    | "DISCOVERY"
    | "KNOWLEDGE"
    | "GREETING"
    | "FEEDBACK"
    | "OUT_OF_SCOPE";
  answer: string | null;
  sources: ConversationSource[];
  suggestions?: ConversationSuggestion[];
  response_meta?: Record<string, JsonValue>;
  task_link: {
    task_id: string;
    state: string;
    version: number;
    relationship: "CREATED" | "CONTINUED" | "STATUS" | "CURRENT";
  } | null;
  field_sources?: {
    field: string;
    value: JsonValue;
    sources: {
      source: "USER_CURRENT" | "USER_CONTEXT";
      turn_id?: string;
      span?: JsonValue;
    }[];
    task_id?: string;
    plan_version?: number;
  }[];
  diagnostics?: Record<string, JsonValue>;
  error_code?: string | null;
  created_at: string;
  updated_at: string;
}
export interface Conversation extends ConversationSummary {
  turns: ConversationTurn[];
  has_earlier?: boolean;
  before_cursor?: number | null;
}
export interface ReviewedEvidenceRef {
  review_id: string;
  attachment_version: number;
  sha256: string;
}
export interface ConversationTurnInput {
  recipient_choice?: { origin_turn_id: string; choice_set_id: string; choice_token: string };
  reply_language?: "zh-CN" | "en";
  message: string;
  client_request_id: string;
  context_policy: "AUTO" | "INDEPENDENT";
  current_task_id?: string;
  expected_task_version?: number;
  explicit_task_selection?: boolean;
  reviewed_evidence_refs?: ReviewedEvidenceRef[];
}
export interface PendingConversationRequest {
  scope: string;
  conversation_id: string | null;
  conversation_request_id: string;
  title: string;
  payload: ConversationTurnInput;
  created_at: string;
  lookup_only?: boolean;
}
export interface ConversationAcknowledgement {
  status: "ACCEPTED" | "UNDETERMINED";
  client_request_id: string;
  conversation: ConversationSummary | null;
  turn: ConversationTurn | null;
  read_only: true;
  bank_confirmation_permitted: false;
}
