import type { AdminTask, AdminTrace } from "./types";
import type { JsonValue } from "../types";
import { exactMajorAmount } from "../typedMoney.ts";

/** Navigation categories only. They never grant a capability or prove execution. */
export const judgeScenes = [
  { id: "TRANSFER", label: "智能转账" },
  { id: "BILLS", label: "账单分析" },
  { id: "WEALTH", label: "理财操作" },
  { id: "CARDS", label: "卡片管理" },
  { id: "SUBSCRIPTIONS", label: "订阅／代扣" },
  { id: "CROSS", label: "跨场景联动" },
] as const;
export type JudgeScene = typeof judgeScenes[number]["id"];
export type EvidenceStateFilter = "ALL" | "SUCCEEDED" | "UNKNOWN" | "PENDING";
const intentScenes: Record<string, JudgeScene> = {
  TRANSFER: "TRANSFER", TRANSFER_SCHEDULE: "TRANSFER", AA_REQUEST: "TRANSFER", FOREX_TRANSFER: "TRANSFER",
  BILL_ANALYSIS: "BILLS", WEALTH: "WEALTH", CARD_MANAGEMENT: "CARDS",
  SUBSCRIPTION_MANAGEMENT: "SUBSCRIPTIONS", CROSS_SCENARIO: "CROSS",
};
export function sceneForRegisteredIntent(intent: unknown): JudgeScene | null {
  return typeof intent === "string" ? intentScenes[intent] || null : null;
}
export function filterEvidenceTasks(rows: readonly AdminTask[], scene: JudgeScene | "ALL", state: EvidenceStateFilter): AdminTask[] {
  return rows.filter(row => (scene === "ALL" || sceneForRegisteredIntent(row.intent_type) === scene) &&
    (state === "ALL" || state === "PENDING" && ["NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION", "WAITING"].includes(row.state) || row.state === state));
}
const object = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const whole = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
const token = (value: unknown): string | null => typeof value === "string" && /^[A-Za-z0-9_.:/ -]{1,100}$/.test(value) &&
  !/sk-|bearer|password|secret|token|\[REDACTED/i.test(value) ? value : null;
const bool = (value: unknown): boolean | null => typeof value === "boolean" ? value : null;
const identifier = (value: unknown): string | null => typeof value === "string" && /^[A-Za-z0-9_.:/ -]{1,120}$/.test(value)
  && !/(?:^|[^A-Za-z0-9_])sk-|bearer|password|secret|token|\[REDACTED/i.test(value) ? value : null;
const digest = (value: unknown): string | null => typeof value === "string" && /^[a-f0-9]{64}$/.test(value) ? value : null;
const recordedTime = (value: unknown): string | null => typeof value === "string" && value.length <= 40
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value)) ? value : null;
const displayText = (value: unknown): string | null => typeof value === "string" && value.length > 0 && value.length <= 200
  && !/[\u0000-\u001f\u007f]|sk-|bearer|password|secret|token|credential|passcode|api[_ -]?key|\[REDACTED|验证码|校验码|动态码|密码|密钥|口令|\bOTP\b/i.test(value) ? value : null;
const release = (value: unknown): string | null => typeof value === "string"
  && /^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}$/.test(value) ? value : null;

