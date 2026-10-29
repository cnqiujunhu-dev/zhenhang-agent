const repeatedCreditExplanation = new Set([
  "人民币和美元共用人民币计价的授信池；信用额度不是现金。",
  "CNY and USD share one CNY-based credit pool; credit is not cash.",
]);

type Data = Record<string, unknown>;
const object = (value: unknown): Data => value !== null && typeof value === "object" && !Array.isArray(value)
  ? value as Data : {};
type Scope = {
  state: string; pendingConfirmation: boolean; readOnly?: boolean; detailKind?: string;
  nodes: readonly { node_id?: string; action: string; status?: string; result?: unknown }[];
};
function creditRead(scope: Scope) {
  return scope.state === "SUCCEEDED" && !scope.pendingConfirmation && scope.readOnly === true && scope.detailKind === "CREDIT"
    && scope.nodes.some(node => node.action === "CREDIT_VIEW") && scope.nodes.every(node => node.status === "SUCCEEDED"
      && ["CREDIT_VIEW", "CREDIT_STATEMENT", "CARD_STATUS"].includes(node.action));
}

/** Match the complete normal-status copy only against its typed read proof.
 * A lost/locked/unverified/overdue notice or any extra warning is untouched.
 */
export function redundantNormalCreditNotice(value: unknown, nodeId: string, cardValue: unknown, displayValue: unknown): boolean {
  if (typeof value !== "string") return false;
  const card = object(cardValue), display = object(displayValue);
  if (card.status !== "ACTIVE" || display.card_status !== "ACTIVE" || display.status !== "READY"
    || display.schema_version !== "credit-status-presentation-v1" || display.node_id !== nodeId
    || typeof card.card_id !== "string" || display.card_id !== card.card_id) return false;
  const tail = typeof card.display_last4 === "string" ? card.display_last4 : typeof card.card_number === "string" ? card.card_number.slice(-4) : "";
  if (!/^[0-9a-f]{4}$/i.test(tail)) return false;
  const normal = [
    `本次查询显示尾号${tail}信用卡状态正常。消费仍需核对可用额度、单笔限额和交易条件，不保证一定可付。`,
    `This read shows credit card ending ${tail} is active. A purchase still needs available credit, the transaction limit and current transaction checks; payment is not guaranteed.`,
  ];
  return normal.some(text => value.trim() === text || value.trim() === `好的，${text}`);
}

export function compactCreditQueryIntro<T extends string | null | undefined>(value: T, scope: Scope): T | null {
  if (!creditRead(scope)) return value;
  for (const node of scope.nodes) {
    if (node.action !== "CREDIT_VIEW" || typeof node.node_id !== "string") continue;
    const data = object(node.result);
    if (Array.isArray(data.cards) && data.cards.some(card => redundantNormalCreditNotice(value, node.node_id!, card, data.credit_status_display))) return null;
  }
  return value;
}

/** This fixed server explanation duplicates the card's typed currency/limit
 * fields. Keep every other reply, status warning and operation confirmation.
 */
export function compactCreditQueryDetail<T extends string | null | undefined>(value: T, scope: Scope): T | null {
  if (typeof value !== "string" || !creditRead(scope)) return value;
  return repeatedCreditExplanation.has(value.trim()) ? null : value;
}
