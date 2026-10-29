import { StateBadge, StructuredData } from "./components";
import type { JsonValue } from "./types";
import { LayaReadonlyObservation } from "./admin/LayaReadonlyObservation";

const modeLabels: Record<string, string> = {
  AUTO: "自动受控路由",
  LOCAL_ONLY: "仅本地",
  PREFER_CLOUD: "优先云端",
  CLOUD_ONLY: "仅云端（不回退本地）",
};
const known = (value: JsonValue | undefined) =>
  value === undefined || value === null ? "未记录" : String(value);

/** Diagnostics are observations, never model accuracy or hidden reasoning. */
export function ModelDiagnostics({
  data,
  adminLayaAdvice,
}: {
  data: Record<string, JsonValue>;
  adminLayaAdvice?: unknown;
}) {
  const attempts = Array.isArray(data.attempts)
    ? data.attempts.filter((item): item is Record<string, JsonValue> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  return (
    <section className="evidence-section model-diagnostics">
      <h3>
        实际模型路由 <small>出站请求尝试，不代表成功数或准确率</small>
      </h3>
      <div className="model-route-summary">
        <div>
          <span>受控策略</span>
          <strong>
            {modeLabels[String(data.routing_mode)] || known(data.routing_mode)}
          </strong>
          <small>策略版本 {known(data.routing_policy_version)}</small>
        </div>
        <div>
          <span>实际提供方 / 模型</span>
          <strong>{known(data.actual_provider)}</strong>
          <small>{known(data.actual_model)}</small>
        </div>
        <div>
          <span>路由降级</span>
          <strong>
            {data.degraded === true
              ? "已记录降级"
              : data.degraded === false
                ? "未降级"
                : "未记录"}
          </strong>
          <small>{known(data.route_reason)}</small>
        </div>
      </div>
      {(data.plan_source !== undefined ||
        data.skipped_primary_model !== undefined ||
        data.laya_contribution !== undefined) && (
        <div className="model-planning-path">
          <strong>
            {data.plan_source === "STANDARD_READ_COMPILER"
              ? "标准只读编译器路径"
              : data.plan_source === "MODEL_PROPOSAL"
                ? "模型提案 + 服务端约束核验"
                : "返回的规划路径"}
          </strong>
          <p>
            {data.skipped_primary_model === true
              ? "本轮跳过主规划模型。"
              : data.skipped_primary_model === false
                ? "本轮未跳过主规划模型。"
                : "主模型是否跳过未记录。"}
            {typeof data.skip_reason === "string" ? ` ${data.skip_reason}` : ""}
          </p>
          <small>
            Laya贡献：
            {data.laya_contribution === "NONE"
              ? "无"
              : data.laya_contribution === "SHADOW_OBSERVATION"
                ? "影子分类观察，未接管授权"
                : data.laya_contribution === "CALIBRATED_READ_CLASSIFICATION"
                  ? "已返回校准只读分类标记；具体采用依据请核对服务端记录"
                  : known(data.laya_contribution)}
            。标准只读编译器不能冒认为Laya增益。
          </small>
        </div>
      )}
      {attempts.length ? (
        <ol className="model-attempt-list">
          {attempts.map((attempt, index) => (
            <li key={index}>
              <div>
                <strong>
                  尝试 {known(attempt.attempt)} · {known(attempt.provider)}
                </strong>
                <StateBadge state={String(attempt.status || "UNKNOWN")} />
              </div>
              <p>
                {known(attempt.model)}
                {attempt.response_model
                  ? ` → 回包 ${known(attempt.response_model)}`
                  : ""}
              </p>
              <small>
                HTTP{" "}
                {attempt.http_issued === false
                  ? "未发出"
                  : attempt.http_issued === true
                    ? known(attempt.http_status)
                    : "是否发出未记录"}{" "}
                ·{" "}
                {typeof attempt.elapsed_ms === "number"
                  ? `${attempt.elapsed_ms.toFixed(0)} ms`
                  : "耗时未记录"}
                {attempt.error_code ? ` · ${known(attempt.error_code)}` : ""}
              </small>
              {attempt.usage && (
                <details className="structured-technical">
                  <summary>本次已记录用量</summary>
                  <StructuredData data={attempt.usage} technical />
                </details>
              )}
              {(attempt.raw_action_count !== undefined ||
                attempt.grounded_action_count !== undefined ||
                attempt.filter_reason_codes !== undefined) && (
                <details className="model-semantic-abstention">
                  <summary>候选与语义约束结果（不是执行授权）</summary>
                  <p>
                    原始候选 {known(attempt.raw_action_count)} · 原文核验后{" "}
                    {known(attempt.grounded_action_count)}
                  </p>
                  {Array.isArray(attempt.raw_action_kinds) && (
                    <p>
                      候选动作：
                      {attempt.raw_action_kinds.map(String).join("、") || "无"}
                    </p>
                  )}
                  {Array.isArray(attempt.filter_reason_codes) && (
                    <p>
                      弃权/过滤原因：
                      {attempt.filter_reason_codes.map(String).join("、") ||
                        "未返回过滤原因"}
                    </p>
                  )}
                </details>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty-text">此规划版本没有出站尝试明细，未知值不补造。</p>
      )}
      {(data.max_http_attempts !== undefined ||
        data.max_elapsed_seconds !== undefined ||
        data.abstention_reasons !== undefined ||
        data.failure_reasons !== undefined ||
        data.circuit_status !== undefined) && (
        <div className="model-safety-boundary">
          <h4>预算、隔离与语义弃权记录</h4>
          <StructuredData
            data={{
              ...(data.max_http_attempts !== undefined
                ? { max_http_attempts: data.max_http_attempts }
                : {}),
              ...(data.max_elapsed_seconds !== undefined
                ? { max_elapsed_seconds: data.max_elapsed_seconds }
                : {}),
              ...(data.circuit_status !== undefined
                ? { circuit_status: data.circuit_status }
                : {}),
              ...(data.abstention_reasons !== undefined
                ? { abstention_reasons: data.abstention_reasons }
                : {}),
              ...(data.failure_reasons !== undefined
                ? { failure_reasons: data.failure_reasons }
                : {}),
            }}
            technical
          />
          <p>仅展示服务端实际记录。未登记的隔离、弃权或预算状态不补造。</p>
        </div>
      )}
      <details className="structured-technical">
        <summary>完整规划诊断（不含思维链或密钥）</summary>
        <StructuredData data={data} technical />
      </details>
      {adminLayaAdvice !== undefined && <LayaReadonlyObservation advice={adminLayaAdvice} />}
    </section>
  );
}
