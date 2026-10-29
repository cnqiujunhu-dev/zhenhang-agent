import { useRef } from "react";
import {
  dateText,
  Dag,
  Icon,
  Skeleton,
  StateBadge,
  StructuredData,
  useDialogFocus,
  LayaEvidence,
} from "../components";
import { ModelDiagnostics } from "../ModelDiagnostics";
import { LayaReadonlyObservation } from "./LayaReadonlyObservation";
import { withoutLayaReadonlyPayload } from "./layaReadonlyObservationGuard";
import { AdminConversationDetails } from "./AdminConversationDetails";
import { PureRouteSummary } from "./PureRouteSummary";
import { useEndpoint } from "../useEndpoint";
import type { AdminTrace } from "./types";
import type { JsonValue } from "../types";

export function AdminTraceDrawer({
  token,
  taskId,
  onClose,
}: {
  token: string;
  taskId: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { dialogRef, onDismiss: onClose });
  const endpoint = useEndpoint<AdminTrace>(
    `/api/v2/admin/tasks/${encodeURIComponent(taskId)}/trace`,
    token,
  );
  const auditChain = (
    endpoint.data?.runtime_trace as unknown as Record<string, JsonValue> | null
  )?.audit_chain_valid;
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer admin-trace-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-trace-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">运维只读视图</span>
            <h2 id="admin-trace-title">任务状态与事件</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="关闭运维任务检查"
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          {endpoint.loading && <Skeleton lines={5} />}
          {endpoint.error && (
            <p className="login-error" role="alert">
              {endpoint.error}
            </p>
          )}
          {endpoint.data && (
            <>
              <div className="admin-readonly-boundary">
                <Icon name="shield" size={19} />
                <p>只检查执行状态与审计元数据，不提供客户确认或强制执行。</p>
              </div>
              <div className="trace-meta">
                <StateBadge state={endpoint.data.state} />
                <span>版本 {endpoint.data.version}</span>
                <span>{endpoint.data.username}</span>
              </div>
              {endpoint.data.runtime_trace?.message && (
                <section className="evidence-section">
                  <h3>任务原始需求</h3>
                  <p
                    className="trace-message"
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {endpoint.data.runtime_trace.message}
                  </p>
                  <small className="muted">
                    原文用于只读追溯，不从管理员界面重发或执行。
                  </small>
                </section>
              )}
              {endpoint.data.runtime_trace && (
                <>
                  <section className="evidence-section">
                    <h3>实际任务依赖图</h3>
                    <Dag nodes={endpoint.data.runtime_trace.nodes || []} />
                  </section>
                  <section className="evidence-section">
                    <h3>节点事实、策略与回执</h3>
                    {(endpoint.data.runtime_trace.nodes || []).map((node) => (
                      <details className="node-detail" key={node.node_id}>
                        <summary>
                          <span>{node.label || node.action}</span>
                          <StateBadge state={node.status} />
                        </summary>
                        {node.error && (
                          <p className="error-inline">{node.error}</p>
                        )}
                        {node.wait_reason && (
                          <p className="muted">{node.wait_reason}</p>
                        )}
                        {node.result ? (
                          <StructuredData data={node.result} />
                        ) : (
                          <p className="empty-text">暂无执行回执。</p>
                        )}
                        {node.evidence && (
                          <details className="structured-technical">
                            <summary>事实来源与核验记录</summary>
                            <StructuredData data={node.evidence} technical />
                          </details>
                        )}
                      </details>
                    ))}
                  </section>
                  {endpoint.data.runtime_trace.planning_diagnostics && (
                    <PureRouteSummary data={endpoint.data.runtime_trace.planning_diagnostics} />
                  )}
                  {endpoint.data.runtime_trace.planning_diagnostics && (
                    <ModelDiagnostics
                      data={endpoint.data.runtime_trace.planning_diagnostics}
                      adminLayaAdvice={endpoint.data.runtime_trace.laya_advice ?? null}
                    />
                  )}
                  {!endpoint.data.runtime_trace.planning_diagnostics && <LayaReadonlyObservation advice={endpoint.data.runtime_trace.laya_advice ?? null} headingLevel={3} />}
                  {endpoint.data.runtime_trace.laya_advice && (
                    <LayaEvidence
                      advice={endpoint.data.runtime_trace.laya_advice}
                    />
                  )}
                  {endpoint.data.runtime_trace.model_decisions && (
                    <section className="evidence-section">
                      <h3>各计划版本的规划与核验记录</h3>
                      <p className="admin-small-note">
                        展示服务端保留的历史规划，不把最新诊断套用到旧版本；没有记录的耗时、调用数与用量不补造。
                      </p>
                      {(Array.isArray(
                        endpoint.data.runtime_trace.model_decisions,
                      )
                        ? endpoint.data.runtime_trace.model_decisions
                        : [endpoint.data.runtime_trace.model_decisions]
                      ).map((decision, index) => (
                        <details className="node-detail" key={index}>
                          <summary>
                            <span>
                              计划{" "}
                              {decision.plan_version == null
                                ? "版本未记录"
                                : `v${decision.plan_version}`}{" "}
                              ·{" "}
                              {typeof decision.provider === "string"
                                ? decision.provider
                                : "提供方未记录"}
                            </span>
                          </summary>
                          {decision.planning_diagnostics &&
                            typeof decision.planning_diagnostics === "object" &&
                            !Array.isArray(decision.planning_diagnostics) && (
                              <PureRouteSummary data={decision.planning_diagnostics} />
                            )}
                          {decision.planning_diagnostics &&
                            typeof decision.planning_diagnostics === "object" &&
                            !Array.isArray(decision.planning_diagnostics) && (
                              <ModelDiagnostics
                                data={decision.planning_diagnostics}
                                adminLayaAdvice={decision.laya_advice ?? null}
                              />
                            )}
                          {!(decision.planning_diagnostics && typeof decision.planning_diagnostics === "object" && !Array.isArray(decision.planning_diagnostics))
                            && <LayaReadonlyObservation advice={decision.laya_advice ?? null} />}
                          <details className="structured-technical">
                            <summary>本计划版本完整安全记录</summary>
                            <StructuredData data={withoutLayaReadonlyPayload(decision)} technical />
                          </details>
                        </details>
                      ))}
                    </section>
                  )}
                  <section className="evidence-section">
                    <h3>完整运行投影与回执核验</h3>
                    <p className="admin-small-note">
                      审计链校验：
                      {auditChain === true
                        ? "已通过"
                        : auditChain === false
                          ? "未通过"
                          : "未记录"}
                      。链校验不等同于银行操作已成功。
                    </p>
                    <p className="admin-small-note">
                      节点依赖、参数、策略、事实来源、已执行结果与审计链均以服务端投影为准；只读查看不授予确认权限。
                    </p>
                    <details className="structured-technical">
                      <summary>展开完整任务Trace</summary>
                      <StructuredData
                        data={
                          withoutLayaReadonlyPayload(endpoint.data.runtime_trace as unknown as JsonValue)
                        }
                        technical
                      />
                    </details>
                  </section>
                </>
              )}
              <h3 className="admin-section-title">
                运行操作记录（含历史计划）
              </h3>
              <div className="admin-node-list">
                {endpoint.data.operations.map((operation) => (
                  <div key={operation.operation_id}>
                    <strong>{operation.action_kind}</strong>
                    <StateBadge state={operation.status} />
                    <span>计划 v{operation.plan_version}</span>
                  </div>
                ))}
              </div>
              {endpoint.data.tool_calls && (
                <section className="evidence-section admin-tool-calls">
                  <h3>工具调用安全日志</h3>
                  <p className="admin-small-note">
                    当前展示 {endpoint.data.tool_calls.length} 条 · 总计{" "}
                    {endpoint.data.tool_calls_total ?? "未返回"} 条。
                    {endpoint.data.tool_calls_truncated === true
                      ? "本响应已截断，仅返回最新100条；不代表全部调用。"
                      : endpoint.data.tool_calls_truncated === false
                        ? "本响应未截断。"
                        : "是否截断未返回。"}
                    工具调用状态是单次服务端记录，不单独证明客户银行操作已完成。
                  </p>
                  {!endpoint.data.tool_calls.length && (
                    <p className="empty-text">
                      没有返回本任务范围的工具调用记录。
                    </p>
                  )}
                  {endpoint.data.tool_calls.map((call) => (
                    <details className="node-detail" key={call.tool_call_id}>
                      <summary>
                        <span>
                          {call.tool_name} · {dateText(call.created_at, true)}
                        </span>
                        <StateBadge state={call.status} />
                      </summary>
                      <StructuredData
                        data={call as unknown as JsonValue}
                        technical
                      />
                    </details>
                  ))}
                </section>
              )}
              {endpoint.data.plan_snapshots && (
                <section className="evidence-section admin-plan-snapshots">
                  <h3>提案与编译计划快照</h3>
                  <p className="admin-small-note">
                    当前展示 {endpoint.data.plan_snapshots.length} 条 · 总计{" "}
                    {endpoint.data.plan_snapshots_total ?? "未返回"} 条。
                    {endpoint.data.plan_snapshots_truncated === true
                      ? "本响应已截断，仅返回最新20条；不代表全部计划版本。"
                      : endpoint.data.plan_snapshots_truncated === false
                        ? "本响应未截断。"
                        : "是否截断未返回。"}
                    提案和编译计划不是执行授权，真实执行仍须核对对应操作与回执。
                  </p>
                  {!endpoint.data.plan_snapshots.length && (
                    <p className="empty-text">没有返回本任务范围的计划快照。</p>
                  )}
                  {endpoint.data.plan_snapshots.map((snapshot) => (
                    <details className="node-detail" key={snapshot.snapshot_id}>
                      <summary>
                        <span>
                          快照 v{snapshot.version} ·{" "}
                          {dateText(snapshot.created_at, true)}
                        </span>
                      </summary>
                      <StructuredData
                        data={snapshot as unknown as JsonValue}
                        technical
                      />
                    </details>
                  ))}
                </section>
              )}
              <h3 className="admin-section-title">执行事件</h3>
              <ol className="admin-event-timeline">
                {endpoint.data.events.map((event) => (
                  <li key={event.seq}>
                    <span>{event.seq}</span>
                    <div>
                      <strong>{event.event_type}</strong>
                      <time>{dateText(event.created_at, true)}</time>
                    </div>
                  </li>
                ))}
              </ol>
              <details className="structured-technical">
                <summary>完整只读元数据</summary>
                <StructuredData
                  data={withoutLayaReadonlyPayload(endpoint.data as unknown as JsonValue)}
                  technical
                />
              </details>
              <AdminConversationDetails token={token} taskId={taskId} />
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