export interface JudgeScopeField { label: string; value: string; }
const scopeLabels: Record<string, string> = {
  source_account_id: "付款账户", payee_id: "收款对象标识", payee_name: "收款对象", payee_last4: "收款尾号", transfer_channel: "转账渠道",
  recipient_username: "收款用户名", recipient_account_id: "收款账户", card_id: "卡片标识", card_last4: "卡片尾号", card_number: "所选卡号", display_last4: "展示尾号",
  subscription_id: "订阅标识", merchant: "商户", source_type: "来源类型", product_id: "产品标识", product_name: "产品", action: "批准动作", order_type: "订单类型",
  amount_minor: "批准金额", limit_minor: "批准限额", units: "份数", unit_price_minor: "单价", currency: "批准币种", source_currency: "扣款币种", target_currency: "收款币种",
  due_at: "预约时间", max_amount_minor: "金额上限", account_id: "账户标识", total_minor: "合计金额", clock_basis: "计时依据", time_zone: "时区", remainder_rule: "余数规则",
  quote_id: "报价标识", quote_scope_hash: "报价范围哈希", requested_currency: "请求币种", requested_amount_minor: "请求金额", debit_minor: "批准扣款", credit_minor: "批准收款",
  fee_minor: "批准费用", rounding: "舍入规则", goal_id: "目标标识", goal_name: "目标名称", target_minor: "目标金额", grantee_user_id: "受托人标识", grantee_name: "受托人",
  owner_user_id: "委托人标识", maximum_amount_minor: "最大金额", expires_at: "有效至", expires_days: "有效天数", limit_period: "限额周期", delegation_id: "委托标识",
  proposal_kind: "提案类型", behavior_id: "行为标识", behavior_name: "行为", reward_id: "权益标识", reward_name: "权益", points: "积分", cost_points: "所需积分",
  quantity: "数量", estimated_avoided_grams: "预计减排克数", proposal_id: "提案标识", alias_name: "别名", recipient_login_account_id: "收款登录账户",
  recipient_bank_account_id: "收款银行账户", requested_reference: "本次引用", card_version: "卡片数据版本", account_version: "账户数据版本", username: "用户名",
  account_ref: "账户引用", login_id: "登录标识", user_id: "用户标识", sandbox_id: "账套标识", currency_source: "币种依据", observed_source_currency: "观察到的账户币种（不替代批准币种）",
};
const nestedScopeLabels: Record<string, string> = { args: "批准参数", alias_binding: "别名绑定", recipient_context: "收款对象上下文", source_card_context: "付款卡上下文", source: "扣款范围", target: "收款范围" };
const listScopeLabels: Record<string, string> = { participants: "参加者", scopes: "委托范围" };
const numericScopeFields = new Set(["units", "expires_days", "points", "cost_points", "quantity", "estimated_avoided_grams", "card_version", "account_version"]);
function scopeFields(value: unknown, prefix = "", depth = 0, inheritedCurrency: string | null = null): JudgeScopeField[] | null {
  const data = object(value);
  if (!data || depth > 3 || Object.keys(data).length > 100) return null;
  const denomination = (key: string) => typeof data[key] === "string" && /^[A-Z]{3}$/.test(data[key] as string) ? data[key] as string : null;
  const currency = denomination("currency") || inheritedCurrency;
  const fields: JudgeScopeField[] = [];
  for (const [key, raw] of Object.entries(data)) {
    if (Object.hasOwn(scopeLabels, key)) {
      const label = `${prefix}${scopeLabels[key]}`;
      if (raw === null) { fields.push({ label, value: "未记录" }); continue; }
      if (key.endsWith("_minor")) {
        const amount = whole(raw);
        if (amount === null) return null;
        const unit = key === "requested_amount_minor" ? denomination("requested_currency")
          : key === "debit_minor" || key === "fee_minor" ? denomination("source_currency")
          : key === "credit_minor" ? denomination("target_currency") : currency;
        fields.push({ label, value: unit === "CNY" || unit === "USD" ? `${exactMajorAmount(amount)} ${unit}`
          : `${amount}（整数最小单位；${unit ? `${unit} 的计价位数未核验` : "币种未记录，不推定 CNY"}）` });
      } else if (numericScopeFields.has(key)) {
        const number = whole(raw); if (number === null) return null;
        fields.push({ label, value: String(number) });
      } else {
        const text = displayText(raw); if (text === null) return null;
        if ((key === "currency" || key.endsWith("_currency")) && !/^[A-Z]{3}$/.test(text)) return null;
        if (key === "quote_scope_hash" && !digest(text)) return null;
        fields.push({ label, value: text });
      }
    } else if (Object.hasOwn(nestedScopeLabels, key)) {
      const child = scopeFields(raw, `${prefix}${nestedScopeLabels[key]} · `, depth + 1, currency);
      if (!child) return null; fields.push(...child);
    } else if (Object.hasOwn(listScopeLabels, key)) {
      if (!Array.isArray(raw) || raw.length > 100) return null;
      for (const [index, item] of raw.entries()) {
        const label = `${prefix}${listScopeLabels[key]} ${index + 1}`;
        if (object(item)) { const child = scopeFields(item, `${label} · `, depth + 1, currency); if (!child) return null; fields.push(...child); }
        else { const text = displayText(item); if (!text || text.length > 100) return null; fields.push({ label, value: text }); }
      }
    } else return null; // The server allowlist is the only source of display fields.
  }
  return fields.length <= 300 ? fields : null;
}
const provenanceModules = new Set(["execution_provenance", "runtime", "services", "tools", "authentication", "workflow", "planning", "posting", "internal_banking", "transfers", "cards", "wealth", "subscriptions", "fx_domain", "fx_banking", "banking_extensions", "contact_domain", "contacts", "extensions"].map(name => `zhenxing.${name}`));
const softwareFields = new Set(["package", "release", "release_source", "module_hash_algorithm", "module_hashes", "module_hash_scope", "runtime_fingerprint"]);
export interface JudgeSoftwareEvidence {
  release: string | null;
  runtimeFingerprint: string;
  moduleHashes: { module: string; hash: string }[];
}
function softwareEvidence(value: unknown): JudgeSoftwareEvidence | null {
  const data = object(value), hashes = object(data?.module_hashes), fingerprint = digest(data?.runtime_fingerprint);
  if (!data || Object.keys(data).some(key => !softwareFields.has(key)) || data.package !== "zhenxing-agent" || data.release_source !== "INSTALLED_DISTRIBUTION_METADATA"
    || data.module_hash_algorithm !== "SHA256_MARSHALLED_LOADED_CODE_V1" || data.module_hash_scope !== "LISTED_LOADED_MODULE_CODE_ONLY"
    || !hashes || !Object.keys(hashes).length || !fingerprint || (data.release !== null && !release(data.release))) return null;
  const moduleHashes = Object.entries(hashes).map(([module, hash]) => ({ module, hash: digest(hash) }));
  if (moduleHashes.some(row => !provenanceModules.has(row.module) || !row.hash)) return null;
  return { release: release(data.release), runtimeFingerprint: fingerprint, moduleHashes: moduleHashes as { module: string; hash: string }[] };
}
type ProvenanceStatus = "RECORDED" | "NOT_RECORDED" | "UNVERIFIED";
export interface JudgeAuthorizationEvidence {
  status: ProvenanceStatus; eventSeq: number | null; recordedAt: string | null;
  operationId: string | null; action: string | null; planVersion: number | null; expectedTaskVersion: number | null;
  scopeHash: string | null; planHash: string | null; viewHash: string | null; fields: JudgeScopeField[];
}
export interface JudgeExecutionEvidence {
  status: ProvenanceStatus; eventSeq: number | null; recordedAt: string | null;
  operationId: string | null; action: string | null; planVersion: number | null;
  stage: "STARTED" | "FAILED_ATTEMPT" | null; attempt: number | null; dispatchStarted: boolean | null;
  jobId: string | null; software: JudgeSoftwareEvidence | null;
}
export interface JudgeEvidenceSampling<T> {
  records: T[]; total: number | null; returned: number | null; truncated: boolean | null; samplingConflict: boolean;
}
function provenanceSampling<T>(value: unknown, totalValue: unknown, returnedValue: unknown, truncatedValue: unknown, parse: (row: unknown) => T): JudgeEvidenceSampling<T> {
  const rows = Array.isArray(value) ? value : null, total = whole(totalValue), returned = whole(returnedValue), truncated = bool(truncatedValue);
  const sequences = rows?.flatMap(row => { const seq = whole(object(row)?.event_seq); return seq === null ? [] : [seq]; }) || [];
  const samplingConflict = value !== undefined && !rows || rows !== null && rows.length > 100
    || new Set(sequences).size !== sequences.length
    || totalValue !== undefined && total === null || returnedValue !== undefined && returned === null || truncatedValue !== undefined && truncated === null
    || rows !== null && returned !== null && rows.length !== returned || total !== null && returned !== null && (total < returned || truncated !== null && truncated !== (total > returned));
  return { records: rows ? rows.slice(0, 100).map(parse) : [], total, returned, truncated, samplingConflict };
}
function provenanceBinding(row: Record<string, unknown>, value: AdminTrace, eventType: string): boolean {
  const seq = whole(row.event_seq), version = whole(row.plan_version), operationId = identifier(row.operation_id), action = token(row.action_kind), child = identifier(row.child_task_id);
  if (seq === null || seq < 1 || !recordedTime(row.recorded_at) || row.event_type !== eventType || row.settlement_asserted !== false
    || row.schema_version !== "execution-provenance-v1" || version === null || version < 1 || !operationId || !action || !child) return false;
  const events = Array.isArray(value.events) ? value.events : [], operations = Array.isArray(value.operations) ? value.operations : [];
  if (!events.some(event => event?.seq === seq && event.event_type === eventType)
    || !operations.some(operation => operation?.operation_id === operationId && operation.action_kind === action && operation.plan_version === version)) return false;
  const nodes = Array.isArray(value.runtime_trace?.nodes) ? value.runtime_trace.nodes : [];
  const node = nodes.find(item => item?.operation_id === operationId);
  return (!node || node.action === action && (node.child_task_id === undefined || node.child_task_id === child))
    && (whole(value.runtime_trace?.plan_version) ?? -1) >= version;
}
function authorizationEvidence(raw: unknown, value: AdminTrace): JudgeAuthorizationEvidence {
  const row = object(raw);
  const empty: JudgeAuthorizationEvidence = { status: "UNVERIFIED", eventSeq: whole(row?.event_seq), recordedAt: recordedTime(row?.recorded_at), operationId: null,
    action: null, planVersion: null, expectedTaskVersion: null, scopeHash: null, planHash: null, viewHash: null, fields: [] };
  if (!row) return empty;
  if (row.scope_status === "NOT_RECORDED" && row.scope === null && row.scope_hash === null && row.settlement_asserted === false) return { ...empty, status: "NOT_RECORDED" };
  const fields = scopeFields(row.scope), expectedVersion = whole(row.expected_task_version);
  if (row.scope_status !== "RECORDED" || row.stage !== "ACCEPTED_USER_CONFIRMATION" || row.scope_coverage !== "ALLOWLIST_ONLY"
    || !provenanceBinding(row, value, "ACTION_AUTHORIZED") || !identifier(row.node_key) || !digest(row.scope_hash)
    || row.target_id !== undefined && !identifier(row.target_id)
    || !digest(row.plan_hash) || !digest(row.view_hash) || expectedVersion === null || expectedVersion < 1 || !fields) return empty;
  return { ...empty, status: "RECORDED", operationId: identifier(row.operation_id), action: token(row.action_kind), planVersion: whole(row.plan_version),
    expectedTaskVersion: expectedVersion, scopeHash: digest(row.scope_hash), planHash: digest(row.plan_hash), viewHash: digest(row.view_hash), fields };
}
function executionEvidence(raw: unknown, value: AdminTrace): JudgeExecutionEvidence {
  const row = object(raw);
  const empty: JudgeExecutionEvidence = { status: "UNVERIFIED", eventSeq: whole(row?.event_seq), recordedAt: recordedTime(row?.recorded_at),
    operationId: null, action: null, planVersion: null, stage: null, attempt: null, dispatchStarted: null, jobId: null, software: null };
  if (!row) return empty;
  if (row.software_status === "NOT_RECORDED" && row.software === null && row.settlement_asserted === false) return { ...empty, status: "NOT_RECORDED" };
  const stage = row.stage === "STARTED" ? "STARTED" : row.stage === "FAILED_ATTEMPT" ? "FAILED_ATTEMPT" : null;
  const software = softwareEvidence(row.software), attempt = whole(row.attempt), dispatchStarted = bool(row.dispatch_started);
  if (row.software_status !== "RECORDED" || !stage || !software || attempt === null || attempt < 1 || dispatchStarted === null
    || stage === "STARTED" && dispatchStarted !== true || !identifier(row.job_id)
    || !provenanceBinding(row, value, stage === "STARTED" ? "ACTION_STARTED" : "WORKFLOW_JOB_FAILED")) return empty;
  return { ...empty, status: "RECORDED", operationId: identifier(row.operation_id), action: token(row.action_kind), planVersion: whole(row.plan_version),
    stage, attempt, dispatchStarted, jobId: identifier(row.job_id), software };
}

