import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { Icon } from "./components";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { useEndpoint } from "./useEndpoint";
import type { DemoSession } from "./types";

type BoundContext = {
  link_id: string;
  link_version: number;
  relationship: string;
  owner_name: string;
  helper_name: string;
  scopes: string[];
  delegation_valid: boolean;
  role: "FAMILY_HELPER";
  allowed_actions: string[];
  direct_bank_actions_allowed: false;
};
type ContextList = {
  scope: "OWN_BOUND_FAMILY_CONTEXTS";
  contexts: BoundContext[];
  current_role: string;
  balances_disclosed: false;
  main_account_session_preserved: true;
};
export type BoundFamilyActorSession = {
  token: string;
  user_id: string;
  sandbox_id: string;
  display_name: string;
  role: "FAMILY_HELPER";
  expires_at: string;
};
type IssuedContext = Omit<BoundFamilyActorSession, "display_name"> & {
  family_context: BoundContext;
  registered: false;
  account_id: null;
  main_account_session_preserved: true;
};
type Props = {
  token: string;
  revision: number;
  currentSession: DemoSession;
  onActorSession: (session: BoundFamilyActorSession) => void;
};

const scopeLabels = {
  READ_BALANCE: ["查看余额", "View balance"],
  PROPOSE_TRANSFER: ["提出转账建议", "Propose a transfer"],
  PROPOSE_CARD_LOCK: ["提出锁卡建议", "Propose a card lock"],
};
function validContext(value: unknown): value is BoundContext {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as BoundContext;
  return typeof row.link_id === "string" && /^[a-zA-Z0-9-]{1,80}$/.test(row.link_id) &&
    Number.isSafeInteger(row.link_version) && row.link_version > 0 &&
    [row.relationship, row.owner_name, row.helper_name].every(value => typeof value === "string" && value.trim().length > 0) &&
    typeof row.delegation_valid === "boolean" && row.role === "FAMILY_HELPER" &&
    row.direct_bank_actions_allowed === false && Array.isArray(row.scopes) &&
    row.scopes.every(value => Object.hasOwn(scopeLabels, value)) &&
    (row.delegation_valid || row.scopes.length === 0) &&
    Array.isArray(row.allowed_actions) && row.allowed_actions.length === 2 &&
    new Set(row.allowed_actions).size === 2 && row.allowed_actions.includes("FAMILY_VIEW") &&
    row.allowed_actions.includes("FAMILY_PROPOSE");
}
function validList(value: unknown, role: string): value is ContextList {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as ContextList;
  return data.scope === "OWN_BOUND_FAMILY_CONTEXTS" && data.current_role === role &&
    data.balances_disclosed === false && data.main_account_session_preserved === true &&
    Array.isArray(data.contexts) && data.contexts.every(validContext) &&
    new Set(data.contexts.map(row => row.link_id)).size === data.contexts.length;
}

