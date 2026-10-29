import { useRef, useState } from "react";
import { request } from "../api";
import { Icon, useDialogFocus } from "../components";
import type { ManagedAccount } from "./types";

export function AccountEditor({
  token,
  account,
  onClose,
  onSaved,
}: {
  token: string;
  account: ManagedAccount | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const [operation, setOperation] = useState<"create" | "status" | "password">(
    account ? "status" : "create",
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [profile, setProfile] = useState("young_professional");
  const [status, setStatus] = useState(
    account?.status === "ACTIVE" ? "DISABLED" : "ACTIVE",
  );
  const [reason, setReason] = useState("");
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = () => { if (!busy) onClose(); };
  useDialogFocus(true, { dialogRef, onDismiss: close });
  async function apply() {
    setBusy(true);
    setError("");
    try {
      if (!account)
        await request("/api/v2/admin/accounts", token, "POST", {
          username: username.trim().toLowerCase(),
          password,
          ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
          profile,
        });
      else if (operation === "status")
        await request(
          `/api/v2/admin/accounts/${encodeURIComponent(account.account_id)}/status`,
          token,
          "PATCH",
          { status, expected_version: account.version, reason: reason.trim() },
        );
      else
        await request(
          `/api/v2/admin/accounts/${encodeURIComponent(account.account_id)}/reset-password`,
          token,
          "POST",
          {
            new_password: password,
            expected_version: account.version,
            reason: reason.trim(),
          },
        );
      setPassword("");
      onSaved();
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "账号变更未完成");
    } finally {
      setBusy(false);
    }
  }
  const valid = account
    ? reason.trim().length >= 4 &&
      (operation === "status" || password.length >= 6)
    : /^[a-z][a-z0-9_]{2,31}$/.test(username.trim().toLowerCase()) &&
      password.length >= 6;
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        ref={dialogRef}
        className="admin-account-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-editor-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">受控账户管理</span>
            <h2 id="account-editor-title">
              {account ? `管理 ${account.username}` : "创建普通客户账号"}
            </h2>
          </div>
          <button
            className="icon-button"
            onClick={close}
            disabled={busy}
            aria-label="关闭账号管理"
          >
            <Icon name="close" />
          </button>
        </header>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        {review ? (
          <div className="admin-change-review">
            <strong>请核对账号变更</strong>
            <p>
              {account
                ? `${account.username} · ${operation === "status" ? (status === "ACTIVE" ? "启用账号" : "停用账号") : "重置密码"} · 期望版本 ${account.version}`
                : `${username.trim().toLowerCase()} · 普通客户 · ${profile}`}
            </p>
            {account && <p>理由：{reason}</p>}
            <p>
              {operation === "password" || !account
                ? "口令不会在列表或审计中展示。"
                : "停用账号会影响该账号后续登录会话。"}
            </p>
            <div>
              <button
                className="button primary"
                onClick={apply}
                disabled={busy}
              >
                {busy ? "正在提交…" : "确认账号变更"}
              </button>
              <button
                className="button secondary"
                onClick={() => setReview(false)}
                disabled={busy}
              >
                返回修改
              </button>
            </div>
          </div>
        ) : (
          <div className="admin-form">
            {account ? (
              <>
                <label>
                  管理动作
                  <select
                    aria-label="账号管理动作"
                    value={operation}
                    onChange={(event) =>
                      setOperation(event.target.value as "status" | "password")
                    }
                  >
                    <option value="status">启用或停用账号</option>
                    <option value="password">重置登录密码</option>
                  </select>
                </label>
                {operation === "status" && (
                  <label>
                    目标状态
                    <select
                      aria-label="账号目标状态"
                      value={status}
                      onChange={(event) => setStatus(event.target.value)}
                    >
                      <option value="ACTIVE">启用</option>
                      <option value="DISABLED">停用</option>
                    </select>
                  </label>
                )}
              </>
            ) : (
              <>
                <label>
                  用户名
                  <input
                    aria-label="管理创建用户名"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    maxLength={32}
                    autoComplete="off"
                  />
                </label>
                <label>
                  显示名称
                  <input
                    aria-label="管理创建显示名称"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    maxLength={40}
                  />
                </label>
                <label>
                  丰富账套
                  <select
                    aria-label="管理创建账套"
                    value={profile}
                    onChange={(event) => setProfile(event.target.value)}
                  >
                    <option value="young_professional">职场账套</option>
                    <option value="family">家庭账套</option>
                    <option value="pensioner">养老金账套</option>
                    <option value="investor">稳健投资账套</option>
                  </select>
                </label>
                <p className="admin-small-note">
                  创建权限固定为普通客户，不通过此表单增设管理员或数据测试权限。
                </p>
              </>
            )}
            {operation === "password" || !account ? (
              <label>
                设置新口令
                <input
                  aria-label="管理设置密码"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  maxLength={128}
                  autoComplete="new-password"
                />
              </label>
            ) : null}
            {account && (
              <label>
                变更理由
                <textarea
                  aria-label="账号管理变更理由"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  minLength={4}
                  maxLength={200}
                  rows={2}
                />
              </label>
            )}
            <button
              className="button primary"
              disabled={!valid}
              onClick={() => setReview(true)}
            >
              核对账号变更
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
