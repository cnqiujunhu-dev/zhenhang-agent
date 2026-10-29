import type { Card } from "./types";

export function validSimulationCardNumber(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^(?:zhenhang|xiaozhao)[0-9a-f]{8}$/i.test(value)
  );
}
export function currentCardIdentity(
  card: Pick<Card, "card_number" | "display_last4" | "legacy_last4" | "last4">,
) {
  const number = validSimulationCardNumber(card.card_number)
    ? card.card_number
    : null;
  const newTail =
    typeof card.display_last4 === "string" &&
    /^[0-9a-f]{4}$/i.test(card.display_last4)
      ? card.display_last4
      : null;
  return {
    number,
    displayTail: newTail || card.legacy_last4 || card.last4 || "未返回",
    tailKind: newTail ? "SIMULATION_HEX" : "LEGACY_COMPAT",
  };
}
export function protectCardDraft(card: Card) {
  const identity = currentCardIdentity(card);
  return identity.number
    ? `临时锁卡，卡号${identity.number}`
    : `临时锁卡，尾号${identity.displayTail}`;
}
/** Quote identity is an immutable recorded snapshot, never joined with current cards. */
export function sourceCardSnapshot(quote: Record<string, unknown>) {
  const number = validSimulationCardNumber(quote.source_card_number) ? quote.source_card_number : null;
  const tail = typeof quote.source_card_display_last4 === "string" && /^[0-9a-f]{4}$/i.test(quote.source_card_display_last4)
    ? quote.source_card_display_last4 : null;
  return {
    number,
    displayTail: tail || number?.slice(-4) || null,
    legacyTail:
      typeof quote.source_card_legacy_last4 === "string"
        ? quote.source_card_legacy_last4
        : typeof quote.source_card_last4 === "string"
          ? quote.source_card_last4
          : null,
  };
}
