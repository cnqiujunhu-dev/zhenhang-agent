import { useEffect, useRef, useState } from "react";
import {
  dateText,
  Icon,
  Skeleton,
  StateBadge,
  StructuredData,
  useDialogFocus,
} from "../components";
import { useEndpoint } from "../useEndpoint";
import { conversationModeLabels } from "../conversationState";
import { sourceKindLabel } from "../sourcePresentation";
import type { JsonValue } from "../types";

export interface AdminConversationSummary {
  conversation_id: string;
  title: string;
  status: string;
  username: string | null;
  user_id: string;
  sandbox_id: string;
  current_task_id: string | null;
  turn_count: number;
  created_at: string;
  updated_at: string;
}
export interface AdminConversationTurn {
  turn_id: string;
  conversation_id: string;
  sequence: number;
  message: string;
  status: string;
  mode: string | null;
  answer: string | null;
  task_id: string | null;
  requested_task_id: string | null;
  task_relationship: string | null;
  sources_authorized: boolean;
  sources: {
    source_id: string;
    title: string;
    kind: string;
    reference: string;
    observed_at?: string | null;
    version?: JsonValue;
  }[];
  field_sources: JsonValue[];
  diagnostics: Record<string, JsonValue>;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}
export interface AdminConversationEvidence {
  scope:
    "ADMIN_CONVERSATION_EVIDENCE" | "ADMIN_TASK_LINKED_CONVERSATION_EVIDENCE";
  conversation_id?: string;
  task_id?: string;
  conversations: AdminConversationSummary[];
  items: AdminConversationTurn[];
  total: number;
  page: number;
  page_size: number;
  read_only: boolean;
  bank_confirmation_permitted: boolean;
}
const relationLabels: Record<string, string> = {
  CREATED: "本轮新建任务",
  CONTINUED: "继续已关联任务",
  STATUS: "查询关联任务状态",
  CURRENT: "当前关联任务",
};
const recorded = (value: JsonValue | undefined) =>
  value == null ? "未记录" : String(value);

