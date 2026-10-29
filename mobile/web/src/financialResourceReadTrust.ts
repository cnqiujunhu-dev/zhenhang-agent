import { ApiError } from "./api";
import { assertFinancialResource } from "./financialResource";
import type { CustomerLocale } from "./customerLanguage";

export const financialResourceKinds = ["accounts", "cards", "investments", "subscriptions", "notifications", "bill"] as const;
export type FinancialResourceKind = typeof financialResourceKinds[number];
export type ResourceReadScope = { token: string; actorEpoch: number; actorId?: string; visibilityEpoch: number; revision: number; visible: boolean; enabled: boolean };
export type ResourceReadStatus = "UNREAD" | "LOADING" | "READY" | "ERROR" | "CANCELLED";
export type ResourceDatasetRead = { status: ResourceReadStatus; generation: number; data: unknown | null; previous: unknown | null; errorStatus?: number };
export type ResourceReadTicket = { kind: FinancialResourceKind; generation: number; scopeStamp: number; scope: ResourceReadScope };
const emptyRead = (): ResourceDatasetRead => ({ status: "UNREAD", generation: 0, data: null, previous: null });
const sameScope = (a: ResourceReadScope, b: ResourceReadScope) => a.token === b.token && a.actorEpoch === b.actorEpoch && a.actorId === b.actorId
  && a.visibilityEpoch === b.visibilityEpoch && a.revision === b.revision && a.enabled === b.enabled && a.visible === b.visible;

