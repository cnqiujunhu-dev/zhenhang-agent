import { useRef } from "react";
import {
  dateText,
  Icon,
  LayaEvidence,
  Skeleton,
  StateBadge,
  useDialogFocus,
} from "./components";
import { useEndpoint } from "./useEndpoint";
import type { LayaAdvice } from "./types";
import { DecisionEvidencePanel } from "./DecisionEvidencePanel";

type Metrics = {
  scope: string;
  observed_at: string;
  runtime_health: {
    status: string;
    database: string;
    worker: string;
    online_workers: number;
    last_heartbeat_at: string | null;
  };
  tasks: { total: number; by_state: Record<string, number> };
  jobs: {
    total: number;
    by_status: Record<string, number>;
    queued_due: number;
    stale_leases: number;
    oldest_queued_age_seconds: number | null;
  };
  models: {
    planning_invocations: number;
    fallback_count: number;
    fallback_rate: number | null;
    observed_api_calls: number | null;
    api_call_counts_unknown: number;
  };
  scenario_activity: {
    domain: string;
    operations: number;
    by_status: Record<string, number>;
  }[];
  planning_latency_ms: {
    sample_count: number;
    p50: number | null;
    p95: number | null;
    includes_rule_and_model: boolean;
  };
  recent_failures: {
    task_id: string;
    occurred_at: string;
    event_type: string;
    error_type: string;
  }[];
  decision_advice?: {
    by_mode?: Record<string, number>;
    by_status?: Record<string, number>;
    verified_inference_records?: number;
    actual_laya_inference_verified?: boolean;
    scores_are_accuracy?: boolean;
  };
};
type CapabilityCatalog = {
  version: string;
  capabilities: {
    kind: string;
    label: string;
    domain: string;
    tool: string;
    read_only: boolean;
    requires_confirmation: boolean;
  }[];
};
const domainLabels: Record<string, string> = {
  BILL: "账单",
  TRANSFER: "转账",
  CARDS: "卡片",
  CARD: "卡片",
  WEALTH: "理财",
  SUBSCRIPTIONS: "订阅",
  SUBSCRIPTION: "订阅",
  FRAUD: "反诈",
  FAMILY: "家庭协助",
  GOALS: "目标现金流",
  CARBON: "个人低碳",
};
export function SystemPanel({
  token,
  advice,
  onClose,
  onTaskOpen,
}: {
  token: string;
  advice?: LayaAdvice | null;
  onClose: () => void;
  onTaskOpen: (id: string) => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { dialogRef, onDismiss: onClose });
  const metrics = useEndpoint<Metrics>("/api/v2/platform/metrics", token);
  const catalog = useEndpoint<CapabilityCatalog>(
    "/api/v2/platform/capabilities",
    token,
  );
  const snapshot = metrics.data;
  const health = snapshot?.runtime_health;
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer system-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="system-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">当前身份的任务与能力</span>
            <h2 id="system-title">系统观测</h2>
          </div>
          <button
            className="icon-button"
            aria-label="关闭系统观测"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          {metrics.loading && !snapshot && (
            <>
              <Skeleton lines={5} />
              <button className="text-button" onClick={metrics.cancel}>
                取消观测读取
              </button>
            </>
          )}
          {metrics.error && (
            <div className="extension-load-error" role="alert">
              <p>{metrics.error}</p>
              <button
                className="button secondary compact"
                onClick={metrics.refresh}
              >
                重新读取
              </button>
            </div>
          )}
          {metrics.cancelled && (
            <p className="empty-text">
              已取消只读观测，业务任务继续按原流程运行。
            </p>
          )}
          {snapshot && health && (
            <>
              <div className="system-scope">
                <span>
                  {snapshot.scope === "CURRENT_USER"
                    ? "当前用户域"
                    : snapshot.scope}
                </span>
                <small>记录于 {dateText(snapshot.observed_at, true)}</small>
              </div>
              <div
                className={`system-health ${health.status === "ready" ? "online" : "unready"}`}
              >
                <Icon
                  name={health.status === "ready" ? "check" : "info"}
                  size={25}
                />
                <div>
                  <strong>
                    {health.status === "ready"
                      ? "执行服务可用"
                      : "执行服务暂未就绪"}
                  </strong>
                  <p>
                    数据库{" "}
                    {health.database === "ok" ? "可连接" : health.database} ·
                    Worker{" "}
                    {health.worker === "online" ? "在线" : "离线或暂不可用"}
                  </p>
                </div>
                <span>{health.online_workers} 个在线 Worker</span>
              </div>
              <div className="system-stat-grid">
                <div>
                  <span>当前身份任务</span>
                  <strong>{snapshot.tasks.total}</strong>
                </div>
                <div>
                  <span>当前身份工作流 Job</span>
                  <strong>{snapshot.jobs.total}</strong>
                </div>
                <div>
                  <span>到期等待执行</span>
                  <strong>{snapshot.jobs.queued_due}</strong>
                </div>
                <div>
                  <span>过期执行租约</span>
                  <strong>{snapshot.jobs.stale_leases}</strong>
                </div>
              </div>
              <section className="system-section">
                <h3>任务状态</h3>
                {Object.entries(snapshot.tasks.by_state).length ? (
                  <div className="system-status-grid">
                    {Object.entries(snapshot.tasks.by_state).map(
                      ([state, count]) => (
                        <div key={state}>
                          <StateBadge state={state} />
                          <strong>{count}</strong>
                        </div>
                      ),
                    )}
                  </div>
                ) : (
                  <p className="empty-text">当前身份尚无已受理任务。</p>
                )}
              </section>
              <section className="system-section">
                <h3>场景执行活动</h3>
                <p>
                  次数来自当前身份的实际运行节点，多目标任务可以涉及多个领域。
                </p>
                {snapshot.scenario_activity.length ? (
                  <div className="scenario-activity">
                    {snapshot.scenario_activity.map((item) => (
                      <div key={item.domain}>
                        <div>
                          <strong>
                            {domainLabels[item.domain] || item.domain}
                          </strong>
                          <span>{item.operations} 个节点</span>
                        </div>
                        <div>
                          {Object.entries(item.by_status).map(
                            ([status, count]) => (
                              <span key={status}>
                                <StateBadge state={status} />
                                <small>{count}</small>
                              </span>
                            ),
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="empty-text">
                    尚未记录场景节点，没有预设执行统计。
                  </p>
                )}
              </section>
              <section className="system-section">
                <h3>实测规划耗时</h3>
                <div className="planning-latency">
                  <div>
                    <span>P50</span>
                    <strong>
                      {snapshot.planning_latency_ms.p50 === null
                        ? "暂无样本"
                        : `${(snapshot.planning_latency_ms.p50 / 1000).toFixed(2)} s`}
                    </strong>
                  </div>
                  <div>
                    <span>P95</span>
                    <strong>
                      {snapshot.planning_latency_ms.p95 === null
                        ? "暂无样本"
                        : `${(snapshot.planning_latency_ms.p95 / 1000).toFixed(2)} s`}
                    </strong>
                  </div>
                  <div>
                    <span>已记录样本</span>
                    <strong>{snapshot.planning_latency_ms.sample_count}</strong>
                  </div>
                </div>
                <p>
                  已记录的规划版本，包含规则与模型；历史版本缺失的出站统计不补造。
                </p>
                <dl className="system-model-metrics">
                  <div>
                    <dt>规划推理记录</dt>
                    <dd>{snapshot.models.planning_invocations}</dd>
                  </div>
                  <div>
                    <dt>模型回退次数</dt>
                    <dd>{snapshot.models.fallback_count}</dd>
                  </div>
                  <div>
                    <dt>模型回退率</dt>
                    <dd>
                      {snapshot.models.fallback_rate === null
                        ? "暂无调用样本"
                        : `${(snapshot.models.fallback_rate * 100).toFixed(1)}%`}
                    </dd>
                  </div>
                  <div>
                    <dt>出站请求尝试</dt>
                    <dd>
                      {snapshot.models.observed_api_calls === null
                        ? "暂未记录"
                        : snapshot.models.observed_api_calls}
                    </dd>
                  </div>
                </dl>
              </section>
              {snapshot.recent_failures.length > 0 && (
                <section className="system-section">
                  <h3>近期异常记录</h3>
                  {snapshot.recent_failures.map((failure, index) => (
                    <button
                      key={`${failure.task_id}-${index}`}
                      className="system-failure"
                      onClick={() => {
                        onClose();
                        onTaskOpen(failure.task_id);
                      }}
                    >
                      <span>
                        <strong>{failure.error_type}</strong>
                        <small>{dateText(failure.occurred_at, true)}</small>
                      </span>
                      <Icon name="chevron" size={15} />
                    </button>
                  ))}
                </section>
              )}
            </>
          )}
          {snapshot?.decision_advice && (
            <section className="system-section">
              <h3>当前身份 Laya 记录</h3>
              <p>
                {snapshot.decision_advice.actual_laya_inference_verified
                  ? `已验证 ${snapshot.decision_advice.verified_inference_records || 0} 条实际推理记录。`
                  : "当前没有已验证的实际 Laya 推理记录。"}
                原始分数不作为准确率展示。
              </p>
              <div className="decision-mode-counts">
                {Object.entries(snapshot.decision_advice.by_mode || {}).map(
                  ([mode, count]) => (
                    <span key={mode}>
                      {mode === "OFF"
                        ? "OFF · 关闭"
                        : mode === "SHADOW"
                          ? "SHADOW · 影子观察"
                          : mode}
                      <strong>{count} 条</strong>
                    </span>
                  ),
                )}
              </div>
            </section>
          )}
          {advice ? (
            <LayaEvidence advice={advice} />
          ) : (
            <section className="system-section">
              <h3>Laya 影子观察</h3>
              <p>
                当前没有可展示的任务观察记录。配置或响应未返回时不展示概率和耗时。
              </p>
            </section>
          )}
          <DecisionEvidencePanel token={token} />
          <section className="system-section">
            <h3>当前身份已注册能力</h3>
            {catalog.loading && !catalog.data ? (
              <Skeleton lines={4} compact />
            ) : catalog.error ? (
              <p className="error-inline">{catalog.error}</p>
            ) : catalog.data ? (
              <>
                <p>
                  目录 {catalog.data.version} ·{" "}
                  {catalog.data.capabilities.length} 项能力
                </p>
                <div className="capability-list">
                  {catalog.data.capabilities.map((capability) => (
                    <div key={capability.kind}>
                      <span>
                        <strong>{capability.label}</strong>
                        <small>
                          {domainLabels[capability.domain] || capability.domain}{" "}
                          · {capability.tool}
                        </small>
                      </span>
                      <span
                        className={`capability-mode ${capability.read_only ? "read" : "write"}`}
                      >
                        {capability.read_only ? "只读" : "须确认"}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </section>
        </div>
        <footer>
          <span className="muted small">
            显示当前身份范围，不是全局管理后台
          </span>
          <button
            className="button secondary"
            onClick={() => {
              metrics.refresh();
              catalog.refresh();
            }}
          >
            <Icon name="refresh" size={16} />
            刷新观测
          </button>
        </footer>
      </aside>
    </div>
  );
}
