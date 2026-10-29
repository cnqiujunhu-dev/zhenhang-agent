import { ApiError } from "../api.ts";
import type { AccountIdentity, LoginSession, PasswordChangeResult, StoredLogin } from "./types";

const loginRoles = new Set(["CUSTOMER", "DEV_TEST", "ADMIN"]);
const identityRoles = new Set([...loginRoles, "FAMILY_HELPER", "DEMO_OWNER"]);
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
/** Admin identities have no customer profile; missing or malformed profiles are not equivalent to explicit null. */
const validProfile = (role: string, value: unknown): value is string | null =>
  role === "ADMIN" ? value === null : text(value);
function validExpiry(value: unknown, nowMs: number): value is string {
  if (!text(value) || value.length > 64 || !Number.isFinite(nowMs)) return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!parts) return false;
  const [year,month,day,hour,minute,second,zoneHour,zoneMinute] = parts.slice(1).map(Number);
  const maxDay = new Date(Date.UTC(year,month,0)).getUTCDate();
  if (month < 1 || month > 12 || day < 1 || day > maxDay || hour > 23 || minute > 59 || second > 59
    || (parts[7] !== undefined && (zoneHour > 23 || zoneMinute > 59))) return false;
  return Number.isFinite(Date.parse(value)) && Date.parse(value) > nowMs;
}
function invalid(message = "身份回复缺少可核验的信息，请重新核验后继续。") {
  return new ApiError(message, 0, "RESPONSE_SCHEMA_INVALID");
}

export function storedLoginValue(value: unknown, nowMs = Date.now()): StoredLogin | null {
  return object(value) && text(value.token) && validExpiry(value.expires_at, nowMs)
    ? { token: value.token, expires_at: value.expires_at } : null;
}
export function validateLoginSession(
  value: unknown,
  expected?: { username?: string; user_id?: string; sandbox_id?: string; account_id?: string; role?: string },
  nowMs = Date.now(),
): LoginSession {
  if (!object(value) || ("authenticated" in value && value.authenticated !== true) || !storedLoginValue(value, nowMs)
    || !text(value.username) || !text(value.user_id) || !text(value.sandbox_id)
    || !text(value.account_id) || !text(value.role) || !loginRoles.has(value.role)
    || !validProfile(value.role, value.profile)) throw invalid();
  for (const key of ["username", "user_id", "sandbox_id", "account_id", "role"] as const)
    if (expected?.[key] !== undefined && value[key] !== expected[key]) throw invalid("身份回复与当前账号不一致，已停止切换登录状态，请重新核验。");
  return {
    token: value.token as string, expires_at: value.expires_at as string,
    username: value.username, user_id: value.user_id, sandbox_id: value.sandbox_id,
    account_id: value.account_id, profile: value.profile, role: value.role,
  };
}
export function validateAccountIdentity(value: unknown): AccountIdentity {
  if (!object(value) || value.authenticated !== true || typeof value.registered !== "boolean"
    || !text(value.user_id) || !text(value.sandbox_id) || !text(value.display_name)
    || !text(value.role) || !identityRoles.has(value.role)) throw invalid();
  if (loginRoles.has(value.role) && (!value.registered || !text(value.account_id)
    || !text(value.username) || !validProfile(value.role, value.profile)
    || !Number.isSafeInteger(value.version) || Number(value.version) < 1)) throw invalid();
  if (!loginRoles.has(value.role) && (value.registered || value.account_id != null || value.username != null)) throw invalid();
  if (!loginRoles.has(value.role) && value.version != null
    && (!Number.isSafeInteger(value.version) || Number(value.version) < 1)) throw invalid();
  const context = value.family_context;
  if (context != null && (value.role !== "FAMILY_HELPER" || !object(context)
    || !["issuer_account_id", "parent_account_id", "helper_user_id", "parent_sandbox_id"].every(key => text(context[key]))
    || context.issuer_account_id === context.parent_account_id
    || context.helper_user_id !== value.user_id || context.parent_sandbox_id !== value.sandbox_id)) throw invalid();
  return {
    authenticated: true, registered: value.registered, role: value.role,
    user_id: value.user_id, sandbox_id: value.sandbox_id, display_name: value.display_name,
    version: typeof value.version === "number" ? value.version : null,
    ...(text(value.account_id) ? {account_id:value.account_id} : {}),
    ...(text(value.username) ? {username:value.username} : {}),
    ...(text(value.profile) || (value.role === "ADMIN" && value.profile === null) ? {profile:value.profile as string | null} : {}),
    ...(object(context) ? {family_context: {
      issuer_account_id: context.issuer_account_id as string, parent_account_id: context.parent_account_id as string,
      helper_user_id: context.helper_user_id as string, parent_sandbox_id: context.parent_sandbox_id as string,
    }} : {}),
  };
}
export function validatePasswordChange(value: unknown): PasswordChangeResult {
  if (!object(value) || value.password_changed !== true || value.requires_login !== true
    || !Number.isSafeInteger(value.sessions_revoked) || Number(value.sessions_revoked) < 0)
    throw invalid("密码修改结果尚未核验，请先使用新密码核对登录状态；不要连续提交修改请求。");
  return {password_changed:true, requires_login:true, sessions_revoked:value.sessions_revoked as number};
}

export type AuthTicket = { epoch: number; sequence: number };
/** Single-flight and epoch checks apply to UI state only; server authentication remains authoritative. */
export class AuthRequestGate {
  private active: AuthTicket | null = null;
  private sequence = 0;
  start(epoch: number): AuthTicket | null {
    if (this.active || !Number.isSafeInteger(epoch) || epoch < 0) return null;
    this.active = {epoch,sequence:++this.sequence};
    return this.active;
  }
  current(ticket: AuthTicket, epoch: number) { return this.active === ticket && ticket.epoch === epoch; }
  hasActive() { return this.active !== null; }
  finish(ticket: AuthTicket, epoch: number) {
    if (!this.current(ticket,epoch)) return false;
    this.active = null;
    return true;
  }
  invalidate() { this.active = null; }
}
