// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage, LanguageSelector } from "../CustomerLanguageProvider";
import { customerUiText } from "../customerLanguage";
import { useState } from "react";
import { Brand, Icon } from "../components";
import type { LoginInput, RegisterInput } from "./types";

const profiles = [
  { id: "young_professional", label: "职场生活账套" },
  { id: "family", label: "家庭生活账套" },
  { id: "pensioner", label: "养老金生活账套" },
  { id: "investor", label: "稳健投资账套" },
];
export function LoginScreen({
  onLogin,
  onRegister,
  busy,
  error,
  notice,
}: {
  onLogin: (input: LoginInput) => Promise<void>;
  onRegister: (input: RegisterInput) => Promise<void>;
  busy: boolean;
  error: string;
  notice?: string;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [profile, setProfile] = useState("young_professional");
  const [localError, setLocalError] = useState("");
  function changeMode(nextMode: "login" | "register") {
    if (busy) return;
    setMode(nextMode);
    setLocalError("");
    setPassword("");
    setConfirmPassword("");
  }
  function navigateTab(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (busy || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextMode = event.key === "Home" ? "login" : event.key === "End" ? "register" : mode === "login" ? "register" : "login";
    changeMode(nextMode);
    document.getElementById(`login-tab-${nextMode}`)?.focus();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (mode === "register" && password !== confirmPassword) {
      setLocalError("两次输入的密码不一致。");
      document.getElementById("register-confirm-password")?.focus();
      return;
    }
    const input = { username: username.trim().toLowerCase(), password };
    if (mode === "register")
      await onRegister({
        ...input,
        ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
        profile,
      });
    else await onLogin(input);
    setPassword("");
    setConfirmPassword("");
  }
  return (
    <div className="login-shell">
      <div className="login-language"><LanguageSelector /></div>
      <aside className="login-story">
        <Brand />
        <div className="login-story-content">
          <span className="eyebrow"><CustomerText text="圳行 · 对话式金融服务" /></span>
          <h1><CustomerText text="你的金融需求，" /><br /><CustomerText text="直接说就好。" /></h1>
          <p><CustomerText text="查询账户、管理账单和办理服务，从一次自然对话开始。" /></p>
          <div className="login-capabilities">
            <span>
              <Icon name="workflow" /><CustomerText text="一站式金融服务" /></span>
            <span>
              <Icon name="shield" /><CustomerText text="逐次确认与授权" /></span>
            <span>
              <Icon name="eye" /><CustomerText text="办理记录随时查" /></span>
          </div>
          <div className="login-illustration" aria-hidden="true">
            <span className="login-orbit" />
            <span className="login-orbit second" />
            <span className="login-core">
              <Icon name="spark" size={44} />
            </span>
            <span className="login-node node-one">
              <Icon name="card" size={22} />
            </span>
            <span className="login-node node-two">
              <Icon name="leaf" size={22} />
            </span>
            <span className="login-node node-three">
              <Icon name="family" size={22} />
            </span>
          </div>
        </div>
        <footer><CustomerText text="个人金融服务 · 办理记录持续保存" /></footer>
      </aside>
      <main className="login-main">
        <div className="login-card">
          <div className="login-title">
            <span className="eyebrow"><CustomerText text="欢迎来到圳行Agent" /></span>
            <h2>
              {mode === "login" ? customerUiText("登录你的金融工作台") : customerUiText("创建自己的账户")}
            </h2>
            <p>
              {mode === "login"
                ? customerUiText("登录后继续上次对话，或开始新的服务。")
                : customerUiText("注册创建独立的丰富账套，权限为普通客户。")}
            </p>
          </div>
          <div className="login-tabs" role="tablist" aria-label={customerUiText("账户入口")}>
            <button
              type="button"
              role="tab"
              id="login-tab-login"
              aria-controls="login-panel"
              aria-selected={mode === "login"}
              tabIndex={mode === "login" ? 0 : -1}
              disabled={busy}
              onKeyDown={navigateTab}
              onClick={() => changeMode("login")}
            ><CustomerText text="账号登录" /></button>
            <button
              type="button"
              role="tab"
              id="login-tab-register"
              aria-controls="login-panel"
              aria-selected={mode === "register"}
              tabIndex={mode === "register" ? 0 : -1}
              disabled={busy}
              onKeyDown={navigateTab}
              onClick={() => changeMode("register")}
            ><CustomerText text="自主注册" /></button>
          </div>
          {(error || localError) && (
            <p className="login-error" role="alert" id="login-form-feedback">
              <Icon name="info" size={17} />
              {customerUiText(localError || error)}
            </p>
          )}
          {notice && (
            <p className="login-notice" role="status">
              {customerUiText(notice || "")}
            </p>
          )}
          <form id="login-panel" role="tabpanel" aria-labelledby={`login-tab-${mode}`} aria-busy={busy} aria-describedby={error || localError ? "login-form-feedback" : undefined} onSubmit={submit}>
            <label><CustomerText text="用户名" /><input
                aria-label={customerUiText("登录用户名")}
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder={customerUiText("输入账户用户名")}
                minLength={3}
                maxLength={32}
                required
                disabled={busy}
              />
            </label>
            {mode === "register" && (
              <>
                <label><CustomerText text="显示名称（可选）" /><input
                    aria-label={customerUiText("注册显示名称")}
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    maxLength={40}
                    placeholder={customerUiText("在工作台显示的名称")}
                    disabled={busy}
                  />
                </label>
                <label><CustomerText text="初始账套类型" /><select
                    aria-label={customerUiText("注册账套类型")}
                    value={profile}
                    onChange={(event) => setProfile(event.target.value)}
                    disabled={busy}
                  >
                    {profiles.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <label><CustomerText text="密码" /><input
                aria-label={mode === "login" ? customerUiText("登录密码") : customerUiText("注册密码")}
                name={mode === "login" ? "current-password" : "new-password"}
                aria-invalid={localError ? true : undefined}
                aria-describedby={localError ? "login-form-feedback" : undefined}
                type="password"
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                value={password}
                onChange={(event) => { setPassword(event.target.value); setLocalError(""); }}
                placeholder={customerUiText("输入密码")}
                minLength={6}
                maxLength={128}
                required
                disabled={busy}
              />
            </label>
            {mode === "register" && (
              <label><CustomerText text="再次输入密码" /><input
                  aria-label={customerUiText("确认注册密码")}
                  id="register-confirm-password"
                  name="confirm-password"
                  aria-invalid={localError ? true : undefined}
                  aria-describedby={localError ? "login-form-feedback" : undefined}
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(event) => { setConfirmPassword(event.target.value); setLocalError(""); }}
                  minLength={6}
                  maxLength={128}
                  required
                  disabled={busy}
                />
              </label>
            )}
            <button
              className="button primary login-submit"
              type="submit"
              disabled={busy}
            >
              {busy ? (
                <>
                  <span className="spinner" /><CustomerText text="正在核验账户…" /></>
              ) : (
                <>
                  {mode === "login" ? customerUiText("登录工作台") : customerUiText("注册并进入工作台")}
                  <Icon name="arrow" size={18} />
                </>
              )}
            </button>
          </form>
          <p className="sr-only" role="status" aria-atomic="true">{busy ? customerUiText("正在核验账户，请稍候。") : ""}</p>
          <p className="login-persistence">
            <Icon name="lock" size={14} /><CustomerText text="登录状态在本浏览器保留；退出会清除当前私有页面数据。" /></p>
        </div>
        <footer><CustomerText text="圳行Agent · 对话式金融服务" /></footer>
      </main>
    </div>
  );
}
