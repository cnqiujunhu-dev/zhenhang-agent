import { request } from "../api.ts";
import { validateAccountIdentity, validateLoginSession, validatePasswordChange } from "./runtimeValidator.ts";
import type {
  AccountIdentity,
  LoginInput,
  LoginSession,
  RegisterInput,
} from "./types";

export const authApi = {
  login: (input: LoginInput) =>
    request<unknown>("/api/v2/auth/login", "", "POST", input, undefined, { operation: "auth", authAction: "login" })
      .then(value => validateLoginSession(value,{username:input.username.trim().toLowerCase()})),
  register: (input: RegisterInput) =>
    request<unknown>("/api/v2/auth/register", "", "POST", input, undefined, { operation: "auth", authAction: "register" })
      .then(value => validateLoginSession(value,{username:input.username.trim().toLowerCase()})),
  me: (token: string, signal?: AbortSignal) =>
    request<unknown>(
      "/api/v2/auth/me",
      token,
      "GET",
      undefined,
      undefined,
      { signal },
    ).then(validateAccountIdentity),
  refresh: (token: string, identity?: AccountIdentity) =>
    request<unknown>("/api/v2/auth/refresh", token, "POST", {}, undefined, { operation: "auth", authAction: "refresh" })
      .then(value => validateLoginSession(value,identity)),
  logout: (token: string) =>
    request<{ logged_out: boolean }>("/api/v2/auth/logout", token, "POST", {}, undefined, { operation: "auth", authAction: "logout" }),
  password: (token: string, current_password: string, new_password: string) =>
    request<unknown>("/api/v2/auth/password", token, "POST", {
      current_password,
      new_password,
    }, undefined, { operation: "auth", authAction: "password" }).then(validatePasswordChange),
};
