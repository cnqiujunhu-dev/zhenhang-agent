import { customerRecovery } from "./customerRecovery.ts";
import type { RecoveryOperation, RecoveryAuthAction } from "./customerRecovery.ts";
import { retryAfterDelay } from "./retryAfter.ts";
import { getCustomerRequestLanguage } from "./customerLanguage.ts";

export class ApiError extends Error {
  status: number;
  code: string;
  retryAfterMs?: number;
  constructor(
    message: string,
    status: number,
    code: string,
    retryAfterMs?: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterMs = retryAfterMs;
  }
}
export async function request<T>(
  path: string,
  token: string,
  method = "GET",
  body?: unknown,
  key?: string,
  options?: {
    signal?: AbortSignal;
    timeoutMs?: number;
    operation?: RecoveryOperation;
    authAction?: RecoveryAuthAction;
  },
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const readLanguage = getCustomerRequestLanguage();
  if (method === "GET" || method === "HEAD") headers["Accept-Language"] = readLanguage || "*";
  if (key) headers["Idempotency-Key"] = key;
  const operation = options?.operation || (method === "GET" || method === "HEAD" ? "read" : "submit");
  const recoveryOptions = { operation, authAction: options?.authAction };
  const serializedBody = body === undefined ? undefined : JSON.stringify(body);
  const timeout = AbortSignal.timeout(options?.timeoutMs ?? 25000);
  const signal = options?.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const transportFailure = (error: unknown, fallbackCode: string): Error => {
    const name = error instanceof Error ? error.name : "";
    // AbortSignal.any retains the first abort reason, even if both signals later abort.
    const timedOut = signal.aborted
      ? timeout.aborted && signal.reason === timeout.reason
      : name === "TimeoutError";
    const cancelled = signal.aborted && !timedOut || !signal.aborted && name === "AbortError";
    if (cancelled && operation === "read") return new DOMException("读取已中断", "AbortError");
    const code = timedOut ? "NETWORK_TIMEOUT" : cancelled ? "REQUEST_ABORTED" : fallbackCode;
    return new ApiError(customerRecovery({ code }, recoveryOptions).message, 0, code);
  };
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: serializedBody,
      signal,
    });
  } catch (error) {
    throw transportFailure(error, "NETWORK_UNAVAILABLE");
  }
  // Authentication failure is already authoritative once headers arrive; don't wait
  // for a broken/slow body before withdrawing this token's private view.
  if (response.status === 401 && token)
    window.dispatchEvent(new CustomEvent("zx-auth-invalid", { detail: { token } }));
  let data: any = null;
  try {
    data = response.status === 204 ? null : await response.json();
  } catch (error) {
    if (response.ok)
      throw transportFailure(error, "RESPONSE_UNREADABLE");
  }
  if (response.ok && signal.aborted) throw transportFailure(signal.reason, "RESPONSE_UNREADABLE");
  if (!response.ok) {
    const rawCode = data?.error?.code;
    const code = typeof rawCode === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(rawCode)
      ? rawCode : "HTTP_ERROR";
    const retryAfterMs = response.status === 429 ? retryAfterDelay(response.headers.get("Retry-After")) : undefined;
    throw new ApiError(
      customerRecovery({ status: response.status, code, retryAfterMs }, recoveryOptions).message,
      response.status,
      code,
      retryAfterMs,
    );
  }
  return data as T;
}
export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
export function listOf<T>(value: T[] | { items?: T[]; profiles?: T[] }): T[] {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    if (Object.hasOwn(value, "items")) {
      if (Array.isArray(value.items)) return value.items;
    } else if (Object.hasOwn(value, "profiles") && Array.isArray(value.profiles)) return value.profiles;
  }
  throw new ApiError("列表回复尚未通过核验，请重新读取；这不代表没有记录。", 0, "RESPONSE_SCHEMA_INVALID");
}
