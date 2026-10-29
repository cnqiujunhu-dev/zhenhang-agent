import { useRef, useState } from "react";
import { request } from "../api";
import { dateText, Icon, money, useDialogFocus } from "../components";
import { minorFromYuan } from "./moneyInput";
import type { TestDataRow } from "./types";
import type { JsonValue } from "../types";

const labels: Record<string, string> = {
  balance_minor: "账户现金余额",
  amount_minor: "流水或扣款金额",
  merchant: "商户 / 说明",
  category: "分类",
  flag: "核验标记",
  occurred_at: "流水发生时间",
  status: "状态",
  limit_minor: "卡片限额",
  next_due_at: "下次扣款时间",
  risk_level: "风险等级",
  expires_at: "风险档案有效期",
};
function inputValue(field: string, value: JsonValue) {
  if (typeof value === "number" && field.endsWith("_minor"))
    return (value / 100).toFixed(2);
  if (typeof value === "string" && field.endsWith("_at")) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime()))
      return new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
        .format(date)
        .replace(" ", "T");
  }
  return value === null ? "" : String(value);
}
function display(field: string, value: JsonValue) {
  return typeof value === "number" && field.endsWith("_minor")
    ? money(value)
    : typeof value === "string" && field.endsWith("_at")
      ? dateText(value, true)
      : String(value ?? "—");
}
export function TestDataEditor({
  token,
  entity,
  row,
  fields,
  onClose,
  onSaved,
}: {
  token: string;
  entity: string;
  row: TestDataRow;
  fields: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((field) => [field, inputValue(field, row[field])]),
    ),
  );
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [review, setReview] = useState<Record<string, JsonValue> | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => { if (!busy) onClose(); };
  useDialogFocus(true, { dialogRef, onDismiss: close });
  function prepare() {
    setError("");
    try {
      const changes: Record<string, JsonValue> = {};
      for (const field of fields) {
        if (values[field] === inputValue(field, row[field])) continue;
        if (field.endsWith("_minor"))
          changes[field] = minorFromYuan(values[field]);
        else if (field === "risk_level") {
          if (!/^[1-5]$/.test(values[field]))
            throw new Error("风险等级应为1至5的整数。");
          changes[field] = Number(values[field]);
        } else if (field.endsWith("_at")) {
          if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(values[field]))
            throw new Error("请明确完整日期和时间。");
          changes[field] = `${values[field]}:00+08:00`;
        } else changes[field] = values[field];
      }
      if (!Object.keys(changes).length)
        throw new Error("请先修改至少一个服务端允许的字段。");
      if (reason.trim().length < 4)
        throw new Error("请填写至少4个字符的调整理由。");
      setReview(changes);
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "数据输入未通过核对",
      );
    }
  }
  async function save() {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{
        edited: boolean;
        journal_balanced: boolean;
        bank_receipts_changed: boolean;
      }>(
        `/api/v2/test-data/${encodeURIComponent(entity)}/${encodeURIComponent(row.row_id)}`,
        token,
        "PATCH",
        {
          expected_version: row.version,
          changes: review,
          reason: reason.trim(),
        },
      );
      if (
        !result.edited ||
        !result.journal_balanced ||
        result.bank_receipts_changed
      )
        throw new Error("调整回执未满足预期，请刷新核对，避免重复提交。");
      onSaved();
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "数据调整未完成");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="test-editor-backdrop" onClick={(event) => { event.stopPropagation(); close(); }}>
      <section
        ref={dialogRef}
        className="admin-account-modal test-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="test-editor-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">本人白名单数据 · 版本 {row.version}</span>
            <h2 id="test-editor-title">核对开发数据调整</h2>
          </div>
          <button
            className="icon-button"
            aria-label="关闭数据调整"
            onClick={close}
            disabled={busy}
          >
            <Icon name="close" />
          </button>
        </header>
        <p className="admin-small-note">
          仅修改 test
          账号所属的测试数据。执行中或结果未知的任务会阻止调整；不能改写银行回执。
        </p>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        {review ? (
          <div className="admin-change-review">
            <strong>确认本次数据差异</strong>
            <dl className="data-diff">
              {Object.entries(review).map(([field, value]) => (
                <div key={field}>
                  <dt>{labels[field] || field}</dt>
                  <dd>
                    <span>{display(field, row[field])}</span>
                    <Icon name="arrow" size={14} />
                    <strong>{display(field, value)}</strong>
                  </dd>
                </div>
              ))}
            </dl>
            <p>理由：{reason}</p>
            <p>现金差额、独立双分录与流水镜像由服务端按账务守恒处理。</p>
            <div>
              <button className="button primary" onClick={save} disabled={busy}>
                {busy ? "正在调整…" : "确认调整白名单数据"}
              </button>
              <button
                className="button secondary"
                onClick={() => setReview(null)}
                disabled={busy}
              >
                返回编辑
              </button>
            </div>
          </div>
        ) : (
          <div className="admin-form">
            {fields.map((field) => (
              <label key={field}>
                {labels[field] || field}
                {field.endsWith("_minor")
                  ? "（元）"
                  : field.endsWith("_at")
                    ? "（深圳时间）"
                    : ""}
                {field === "flag" ? (
                  <select
                    aria-label={`测试编辑${field}`}
                    value={values[field]}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  >
                    <option value="NORMAL">正常</option>
                    <option value="ANOMALY">待核验</option>
                  </select>
                ) : field === "status" ? (
                  <select
                    aria-label={`测试编辑${field}`}
                    value={values[field]}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  >
                    {(entity === "card"
                      ? ["ACTIVE", "LOCKED", "LOST"]
                      : ["ACTIVE", "CANCELLED"]
                    ).map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={field.endsWith("_at") ? "datetime-local" : "text"}
                    aria-label={`测试编辑${field}`}
                    inputMode={
                      field.endsWith("_minor")
                        ? "decimal"
                        : field === "risk_level"
                          ? "numeric"
                          : "text"
                    }
                    value={values[field]}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  />
                )}
              </label>
            ))}
            <label>
              调整理由
              <textarea
                aria-label="测试数据调整理由"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={2}
                minLength={4}
                maxLength={200}
              />
            </label>
            <button className="button primary" onClick={prepare}>
              核对本次数据差异
              <Icon name="arrow" size={16} />
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
