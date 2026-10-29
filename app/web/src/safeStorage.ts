/** Storage denial is a capability failure, not permission to keep a private UI open. */
function get(kind: "sessionStorage" | "localStorage", key: string): string | null {
  try { return globalThis[kind].getItem(key); } catch { return null; }
}
function set(kind: "sessionStorage" | "localStorage", key: string, value: string): boolean {
  try { globalThis[kind].setItem(key,value); return true; } catch { return false; }
}
function remove(kind: "sessionStorage" | "localStorage", key: string): boolean {
  try { globalThis[kind].removeItem(key); return true; } catch { return false; }
}
export const safeSessionGet = (key: string) => get("sessionStorage",key);
export const safeSessionSet = (key: string,value: string) => set("sessionStorage",key,value);
export const safeSessionRemove = (key: string) => remove("sessionStorage",key);
export const safeLocalGet = (key: string) => get("localStorage",key);
export const safeLocalSet = (key: string,value: string) => set("localStorage",key,value);
export const safeLocalRemove = (key: string) => remove("localStorage",key);
export function safeSessionKeys(): string[] {
  try { return Object.keys(globalThis.sessionStorage); } catch { return []; }
}
