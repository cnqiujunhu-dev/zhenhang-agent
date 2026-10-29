export type AccountRole =
  | "CUSTOMER"
  | "DEV_TEST"
  | "ADMIN"
  | "FAMILY_HELPER"
  | string;
export interface LoginSession {
  token: string;
  expires_at: string;
  username: string;
  role: AccountRole;
  user_id: string;
  sandbox_id: string;
  account_id: string;
  profile: string | null;
}
export interface AccountIdentity {
  authenticated: boolean;
  registered?: boolean;
  username?: string;
  role: AccountRole;
  user_id: string;
  sandbox_id: string;
  account_id?: string;
  profile?: string | null;
  version: number | null;
  display_name: string;
  family_context?: {
    issuer_account_id: string;
    parent_account_id: string;
    helper_user_id: string;
    parent_sandbox_id: string;
  };
}
export interface LoginInput {
  username: string;
  password: string;
}
export interface RegisterInput extends LoginInput {
  display_name?: string;
  profile?: string;
}
export interface StoredLogin {
  token: string;
  expires_at: string;
}
export interface PasswordChangeResult {
  password_changed: true;
  sessions_revoked: number;
  requires_login: true;
}