/** ADMIN-only evidence endpoints; this component has no customer mutation path. */
export function AdminConversationDetails({
  token,
  taskId,
  conversationId,
  onTaskOpen,
}: {
  token: string;
  taskId?: string;
  conversationId?: string;
  onTaskOpen?: (id: string) => void;
}) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [token, taskId, conversationId]);
  const endpoint = useEndpoint<AdminConversationEvidence>(
    taskId
      ? `/api/v2/admin/tasks/${encodeURIComponent(taskId)}/conversation-evidence?page=${page}&page_size=25`
      : conversationId
        ? `/api/v2/admin/conversations/${encodeURIComponent(conversationId)}/evidence?page=${page}&page_size=25`
        : null,
    token,
  );
  const data = endpoint.data;
  const safeScope = Boolean(
    data &&
    data.read_only === true &&
    data.bank_confirmation_permitted === false &&
    (taskId
      ? data.scope === "ADMIN_TASK_LINKED_CONVERSATION_EVIDENCE" &&
        data.task_id === taskId
      : data.scope === "ADMIN_CONVERSATION_EVIDENCE" &&
        data.conversation_id === conversationId),
  );
  return (
    <section
      className="evidence-section admin-conversation-details"
      aria-label="管理员会话只读证据"
    >
      <div className="small-panel-heading">
        <h3>{taskId ? "与任务精确关联的会话消息" : "会话消息与咨询路径"}</h3>
        <button
          className="icon-button"
          aria-label="刷新会话只读证据"
          onClick={endpoint.refresh}
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>
      <p className="admin-small-note">
        {taskId
          ? "仅展示本任务或已归一父任务的关联消息，不展开整段无关会话。"
          : "咨询与办理分流来自服务端记录；没有银行任务的咨询不是执行失败。"}
        管理员只能读取脱敏记录，不能续写会话、确认客户操作或代客户付款。
      </p>
      {endpoint.loading && <Skeleton lines={3} compact />}
      {endpoint.error && (
        <p className="error-inline" role="alert">
          会话证据未能加载：{endpoint.error}
        </p>
      )}
      {data && !safeScope && (
        <p className="error-inline" role="alert">
          未返回匹配的管理员只读投影，未展示会话内容。
        </p>
      )}
      {safeScope && data && (
        <>
          {data.conversations.map((conversation) => (
            <details
              className="structured-technical"
              key={conversation.conversation_id}
            >
              <summary>
                {conversation.title || "未命名会话"} ·{" "}
                {conversation.username || "账号未记录"} ·{" "}
                {conversation.turn_count} 轮
              </summary>
              <StructuredData
                data={conversation as unknown as JsonValue}
                technical
              />
            </details>
          ))}
          {!data.items.length && (
            <p className="empty-text">没有符合当前关联范围的会话记录。</p>
          )}
          {data.items.map((turn) => (
            <details
              className="node-detail admin-conversation-turn"
              key={turn.turn_id}
              data-turn-id={turn.turn_id}
            >
              <summary>
                <span>
                  消息 {turn.sequence} ·{" "}
                  {turn.mode
                    ? conversationModeLabels[turn.mode] || turn.mode
                    : "分流未完成"}
                </span>
                <StateBadge state={turn.status} />
              </summary>
              <p className="admin-small-note">
                收到 {dateText(turn.created_at, true)} · 完成{" "}
                {turn.completed_at
                  ? dateText(turn.completed_at, true)
                  : "尚无完成记录"}
              </p>
              <h4>脱敏用户消息</h4>
              <p
                className="trace-message"
                style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {turn.message}
              </p>
              <h4>服务端回答</h4>
              <p
                className="trace-message"
                style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {turn.sources_authorized !== true
                  ? "相关资料的授权范围已失效或尚未核验，历史解释与来源未展示。"
                  : turn.answer || "尚未记录回答。"}
              </p>
              <p className="admin-small-note">
                消息处理状态不是银行执行回执；是否付款或变更仍以关联任务的核验结果为准。
              </p>
              {(turn.task_id || turn.requested_task_id) && (
                <div className="structured-technical">
                  <p>
                    请求关联任务：
                    <code>{turn.requested_task_id || "本轮未指定"}</code>
                  </p>
                  <p>
                    实际关联任务：<code>{turn.task_id || "未产生任务"}</code> ·{" "}
                    {turn.task_relationship
                      ? relationLabels[turn.task_relationship] ||
                        turn.task_relationship
                      : "关联关系未记录"}
                  </p>
                  {turn.task_id && onTaskOpen && (
                    <><button
                      className="text-button"
                      onClick={() => onTaskOpen(turn.task_id!)}
                    >
                      查看关联任务的只读运行
                    </button><p className="admin-small-note">Laya 只读候选属于关联任务及其计划版本，请在该任务诊断中查看；不套用到本轮消息。</p></>
                  )}
                </div>
              )}
              {turn.sources_authorized === true && (
                <>
                  <details className="turn-sources">
                    <summary>
                      消息依据与资料来源 · {turn.sources.length} 项
                    </summary>
                    {turn.sources.map((source) => (
                      <div key={source.source_id}>
                        <strong>{source.title}</strong>
                        <small>
                          {sourceKindLabel(source.kind)} · {source.kind}
                          {source.observed_at
                            ? ` · ${dateText(source.observed_at, true)}`
                            : ""}
                        </small>
                        <code>{source.reference}</code>
                        {source.version !== undefined && (
                          <small>来源版本：{recorded(source.version)}</small>
                        )}
                      </div>
                    ))}
                    {!turn.sources.length && (
                      <p className="empty-text">
                        本轮没有返回可展示的资料来源。
                      </p>
                    )}
                  </details>
                  <details className="turn-sources">
                    <summary>
                      字段来源与任务绑定 · {turn.field_sources.length} 项
                    </summary>
                    <p className="admin-small-note">
                      只读展示服务端保存的本轮原文/上下文来源、任务与计划版本，不由管理员界面补齐金额或目标。
                    </p>
                    {turn.field_sources.length ? (
                      <StructuredData data={turn.field_sources} technical />
                    ) : (
                      <p className="empty-text">本轮没有已保存的字段绑定。</p>
                    )}
                  </details>
                </>
              )}
              <details className="turn-diagnostics">
                <summary>消息分流、咨询诊断与模型观测</summary>
                {turn.error_code && <p>错误码：{turn.error_code}</p>}
                <p>
                  实际提供方 / 模型：
                  {recorded(turn.diagnostics.actual_provider)} /{" "}
                  {recorded(turn.diagnostics.actual_model)}
                </p>
                <p>
                  本轮已记录出站尝试：
                  {recorded(turn.diagnostics.model_api_call_count)} ·
                  未知不当作零，也不是节省次数。
                </p>
                <StructuredData data={turn.diagnostics} technical />
              </details>
            </details>
          ))}
          <div className="pagination">
            <span>
              共 {data.total} 条关联消息 · 第 {data.page} 页
            </span>
            <div>
              <button
                disabled={page <= 1 || endpoint.loading}
                onClick={() => setPage((value) => value - 1)}
              >
                上一页
              </button>
              <button
                disabled={
                  endpoint.loading || page * data.page_size >= data.total
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
  );
}

export function AdminConversationDrawer({
  token,
  conversationId,
  onClose,
  onTaskOpen,
}: {
  token: string;
  conversationId: string;
  onClose: () => void;
  onTaskOpen: (id: string) => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { dialogRef, onDismiss: onClose });
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer admin-conversation-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-conversation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">管理员只读会话审计</span>
            <h2 id="admin-conversation-title">会话与消息路径</h2>
          </div>
          <button
            className="icon-button"
            aria-label="关闭会话只读检查"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          <AdminConversationDetails
            token={token}
            conversationId={conversationId}
            onTaskOpen={onTaskOpen}
          />
        </div>
      </aside>
    </div>
  );
}
