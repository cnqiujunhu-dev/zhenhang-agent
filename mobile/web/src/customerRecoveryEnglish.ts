import type { RecoveryAuthAction, RecoveryOperation } from "./customerRecovery";
export function recoveryEnglish(kind: string, code: string, operation: RecoveryOperation, authAction?: RecoveryAuthAction, retryAfterMs?: number) {
  const unknown = operation === "submit" || operation === "confirm";
  const uncertain = ["INTERRUPTED", "TIMEOUT", "OFFLINE", "INCOMPLETE_RESPONSE", "UNAVAILABLE", "UNEXPECTED"].includes(kind);
  if (uncertain) {
    const prefix = ({ INTERRUPTED: "The connection was interrupted.", TIMEOUT: "The reply is taking longer than expected.", OFFLINE: "The service cannot be reached.", INCOMPLETE_RESPONSE: "The reply was not fully read.", UNAVAILABLE: "The service is temporarily busy.", UNEXPECTED: "The reply has not been verified." } as Record<string,string>)[kind];
    if (operation === "auth") {
      const verification = authAction === "challenge" || authAction === "verify";
      const next = verification ? "Read the latest confirmation before trying verification again. A missing reply does not mean verification succeeded." : authAction === "register" ? "Try signing in with this account before registering again." : authAction === "password" ? "Try signing in with the new password to check the result; do not repeatedly submit a password change." : authAction === "logout" ? "Close private pages and check sign-in status when the network is restored." : "Check the network and sign in again.";
      return { message: `${prefix} ${next}`, actionLabel: verification ? "View latest confirmation" : "Check sign-in status" };
    }
    return { message: `${prefix} ${unknown ? "The result is uncertain. Verify the original request's result; it has been retained. Do not repeat the same payment." : "Latest information has not been loaded. You can read it again when connected; existing requests are unaffected."}`, actionLabel: unknown ? "Verify original request" : "Reload" };
  }
  const messages: Record<string,string> = {
    CANCELLED: "This read was stopped. Existing requests are unaffected.",
    CREDENTIAL_INVALID: code === "AUTH_CODE_INVALID" ? "The code did not pass verification. Check it and enter it again. Never send a verification code in the conversation." : authAction === "password" ? "The current password did not pass verification. Check it before trying again." : "The username or password did not pass verification. Check them before trying again.",
    AUTH_EXPIRED: "Your session has expired. Sign in again to view the original request; do not initiate the payment again.",
    ACCOUNT_INPUT_REVIEW: ({ USERNAME_UNAVAILABLE: "This username is unavailable. Choose another, or try signing in if you already registered.", INVALID_USERNAME: "Use 3–32 lowercase letters, digits or underscores, beginning with a letter.", INVALID_PASSWORD_LENGTH: "Use a password of 6–128 characters.", INVALID_ACCOUNT_PROFILE: "Choose an account profile offered by this page." } as Record<string,string>)[code] || "Review your account details.",
    CONFIRMATION_EXPIRED: "This verification or confirmation has expired. Read the latest request details, then review and verify the current action.",
    CONFIRMATION_NOT_CURRENT: "This is no longer the current confirmable action. It may already have been processed. Verify the original request's result; do not confirm or pay again.",
    VERIFICATION_EXPIRED: "This code can no longer be used. It may have expired, been used, or reached its attempt limit. Read the latest status before requesting a new code.",
    VERIFICATION_CHANGED: "Request details changed. The old code no longer applies. Read and verify only the current confirmation.",
    CONFIRMATION_MISSING: "This confirmation cannot be found or is outside your access. Check the original request; do not resubmit an old confirmation.",
    REQUEST_CONFLICT: "These details differ from the original request. Check its result before asking to make the same payment again.",
    BUSINESS_REVIEW: ({ INSUFFICIENT_FUNDS: "Available funds are insufficient. Review the paying account, currency and amount.", INVALID_AMOUNT: "The amount was not verified. Check the amount and currency.", RISK_MISMATCH: "This product does not match the current risk assessment.", RISK_PROFILE_EXPIRED: "Your risk assessment has expired. Complete a new assessment before choosing a product.", FRAUD_TRANSFER_BLOCKED: "This transfer is blocked by a risk check. Verify the recipient through a trusted channel.", EMERGENCY_RESERVE_BREACH: "This action would affect your emergency reserve. Review the budget before proceeding.", GOAL_RESERVE_BREACH: "This action would affect reserved goal funds. Review the budget before proceeding." } as Record<string,string>)[code] || "Review the request details.",
    CHANGED: "Request details have changed. Read the latest information and review it again; the old confirmation cannot be reused.",
    HISTORICAL_READ_UNAVAILABLE: "This query result cannot be expanded right now. You can ask again for the information you need.",
    FORBIDDEN: "This identity cannot view or perform this action. Check your account and authorization; do not use another person's records.",
    NOT_FOUND: "This record cannot be found or is outside your current access. Refresh the list and choose an accessible record.",
    RATE_LIMITED: `${retryAfterMs ? `Wait ${Math.ceil(retryAfterMs / 1000)} seconds before checking again. ` : ""}Requests are too frequent. Wait before checking; do not repeatedly submit. Existing request records are retained.`,
    INPUT_REVIEW: operation === "auth" ? "Review the required account details and their format before continuing." : "Review the required information, amount and currency before continuing.",
  };
  const labels: Record<string,string> = { AUTH_EXPIRED: "Sign in again", CONFIRMATION_EXPIRED: "View latest confirmation", CONFIRMATION_NOT_CURRENT: "Verify original request", VERIFICATION_EXPIRED: "View latest verification", VERIFICATION_CHANGED: "View latest confirmation", CONFIRMATION_MISSING: "Verify original request", REQUEST_CONFLICT: "View original request", BUSINESS_REVIEW: "Review request", CHANGED: "Read latest information", FORBIDDEN: "Check access", NOT_FOUND: "Refresh records", RATE_LIMITED: "Try later" };
  return { message: messages[kind] || "The result has not been verified. Check the original request before continuing.", actionLabel: labels[kind] || "" };
}
