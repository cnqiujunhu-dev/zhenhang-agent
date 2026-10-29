import { useEffect, useState } from "react";
import {
  Brand,
  dateText,
  Icon,
  Skeleton,
  StateBadge,
  StructuredData,
} from "../components";
import { useEndpoint } from "../useEndpoint";
import { AccountEditor } from "./AccountEditor";
import { AdminTraceDrawer } from "./AdminTraceDrawer";
import { ModelRoutingPanel } from "./ModelRoutingPanel";
import { AdminModelObservations } from "./AdminModelObservations";
import { AdminConversationDrawer } from "./AdminConversationDetails";
import { JudgeEvidenceGuide } from "./JudgeEvidenceGuide";
import { KnowledgePanel } from "./KnowledgePanel";
import { CapabilityHealthPanel } from "./CapabilityHealthPanel";
import { ImageDiagnosticsPanel } from "./ImageDiagnosticsPanel";
import { CreditReviewPanel } from "./CreditReviewPanel";
import { AccountRiskLabels } from './AccountRiskLabels';
import type { AccountIdentity } from "../auth/types";
import type { JsonValue } from "../types";
import type { AdminOverview, ManagedAccount, PageResult } from "./types";

type AdminPage =
  | "overview"
  | "tasks"
  | "conversations"
  | "jobs"
  | "audit"
  | "policies"
  | "routing"
  | "knowledge"
  | "images"
  | "credit"
  | "evidence"
  | "accounts";
