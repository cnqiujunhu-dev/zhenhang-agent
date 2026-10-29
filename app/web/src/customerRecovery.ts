export type RecoveryOperation = "read" | "submit" | "confirm" | "auth";
import { getCustomerLocale, type CustomerLocale } from "./customerLanguage.ts";
import { recoveryEnglish } from "./customerRecoveryEnglish.ts";
export type RecoveryAuthAction = "login" | "register" | "password" | "refresh" | "logout" | "challenge" | "verify";
export type RecoveryAction =
  | "REFRESH"
  | "RECOVER_ORIGINAL"
  | "LOGIN"
  | "REVIEW_LATEST"
  | "VERIFY_ACCESS"
  | "WAIT"
  | "NONE";
export type CustomerRecovery = {
  kind: string;
  message: string;
  action: RecoveryAction;
  actionLabel: string;
  resultUnknown: boolean;
  preserveRequest: boolean;
  silent: boolean;
  retryAfterMs?: number;
};

/** Messages are selected from controlled copy, never from response bodies or credentials. */
export function customerRecovery(
  error: unknown,
  options: { operation?: RecoveryOperation; authAction?: RecoveryAuthAction; locale?: CustomerLocale } = {},
): CustomerRecovery {
  const operation = options.operation || "read";
  const write = operation === "submit" || operation === "confirm";
  const value = error && typeof error === "object"
    ? error as { status?: unknown; code?: unknown; name?: unknown; retryAfterMs?: unknown }
    : {};
  const status = typeof value.status === "number" ? value.status : 0;
  const code = typeof value.code === "string" ? value.code : "";
  const retryAfterMs = status === 429 && typeof value.retryAfterMs === "number"
    && Number.isFinite(value.retryAfterMs) && value.retryAfterMs >= 0
    ? Math.min(60_000, value.retryAfterMs) : undefined;
  const make = (
    kind: string, message: string, action: RecoveryAction,
    actionLabel: string, resultUnknown = false, silent = false,
  ): CustomerRecovery => {
    const english = (options.locale || getCustomerLocale()) === "en" ? recoveryEnglish(kind, code, operation, options.authAction, retryAfterMs) : null;
    return { kind, message: english?.message || message, action, actionLabel: english ? english.actionLabel : actionLabel, resultUnknown,
      preserveRequest: write, silent, retryAfterMs };
  };
  const recoverUnknown = (kind: string, prefix: string) => {
    if (operation === "auth") {
      const authCopy = {
        login: "登录结果尚未核验，请确认网络后重新登录。",
        register: "注册结果尚未核验。请先尝试使用该账号登录，确认账号是否已建立，再决定是否重新注册。",
        password: "密码修改结果尚未核验。请先尝试使用新密码重新登录，核对是否修改成功；不要连续提交修改请求。",
        refresh: "登录续期结果尚未核验，请重新登录后继续。",
        logout: "退出请求的结果尚未核验。请关闭私有页面，并在网络恢复后核对登录状态。",
        challenge: "验证码获取结果尚未核验。请先读取最新确认状态，核对后再获取验证码；不要连续请求。",
        verify: "本次验证结果尚未核验。请先读取最新确认状态并重新核对；不要将未收到回复当作验证成功。",
      };
      const businessVerification = options.authAction === "challenge" || options.authAction === "verify";
      return make(kind, `${prefix}。${authCopy[options.authAction || "login"]}`,
        businessVerification ? "REVIEW_LATEST" : "LOGIN",
        businessVerification ? "查看最新确认" : "核对登录状态", true);
    }
    return make(kind,
      write
        ? `${prefix}。暂未收到完整结果；原请求已保留，请先核实原请求结果，请勿重新发起相同付款。`
        : `${prefix}，暂未读到最新信息。网络恢复后可重新读取，不影响已有办理。`,
      write ? "RECOVER_ORIGINAL" : "REFRESH",
      write ? "核实原请求" : "重新读取", write,
    );
  };
  if (value.name === "AbortError" || code === "REQUEST_ABORTED")
    return write || operation === "auth"
      ? recoverUnknown("INTERRUPTED", "这次连接已中断")
      : make("CANCELLED", "已停止这次读取，不影响已有办理。", "NONE", "", false, true);
  if (code === "NETWORK_TIMEOUT" || value.name === "TimeoutError")
    return recoverUnknown("TIMEOUT", "等待回复的时间较长");
  if (code === "NETWORK_UNAVAILABLE" || value.name === "TypeError")
    return recoverUnknown("OFFLINE", "暂时无法连接服务");
  if (code === "RESPONSE_UNREADABLE")
    return recoverUnknown("INCOMPLETE_RESPONSE", "服务回复未完整读取");
  if (status >= 500)
    return recoverUnknown("UNAVAILABLE", "服务暂时繁忙");
  if (["INVALID_CREDENTIALS", "AUTH_CODE_INVALID"].includes(code))
    return make("CREDENTIAL_INVALID", code === "AUTH_CODE_INVALID"
      ? "验证码未通过核验，请检查后重新输入；不要在对话中发送验证码。"
      : options.authAction === "password"
        ? "当前密码未通过核验，请检查当前密码后重试。"
        : "账号或密码未通过核验，请检查后重试。", "NONE", "");
  if (status === 401 || ["AUTH_REQUIRED", "SESSION_EXPIRED", "TOKEN_EXPIRED"].includes(code))
    return make("AUTH_EXPIRED", operation === "auth"
      ? "登录状态已失效，请重新登录后继续。"
      : "登录已过期，请重新登录后核实原请求结果；无需再次发起付款。", "LOGIN", "重新登录");
  const accountCopy: Record<string, string> = {
    USERNAME_UNAVAILABLE: "这个账号目前不可使用，请更换账号名称；如果你已注册，请先尝试登录。",
    INVALID_USERNAME: "账号需为3至32位小写字母、数字或下划线，并以字母开头。",
    INVALID_PASSWORD_LENGTH: "密码需为6至128个字符，请检查长度后重试。",
    INVALID_ACCOUNT_PROFILE: "请选择页面提供的账户类型后继续注册。",
  };
  if (accountCopy[code])
    return make("ACCOUNT_INPUT_REVIEW", accountCopy[code], "NONE", "");
  if (["STEP_UP_REQUIRED", "AUTH_GRANT_INVALID", "CONFIRMATION_EXPIRED"].includes(code))
    return make("CONFIRMATION_EXPIRED", "这次验证或确认已失效。请读取最新办理信息，再核对并完成本次验证。", "REVIEW_LATEST", "查看最新确认");
  if (["CONFIRMATION_USED", "NO_CONFIRMABLE_ACTION", "TASK_NOT_CONFIRMABLE", "AUTH_TARGET_INVALID"].includes(code))
    return make("CONFIRMATION_NOT_CURRENT", "这张确认已不是当前可提交的动作，可能已经处理或办理状态已变化。请先核实原请求结果，不要重复确认或重新付款。", "RECOVER_ORIGINAL", "核实原请求");
  if (["AUTH_CHALLENGE_INVALID", "AUTH_CHALLENGE_NOT_FOUND"].includes(code))
    return make("VERIFICATION_EXPIRED", "这次验证码已不可继续使用，可能已过期、已使用或达到尝试上限。请先读取最新办理状态，再按当前确认页面获取新的验证码。", "REVIEW_LATEST", "查看最新验证");
  if (code === "AUTH_PLAN_CHANGED")
    return make("VERIFICATION_CHANGED", "办理内容已变化，原验证码不再适用。请读取最新信息并重新核对，只对当前确认完成验证。", "REVIEW_LATEST", "查看最新确认");
  if (code === "CONFIRMATION_NOT_FOUND")
    return make("CONFIRMATION_MISSING", "暂时找不到这次确认，或它已不在当前可访问范围内。请先核实原请求结果，不要沿用旧确认重复提交。", "RECOVER_ORIGINAL", "核实原请求");
  if (code.includes("IDEMPOTENCY"))
    return make("REQUEST_CONFLICT", "这次内容与原办理信息不一致。请先核对原办理结果，不要再次发起相同付款。", "RECOVER_ORIGINAL", "查看原办理");
  const businessCopy: Record<string, string> = {
    INSUFFICIENT_FUNDS: "可用余额不足，请核对付款账户、币种和金额。",
    INVALID_AMOUNT: "金额尚未通过核验，请检查金额和币种。",
    RISK_MISMATCH: "这项产品与当前风险评估不匹配，请先核对适合的产品范围。",
    RISK_PROFILE_EXPIRED: "风险评估已过期，请完成新的评估后再选择产品。",
    FRAUD_TRANSFER_BLOCKED: "这笔转账存在风险，已停止继续办理。请通过可信渠道核实收款方。",
    EMERGENCY_RESERVE_BREACH: "这笔安排会影响已设定的应急资金，请核对预算后再决定。",
    GOAL_RESERVE_BREACH: "这笔安排会影响已设定的目标资金，请核对预算后再决定。",
  };
  if (businessCopy[code])
    return make("BUSINESS_REVIEW", businessCopy[code], "REVIEW_LATEST", "核对办理信息");
  if (operation === "read" && ["UNAVAILABLE_SCOPE", "CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED", "READ_SELECTION_SOURCE_CHANGED"].includes(code))
    return make("HISTORICAL_READ_UNAVAILABLE", "这条查询结果暂时不能展开。你可以重新查询需要的信息。", "NONE", "");
  if (status === 409 || /(?:VERSION_CHANGED|VIEW_CHANGED|PLAN_CHANGED|SCOPE_CHANGED|TARGET_CHANGED|SOURCE_CHANGED|HOLDING_CHANGED|FACTS_CHANGED)$/.test(code))
    return make("CHANGED", "办理信息已更新。请先读取最新内容并重新核对；旧确认不会继续使用。", "REVIEW_LATEST", "读取最新信息");
  if (status === 403)
    return make("FORBIDDEN", "当前身份无法访问或办理这项内容。请核对登录账号和授权范围，不要使用其他人的记录。", "VERIFY_ACCESS", "核对访问权限");
  if (status === 404)
    return make("NOT_FOUND", "暂时找不到这条记录，或它已不在当前可访问范围内。请刷新列表后选择仍可查看的记录。", "REFRESH", "刷新记录");
  if (status === 429)
    return make("RATE_LIMITED", `${retryAfterMs === undefined || retryAfterMs === 0 ? "" : `建议等待${Math.ceil(retryAfterMs / 1000)}秒后再查看。`}${operation === "auth"
      ? options.authAction === "challenge" || options.authAction === "verify"
        ? "验证请求较频繁，请稍等再试，避免连续请求或提交验证码。"
        : "验证请求较频繁，请稍等再试，避免连续提交登录或密码修改。"
      : "请求较频繁，请稍等再试，避免连续点击提交。已有办理信息会保留。"}`, "WAIT", "稍后再试");
  if (status === 400 || status === 422)
    return make("INPUT_REVIEW", operation === "auth"
      ? "账号信息还需要核对，请检查页面提示的必填信息和格式后再继续。"
      : "提交内容还需要核对，请检查页面提示的必填信息、金额和币种后再继续。", "NONE", "");
  return recoverUnknown("UNEXPECTED", "这次回复暂未完成");
}
