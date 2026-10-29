export const blindAcceptanceTasks = [
  { id: "FIND_SUBSCRIPTIONS", label: "找出正在使用的订阅", instruction: "从对话入口自己提出查询。找到两项服务的状态和下一步；不要取消。", expected: "结果直接可见，能区分记录日期与实际扣费，不必理解工具或工作流。" },
  { id: "ASK_SPENDING_CHANGE", label: "理解一段时间的支出变化", instruction: "自己问为什么某段时间花费更多。选择账套已有资料的月份；遇到资料不足时看是否说明。", expected: "能区分消费、资金搬移与未知，不把不完整资料误读为零。" },
  { id: "REVISE_TRANSFER", label: "更正待确认的转账", instruction: "使用分配的专用测试账户和测试收款对象准备一笔转账，再用自己的话改金额。本任务停在核对页，不执行付款。", expected: "新的核对范围清楚；旧范围不能继续确认，聊天里的“好的”不授权。" },
  { id: "UNDERSTAND_UNKNOWN", label: "判断不确定的办理结果", instruction: "在提前分配的 UNKNOWN 只读任务中判断是否需要再次付款。不要构造故障，不要重发。", expected: "明确知道先查原请求而非重复付款；如没有 UNKNOWN 样本记未完成，不造成功。" },
  { id: "ENGLISH_QUERY", label: "用自己的英文查询", instruction: "不复制示例，用自己的英文查询订阅或卡片状态，再切换一次界面语言。不要挂失、补卡或锁卡。", expected: "原始标识、金额和币种不改变，主要结论与下一步可理解。" },
  { id: "LONG_RESULT", label: "阅读长结果并继续交流", instruction: "阅读有多项结果的已分配对话，向上查看旧内容，再返回新结果并输入一个新问题。测试只读问题即可。", expected: "不抢走阅读位置，输入不中断，结果出现方式舒服；键盘同样可操作。" },
] as const;
export type BlindTaskId = typeof blindAcceptanceTasks[number]["id"];
export type AcceptanceParticipant = "DEVELOPER_SELF_TEST" | "AGENT_OR_FIXTURE" | "NONDEVELOPER_SELF_REPORTED";
export type AcceptanceResult = "COMPLETED" | "PARTIAL" | "NOT_COMPLETED";
export type AcceptanceHelp = "NONE" | "PROMPT_ONLY" | "HANDS_ON";
export interface AcceptanceMeasurement {
  task_id: BlindTaskId; elapsed_ms: number; result: AcceptanceResult; assistance: AcceptanceHelp;
  interaction_error_observed: boolean; clarity: 1 | 2 | 3 | 4 | 5 | null;
}
export interface AcceptanceAttempt extends AcceptanceMeasurement { attempt_sequence: number; }
export const MAX_ACCEPTANCE_ATTEMPTS = 18;
export interface AcceptanceSession {
  schema_version: "anonymous-acceptance-v1";
  participant_nonce: string;
  participant_kind: AcceptanceParticipant;
  participant_claim_independently_verified: false;
  current_display_version: string;
  loaded_ui_entry?: string | null;
  loaded_ui_entry_scope?: "CLIENT_DOM_ENTRY_NOT_VERIFIED_FULL_BUILD";
  measurements: AcceptanceMeasurement[];
  attempts?: AcceptanceAttempt[];
}
const taskIds = new Set<string>(blindAcceptanceTasks.map(task => task.id));
const kinds = new Set<string>(["DEVELOPER_SELF_TEST", "AGENT_OR_FIXTURE", "NONDEVELOPER_SELF_REPORTED"]);
const results = new Set<string>(["COMPLETED", "PARTIAL", "NOT_COMPLETED"]);
const assistance = new Set<string>(["NONE", "PROMPT_ONLY", "HANDS_ON"]);
/** Public route observation only: not a URL, identity or verified build digest. */
export function safeLoadedUiEntry(value: unknown): string | null {
  return typeof value === "string" && /^\/ui\/assets\/index-[A-Za-z0-9_-]{1,80}\.js$/.test(value)
    && !/sk-|token|password|secret|credential|bearer|cookie|username|account|session/i.test(value) ? value : null;
}
export function loadedUiEntryFromModulePaths(paths: readonly unknown[]): string | null {
  const entries = [...new Set(paths.map(safeLoadedUiEntry).filter((entry): entry is string => entry !== null))];
  return entries.length === 1 ? entries[0] : null;
}
export function acceptanceTaskCoverage(session: Pick<AcceptanceSession, "measurements">) {
  const seen = new Set<string>();
  const counts = { completed: 0, partial: 0, not_completed: 0, unattempted: blindAcceptanceTasks.length };
  if (!Array.isArray(session.measurements) || session.measurements.length > blindAcceptanceTasks.length) throw new Error("ACCEPTANCE_MEASUREMENTS_INVALID");
  for (const row of session.measurements) {
    if (!row || !taskIds.has(row.task_id) || !results.has(row.result)) throw new Error("ACCEPTANCE_MEASUREMENT_INVALID");
    if (seen.has(row.task_id)) throw new Error("ACCEPTANCE_DUPLICATE_TASK");
    seen.add(row.task_id);
    if (row.result === "COMPLETED") counts.completed += 1;
    else if (row.result === "PARTIAL") counts.partial += 1;
    else counts.not_completed += 1;
    counts.unattempted -= 1;
  }
  return { task_denominator: blindAcceptanceTasks.length, recorded_unique_tasks: seen.size, ...counts,
    scope: "ALL_SIX_TASKS_NOT_PARTICIPANT_COUNT" as const };
}
/** The only persisted artifact is an explicit download; never includes user text or bank identities. */
export function newAcceptanceSession(nonce: string, kind: AcceptanceParticipant, version: string, loadedUiEntry: unknown = null): AcceptanceSession {
  if (typeof nonce !== "string" || typeof kind !== "string" || typeof version !== "string" || !/^[a-f0-9]{32}$/.test(nonce) || !kinds.has(kind) || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error("ACCEPTANCE_CONTEXT_INVALID");
  return {schema_version: "anonymous-acceptance-v1", participant_nonce: nonce, participant_kind: kind,
    participant_claim_independently_verified: false, current_display_version: version,
    loaded_ui_entry: safeLoadedUiEntry(loadedUiEntry), loaded_ui_entry_scope: "CLIENT_DOM_ENTRY_NOT_VERIFIED_FULL_BUILD", measurements: [], attempts: []};
}
function safeMeasurement(measurement: AcceptanceMeasurement): AcceptanceMeasurement {
  if (!measurement || typeof measurement !== "object") throw new Error("ACCEPTANCE_MEASUREMENT_INVALID");
  if (!taskIds.has(measurement.task_id) || !Number.isSafeInteger(measurement.elapsed_ms) || measurement.elapsed_ms < 0 || measurement.elapsed_ms > 86_400_000 ||
      !results.has(measurement.result) || !assistance.has(measurement.assistance) || typeof measurement.interaction_error_observed !== "boolean" ||
      !(measurement.clarity === null || Number.isInteger(measurement.clarity) && measurement.clarity >= 1 && measurement.clarity <= 5)) throw new Error("ACCEPTANCE_MEASUREMENT_INVALID");
  return {task_id: measurement.task_id, elapsed_ms: measurement.elapsed_ms, result: measurement.result, assistance: measurement.assistance,
    interaction_error_observed: measurement.interaction_error_observed, clarity: measurement.clarity};
}
function safeAttempts(value: unknown): AcceptanceAttempt[] {
  if (value === undefined) return []; // Legacy exports retain latest measurements, not invented history.
  if (!Array.isArray(value) || value.length > MAX_ACCEPTANCE_ATTEMPTS) throw new Error("ACCEPTANCE_ATTEMPTS_INVALID");
  return value.map((row,index)=> {
    if (!row || row.attempt_sequence !== index + 1) throw new Error("ACCEPTANCE_ATTEMPT_SEQUENCE_INVALID");
    return {...safeMeasurement(row), attempt_sequence:index + 1};
  });
}
export function acceptanceAttemptSummary(session: Pick<AcceptanceSession,"attempts"|"measurements">) {
  const attempts=safeAttempts(session.attempts);
  const latest=new Map(attempts.map(row=>[row.task_id,safeMeasurement(row)]));
  const matchesLatest=Array.isArray(session.measurements) && session.measurements.length === latest.size
    && session.measurements.every(row=>JSON.stringify(safeMeasurement(row))===JSON.stringify(latest.get(row.task_id)));
  return {recorded_attempts:attempts.length, attempt_limit:MAX_ACCEPTANCE_ATTEMPTS,
    unsuccessful_attempts:attempts.filter(row=>row.result!=="COMPLETED").length,
    assisted_attempts:attempts.filter(row=>row.assistance!=="NONE").length,
    interaction_error_attempts:attempts.filter(row=>row.interaction_error_observed).length,
    unassisted_successful_attempts:attempts.filter(row=>row.result==="COMPLETED"&&row.assistance==="NONE"&&!row.interaction_error_observed).length,
    latest_measurements_match_journal:matchesLatest,
    legacy_or_missing_history:session.attempts===undefined || !matchesLatest,
    scope:"LOCAL_OBSERVER_RECORDED_ATTEMPTS_NOT_INDEPENDENT_PARTICIPANTS" as const};
}
export function recordAcceptanceMeasurement(session: AcceptanceSession, measurement: AcceptanceMeasurement): AcceptanceSession {
  const safe=safeMeasurement(measurement), prior=safeAttempts(session.attempts);
  if (prior.length>=MAX_ACCEPTANCE_ATTEMPTS) throw new Error("ACCEPTANCE_ATTEMPT_LIMIT_REACHED");
  acceptanceTaskCoverage(session);
  return {...newAcceptanceSession(session.participant_nonce, session.participant_kind, session.current_display_version, session.loaded_ui_entry),
    measurements: [...session.measurements.filter(row => row.task_id !== safe.task_id), safe],
    attempts:[...prior,{...safe,attempt_sequence:prior.length + 1}]};
}
export function exportAcceptanceSession(session: AcceptanceSession): string {
  // Rebuild every field from an allowlist. Extra data injected by callers cannot leak into export.
  let safe = newAcceptanceSession(session.participant_nonce, session.participant_kind, session.current_display_version, session.loaded_ui_entry);
  if (!Array.isArray(session.measurements) || session.measurements.length > blindAcceptanceTasks.length) throw new Error("ACCEPTANCE_MEASUREMENTS_INVALID");
  acceptanceTaskCoverage(session); // Imported duplicates are rejected, never silently collapsed on export.
  safe.measurements=session.measurements.map(safeMeasurement);
  safe.attempts=safeAttempts(session.attempts);
  return JSON.stringify({...safe, task_coverage: acceptanceTaskCoverage(safe), data_scope: "EXPLICIT_LOCAL_EXPORT_ONLY", independently_verified_participant_count: 0,
    attempt_history:acceptanceAttemptSummary({...safe,attempts:session.attempts===undefined?undefined:safe.attempts}),
    participant_count_scope: "THIS_LOCAL_SESSION_ONLY", nondeveloper_participation_claim: safe.participant_kind === "NONDEVELOPER_SELF_REPORTED" ? "SELF_REPORTED_NOT_VERIFIED" : "NONE",
    note: "开发自测、代理与fixtures不计作真人验收；此文件不能证明独立真人参与，也不是赛事评分。"}, null, 2);
}