export interface JudgeRouteObservation {
  planVersion: number | null;
  purpose: string | null;
  provider: string | null;
  model: string | null;
  httpIssued: number | null;
  httpRecordsUnknown: number;
  attemptRecords: number;
  countScope: "RECORDED_ATTEMPTS_ONLY" | "NOT_RECORDED";
}
export function routeObservation(value: unknown, planVersion: unknown): JudgeRouteObservation {
  const data = object(value);
  const attempts = Array.isArray(data?.attempts) ? data.attempts : null;
  const unknown = attempts?.filter(attempt => bool(object(attempt)?.http_issued) === null).length || 0;
  return {
    planVersion: whole(planVersion), purpose: token(data?.purpose),
    provider: token(data?.actual_provider), model: token(data?.actual_model),
    httpIssued: attempts && unknown === 0 ? attempts.filter(attempt => object(attempt)?.http_issued === true).length : null,
    httpRecordsUnknown: unknown, attemptRecords: attempts?.length || 0,
    countScope: attempts ? "RECORDED_ATTEMPTS_ONLY" : "NOT_RECORDED",
  };
}
export interface JudgeEvidenceSummary {
  identityMatches: boolean;
  projectionReadOnly: boolean;
  versionConflict: boolean;
  taskVersion: number | null;
  planVersion: number | null;
  executionSoftwareVersion: string | null;
  executionReleases: string[];
  executionVersionMixed: boolean;
  authorizationEvidence: JudgeEvidenceSampling<JudgeAuthorizationEvidence>;
  executionEvidence: JudgeEvidenceSampling<JudgeExecutionEvidence>;
  state: string;
  outcome: "VERIFIED_QUERY" | "RECORDED_BUSINESS_RESULT" | "RECORDED_PROPOSAL" | "UNKNOWN" | "UNVERIFIED";
  auditChainValid: boolean | null;
  route: JudgeRouteObservation;
  historicalRoutes: JudgeRouteObservation[];
  policyRecords: { node: string; decision: string; policyVersion: string | null }[];
  authorizationEventRecords: number;
  approvedScopeAvailable: boolean;
  reconciliation: { node: string; balanced: boolean | null; recordedErrorCount: number | null }[];
  nodeCount: number;
  successfulNodeCount: number;
  unknownNodeCount: number;
  toolRecords: number;
  toolTotal: number | null;
  toolsTruncated: boolean | null;
  snapshotsTotal: number | null;
  snapshotsTruncated: boolean | null;
}
const taskStates = new Set(["QUEUED", "UNDERSTANDING", "PLANNING", "PLANNED", "POLICY_CHECKED", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION",
  "WAITING", "AUTHORIZED", "EXECUTING", "COMPENSATING", "SUCCEEDED", "PARTIAL", "FAILED", "CLOSED", "UNKNOWN", "CANCELLED", "CANCELED"]);