/** Each dataset owns its latest GET stamp. A newer poll/refresh/actor always wins. */
export class FinancialResourceReadTrust {
  private scope: ResourceReadScope = { token: "", actorEpoch: -1, visibilityEpoch: -1, revision: -1, visible: false, enabled: false };
  private scopeStamp = 0;
  private generation = 0;
  private stopped = false;
  private reads = Object.fromEntries(financialResourceKinds.map(kind => [kind, emptyRead()])) as Record<FinancialResourceKind, ResourceDatasetRead>;
  private listeners = new Set<() => void>();
  constructor(private visibleNow: () => boolean = () => true) {}
  bind(scope: ResourceReadScope) {
    if (sameScope(this.scope, scope)) return;
    const sameOwner = this.scope.token === scope.token && this.scope.actorEpoch === scope.actorEpoch && this.scope.actorId === scope.actorId && scope.enabled;
    const previousNotifications = sameOwner ? this.reads.notifications.data ?? this.reads.notifications.previous : null;
    this.scope = { ...scope }; this.scopeStamp += 1;
    this.reads = Object.fromEntries(financialResourceKinds.map(kind => [kind, {
      ...emptyRead(), previous: kind === "notifications" ? previousNotifications : null,
    }])) as Record<FinancialResourceKind, ResourceDatasetRead>;
  }
  subscribe(listener: () => void) {
    this.stopped = false; this.listeners.add(listener);
    return () => { this.listeners.delete(listener); if (!this.listeners.size) this.stop(); };
  }
  private changed() { for (const listener of this.listeners) listener(); }
  stop() { this.stopped = true; this.scopeStamp += 1; }
  begin(kind: FinancialResourceKind, bound: ResourceReadScope): ResourceReadTicket | null {
    if (this.stopped || !bound.token || !bound.enabled || !bound.visible || !this.visibleNow() || !sameScope(bound, this.scope)) return null;
    const previous = this.reads[kind].data ?? this.reads[kind].previous;
    const ticket = { kind, generation: ++this.generation, scopeStamp: this.scopeStamp, scope: { ...bound } };
    this.reads[kind] = { status: "LOADING", generation: ticket.generation, data: null, previous };
    this.changed(); return ticket;
  }
  current(ticket: ResourceReadTicket) {
    return !this.stopped && this.scope.enabled && this.scope.visible && this.visibleNow() && ticket.scopeStamp === this.scopeStamp
      && sameScope(ticket.scope, this.scope) && this.reads[ticket.kind].generation === ticket.generation;
  }
  settle(ticket: ResourceReadTicket, result: PromiseSettledResult<unknown>): boolean {
    if (!this.current(ticket)) return false;
    let value: unknown, reason: unknown;
    if (result.status === "fulfilled") {
      try {
        value = assertFinancialResource(ticket.kind, result.value);
        if (ticket.kind === "investments" && (value as Record<string, unknown>[]).some(row =>
          (row.holding_units as number) < 0 || (row.price_minor as number) < 0
          || (row.risk_level as number) < 1 || (row.risk_level as number) > 5 || row.currency !== undefined && row.currency !== "CNY"))
          throw new ApiError("", 0, "RESOURCE_SCHEMA_INVALID");
        if (ticket.kind === "cards" && (value as Record<string, unknown>[]).some(row => (row.limit_minor as number) < 0))
          throw new ApiError("", 0, "RESOURCE_SCHEMA_INVALID");
      }
      catch (error) { reason = error; }
    } else reason = result.reason;
    if (result.status === "fulfilled" && reason === undefined) {
      this.reads[ticket.kind] = { status: "READY", generation: ticket.generation, data: value, previous: value };
      this.changed(); return true;
    }
    const status = reason instanceof ApiError ? reason.status : undefined;
    const previous = [401, 403, 404].includes(status ?? 0) ? null : this.reads[ticket.kind].previous;
    this.reads[ticket.kind] = { status: "ERROR", generation: ticket.generation, data: null, previous, errorStatus: status };
    this.changed(); return false;
  }
  cancel(ticket: ResourceReadTicket) {
    if (!this.current(ticket)) return;
    this.reads[ticket.kind] = { ...this.reads[ticket.kind], status: "CANCELLED", generation: ++this.generation, data: null };
    this.changed();
  }
  cancelLatest(kind: FinancialResourceKind) {
    if (!this.scope.enabled || !this.scope.visible || !this.visibleNow() || this.stopped) return;
    this.reads[kind] = { ...this.reads[kind], status: "CANCELLED", generation: ++this.generation, data: null };
    this.changed();
  }
  view(kind: FinancialResourceKind): ResourceDatasetRead {
    if (!this.scope.enabled || !this.scope.visible || !this.visibleNow() || this.stopped) return emptyRead();
    return this.reads[kind];
  }
  ready(kind: FinancialResourceKind, boundGeneration?: number) {
    const read = this.view(kind);
    return read.status === "READY" && (boundGeneration === undefined || read.generation === boundGeneration);
  }
  warning(locale: CustomerLocale) {
    const failed = financialResourceKinds.filter(kind => ["ERROR", "CANCELLED"].includes(this.view(kind).status));
    if (!failed.length) return "";
    const names = failed.map(kind => resourceDatasetName(kind, locale)).join(locale === "en" ? ", " : "、");
    return locale === "en" ? `${names}: latest information is unverified. Reload to check it; this does not mean there are no records.`
      : `${names}：最新资料尚未核验，可重新读取；这不代表没有记录。`;
  }
}

export function resourceDatasetName(kind: FinancialResourceKind, locale: CustomerLocale) {
  const names: Record<FinancialResourceKind, [string, string]> = {
    accounts: ["账户余额", "Account balances"], cards: ["卡片", "Cards"], investments: ["持仓", "Holdings"],
    subscriptions: ["订阅", "Subscriptions"], notifications: ["办理通知", "Notifications"], bill: ["账单", "Statements"],
  };
  return names[kind][locale === "en" ? 1 : 0];
}
export function resourceReadNotice(kind: FinancialResourceKind, read: ResourceDatasetRead, locale: CustomerLocale) {
  const name = resourceDatasetName(kind, locale);
  if (locale === "en") return read.status === "LOADING" ? `Reading ${name.toLowerCase()}…`
    : read.status === "CANCELLED" ? `${name}: this read was cancelled. Latest information is unverified.`
    : read.status === "ERROR" ? `${name} could not be loaded or verified. This does not mean there are no records. Please reload.`
    : `${name} have not been verified. Please reload.`;
  return read.status === "LOADING" ? `正在读取${name}…`
    : read.status === "CANCELLED" ? `${name}读取已取消，最新资料尚未核验。`
    : read.status === "ERROR" ? `${name}暂未读到或未通过核验，不代表没有记录。请重新读取。`
    : `${name}资料尚未核验，请重新读取。`;
}