const tabs: {
  id: AdminPage;
  label: string;
  icon:
    "workflow" | "eye" | "clock" | "history" | "shield" | "spark" | "family";
}[] = [
  { id: "overview", label: "系统运行", icon: "workflow" },
  { id: "evidence", label: "专家证据导览", icon: "eye" },
  { id: "tasks", label: "任务只读检查", icon: "eye" },
  { id: "conversations", label: "会话只读检查", icon: "history" },
  { id: "jobs", label: "执行队列", icon: "clock" },
  { id: "audit", label: "运维审计", icon: "history" },
  { id: "policies", label: "策略决定", icon: "shield" },
  { id: "routing", label: "模型路由", icon: "spark" },
  { id: "knowledge", label: "知识与产品", icon: "spark" },
  { id: "images", label: "图片处理诊断", icon: "eye" },
  { id: "accounts", label: "账号管理", icon: "family" },
  { id: "credit", label: "信用申请审核", icon: "shield" },
];
const roleLabels: Record<string, string> = {
  CUSTOMER: "普通客户",
  DEV_TEST: "数据测试",
  ADMIN: "运维管理员",
  FAMILY_HELPER: "家庭协助",
};
const columns: Record<string, { key: string; label: string }[]> = {
  accounts: [
    { key: "username", label: "用户名" },
    { key: "role", label: "角色" },
    { key: "status", label: "状态" },
    { key: "profile", label: "账套类型" },
    { key: "version", label: "版本" },
    { key: "last_login_at", label: "最近登录" },
  ],
  tasks: [
    { key: "username", label: "所属账号" },
    { key: "intent_type", label: "意图类型" },
    { key: "state", label: "任务状态" },
    { key: "version", label: "版本" },
    { key: "updated_at", label: "更新于" },
  ],
  conversations: [
    { key: "username", label: "所属账号" },
    { key: "title", label: "会话标题" },
    { key: "status", label: "会话状态" },
    { key: "turn_count", label: "消息轮数" },
    { key: "updated_at", label: "更新于" },
  ],
  jobs: [
    { key: "kind", label: "作业类型" },
    { key: "status", label: "状态" },
    { key: "attempts", label: "已尝试" },
    { key: "max_attempts", label: "尝试上限" },
    { key: "next_run_at", label: "下次执行" },
  ],
  audit: [
    { key: "created_at", label: "时间" },
    { key: "kind", label: "审计类型" },
    { key: "action", label: "动作" },
    { key: "reason", label: "安全摘要 / 理由" },
    { key: "seq", label: "事件序号" },
  ],
  policies: [
    { key: "decision", label: "策略决定" },
    { key: "reason_codes", label: "原因代码" },
    { key: "policy_version", label: "策略版本" },
    { key: "created_at", label: "记录时间" },
  ],
};
export function AdminWorkspace({
  token,
  account,
  onLogout,
  onPassword,
}: {
  token: string;
  account: AccountIdentity;
  onLogout: () => void;
  onPassword: () => void;
}) {
  const [tab, setTab] = useState<AdminPage>("overview");
  const [page, setPage] = useState(1);
  const [auditKind, setAuditKind] = useState("ALL");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [conversationId, setConversationId] = useState("");
  const allowedRole = account.role === "ADMIN";
  const [editAccount, setEditAccount] = useState<
    ManagedAccount | null | undefined
  >(undefined);
  const overview = useEndpoint<AdminOverview>(
    allowedRole && tab === "overview" ? "/api/v2/admin/overview" : null,
    token,
  );
  const table = useEndpoint<PageResult<Record<string, JsonValue>>>(
    allowedRole &&
      [
        "tasks",
        "conversations",
        "jobs",
        "audit",
        "policies",
        "accounts",
      ].includes(tab)
      ? `/api/v2/admin/${tab}?page=${page}&page_size=20${tab === "audit" ? `&kind=${auditKind}` : ""}`
      : null,
    token,
  );
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector('[role="dialog"]')) {
        setSidebarOpen(false);
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, []);
  const refresh = () => {
    overview.refresh();
    table.refresh();
  };
  const col = columns[tab] || [];
  function display(key: string, value: JsonValue | undefined) {
    if (value === null || value === undefined) return "—";
    if (["status", "state"].includes(key))
      return (
        <StateBadge
          state={String(value)}
          label={
            tab === "jobs" && value === "UNKNOWN" ? "历史提交未知" : undefined
          }
        />
      );
    if (key === "role") return roleLabels[String(value)] || String(value);
    if (key.endsWith("_at")) return dateText(String(value), true);
    return Array.isArray(value) ? value.join("、") : String(value);
  }
  if (!allowedRole)
    return (
      <p className="login-error" role="alert">
        此界面仅供管理员读取运维记录。
      </p>
    );
  return (
    <div className="admin-shell">
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭运维导航"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`admin-sidebar ${sidebarOpen ? "open" : ""}`}>
        <Brand />
        <span className="admin-sidebar-label">独立运维后台</span>
        <nav aria-label="运维后台功能">
          {tabs.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${item.id === tab ? "active" : ""}`}
              onClick={() => {
                setTab(item.id);
                setPage(1);
                setSidebarOpen(false);
              }}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="admin-privilege-note">
          <Icon name="shield" size={18} />
          <p>
            只读银行运维
            <br />
            账号与受控路由管理
            <br />
            不代客户确认或付款
          </p>
        </div>
      </aside>
      <div className="admin-main">
        <header className="admin-topbar">
          <div>
            <button
              className="icon-button mobile-menu"
              aria-label="打开运维导航"
              onClick={() => setSidebarOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <span>
              运维控制台 / {tabs.find((item) => item.id === tab)?.label}
            </span>
          </div>
          <div>
            <span>{account.username} · 管理员</span>
            <button className="text-button" onClick={onPassword}>
              修改密码
            </button>
            <button className="text-button" onClick={onLogout}>
              退出登录
            </button>
          </div>
        </header>
        <main className="admin-content">
          <div className="admin-page-title">
            <div>
              <span className="eyebrow">真实运行与权限边界</span>
              <h1>{tabs.find((item) => item.id === tab)?.label}</h1>
              <p>运维来自服务端记录，银行执行仍由客户独立确认。</p>
            </div>
            {tab !== "routing" && tab !== "evidence" && tab !== "knowledge" && (
              <button className="button secondary" onClick={refresh}>
                <Icon name="refresh" size={16} />
                刷新
              </button>
            )}
          </div>
          {tab === "overview" ? (
            <>
              <CapabilityHealthPanel token={token} role={account.role}/>
              {overview.loading && !overview.data && <Skeleton lines={5} />}
              {overview.error && (
                <p className="login-error" role="alert">
                  {overview.error}
                </p>
              )}
              {overview.data && (
                <>
                  <div
                    className={`admin-health-banner ${overview.data.runtime_health.status === "ready" ? "ready" : "unready"}`}
                  >
                    <Icon
                      name={
                        overview.data.runtime_health.status === "ready"
                          ? "check"
                          : "info"
                      }
                      size={23}
                    />
                    <div>
                      <strong>
                        {overview.data.runtime_health.status === "ready"
                          ? "执行服务运行正常"
                          : "执行服务暂未就绪"}
                      </strong>
                      <small>
                        数据库 {overview.data.runtime_health.database} · Worker{" "}
                        {overview.data.runtime_health.worker} ·{" "}
                        {overview.data.runtime_health.online_workers} 个在线
                      </small>
                    </div>
                    <span>{overview.data.scope}</span>
                  </div>
                  <div className="admin-overview-grid">
                    <div>
                      <span>已登记账号</span>
                      <strong>{overview.data.accounts.total}</strong>
                    </div>
                    <div>
                      <span>已受理任务</span>
                      <strong>{overview.data.tasks.total}</strong>
                    </div>
                    <div>
                      <span>历史工作流作业</span>
                      <strong>{overview.data.jobs.total}</strong>
                    </div>
                  </div>
                  <div className="admin-summary-grid">
                    {[
                      {
                        title: "账号角色",
                        values: overview.data.accounts.by_role,
                      },
                      {
                        title: "任务状态",
                        values: overview.data.tasks.by_state,
                      },
                      {
                        title: "历史作业状态",
                        values: overview.data.jobs.by_status,
                        historicalJobs: true,
                      },
                      ...(overview.data.policies
                        ? [
                            {
                              title: "策略决定",
                              values: overview.data.policies.by_decision,
                            },
                          ]
                        : []),
                    ].map((section) => (
                      <section className="panel" key={section.title}>
                        <div className="small-panel-heading">
                          <h2>{section.title}</h2>
                        </div>
                        <div className="admin-distribution">
                          {Object.entries(section.values).length ? (
                            Object.entries(section.values).map(
                              ([state, count]) => (
                                <div key={state}>
                                  <span>
                                    {roleLabels[state] || (
                                      <StateBadge
                                        state={state}
                                        label={
                                          "historicalJobs" in section &&
                                          section.historicalJobs &&
                                          state === "UNKNOWN"
                                            ? "历史提交未知"
                                            : undefined
                                        }
                                      />
                                    )}
                                  </span>
                                  <strong>{count}</strong>
                                </div>
                              ),
                            )
                          ) : (
                            <p>暂无已记录数据</p>
                          )}
                        </div>
                        {"historicalJobs" in section &&
                          section.historicalJobs && (
                            <div className="admin-job-scope-note">
                              <p>历史作业分布，不等于当前待查银行结果。</p>
                              <span>
                                未解决的活跃执行操作{" "}
                                <strong>
                                  {overview.data?.jobs
                                    .active_unknown_operations ?? "未返回"}
                                </strong>
                              </span>
                            </div>
                          )}
                      </section>
                    ))}
                  </div>
                  {overview.data.models && (
                    <AdminModelObservations data={overview.data.models} />
                  )}
                </>
              )}
            </>
          ) : tab === "routing" ? (
            <ModelRoutingPanel token={token} />
          ) : tab === "evidence" ? (
            <JudgeEvidenceGuide token={token} role={account.role} onTaskOpen={setTaskId} />
          ) : tab === "knowledge" ? (
            <KnowledgePanel token={token} />
          ) : tab === "images" ? (
            <ImageDiagnosticsPanel token={token} role={account.role}/>
          ) : tab === "credit" ? (
            <CreditReviewPanel token={token}/>
          ) : (
            <section className="panel admin-table-panel">
              {tab === 'accounts' && <AccountRiskLabels token={token}/>}
              <div className="panel-heading">
                <h2>{tabs.find((item) => item.id === tab)?.label}</h2>
                {tab === "audit" && (
                  <select
                    className="admin-audit-filter"
                    aria-label="审计记录类型"
                    value={auditKind}
                    onChange={(event) => {
                      setAuditKind(event.target.value);
                      setPage(1);
                    }}
                  >
                    <option value="ALL">全部安全审计</option>
                    <option value="ACCESS">账号与运维访问</option>
                    <option value="WORKFLOW">工作流执行事件</option>
                  </select>
                )}
                {tab === "accounts" && (
                  <button
                    className="button primary"
                    onClick={() => setEditAccount(null)}
                  >
                    创建普通客户
                    <Icon name="arrow" size={16} />
                  </button>
                )}
              </div>
              {tab === "jobs" && (
                <p className="admin-small-note admin-panel-body">
                  这里保留历史作业状态，不等于当前待查银行结果。实际任务和操作结果请进入“任务只读检查”查看。
                </p>
              )}
              {table.loading && !table.data && (
                <div className="admin-panel-body">
                  <Skeleton lines={5} />
                </div>
              )}
              {table.error && (
                <p className="login-error admin-panel-body" role="alert">
                  {table.error}
                </p>
              )}
              {table.data && (
                <>
                  <div className="admin-table-scroll">
                    <table>
                      <thead>
                        <tr>
                          {col.map((column) => (
                            <th key={column.key}>{column.label}</th>
                          ))}
                          <th>只读详情 / 管理</th>
                        </tr>
                      </thead>
                      <tbody>
                        {table.data.items.map((row, index) => (
                          <tr
                            key={String(
                              row.account_id ||
                                row.conversation_id ||
                                row.task_id ||
                                row.job_id ||
                                row.event_id ||
                                index,
                            )}
                          >
                            {col.map((column) => (
                              <td key={column.key}>
                                {display(column.key, row[column.key])}
                              </td>
                            ))}
                            <td>
                              {tab === "accounts" ? (
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    setEditAccount(
                                      row as unknown as ManagedAccount,
                                    )
                                  }
                                >
                                  账号管理
                                </button>
                              ) : tab === "tasks" ? (
                                <button
                                  className="text-button"
                                  onClick={() => setTaskId(String(row.task_id))}
                                >
                                  查看只读运行
                                </button>
                              ) : tab === "conversations" ? (
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    setConversationId(
                                      String(row.conversation_id),
                                    )
                                  }
                                >
                                  查看会话路径
                                </button>
                              ) : (
                                <details className="admin-row-details">
                                  <summary>安全元数据</summary>
                                  <StructuredData data={row} technical />
                                </details>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!table.data.items.length && (
                      <p className="extension-empty admin-panel-body">
                        当前没有符合条件的记录。
                      </p>
                    )}
                  </div>
                  <div className="pagination">
                    <span>
                      共 {table.data.total} 条 · 第 {table.data.page} 页
                    </span>
                    <div>
                      <button
                        disabled={page <= 1}
                        onClick={() => setPage((value) => value - 1)}
                      >
                        上一页
                      </button>
                      <button
                        disabled={
                          page * table.data.page_size >= table.data.total
                        }
                        onClick={() => setPage((value) => value + 1)}
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                </>
              )}
            </section>
          )}
        </main>
      </div>
      {taskId && (
        <AdminTraceDrawer
          token={token}
          taskId={taskId}
          onClose={() => setTaskId("")}
        />
      )}
      {conversationId && (
        <AdminConversationDrawer
          token={token}
          conversationId={conversationId}
          onClose={() => setConversationId("")}
          onTaskOpen={(id) => {
            setConversationId("");
            setTaskId(id);
          }}
        />
      )}
      {editAccount !== undefined && (
        <AccountEditor
          token={token}
          account={editAccount}
          onClose={() => setEditAccount(undefined)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
