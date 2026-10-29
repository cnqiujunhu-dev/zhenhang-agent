import type { JsonValue } from "../types";

type Count = number | null;
export type SemanticShadowObservation = {
  schema_version: string;
  record_count?: Count; sampled_records?: Count; truncated?: boolean;
  by_status?: Record<string, unknown>; coverage_reasons?: Record<string, unknown>;
  verified_local_sdk_inferences?: Count; invalid_or_stale_ready_records?: Count;
  agreement?: Record<string, unknown>;
  sdk_latency_ms?: { p50?: Count; p95?: Count; samples?: Count };
  adoption_enabled?: boolean; observed_adoptions?: Count;
  call_savings?: Count; latency_savings_ms?: Count;
};
export type PurposeObservation = {
  observation_count: number; record_count: number; sampled_records: number; truncated: boolean; missing_diagnostics: number;
  http_calls: { observed_sum: Count; complete_total: Count; known_lower_bound: Count; known_observations: number;
    unknown_observations: number; zero_call_observations: number };
  adoption: { adopted_observations: number; rejected_observations: number; not_attempted_observations: number;
    unknown_observations: number; known_attempted_denominator: number; adoption_rate: number | null; rate_is_accuracy: false };
  diagnostic_latency_ms: { p50: Count; p95: Count; sample_count: number; unknown_observations: number; scope: string; minimum_percentile_samples?: number };
  standard_read_compiler_zero_http_observations?: number;
  verified_local_sdk_inferences?: number;
  semantic_shadow?: SemanticShadowObservation;
  fallback?: {fallback_observations:number;nonfallback_observations:number;unknown_observations:number};
  conflicting_diagnostic_observations?:number;
};
export type PurposeObservationLedger = {
  schema_version: string;
  by_purpose: Partial<Record<"understanding" | "planning" | "consultation" | "narration" | "vision" | "laya", PurposeObservation>>;
  scope?:string;whole_client_journey_complete?:boolean;nested_diagnostics_counted_once?:boolean;
  customer_journey_latency_ms?: { p50: Count; p95: Count; sample_count: number; scope: string; server_latency_is_customer_journey: false };
};
const purposes = [ ["understanding", "统一需求理解"], ["consultation", "咨询与业务解释"], ["narration", "工具后结果叙述"],
  ["planning", "历史规划 / 确定性编译"], ["vision", "图片资料"], ["laya", "Laya（历史规划路径）"] ] as const;
