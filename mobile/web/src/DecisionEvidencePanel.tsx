import { Icon, Skeleton, StructuredData } from "./components";
import { useEndpoint } from "./useEndpoint";
import type { JsonValue } from "./types";
interface DecisionStatus {
  scope: "CURRENT_ACTOR_DECISION_EVIDENCE";
  engine: {
    mode: "OFF" | "SHADOW";
    status: string;
    inference_verified: boolean;
    calibrated: boolean;
    adoption_enabled: boolean;
    identity: Record<string, JsonValue> | null;
    explanation: string;
  };
  metrics: {
    planning_versions: number;
    known_model_http_calls: number;
    unknown_model_call_records: number;
    actual_laya_inferences: number;
    standard_read_compiler_uses: number;
    planning_latency_ms: {
      p50: number | null;
      p95: number | null;
      samples: number;
    };
  };
  comparison: {
    status: "NOT_RUN" | "AVAILABLE";
    baseline_model_calls: number | null;
    candidate_model_calls: number | null;
    call_difference: number | null;
    latency_difference_ms: number | null;
    quality_guard_passed: boolean | null;
    note: string;
    scope?: string;
    candidate_variant?: string;
    laya_incremental_http_reduction?: number;
  };
  eligibility: {
    readonly_only: boolean;
    write_authorization_affected: boolean;
  };
  synthetic_bank: boolean;
}
export function DecisionEvidencePanel({ token }: { token: string }) {
  const endpoint = useEndpoint<DecisionStatus>(
    "/api/v3/decision/status",
    token,
  );
  const data = endpoint.data;
  return (
    <section className="system-section decision-evidence-v3">
      <h3>效率路径与 Laya 边界</h3>
      {endpoint.loading && !data && <Skeleton lines={3} />}
      {endpoint.error && (
        <p className="error-inline">观测数据暂未取得：{endpoint.error}</p>
      )}
      {data && data.scope !== "CURRENT_ACTOR_DECISION_EVIDENCE" && (
        <p className="error-inline">
          返回范围未核对，未展示其他身份的效率信息。
        </p>
      )}
      {data?.scope === "CURRENT_ACTOR_DECISION_EVIDENCE" && (
        <>
          <p>
            <Icon name="info" size={14} />
            {data.engine.mode === "SHADOW" ? "影子观察" : "已关闭"} ·{" "}
            {(
              {
                OFF: "关闭状态",
                NOT_CONFIGURED: "后端未配置",
                READY: "后端已返回就绪记录",
                UNAVAILABLE: "当前不可用",
              } as Record<string, string>
            )[data.engine.status] || data.engine.status}{" "}
            ·{" "}
            {data.engine.inference_verified
              ? "有已核验的实际推理记录"
              : "没有已核验的实际Laya推理记录"}
          </p>
          <p>
            {data.engine.calibrated
              ? "已返回分类校准标记；校准不等于金融安全保证，具体范围以证据为准。"
              : "原始分类尚未校准，不表示安全概率或准确率。"}
            {data.engine.adoption_enabled
              ? "已返回只读路径采用标记；不改变写操作授权。"
              : "未启用分类接管，影子观察不替代主规划或银行授权。"}
          </p>
          <p>{data.engine.explanation}</p>
          <div className="decision-evidence-numbers">
            <span>
              本身份已记录规划<strong>{data.metrics.planning_versions}</strong>
            </span>
            <span>
              已知规划模型HTTP尝试
              <strong>
                {typeof data.metrics.known_model_http_calls === "number"
                  ? data.metrics.known_model_http_calls
                  : "未记录"}
              </strong>
              <small>
                {data.metrics.unknown_model_call_records} 项计数未知
              </small>
            </span>
            <span>
              实际Laya推理<strong>{data.metrics.actual_laya_inferences}</strong>
            </span>
            <span>
              标准只读编译器
              <strong>{data.metrics.standard_read_compiler_uses}</strong>
              <small>不等同于Laya</small>
            </span>
          </div>
          <p className="muted small">
            这里统计Parent规划决策，不含咨询消息ChatTurn与图片AttachmentJob；不是全部提供方消耗或账单。历史计数未知保留未知，不填成零。
          </p>
          <details>
            <summary>对照与规划延迟：只展示已记录的值</summary>
            <p>{data.comparison.note}</p>
            {data.comparison.status === "NOT_RUN" ? (
              <p>尚未运行收益对照，不展示节省时间、Token或安全置信度。</p>
            ) : data.comparison.quality_guard_passed === true ? (
              <div className="decision-controlled-ab">
                <strong>小样本提案对照 · 标准只读编译器</strong>
                <p>
                  不是本用户累计节省，也不是银行全流程性能。基线出站尝试{" "}
                  {data.comparison.baseline_model_calls === null
                    ? "未记录"
                    : data.comparison.baseline_model_calls}
                  ，候选{" "}
                  {data.comparison.candidate_model_calls === null
                    ? "未记录"
                    : data.comparison.candidate_model_calls}
                  ；差值{" "}
                  {data.comparison.call_difference === null
                    ? "未记录"
                    : data.comparison.call_difference}
                  。
                </p>
                <p>
                  Laya增量调用减少{" "}
                  {typeof data.comparison.laya_incremental_http_reduction ===
                  "number"
                    ? data.comparison.laya_incremental_http_reduction
                    : "未记录"}
                  ；不能把只读编译器的差值归因于Laya加速。
                </p>
                <details>
                  <summary>原始小样本观测与范围</summary>
                  <StructuredData
                    data={data.comparison as unknown as JsonValue}
                    technical
                  />
                </details>
              </div>
            ) : (
              <p>
                对照质量门尚未通过或未核验，调用与延迟差不能当作已实现收益；请查看原始对照说明。
              </p>
            )}
            <p>
              P50{" "}
              {data.metrics.planning_latency_ms.p50 === null
                ? "未记录"
                : `${data.metrics.planning_latency_ms.p50} ms`}{" "}
              · P95{" "}
              {data.metrics.planning_latency_ms.p95 === null
                ? "未记录"
                : `${data.metrics.planning_latency_ms.p95} ms`}{" "}
              · {data.metrics.planning_latency_ms.samples}{" "}
              个规划样本；不是用户全流程耗时。
            </p>
          </details>
          <p className="muted small">
            只读辅助路径不改变写操作授权。原始分数不等同正确率，收益需要实际对照证据。
          </p>
          {data.engine.identity && (
            <details>
              <summary>已返回的模型身份</summary>
              <StructuredData data={data.engine.identity} technical />
            </details>
          )}
        </>
      )}
      <button className="text-button" onClick={endpoint.refresh}>
        重新读取观测
      </button>
    </section>
  );
}
