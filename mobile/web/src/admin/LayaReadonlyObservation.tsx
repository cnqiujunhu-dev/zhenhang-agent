import { customerActionLabel } from "../customerTaskPresentation";
import { inspectLayaReadonlyObservation, layaReadonlyReasons } from "./layaReadonlyObservationGuard";

/** Used only by explicitly authorized admin trace callers; never runs SDK. */
export function LayaReadonlyObservation({advice, headingLevel = 4}: {advice: unknown; headingLevel?: 3 | 4}) {
  const view = inspectLayaReadonlyObservation(advice);
  const Heading = headingLevel === 3 ? "h3" : "h4";
  if (view.kind !== "valid") return <section className="evidence-section laya-readonly-observation" data-laya-candidate-state="UNKNOWN">
    <Heading>Laya 只读候选观察</Heading>
    <span className="state-badge neutral">未知</span>
    <p>{view.reason}</p>
    <p className="admin-small-note">SDK 是否运行、延迟、采用状态与候选节省调用均未确认；不把缺失记录当作零或已运行。</p>
  </section>;
  const {observation, pin} = view.envelope;
  return <section className="evidence-section laya-readonly-observation" data-laya-candidate-state={observation.status}>
    <Heading>Laya 只读候选观察</Heading>
    <span className="state-badge neutral">{observation.status === "ABSTAIN" ? "已弃权 · 未采用" : "实验候选 · 未采用"}</span>
    <div className="model-route-summary">
      <div><span>候选 SDK 推理记录</span><strong>{observation.sdk_inference_used ? "已记录使用 SDK 推理" : "未记录使用 SDK 推理"}</strong><small>仅按服务端标记展示</small></div>
      <div><span>观察延迟</span><strong>{observation.latency_ms === null ? "未记录" : `${observation.latency_ms.toFixed(1)} ms`}</strong></div>
      <div><span>候选能力</span><strong>{observation.candidate_kind === null ? "无（已弃权）" : customerActionLabel(observation.candidate_kind)}</strong></div>
      <div><span>校准分数（非正确率）</span><strong>{observation.calibrated_score === null ? "未记录" : observation.calibrated_score.toFixed(4)}</strong></div>
    </div>
    <p>观察原因：{layaReadonlyReasons[observation.reason]}</p>
    <p>质量门未通过 · 采用未启用 · 本次未采用。因 Laya 候选省下的模型调用：0。</p>
    <p className="admin-small-note">仅用于观察，不生成授权或接管计划。校准分数不是正确概率或线上准确率；标准只读编译器跳过模型不计为 Laya 收益。此界面不重新核验或运行 SDK。</p>
    <details className="structured-technical"><summary>固定观察摘要（服务端记录）</summary>
      <dl>{Object.entries(pin).map(([key, value]) => <div key={key}><dt>{key}</dt><dd><code>{value}</code></dd></div>)}</dl>
      <small>格式检查不等于独立证明固定模型或真实推理；不展示原始输入。</small>
    </details>
  </section>;
}