export function observationNumber(value: unknown): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString("zh-CN") : "未记录";
}
function latency(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${Math.round(value)} ms` : "未记录";
}
function percentile(row: PurposeObservation, key: "p50" | "p95") {
  const metric = row.diagnostic_latency_ms;
  if (metric?.scope !== "SERVER_PURPOSE_DIAGNOSTIC_NOT_CLIENT_JOURNEY") return "未记录";
  const threshold = metric.minimum_percentile_samples ?? 3;
  if (observationNumber(threshold) === "未记录" || threshold < 1
    || observationNumber(metric.sample_count) === "未记录" || metric.sample_count < threshold) return "样本不足";
  return latency(metric[key]);
}
function shadowCounts(values: unknown, keys: readonly string[]) {
  if (!values || typeof values !== "object" || Array.isArray(values)) return "未记录";
  const counts = values as Record<string, unknown>;
  let total = 0;
  // Server Counter maps are sparse: an absent key inside a present map means
  // zero occurrences. A missing map or malformed recorded count stays unknown.
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(counts, key)) continue;
    const value = counts[key];
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return "未记录";
    total += value;
    if (!Number.isSafeInteger(total)) return "未记录";
  }
  return observationNumber(total);
}
function shadowPercentile(row: SemanticShadowObservation, key: "p50" | "p95") {
  const samples = row.sdk_latency_ms?.samples;
  if (typeof samples !== "number" || !Number.isSafeInteger(samples) || samples < 0) return "未记录";
  return samples < 3 ? "样本不足" : latency(row.sdk_latency_ms?.[key]);
}
function SemanticShadowMetrics({ data }: { data?: SemanticShadowObservation }) {
  if (!data || data.schema_version !== "semantic-shadow-status-v1") return <section aria-label="当前统一Pro对话旁路">
    <h3 className="admin-section-title">当前统一Pro对话旁路</h3>
    <p className="admin-small-note">本路径尚无有效统计；不能用历史规划记录补零或推断当前运行情况。</p>
  </section>;
  const noAdoption = data.adoption_enabled === false && data.observed_adoptions === 0;
  const noSavingsEvidence = data.call_savings === null && data.latency_savings_ms === null;
  return <section aria-label="当前统一Pro对话旁路">
    <h3 className="admin-section-title">当前统一Pro对话旁路</h3>
    <p className="admin-small-note">已记录 {observationNumber(data.record_count)} 轮 · 本次取样 {observationNumber(data.sampled_records)} 轮{data.truncated === true ? " · 取样截断，以下仅为样本计数" : data.truncated === false ? "" : " · 取样完整性未记录"}。与历史规划路径分开统计，不合并计算。</p>
    <div className="admin-overview-grid model-metric-grid">
      <div><span>当前有效SDK推理</span><strong>{observationNumber(data.verified_local_sdk_inferences)}</strong><small>来源无效或已过期的就绪记录 {observationNumber(data.invalid_or_stale_ready_records)}</small></div>
      <div><span>待处理 / 不可用</span><strong>{shadowCounts(data.by_status, ["QUEUED", "LEASED", "PENDING"])} / {shadowCounts(data.by_status, ["OFF", "UNAVAILABLE", "ERROR", "TIMED_OUT", "BUSY", "BUDGET_EXCEEDED", "UNKNOWN"])}</strong><small>等待、离线、忙碌或失败不计作成功推理</small></div>
      <div><span>跳过观察</span><strong>{shadowCounts(data.by_status, ["SKIPPED"])}</strong><small>咨询或连续上下文、未覆盖能力 {shadowCounts(data.coverage_reasons, ["SEMANTIC_MODE_NOT_STANDALONE_REQUEST", "CONTEXT_REFERENCES_REQUIRE_PRIMARY", "UNSUPPORTED_CAPABILITY"])} · 预算不足 {shadowCounts(data.coverage_reasons, ["OBSERVER_INPUT_BUDGET"])}</small></div>
    </div>
    <p className="admin-small-note">已验证领域判断 · 一致 / 分歧 / 未能比较：{shadowCounts(data.agreement, ["AGREE"])} / {shadowCounts(data.agreement, ["DISAGREE"])} / {shadowCounts(data.agreement, ["UNKNOWN"])}。分歧仅用于人工复核，不是准确率，也不改变业务动作。</p>
    <p className="admin-small-note">本机SDK耗时 p50 / p95：{shadowPercentile(data, "p50")} / {shadowPercentile(data, "p95")} · {observationNumber(data.sdk_latency_ms?.samples)} 个有效样本（至少3个才显示分位数）。不是客户等待时间。</p>
    <p className="admin-small-note">{noAdoption && noSavingsEvidence ? "当前未采纳Laya判断；未证明省调用或加速收益。" : "采纳或收益证据缺失／不一致，暂不据此认定有效接管或节省。"} 主模型照常理解需求，影子观察不授予执行权。</p>
  </section>;
}
export function PurposeObservationMetrics({data}:{data?: PurposeObservationLedger}) {
  if (!data || data.schema_version !== "purpose-observation-metrics-v1") return <p className="admin-small-note">各目的统计尚未提供；旧规划统计不代表咨询、图片或全旅程调用。</p>;
  return <section className="purpose-observations" aria-label="分目的实际观测">
    <h3 className="admin-section-title">分目的实际观测</h3>
    <p className="admin-small-note">分别读取理解、咨询与工具后叙述的已保存调用见证；同一轮的嵌套副本不重复计算。出站尝试、输出采纳与业务结果分开。作业成功仍可能使用回退说明。</p>
    <p className="admin-small-note">这是已登记用途的持久观测，历史缺失保持未知；不是完整客户端全链路。服务器诊断耗时不是用户输入到可见结果的时间，也不是准确率。</p>
    <div className="admin-table-scroll"><table><thead><tr><th>用途</th><th>已知HTTP尝试 / 完整总数</th><th>已知 / 未知观测</th><th>采纳 / 拒绝 / 未尝试</th><th>回退 / 未回退 / 未知</th><th>已知采纳分母</th><th>服务器诊断 p50 / p95</th></tr></thead><tbody>
      {purposes.map(([key,label]) => {
        const row = data.by_purpose?.[key];
        if (!row) return <tr key={key}><th scope="row">{label}</th><td colSpan={6}>未记录，不能视为零</td></tr>;
        return <tr key={key}><th scope="row">{label}{row.truncated && <small> · 取样截断</small>}</th>
          <td>{observationNumber(row.http_calls?.observed_sum)} / {observationNumber(row.truncated ? null : row.http_calls?.complete_total)}<small> 下界 {observationNumber(row.http_calls?.known_lower_bound)}</small></td>
          <td>{observationNumber(row.http_calls?.known_observations)} / {observationNumber(row.http_calls?.unknown_observations)}<small> 缺诊断 {observationNumber(row.missing_diagnostics)}</small></td>
          <td>{observationNumber(row.adoption?.adopted_observations)} / {observationNumber(row.adoption?.rejected_observations)} / {observationNumber(row.adoption?.not_attempted_observations)}<small> 采用未知 {observationNumber(row.adoption?.unknown_observations)}</small></td>
          <td>{observationNumber(row.fallback?.fallback_observations)} / {observationNumber(row.fallback?.nonfallback_observations)} / {observationNumber(row.fallback?.unknown_observations)}
            {typeof row.conflicting_diagnostic_observations==="number"&&row.conflicting_diagnostic_observations>0&&<small> 证据冲突 {observationNumber(row.conflicting_diagnostic_observations)}</small>}</td>
          <td>{observationNumber(row.adoption?.known_attempted_denominator)} 项</td>
          <td>{percentile(row, "p50")} / {percentile(row, "p95")}<small> {observationNumber(row.diagnostic_latency_ms?.sample_count)} 个样本 · 耗时未知 {observationNumber(row.diagnostic_latency_ms?.unknown_observations)} · 门槛 {observationNumber(row.diagnostic_latency_ms?.minimum_percentile_samples ?? 3)}</small></td></tr>;
      })}
    </tbody></table></div>
    <p className="admin-small-note">客户端全旅程 p50 / p95：未记录。本统计没有客户页面的受理、首次可读和可见回复结束计时；不能用服务器规划或Laya耗时替代，也不能据此计算加速比例。</p>
    <p className="admin-small-note">标准只读编译器零HTTP观测：{observationNumber(data.by_purpose?.planning?.standard_read_compiler_zero_http_observations)}。这是确定性编译，不是本地LLM或Laya节省。</p>
    <p className="admin-small-note">Laya历史规划路径已验证本地SDK推理：{observationNumber(data.by_purpose?.laya?.verified_local_sdk_inferences)}。观察不授予执行权；当前不采用，未证明增量省调用。无独立标注质量数据时，准确率保持未知。</p>
    <SemanticShadowMetrics data={data.by_purpose?.laya?.semantic_shadow}/>
  </section>;
}

// Never render arbitrary observation payloads, model prose or credential-like fields here.
export type PurposeMetricsJson = JsonValue;
