// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "../CustomerLanguageProvider";
import { customerUiText } from "../customerLanguage";
import { useEffect, useRef, useState } from "react";
import { Icon, useDialogFocus } from "../components";
import { authApi } from "./api";
import { AuthRequestGate, validatePasswordChange } from "./runtimeValidator";

export function PasswordDialog({
  token,
  onClose,
  onChanged,
}: {
  token: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { onDismiss: close, dialogRef });
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const epoch = useRef(0), gate = useRef(new AuthRequestGate()), mounted = useRef(true);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  useEffect(() => {
    mounted.current = true;
    epoch.current += 1; gate.current.invalidate();
    setCurrent(""); setNext(""); setAgain(""); setBusy(false); setError("");
    return () => { mounted.current = false; epoch.current += 1; gate.current.invalidate(); };
  }, [token]);
  function close() {
    if (busy) return;
    mounted.current = false; epoch.current += 1; gate.current.invalidate();
    setCurrent(""); setNext(""); setAgain("");
    onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!mounted.current) return;
    setError("");
    if (next !== again) {
      setError("两次输入的新密码不一致。");
      return;
    }
    const ticket = gate.current.start(epoch.current);
    if (!ticket) return;
    const actorToken = token;
    const currentOperation = () => mounted.current && tokenRef.current === actorToken && gate.current.current(ticket, epoch.current);
    setBusy(true);
    try {
      const result = await authApi.password(actorToken, current, next);
      if (!currentOperation()) return;
      validatePasswordChange(result);
      setCurrent("");
      setNext("");
      setAgain("");
      onChanged();
    } catch (reason) {
      if (currentOperation()) {
        setError(reason instanceof Error ? reason.message : "密码修改未完成");
        setCurrent(""); setNext(""); setAgain("");
      }
    } finally {
      if (gate.current.finish(ticket, epoch.current) && mounted.current && tokenRef.current === actorToken) setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        ref={dialogRef}
        className="password-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="password-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow"><CustomerText text="账户安全" /></span>
            <h2 id="password-title"><CustomerText text="修改登录密码" /></h2>
          </div>
          <button
            className="icon-button"
            aria-label={customerUiText("关闭密码修改")}
            disabled={busy}
            onClick={close}
          >
            <Icon name="close" />
          </button>
        </header>
        <p><CustomerText text="修改成功后，现有登录会话将被撤销，请使用新密码重新登录。" /></p>
        <form onSubmit={submit} aria-busy={busy} aria-describedby={error ? "password-form-feedback" : undefined}>
          <label><CustomerText text="当前密码" /><input
              type="password"
              aria-label={customerUiText("当前登录密码")}
              name="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              autoComplete="current-password"
              required
              minLength={6}
              maxLength={128}
              disabled={busy}
            />
          </label>
          <label><CustomerText text="新密码" /><input
              type="password"
              aria-label={customerUiText("新登录密码")}
              name="new-password"
              aria-describedby={error ? "password-form-feedback" : undefined}
              value={next}
              onChange={(event) => { setNext(event.target.value); if (error === "两次输入的新密码不一致。") setError(""); }}
              autoComplete="new-password"
              required
              minLength={6}
              maxLength={128}
              disabled={busy}
            />
          </label>
          <label><CustomerText text="再次输入新密码" /><input
              type="password"
              aria-label={customerUiText("再次输入新登录密码")}
              name="confirm-password"
              aria-invalid={error === "两次输入的新密码不一致。" ? true : undefined}
              aria-describedby={error ? "password-form-feedback" : undefined}
              value={again}
              onChange={(event) => { setAgain(event.target.value); if (error === "两次输入的新密码不一致。") setError(""); }}
              autoComplete="new-password"
              required
              minLength={6}
              maxLength={128}
              disabled={busy}
            />
          </label>
          {error && (
            <p className="login-error" role="alert" id="password-form-feedback">
              {error}
            </p>
          )}
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? customerUiText("正在提交…") : customerUiText("确认修改密码")}
          </button>
        </form>
        <p className="sr-only" role="status" aria-atomic="true">{busy ? customerUiText("正在提交密码修改，请稍候。") : ""}</p>
      </section>
    </div>
  );
}
