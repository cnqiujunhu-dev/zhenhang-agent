/** Display-only grouping after an owned credit read; never financial authority. */
type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown, minimum = 0): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) => object(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
export type CreditFacilityDisplay = { groups: { cardIds: string[]; tails: string[]; limitMinor: number }[]; groupByCard: Map<string, number> };

export function creditFacilityDisplay(value: unknown): CreditFacilityDisplay | null {
  if (!Array.isArray(value)) return null;
  const groups: CreditFacilityDisplay["groups"] = [], groupByCard = new Map<string, number>();
  const facilities = new Map<string, { index: number; proof: string }>(), numbers = new Set<string>(), tails = new Set<string>();
  const values = ["base_credit_limit_minor", "used_base_exposure_minor", "authorization_base_exposure_minor", "available_base_credit_minor"];
  const balanceFields = ["principal_minor", "fees_minor", "interest_minor", "credit_balance_minor", "outstanding_minor"];
  for (const card of value) {
    if (!object(card) || !["card_id", "credit_account_id", "card_number"].every(key => typeof card[key] === "string" && (card[key] as string).trim())) return null;
    const id = card.card_id as string, facility = card.credit_account_id as string, number = card.card_number as string;
    if (!/^(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}$/.test(number) || groupByCard.has(id) || numbers.has(number) || tails.has(number.slice(-4))) return null;
    const policy = card.policy;
    if (card.instrument_type !== "CREDIT" || card.credit_is_cash !== false || card.base_currency !== "CNY"
      || canonical(card.supported_currencies) !== '["CNY","USD"]' || card.shared_credit_limit !== true || !integer(card.facility_version, 1)
      || !object(policy) || policy.policy_version !== "sim-credit-v1" || policy.base_currency !== "CNY"
      || canonical(policy.supported_currencies) !== '["CNY","USD"]' || (Object.hasOwn(policy, "shared_credit_limit") ? policy.shared_credit_limit : true) !== true
      || !values.every(key => integer(card[key])) || values.slice(1).reduce((sum, key) => sum + (card[key] as number), 0) !== card.base_credit_limit_minor) return null;
    if (!Array.isArray(card.balances) || card.balances.length !== 2 || !card.balances.every(object)
      || new Set(card.balances.map(row => row.currency)).size !== 2 || card.balances.some(row => row.currency !== "CNY" && row.currency !== "USD")) return null;
    const balances = [...card.balances].sort((a, b) => String(a.currency).localeCompare(String(b.currency)));
    if (balances.some(row => !balanceFields.every(key => integer(row[key]))
      || balanceFields.slice(0, 3).reduce((sum, key) => sum + (row[key] as number), 0) !== row.outstanding_minor)) return null;
    const proof = canonical({ ...Object.fromEntries(["facility_version", "base_currency", "supported_currencies", "shared_credit_limit", ...values].map(key => [key, card[key]])),
      policy, balances: balances.map(row => Object.fromEntries(["currency", ...balanceFields].map(key => [key, row[key]]))) });
    const existing = facilities.get(facility);
    if (existing && existing.proof !== proof) return null;
    const index = existing?.index ?? groups.length;
    if (!existing) {
      facilities.set(facility, { index, proof });
      groups.push({ cardIds: [], tails: [], limitMinor: card.base_credit_limit_minor as number });
    }
    groups[index].cardIds.push(id); groups[index].tails.push(number.slice(-4)); groupByCard.set(id, index);
    numbers.add(number); tails.add(number.slice(-4));
  }
  return { groups, groupByCard };
}
