import { useRef, useState } from "react";
import {
  dateText,
  Icon,
  money,
  Skeleton,
  StateBadge,
  StructuredData,
  useDialogFocus,
} from "../components";
import { useEndpoint } from "../useEndpoint";
import { TestDataEditor } from "./TestDataEditor";
import type { TestDataRow, TestDataSnapshot } from "./types";
import type { JsonValue } from "../types";

type Entity = "account" | "history" | "card" | "subscription" | "risk_profile";
const labels: Record<Entity, string> = {
  account: "账户现金",
  history: "历史流水",
  card: "卡片数据",
  subscription: "订阅代扣",
  risk_profile: "风险档案",
};
export function TestDataTools({
  token,
  onClose,
  onDataChanged,
}: {
  token: string;
  onClose: () => void;
  onDataChanged?: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { dialogRef, onDismiss: onClose });
  const [entity, setEntity] = useState<Entity>("account");
  const [historyPage, setHistoryPage] = useState(1);
  const [editing, setEditing] = useState<{
    entity: Entity;
    row: TestDataRow;
  } | null>(null);
  const endpoint = useEndpoint<TestDataSnapshot>(
    `/api/v2/test-data?history_page=${historyPage}&history_page_size=30`,
    token,
  );
  const snapshot = endpoint.data;
  const rows = snapshot
    ? entity === "account"
      ? [snapshot.account]
      : entity === "risk_profile"
        ? [snapshot.risk_profile]
        : entity === "card"
          ? snapshot.cards
          : entity === "subscription"
            ? snapshot.subscriptions
            : snapshot.history
    : [];
  const fields = snapshot?.editable_fields[entity] || [];
  function label(row: TestDataRow) {
    return String(
      row.name ||
        row.merchant ||
        (row.last4 && `尾号 ${row.last4}`) ||
        (row.risk_level && `风险等级 R${row.risk_level}`) ||
        "本人数据",
    );
  }
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer test-data-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="test-tools-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">专用 test 账号 · 本人白名单</span>
            <h2 id="test-tools-title">开发数据工具</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="关闭开发数据工具"
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          <div className="test-data-boundary">
            <Icon name="shield" size={20} />
            <p>
              仅编辑服务端白名单。每次调整需要理由、版本与独立核对，已完成的银行回执保持原值。
            </p>
          </div>
          {endpoint.loading && !snapshot && <Skeleton lines={6} />}
          {endpoint.error && (
            <p className="login-error" role="alert">
              {endpoint.error}
            </p>
          )}
          {snapshot && (
            <>
              <div className="test-data-scope">
                <span>{snapshot.scope}</span>
                <small>
                  银行业务时间 {dateText(snapshot.business_time, true)}
                </small>
              </div>
              <div
                className="test-data-tabs"
                role="tablist"
                aria-label="测试数据类型"
              >
                {(Object.keys(labels) as Entity[]).map((key) => (
                  <button
                    key={key}
                    role="tab"
                    aria-selected={entity === key}
                    onClick={() => setEntity(key)}
                  >
                    {labels[key]}
                  </button>
                ))}
              </div>
              <div className="test-data-rows">
                {rows.filter(Boolean).map((row) => (
                  <article key={row.row_id}>
                    <header>
                      <strong>{label(row)}</strong>
                      <span>版本 {row.version}</span>
                      <button
                        className="button secondary compact"
                        disabled={!fields.length}
                        onClick={() => setEditing({ entity, row })}
                      >
                        编辑白名单字段
                      </button>
                    </header>
                    <div className="test-row-values">
                      {fields.map((field) => (
                        <div key={field}>
                          <span>
                            {{
                              balance_minor: "现金余额",
                              amount_minor: "金额",
                              merchant: "商户",
                              category: "分类",
                              flag: "核验标记",
                              occurred_at: "发生时间",
                              status: "状态",
                              limit_minor: "限额",
                              next_due_at: "下次扣款",
                              risk_level: "风险等级",
                              expires_at: "有效截止",
                            }[field] || field}
                          </span>
                          <strong>
                            {typeof row[field] === "number" &&
                            field.endsWith("_minor") ? (
                              money(row[field] as number)
                            ) : typeof row[field] === "string" &&
                              field.endsWith("_at") ? (
                              dateText(String(row[field]), true)
                            ) : field === "status" ? (
                              <StateBadge state={String(row[field])} />
                            ) : (
                              String(row[field] ?? "—")
                            )}
                          </strong>
                        </div>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
              {entity === "history" && (
                <div className="pagination">
                  <span>
                    共 {snapshot.history_total} 笔 · 第 {snapshot.history_page}{" "}
                    页
                  </span>
                  <div>
                    <button
                      disabled={historyPage <= 1}
                      onClick={() => setHistoryPage((value) => value - 1)}
                    >
                      上一页
                    </button>
                    <button
                      disabled={
                        historyPage * snapshot.history_page_size >=
                        snapshot.history_total
                      }
                      onClick={() => setHistoryPage((value) => value + 1)}
                    >
                      下一页
                    </button>
                  </div>
                </div>
              )}
              <details className="test-data-edit-history">
                <summary>最近数据调整记录</summary>
                <StructuredData
                  data={snapshot.recent_edits as unknown as JsonValue}
                  technical
                />
              </details>
            </>
          )}
        </div>
        <footer>
          <span className="muted small">不是普通客户银行操作入口</span>
          <button className="button secondary" onClick={endpoint.refresh}>
            <Icon name="refresh" size={16} />
            刷新数据
          </button>
        </footer>
      </aside>
      {editing && (
        <TestDataEditor
          token={token}
          entity={editing.entity}
          row={editing.row}
          fields={snapshot?.editable_fields[editing.entity] || []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            endpoint.refresh();
            onDataChanged?.();
          }}
        />
      )}
    </div>
  );
}
