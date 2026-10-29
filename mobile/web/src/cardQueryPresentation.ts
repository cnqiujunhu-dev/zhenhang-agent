import type { CustomerLocale } from "./customerLanguage";

type CardRecord = Record<string, unknown>;
export type CardQueryNode = { action: string; status?: string; result?: unknown };
export type CardQueryCounts = { total: number; debit: number; credit: number };
const record = (value: unknown): CardRecord | null => value !== null && typeof value === "object" && !Array.isArray(value)
  ? value as CardRecord : null;
const identifier = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_.:-]{1,160}$/.test(value) ? value : null;
const number = (value: unknown) => typeof value === "string" && /^(?:zhenhang|xiaozhao)[0-9a-f]{8}$/i.test(value)
  ? value.toLowerCase() : null;

/** Counts the returned instruments, never currency wallets, balances or facilities.
 * Missing identity/collections are unknown, not an empty account or a zero count.
 * Card IDs and full registered numbers may join duplicate representations; tail
 * numbers, equal balances and shared account refs are not identity evidence.
 */
export function cardQueryCounts(resultOrNodes: unknown): CardQueryCounts | null {
  const nodes: CardQueryNode[] = Array.isArray(resultOrNodes) ? resultOrNodes as CardQueryNode[]
    : [{ action: "CARD_STATUS", status: "SUCCEEDED", result: resultOrNodes }];
  if (!nodes.length) return null;
  const entries: { id: string | null; number: string | null; kind: "debit" | "credit" }[] = [];
  for (const node of nodes) {
    if (!record(node) || node.status !== "SUCCEEDED" || !["CARD_STATUS", "CREDIT_VIEW"].includes(node.action)) return null;
    const data = record(node.result);
    if (!data || !Array.isArray(data.cards)) return null;
    const collections: [unknown, "debit" | "credit"][] = [[data.cards, node.action === "CREDIT_VIEW" ? "credit" : "debit"]];
    if (node.action === "CARD_STATUS" && Object.hasOwn(data, "credit_cards")) collections.push([data.credit_cards, "credit"]);
    for (const [rows, kind] of collections) {
      if (!Array.isArray(rows)) return null;
      for (const row of rows) {
        const card = record(row);
        if (!card) return null;
        const id = identifier(card.card_id), pan = number(card.card_number);
        if (!id && !pan || card.instrument_type !== undefined && card.instrument_type !== kind.toUpperCase()) return null;
        if (pan && typeof card.display_last4 === "string" && /^[0-9a-f]{4}$/i.test(card.display_last4)
          && card.display_last4.toLowerCase() !== pan.slice(-4)) return null;
        entries.push({ id, number: pan, kind });
      }
    }
  }
  const idsByNumber = new Map<string, Set<string>>(), numbersById = new Map<string, Set<string>>();
  for (const row of entries) if (row.id && row.number) {
    const ids = idsByNumber.get(row.number) || new Set<string>(); ids.add(row.id); idsByNumber.set(row.number, ids);
    const pans = numbersById.get(row.id) || new Set<string>(); pans.add(row.number); numbersById.set(row.id, pans);
  }
  if ([...idsByNumber.values(), ...numbersById.values()].some(values => values.size > 1)) return null;
  const instruments = new Map<string, "debit" | "credit">();
  for (const row of entries) {
    const linkedId = row.number ? [...(idsByNumber.get(row.number) || [])][0] : undefined;
    const key = row.id || linkedId ? `id:${row.id || linkedId}` : `number:${row.number}`;
    if (instruments.has(key) && instruments.get(key) !== row.kind) return null;
    instruments.set(key, row.kind);
  }
  return { total: instruments.size, debit: [...instruments.values()].filter(kind => kind === "debit").length,
    credit: [...instruments.values()].filter(kind => kind === "credit").length };
}

export function cardQueryTitle(result: unknown, locale: CustomerLocale): string {
  return cardQueryTitleForNodes([{ action: "CARD_STATUS", status: "SUCCEEDED", result }], locale);
}

export function cardQueryTitleForNodes(nodes: readonly CardQueryNode[], locale: CustomerLocale): string {
  const counts = cardQueryCounts(nodes), en = locale === "en";
  return counts === null ? en ? "Card details need verification" : "银行卡资料待核对"
    : counts.total === 0 ? en ? "No matching bank cards" : "暂无匹配的银行卡"
      : en ? `Found ${counts.total} bank ${counts.total === 1 ? "card" : "cards"}` : `查到 ${counts.total} 张银行卡`;
}

/** Remove only an exact, now-title-owned quantity sentence with matching counts.
 * Keep a conflicting count and every balance, status, warning or other answer.
 */
export function cardQueryNarration<T extends string | null | undefined>(value: T, resultOrNodes: unknown,
  _locale: CustomerLocale = "zh-CN"): T {
  if (typeof value !== "string") return value;
  const counts = cardQueryCounts(resultOrNodes);
  if (counts === null) return value;
  const exact = [
    `本次查到${counts.total}张卡：${counts.debit}张借记卡、${counts.credit}张信用卡。`,
    `This query found ${counts.total} cards: ${counts.debit} debit and ${counts.credit} credit.`,
    `查到 ${counts.total} 张银行卡`, `本次查到${counts.total}张银行卡。`,
    `Found ${counts.total} bank ${counts.total === 1 ? "card" : "cards"}`,
  ];
  return value.split(/\r?\n/).map(line => {
    const text = line.trim();
    if (exact.includes(text)) return "";
    const prefix = exact.find(sentence => /[。.]$/.test(sentence) && text.startsWith(sentence));
    return prefix ? text.slice(prefix.length).trimStart() : line;
  }).filter(line => line.trim()).join("\n").trim() as T;
}

// Shared wording for the complete card read. This is presentation only: it
// neither interprets a customer's question nor selects a banking operation.
export function cardQueryPresentation(locale: CustomerLocale) {
  return locale === "en"
    ? {
        title: "Bank cards",
        limitLabel: "Per-payment limit",
        balanceLabels: { CNY: "CNY account balance", USD: "USD wallet balance" },
        balanceMissing: "Balance not returned",
        sharedBadge: "Shared account balance",
        conflictingBalance: "The account's balance records conflict. Read again to verify them.",
        unknownBalanceCurrency: "A returned balance currency is unsupported. Its amount is not shown or treated as CNY.",
        footer: "No funds or card states changed. Card actions still require review and confirmation.",
        moreServices: "More card services",
      }
    : {
        title: "银行卡",
        limitLabel: "单笔限额",
        balanceLabels: { CNY: "CNY 账户总余额", USD: "USD 钱包余额" },
        balanceMissing: "余额未返回",
        sharedBadge: "共用账户余额",
        conflictingBalance: "同一账户的余额记录不一致，请重新查询核对。",
        unknownBalanceCurrency: "返回的余额包含未支持的币种；未展示该金额，也不把它当作人民币。",
        footer: "本次没有改变资金或卡状态；办理仍需重新核对和确认。",
        moreServices: "更多卡片服务",
      };
}