export function FamilyBoundContexts({ token, revision, currentSession, onActorSession }: Props) {
  const { locale } = useCustomerLanguage();
  const english = locale === "en";
  const role = currentSession.actor_role || "";
  const isMain = ["CUSTOMER", "DEV_TEST"].includes(role) && currentSession.token === token;
  const endpoint = useEndpoint<ContextList>(isMain ? "/api/v2/extensions/family/contexts" : null, token, revision);
  const endpointRef = useRef(endpoint);
  endpointRef.current = endpoint;
  const identity = `${token}:${currentSession.user_id}:${currentSession.sandbox_id}:${role}:${revision}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const requestRef = useRef<{ identity: string; abort: AbortController } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ identity: string; message: string } | null>(null);
  useEffect(() => {
    return () => { requestRef.current?.abort.abort(); requestRef.current = null; };
  }, [identity]);
  const invalid = endpoint.data !== null && !validList(endpoint.data, role);
  const contexts = !invalid && endpoint.data ? endpoint.data.contexts : [];
  const currentFailure = failure?.identity === identity ? failure.message : "";
  const busyHere = requestRef.current?.identity === identity ? busy : null;

  async function enter(context: BoundContext) {
    if (!isMain || requestRef.current || endpoint.loading || endpoint.error || invalid || !endpoint.data ||
      !contexts.some(row => row.link_id === context.link_id && row.link_version === context.link_version)) return;
    const requestIdentity = identity;
    const readGeneration = endpoint.getReadGeneration();
    const abort = new AbortController();
    requestRef.current = { identity: requestIdentity, abort };
    setBusy(context.link_id);
    setFailure(null);
    try {
      const result = await request<IssuedContext>(
        `/api/v2/extensions/family/contexts/${encodeURIComponent(context.link_id)}/session`, token, "POST",
        { expected_link_version: context.link_version }, undefined, { signal: abort.signal });
      const freshRead = endpointRef.current;
      if (abort.signal.aborted || identityRef.current !== requestIdentity || !freshRead.data ||
        freshRead.error || freshRead.loading || !validList(freshRead.data, role) ||
        freshRead.getReadGeneration() !== readGeneration || !freshRead.data.contexts.some(row =>
          row.link_id === context.link_id && row.link_version === context.link_version)) return;
      const expiry = Date.parse(result?.expires_at);
      if (!result || result.role !== "FAMILY_HELPER" || result.registered !== false || result.account_id !== null ||
        result.main_account_session_preserved !== true || !validContext(result.family_context) ||
        result.family_context.link_id !== context.link_id || result.family_context.link_version !== context.link_version ||
        result.family_context.owner_name !== context.owner_name || result.family_context.helper_name !== context.helper_name ||
        result.family_context.relationship !== context.relationship ||
        typeof result.token !== "string" || !result.token.trim() || result.token === token ||
        typeof result.user_id !== "string" || !result.user_id || result.user_id === currentSession.user_id ||
        typeof result.sandbox_id !== "string" || !result.sandbox_id || result.sandbox_id === currentSession.sandbox_id ||
        !Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.now() + 31 * 60_000) {
        throw new Error(english ? "The assistance identity could not be verified. Your own account has not changed." :
          "协助身份未能核对，请重新读取家庭关系。你的账户没有改变。");
      }
      onActorSession({ token: result.token, user_id: result.user_id, sandbox_id: result.sandbox_id,
        display_name: result.family_context.helper_name, role: result.role, expires_at: result.expires_at });
    } catch (error) {
      if (!abort.signal.aborted && identityRef.current === requestIdentity) {
        setFailure({ identity: requestIdentity, message: error instanceof Error ? error.message :
          english ? "Unable to enter assistance. Your own account is unchanged." : "暂未进入家庭协助，你的账户没有改变。" });
      }
    } finally {
      if (requestRef.current?.abort === abort) {
        requestRef.current = null;
        setBusy(null);
      }
    }
  }

  if (!isMain || (!endpoint.loading && !endpoint.error && !invalid && !contexts.length)) return null;
  const relationLabel = (row: BoundContext) => {
    const father = /^(?:father|父亲|爸爸)$/i.test(row.relationship);
    const mother = /^(?:mother|母亲|妈妈)$/i.test(row.relationship);
    return english ? `Assist ${father ? "Dad" : mother ? "Mom" : row.owner_name}` :
      `协助${father ? "爸爸" : mother ? "妈妈" : row.owner_name}`;
  };
  return <div className="family-bound-contexts" aria-label={english ? "Your family assistance identities" : "我的家庭协助身份"}>
    <h3><Icon name="family" size={16} />{english ? "Help a family member" : "协助家人"}</h3>
    {endpoint.loading && !endpoint.data && <p role="status">{english ? "Loading family relationships…" : "正在读取家庭关系…"}</p>}
    {(endpoint.error || invalid) && <div className="error-inline" role="alert">
      <p>{endpoint.error || (english ? "Family relationships could not be verified." : "家庭关系未能核对，暂不进入协助。")}</p>
      <button type="button" className="text-button" onClick={() => endpoint.refresh()} disabled={Boolean(busyHere)}>
        {english ? "Reload relationships" : "重新读取家庭关系"}
      </button>
    </div>}
    {!endpoint.error && !invalid && contexts.map(row => <div className="actor-context" key={row.link_id}>
      <div>
        <strong>{relationLabel(row)} · {row.owner_name}</strong>
        <small>{row.scopes.length ? row.scopes.map(scope => scopeLabels[scope as keyof typeof scopeLabels][english ? 1 : 0]).join(" · ") :
          english ? "No permission granted yet" : "暂未授权"}</small>
      </div>
      <button type="button" className="button secondary compact" onClick={() => enter(row)}
        disabled={Boolean(busyHere) || endpoint.loading} aria-label={english ? `Enter: ${relationLabel(row)}` : `进入${relationLabel(row)}`}>
        {busyHere === row.link_id ? english ? "Entering…" : "正在进入…" : english ? "Enter assistance" : "进入协助"}
      </button>
    </div>)}
    {currentFailure && <p className="error-inline" role="alert">{currentFailure}</p>}
    {contexts.length > 0 && <p className="extension-explanation">{english ?
      "Only the granted assistance is available. You can return to your own account at any time." :
      "仅按家人的授权协助，随时可返回自己的账户。"}</p>}
  </div>;
}
