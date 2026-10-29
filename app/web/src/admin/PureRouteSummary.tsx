const object = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
const duration = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${Math.round(value)} ms` : "未记录";
const providers: Record<string, string> = {
  deepseek: "DeepSeek云端", ollama: "本地LLM", deterministic: "确定性编译器", rule: "规则回退", TEST_STUB: "测试替身",
};
const reasons: Record<string, string> = {
  EXACT_SINGLE_READ_WITHIN_SERVER_ALLOWLIST: "精确单项只读，且在服务端动作白名单内",
  STRICT_CLOUD_ONLY_NO_LOCAL_ALTERNATE: "全局策略仅允许登记云模型，无本地模型替代",
  INVALID_SERVER_POLICY_NO_MODEL_EGRESS: "服务端策略校验失败，未发出模型请求",
  ADMIN_PREFER_CLOUD: "管理员策略优先云端",
  LOCAL_ONLY_NO_CLOUD: "全局策略仅允许本地模型",
  AUTO_COMPLEX_OR_FAMILY_GOAL: "自动路由识别为跨动作或家庭、目标请求",
  AUTO_SIMPLE_READ: "自动路由识别为简单读取",
  AUTO_SINGLE_STEP_OR_UNKNOWN_LOCAL: "自动路由识别为单步骤或未分类请求",
};
const compilerGates: Record<string, string> = {
  NO_ROUTING_REGISTRY: "当次未使用已登记的路由器，未进入标准读取编译入口",
  NO_SERVER_POLICY: "当次没有可用的服务端编译策略记录",
  INVALID_SERVER_POLICY: "当次服务端策略未通过校验，未采用标准读取编译",
  DISABLED_BY_POLICY: "当次服务端策略关闭标准只读编译；其余请求沿主规划路径",
  CUSTOMER_DIALOGUE_REQUIRES_MODEL: "本轮是自然会话请求，按策略先由云模型理解，再执行受控查询",
  REVIEWED_EVIDENCE_PRESENT: "当次含已复核资料引用，保留完整主规划与来源核验",
  NO_EXACT_AUTHORIZED_MATCH: "当次未满足精确单项且在动作白名单内的编译条件",
  INVALID_COMPILER_CONTEXT: "当次编译上下文未通过检查，未采用标准读取编译",
};
export function routeReasonText(value: unknown): string {
  if (typeof value !== "string") return "未记录";
  if (Object.hasOwn(reasons, value)) return reasons[value];
  const suffix = ":PREFERRED_NOT_CONFIGURED";
  const base = value.endsWith(suffix) ? value.slice(0, -suffix.length) : "";
  return Object.hasOwn(reasons, base) ? `${reasons[base]}；首选模型未配置` : "未记录可识别原因";
}
export function summarizePlanningPath(value: unknown) {
  const data = object(value) || {};
  const provider = typeof data.actual_provider === "string" && Object.hasOwn(providers, data.actual_provider)
    ? providers[data.actual_provider] : "未记录";
  const calls = count(data.model_api_call_count);
  const compiler = data.plan_source === "STANDARD_READ_COMPILER";
  const recordedGate = typeof data.standard_read_gate === "string" ? data.standard_read_gate : null;
  const compilerProof = compiler && calls === 0 && data.actual_provider === "deterministic"
    && data.skipped_primary_model === true && (data.attempts === undefined
      || Array.isArray(data.attempts) && data.attempts.length === 0);
  const gatePresent = data.standard_read_gate !== undefined && data.standard_read_gate !== null;
  const compilerTrusted = compilerProof && (!gatePresent || recordedGate === "MATCHED");
  const localConflict = data.routing_mode === "CLOUD_ONLY" && data.actual_provider === "ollama";
  let path = "规划路径未记录";
  if (compiler) path = compilerTrusted
    ? "标准只读编译器；零模型HTTP" : "编译器路径记录不一致，需核对原始诊断";
  else if (localConflict) path = "提供方与仅云端策略记录不一致，需核对";
  else if (data.plan_source === "MODEL_PROPOSAL") path = data.actual_provider === "deepseek" ? "云端模型提案 + 服务端核验"
    : data.actual_provider === "TEST_STUB" ? "测试替身提案，非真实云调用"
    : data.actual_provider === "ollama" ? "已记录本地模型提案 + 服务端核验" : "模型提案；实际提供方未核验";
  else if (data.plan_source === "RULE_FALLBACK") path = "规则回退；不计为模型采纳";
  const laya = data.laya_contribution === "SHADOW_OBSERVATION" ? "Laya影子观察，未接管；未证明省调用"
    : data.laya_contribution === "NONE" ? "Laya贡献无；编译器不能计为Laya收益"
    : "Laya采用与增量收益未核验";
  const attempts = Array.isArray(data.attempts) ? data.attempts.slice(0, 3).map(item => {
    const attempt = object(item) || {};
    return {provider: typeof attempt.provider === "string" && Object.hasOwn(providers, attempt.provider) ? providers[attempt.provider] : "未记录",
      elapsed: duration(attempt.elapsed_ms), issued: attempt.http_issued === true ? "已发出" : attempt.http_issued === false ? "未发出" : "是否发出未记录"};
  }) : [];
  const gateConsistent = compilerProof ? !gatePresent || recordedGate === "MATCHED"
    : !compiler && recordedGate !== "MATCHED";
  const compilerGate = !gateConsistent ? "编译路径与当次门记录不一致，需核对原始诊断"
    : compilerTrusted ? "已记录精确编译命中"
    : recordedGate && Object.hasOwn(compilerGates, recordedGate) ? compilerGates[recordedGate]
    : "编译器未命中或未启用的具体原因未记录；当前策略不能反推历史原因";
  return {path, provider, calls: calls === null ? "未记录" : String(calls),
    reason: routeReasonText(data.route_reason), elapsed: duration(data.elapsed_ms), laya, attempts,
    compilerGate};
}

/** Present persisted, allowlisted observations without guessing a missing routing gate. */
export function PureRouteSummary({data}: {data: unknown}) {
  const summary = summarizePlanningPath(data);
  return <section className="evidence-section" aria-label="规划路径与耗时摘要">
    <h3>规划路径与耗时摘要</h3>
    <p><strong>{summary.path}</strong> · {summary.provider} · 已记录模型HTTP {summary.calls}</p>
    <p>路由原因：{summary.reason}。{summary.compilerGate}。</p>
    <p>{summary.laya}。</p>
    <dl><div><dt>服务器规划诊断总耗时</dt><dd>{summary.elapsed}</dd></div></dl>
    {summary.attempts.length > 0 && <ol>{summary.attempts.map((attempt, index) => <li key={index}>
      尝试 {index + 1} · {attempt.provider} · HTTP {attempt.issued} · {attempt.elapsed}
    </li>)}</ol>}
    <p className="admin-small-note">逐次尝试耗时属于服务器规划诊断，不能相加为全旅程。排队、资料读取、结果送达和浏览器渲染阶段没有独立计时记录。客户端受理、首次可读和可见回复结束仅保留在客户页面，本后台未收到；未知阶段不补零。</p>
  </section>;
}