/** A curated summary of server facts. A client hash/state never certifies a bank debit. */
export function summarizeJudgeEvidence(value: AdminTrace, expectedTaskId: string): JudgeEvidenceSummary {
  const trace = value.runtime_trace;
  const identityMatches = !!identifier(expectedTaskId) && value.task_id === expectedTaskId && trace?.task_id === expectedTaskId;
  const versionConflict = !!trace && (whole(value.version) === null || whole(trace.version) === null || value.version !== trace.version || value.state !== trace.state || !taskStates.has(trace.state));
  const projectionReadOnly = value.read_only === true && value.bank_confirmation_permitted === false;
  const trusted = identityMatches && !versionConflict && projectionReadOnly;
  const rawNodes = trusted && Array.isArray(trace?.nodes) ? trace.nodes : [];
  const nodes = rawNodes.filter(node => object(node) && identifier(node.node_id) && token(node.action) && token(node.status));
  const outcome = trace?.outcome;
  const verified = trusted && trace?.state === "SUCCEEDED" && outcome?.state === "SUCCEEDED" && outcome.verified === true &&
    !trace.pending_confirmation && nodes.length > 0 && nodes.length === rawNodes.length && nodes.every(node => node.status === "SUCCEEDED" && !node.error && !node.result?.error);
  const raw = object(trace);
  const decisions = Array.isArray(trace?.model_decisions) ? trace.model_decisions : trace?.model_decisions ? [trace.model_decisions] : [];
  const policyRecords = nodes.flatMap(node => {
    const policies = node.evidence?.policy;
    return Array.isArray(policies) ? policies.flatMap(policy => {
      const item = object(policy), decision = token(item?.decision);
      return decision ? [{node: node.node_id, decision, policyVersion: token(item?.version)}] : [];
    }) : [];
  });
  const reconciliation = nodes.flatMap(node => {
    const result = object(node.result), check = object(result?.reconciliation);
    return check ? [{node: node.node_id, balanced: bool(check.balanced), recordedErrorCount: Array.isArray(check.errors) ? check.errors.length : null}] : [];
  });
  const authorizations = provenanceSampling(trusted ? value.authorization_evidence : undefined, trusted ? value.authorization_evidence_total : undefined,
    trusted ? value.authorization_evidence_returned : undefined, trusted ? value.authorization_evidence_truncated : undefined, row => authorizationEvidence(row, value));
  const executions = provenanceSampling(trusted ? value.execution_evidence : undefined, trusted ? value.execution_evidence_total : undefined,
    trusted ? value.execution_evidence_returned : undefined, trusted ? value.execution_evidence_truncated : undefined, row => executionEvidence(row, value));
  if (authorizations.samplingConflict) authorizations.records = authorizations.records.map(row => ({ ...row, status: "UNVERIFIED", fields: [] }));
  if (executions.samplingConflict) executions.records = executions.records.map(row => ({ ...row, status: "UNVERIFIED", software: null }));
  const executionReleases = [...new Set(executions.records.flatMap(row => row.status === "RECORDED" && row.software?.release ? [row.software.release] : []))];
  const allReleasesRecorded = executions.records.length > 0 && executions.records.every(row => row.status === "RECORDED" && row.software?.release);
  return {
    identityMatches, versionConflict, projectionReadOnly, taskVersion: whole(value.version), planVersion: whole(trace?.plan_version),
    executionSoftwareVersion: allReleasesRecorded && executionReleases.length === 1 ? executionReleases[0] : null,
    executionReleases, executionVersionMixed: executionReleases.length > 1, authorizationEvidence: authorizations, executionEvidence: executions,
    state: trusted ? trace!.state : "UNVERIFIED", outcome: trusted && trace?.state === "UNKNOWN" ? "UNKNOWN" : !verified ? "UNVERIFIED" :
      outcome!.kind === "QUERY_RESULT" ? "VERIFIED_QUERY" : outcome!.kind === "BUSINESS_RECEIPT" ? "RECORDED_BUSINESS_RESULT" : outcome!.kind === "PROPOSAL_SAVED" ? "RECORDED_PROPOSAL" : "UNVERIFIED",
    auditChainValid: trusted ? bool(raw?.audit_chain_valid) : null,
    route: routeObservation(trusted ? trace?.planning_diagnostics : null, trusted ? trace?.plan_version : null),
    historicalRoutes: trusted ? decisions.map(decision => { const data = object(decision); return routeObservation(data?.planning_diagnostics, data?.plan_version); }) : [],
    policyRecords, authorizationEventRecords: trusted && Array.isArray(value.events) ? value.events.filter(event => event?.event_type === "ACTION_AUTHORIZED").length : 0,
    approvedScopeAvailable: authorizations.records.some(row => row.status === "RECORDED"), reconciliation,
    nodeCount: nodes.length, successfulNodeCount: nodes.filter(node => node.status === "SUCCEEDED").length,
    unknownNodeCount: nodes.filter(node => node.status === "UNKNOWN").length,
    toolRecords: trusted && Array.isArray(value.tool_calls) ? value.tool_calls.length : 0,
    toolTotal: trusted ? whole(value.tool_calls_total) : null, toolsTruncated: trusted ? bool(value.tool_calls_truncated) : null,
    snapshotsTotal: trusted ? whole(value.plan_snapshots_total) : null, snapshotsTruncated: trusted ? bool(value.plan_snapshots_truncated) : null,
  };
}
export const judgeEvidenceOutcomeLabels: Record<JudgeEvidenceSummary["outcome"], string> = {
  VERIFIED_QUERY: "查询结果已记录核验（不是资金操作）", RECORDED_BUSINESS_RESULT: "业务执行结果已记录（仍需核对具体业务核验）",
  RECORDED_PROPOSAL: "提案已保存（不是代客办理）", UNKNOWN: "执行结果 UNKNOWN · 不可重发付款", UNVERIFIED: "尚无可确认的完整结果",
};
export function safeHealthVersion(value: unknown): string | null {
  const version = object(value)?.version;
  return typeof version === "string" && /^\d+\.\d+\.\d+$/.test(version) ? version : null;
}
export function policySummaryData(summary: JudgeEvidenceSummary): JsonValue {
  return summary.policyRecords.map(row => ({node: row.node, decision: row.decision, policy_version: row.policyVersion}));
}
