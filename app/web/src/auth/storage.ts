import type { StoredLogin } from "./types";
import { storedLoginValue } from "./runtimeValidator.ts";
import { safeLocalGet, safeLocalSet, safeLocalRemove, safeSessionRemove, safeSessionKeys } from "../safeStorage.ts";

const ACCOUNT_KEY = "zx_account_login";
const privateSessionKeys = [
  "zx_web_session",
  "zx_web_owner_session",
  "zx_web_helper_sessions",
  "zx_web_task",
  "zx_web_pending_request",
];
export function readStoredLogin(): StoredLogin | null {
  try {
    const value = JSON.parse(safeLocalGet(ACCOUNT_KEY) || "null");
    return storedLoginValue(value);
  } catch {
    return null;
  }
}
export function persistLogin(value: StoredLogin): boolean {
  const valid = storedLoginValue(value);
  if (!valid) throw new Error("登录状态尚未通过核验，未保存身份。");
  return safeLocalSet(
    ACCOUNT_KEY,
    JSON.stringify(valid),
  );
}
export function forgetLogin() {
  const accountRemoved = safeLocalRemove(ACCOUNT_KEY);
  const privateRemoved = clearPrivateViewState();
  return accountRemoved && privateRemoved;
}
export function clearPrivateViewState() {
  let cleared = true;
  for (const key of privateSessionKeys) if (!safeSessionRemove(key)) cleared = false;
  for (const key of safeSessionKeys())
    if (key.startsWith("zx_confirm_") || key.startsWith("zx_v3_"))
      if (!safeSessionRemove(key)) cleared = false;
  return cleared;
}
