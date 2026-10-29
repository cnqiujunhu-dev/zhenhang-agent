// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "./api";
import { request } from "./api";
import { authApi } from "./auth/api";
import { LoginScreen } from "./auth/LoginScreen";
import { PasswordDialog } from "./auth/PasswordDialog";
import {
  clearPrivateViewState,
  forgetLogin,
  persistLogin,
  readStoredLogin,
} from "./auth/storage";
import type {
  AccountIdentity,
  LoginInput,
  LoginSession,
  RegisterInput,
  StoredLogin,
} from "./auth/types";
import { AuthRequestGate, type AuthTicket } from "./auth/runtimeValidator";
import { Brand, Icon, Skeleton } from "./components";
import { DeferredPanelBoundary } from "./DeferredPanelBoundary";
import type { DemoSession } from "./types";
import { safeSessionGet, safeSessionSet } from "./safeStorage";
import { VolatileDraftBridge, type DraftActorIdentity } from "./volatileDraftBridge";

const AdminWorkspace = lazy(() => import("./admin/AdminWorkspace").then(module => ({ default: module.AdminWorkspace })));
const TestDataTools = lazy(() => import("./admin/TestDataTools").then(module => ({ default: module.TestDataTools })));
const App = lazy(() => import("./App"));

export default function AuthShell() {
  const { bindAccount, t } = useCustomerLanguage();
  const customerUiText = t;
  const [stored, setStored] = useState<StoredLogin | null>(() =>
    readStoredLogin(),
  );
  const [identity, setIdentity] = useState<AccountIdentity | null>(null);
  useEffect(() => {
    bindAccount(identity?.role === "ADMIN" ? "" : stored?.token || "", identity?.role === "ADMIN" ? "" : identity?.account_id || "");
  }, [stored?.token, identity?.account_id, identity?.role, bindAccount]);
  const [restoredActor, setRestoredActor] = useState<DemoSession | null>(null);
  const [checking, setChecking] = useState(Boolean(stored));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [persistenceNotice, setPersistenceNotice] = useState("");
  const [draftHandoffNonce, setDraftHandoffNonce] = useState<string | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [resourceRevision, setResourceRevision] = useState(0);
  const [path, setPath] = useState(location.hash || "#/workspace");
  const [verifyNonce, setVerifyNonce] = useState(0);
  const authEpoch = useRef(0);
  const authGate = useRef(new AuthRequestGate());
  const storedRef = useRef(stored);
  const cachedRef = useRef(readStoredLogin());
  const identityRef = useRef(identity), actorRef = useRef(restoredActor);
  const draftBridge = useRef(new VolatileDraftBridge());
  storedRef.current = stored;
  identityRef.current = identity; actorRef.current = restoredActor;
  function draftIdentity(value: AccountIdentity | DemoSession | LoginSession | null): DraftActorIdentity | null {
    if (!value?.user_id || !value.sandbox_id || !value.account_id) return null;
    const role="role" in value ? value.role : value.actor_role;
    if (!role) return null;
    return {user_id:value.user_id,sandbox_id:value.sandbox_id,account_id:value.account_id,
      role};
  }
  function sameCached(left: StoredLogin | null,right: StoredLogin | null) {
    return left?.token === right?.token && left?.expires_at === right?.expires_at;
  }
  function prepareDraftHandoff() {
    const owner=draftIdentity(identityRef.current), actor=draftIdentity(actorRef.current);
    return owner && actor ? draftBridge.current.prepare(owner,actor) : null;
  }
  function adoptCachedSession(next: StoredLogin | null) {
    const handoff=next && storedRef.current ? prepareDraftHandoff() : null;
    if (!handoff) draftBridge.current.clear();
    cachedRef.current=next;
    clearPrivateViewState(); invalidateAuth();
    storedRef.current=next; identityRef.current=null; actorRef.current=null;
    setIdentity(null); setRestoredActor(null); setPasswordOpen(false); setTestOpen(false);
    setChecking(Boolean(next)); setStored(next); setError(""); setDraftHandoffNonce(handoff);
    setPersistenceNotice("");
  }
  function invalidateAuth() {
    authEpoch.current += 1;
    authGate.current.invalidate();
    setBusy(false);
  }
  function clearLogin(message = "") {
    invalidateAuth();
    const cleared=forgetLogin();
    cachedRef.current=readStoredLogin();
    draftBridge.current.clear(); setDraftHandoffNonce(null);
    storedRef.current = null;
    identityRef.current=null; actorRef.current=null;
    setStored(null);
    setIdentity(null);
    setRestoredActor(null);
    setChecking(false);
    setPasswordOpen(false);
    setTestOpen(false);
    setNotice(cleared ? message : "已退出当前页面，私有页面已关闭。浏览器缓存未能清理，请关闭页面并恢复连接后再核对登录状态。");
    setPersistenceNotice("");
    setError("");
  }
  function acceptSession(session: LoginSession, ticket: AuthTicket, preserveActor = false) {
    if (!authGate.current.current(ticket, authEpoch.current)) return false;
    const observed=readStoredLogin();
    if (!sameCached(observed,cachedRef.current)) {adoptCachedSession(observed);return false;}
    const handoff=preserveActor ? prepareDraftHandoff() : null;
    if (!handoff) draftBridge.current.clear();
    if (!preserveActor) clearPrivateViewState();
    const persisted=persistLogin(session);
    cachedRef.current=readStoredLogin();
    storedRef.current = {
      token: session.token,
      expires_at: session.expires_at,
    };
    invalidateAuth();
    setIdentity(null);
    setRestoredActor(null);
    setStored({ token: session.token, expires_at: session.expires_at });
    setDraftHandoffNonce(handoff);
    setPersistenceNotice(persisted ? "" : "浏览器暂时无法保存登录状态，本页仍可使用；刷新或关闭后可能需要重新登录。");
    setChecking(true);
    setError("");
    setNotice("");
    location.hash = session.role === "ADMIN" ? "/admin" : "/workspace";
    return true;
  }
  useEffect(() => () => { authEpoch.current += 1; authGate.current.invalidate(); draftBridge.current.clear(); }, []);
  useEffect(() => {
    if (!draftHandoffNonce) return;
    const nonce=draftHandoffNonce;
    const timer=window.setTimeout(()=>{
      draftBridge.current.discard(nonce);
      setDraftHandoffNonce(current=>current===nonce ? null : current);
    },60_000);
    return ()=>clearTimeout(timer);
  },[draftHandoffNonce]);
  useEffect(() => {
    if (!stored) return;
    const epoch = authEpoch.current;
    const abort = new AbortController();
    setChecking(true);
    authApi
      .me(stored.token, abort.signal)
      .then(async (value) => {
        if (abort.signal.aborted || authEpoch.current !== epoch) return;
        if (
          !value.authenticated ||
          value.registered === false ||
          !value.account_id ||
          !value.username
        ) {
          clearLogin("请使用具名账号登录工作台。");
          return;
        }
        const owner: DemoSession = {
          token: stored.token,
          expires_at: stored.expires_at,
          username: value.username,
          user_id: value.user_id,
          sandbox_id: value.sandbox_id,
          account_id: value.account_id,
          profile: value.profile ?? undefined,
          actor_role: value.role,
          display_name: value.display_name,
        };
        let actor = owner;
        let savedActor: DemoSession | null = null;
        try {
          savedActor = JSON.parse(
            safeSessionGet("zx_web_session") || "null",
          );
        } catch {
          /* Ignore malformed cached view state. */
        }
        if (
          value.role !== "ADMIN" &&
          savedActor?.actor_role === "FAMILY_HELPER" &&
          ((savedActor.sandbox_id === value.sandbox_id && savedActor.account_id === value.account_id) ||
            savedActor.main_account_id === value.account_id)
        ) {
          try {
            const helper = await authApi.me(savedActor.token, abort.signal);
            const family = await request<{
              current_actor: {
                user_id: string;
                display_name: string;
                role: string;
              };
              actors: { user_id: string; role: string }[];
            }>(
              "/api/v2/extensions/family",
              savedActor.token,
              "GET",
              undefined,
              undefined,
              { signal: abort.signal },
            );
            if (
              !abort.signal.aborted && authEpoch.current === epoch &&
              helper.authenticated &&
              helper.role === "FAMILY_HELPER" &&
              helper.user_id === savedActor.user_id && helper.sandbox_id === savedActor.sandbox_id &&
              family.current_actor.user_id === savedActor.user_id &&
              (helper.family_context ?
                helper.family_context.issuer_account_id === value.account_id : family.actors.some(
                (item) =>
                  item.user_id === value.user_id && item.role === "OWNER",
              ))
            ) {
              actor = {
                ...savedActor,
                actor_role: "FAMILY_HELPER",
                display_name: family.current_actor.display_name,
                main_account_id: value.account_id,
                account_id: null,
                profile: null,
                registered: false,
                username: undefined,
              };
              safeSessionSet(
                "zx_web_owner_session",
                JSON.stringify(owner),
              );
            }
          } catch {
            if (!abort.signal.aborted && authEpoch.current === epoch) {
              clearPrivateViewState();
              actor = owner;
            }
          }
        } else if (
          savedActor?.account_id &&
          savedActor.account_id !== value.account_id
        )
          clearPrivateViewState();
        if (abort.signal.aborted || authEpoch.current !== epoch) return;
        if (value.role !== "ADMIN")
          if (!safeSessionSet("zx_web_session", JSON.stringify(actor)))
            setPersistenceNotice("浏览器会话缓存暂不可用，已核验身份仍可在本页使用；刷新后可能需要重新登录。");
        if (draftHandoffNonce) {
          const ownerScope=draftIdentity(value),actorScope=draftIdentity(actor);
          if (!ownerScope || !actorScope) {draftBridge.current.clear();setDraftHandoffNonce(null);}
        }
        setRestoredActor(actor);
        setIdentity(value);
        setChecking(false);
        setError("");
      })
      .catch((reason) => {
        if (abort.signal.aborted || authEpoch.current !== epoch) return;
        if (reason instanceof ApiError && [401, 403].includes(reason.status))
          clearLogin("登录会话已失效，请重新登录。");
        else {
          setChecking(false);
          setError(
            reason instanceof Error ? reason.message : "账户核验暂未完成",
          );
        }
      });
    return () => abort.abort();
  }, [stored?.token, verifyNonce]);
  useEffect(() => {
    const onHash = () => setPath(location.hash || "#/workspace");
    window.addEventListener("hashchange", onHash);
    const onStorage = (event: StorageEvent) => {
      if (event.key === "zx_account_login" || event.key === null) {
        const next = readStoredLogin();
        if (sameCached(next,cachedRef.current) && sameCached(next,storedRef.current) && !authGate.current.hasActive()) return;
        adoptCachedSession(next);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  function recoverAccount() {
    invalidateAuth();
    draftBridge.current.clear();setDraftHandoffNonce(null);
    clearPrivateViewState();
    setIdentity(null);
    setRestoredActor(null);
    setChecking(true);
    setPasswordOpen(false);
    setTestOpen(false);
    setVerifyNonce((value) => value + 1);
  }
  useEffect(() => {
    const expired = (event: Event) => {
      const token = (event as CustomEvent<{ token: string }>).detail?.token;
      let actorToken: string | undefined;
      try {
        actorToken = JSON.parse(
          safeSessionGet("zx_web_session") || "null",
        )?.token;
      } catch {
        /* Malformed cache is not identity evidence. */
      }
      if (storedRef.current && token === storedRef.current.token) {
        const observed=readStoredLogin();
        if (!sameCached(observed,cachedRef.current) && observed) adoptCachedSession(observed);
        else clearLogin("登录状态已失效，请重新登录后查看原办理记录。");
      }
      else if (storedRef.current && token === actorToken)
        recoverAccount();
    };
    window.addEventListener("zx-auth-invalid", expired);
    return () => window.removeEventListener("zx-auth-invalid", expired);
  }, []);
  useEffect(() => {
    if (!stored || !identity) return;
    const millis = Date.parse(stored.expires_at) - Date.now() - 120000;
    const timer = setTimeout(
      async () => {
        const active = storedRef.current;
        if (!active || active.token !== stored.token || passwordOpen) return;
        const ticket = authGate.current.start(authEpoch.current);
        if (!ticket) return;
        try {
          const renewed = await authApi.refresh(active.token, identity);
          if (authGate.current.current(ticket, authEpoch.current) && storedRef.current?.token === active.token)
            acceptSession(renewed, ticket, true);
        } catch (reason) {
          if (!authGate.current.current(ticket, authEpoch.current) || storedRef.current?.token !== active.token) return;
          if (reason instanceof ApiError && [401, 403].includes(reason.status))
            clearLogin("会话已失效，请重新登录。");
          else setError("登录续期暂未完成，可退出后重新登录。");
        } finally {
          authGate.current.finish(ticket, authEpoch.current);
        }
      },
      Math.max(1000, Math.min(millis, 2147483000)),
    );
    return () => clearTimeout(timer);
  }, [stored?.token, stored?.expires_at, identity?.account_id, passwordOpen]);
  async function login(input: LoginInput) {
    const ticket = authGate.current.start(authEpoch.current);
    if (!ticket) return;
    setBusy(true);
    setError("");
    try {
      acceptSession(await authApi.login(input), ticket);
    } catch (reason) {
      if (authGate.current.current(ticket, authEpoch.current))
        setError(reason instanceof Error ? reason.message : "登录未完成");
    } finally {
      if (authGate.current.finish(ticket, authEpoch.current)) setBusy(false);
    }
  }
  async function register(input: RegisterInput) {
    const ticket = authGate.current.start(authEpoch.current);
    if (!ticket) return;
    setBusy(true);
    setError("");
    try {
      acceptSession(await authApi.register(input), ticket);
    } catch (reason) {
      if (authGate.current.current(ticket, authEpoch.current))
        setError(reason instanceof Error ? reason.message : "注册未完成");
    } finally {
      if (authGate.current.finish(ticket, authEpoch.current)) setBusy(false);
    }
  }
  async function logout() {
    const token = stored?.token;
    clearLogin("已退出登录，私有页面数据已清除。");
    if (token) await authApi.logout(token).catch(() => {});
  }
  const bootstrap = useMemo<DemoSession | undefined>(
    () => (stored && identity && restoredActor ? restoredActor : undefined),
    [stored?.token, identity, restoredActor],
  );
  if (!stored)
    return (
      <LoginScreen
        onLogin={login}
        onRegister={register}
        busy={busy}
        error={error}
        notice={notice}
      />
    );
  if (checking)
    return (
      <div className="auth-checking">
        <Brand />
        <Skeleton lines={4} />
        <p><CustomerText text="正在核验账号与持续账套…" /></p>
      </div>
    );
  if (!identity || !bootstrap)
    return (
      <div className="auth-checking">
        <Brand />
        <p role="alert">{customerUiText(error || "账户身份尚未核验")}</p>
        <button
          className="button secondary"
          onClick={() => {
            setStored((current) => (current ? { ...current } : null));
            location.reload();
          }}
        ><CustomerText text="重新核验" /></button>
        <button className="text-button" onClick={logout}><CustomerText text="返回登录" /></button>
      </div>
    );
  const isAdmin = identity.role === "ADMIN";
  const forbiddenAdmin = path.startsWith("#/admin") && !isAdmin;
  return (
    <>
      {persistenceNotice && <div className="auth-global-notice" role="status">{customerUiText(persistenceNotice)}</div>}
      {error && (
        <div className="auth-global-notice" role="alert">
          {customerUiText(error)}
        </div>
      )}
      {forbiddenAdmin ? (
        <div className="auth-checking">
          <Icon name="lock" size={35} />
          <h2><CustomerText text="当前账号没有运维后台权限" /></h2>
          <p><CustomerText text="返回自己的金融工作台继续办理。" /></p>
          <button
            className="button primary"
            onClick={() => {
              location.hash = "/workspace";
            }}
          ><CustomerText text="返回工作台" /></button>
        </div>
      ) : isAdmin ? (
        <DeferredPanelBoundary resetKey={`${identity.account_id}:${stored.token}`} onClose={logout} onCloseLabel={customerUiText("退出并返回登录")}>
        <Suspense fallback={<section className="auth-checking" role="status"><Skeleton lines={3}/><p><CustomerText text="正在打开管理页面…" /></p></section>}>
        <AdminWorkspace
          key={`${identity.account_id}:${stored.token}`}
          token={stored.token}
          account={identity}
          onLogout={logout}
          onPassword={() => setPasswordOpen(true)}
        />
        </Suspense>
        </DeferredPanelBoundary>
      ) : (
        <DeferredPanelBoundary resetKey={`${identity.account_id}:${stored.token}`} onClose={logout} onCloseLabel={customerUiText("退出并返回登录")}>
        <Suspense fallback={<section className="auth-checking" role="status"><Brand/><Skeleton lines={3}/><p><CustomerText text="正在准备你的对话工作台…" /></p></section>}>
        <App
          key={`${identity.account_id}:${stored.token}`}
          bootstrapSession={bootstrap}
          account={identity}
          onLogout={logout}
          onPassword={() => setPasswordOpen(true)}
          onTestTools={() => setTestOpen(true)}
          onAuthExpired={recoverAccount}
          resourceRevision={resourceRevision}
          draftHandoffNonce={draftHandoffNonce}
          onVolatileDraftChange={(message,actor) => {
            if (storedRef.current?.token !== stored.token) return;
            const current=draftIdentity(actorRef.current);
            if (!current || current.user_id !== actor.user_id || current.sandbox_id !== actor.sandbox_id
              || current.account_id !== actor.account_id || current.role !== actor.role) return;
            draftBridge.current.capture(message,actor);
          }}
          takeVolatileDraft={(nonce,actor) => {
            if (storedRef.current?.token !== stored.token || nonce !== draftHandoffNonce) return null;
            const owner=draftIdentity(identityRef.current),current=draftIdentity(actorRef.current);
            if (!owner || !current || current.user_id !== actor.user_id || current.sandbox_id !== actor.sandbox_id
              || current.account_id !== actor.account_id || current.role !== actor.role) {draftBridge.current.clear();return null;}
            return draftBridge.current.take(nonce,owner,actor);
          }}
        />
        </Suspense>
        </DeferredPanelBoundary>
      )}
      {passwordOpen && (
        <PasswordDialog
          token={stored.token}
          onClose={() => setPasswordOpen(false)}
          onChanged={() => clearLogin("密码已修改，请使用新密码重新登录。")}
        />
      )}
      {testOpen && identity.role === "DEV_TEST" && (
        <DeferredPanelBoundary resetKey={`${identity.account_id}:${stored.token}`} onClose={() => setTestOpen(false)} onCloseLabel={customerUiText("返回工作台")}>
        <Suspense fallback={<section className="deferred-panel-warning" role="status"><p><CustomerText text="正在打开开发数据工具…" /></p><button className="button subtle" onClick={() => setTestOpen(false)}><CustomerText text="返回当前页面" /></button></section>}>
        <TestDataTools
          token={stored.token}
          onClose={() => setTestOpen(false)}
          onDataChanged={() => setResourceRevision((value) => value + 1)}
        />
        </Suspense>
        </DeferredPanelBoundary>
      )}
    </>
  );
}
