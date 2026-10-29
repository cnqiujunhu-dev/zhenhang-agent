import type { Subscription } from "./types";
export interface RecurringClue {
  merchant: string; amount_minor: number; cycle: string; occurrence_count: number;
  can_cancel_bank_mandate: boolean; candidate_only: boolean;
  authoritative_subscription?: { source_type: string } | null;
}
export function partitionRecurringClues(subscriptions: Subscription[], clues: RecurringClue[]) {
  const matches = new Map<string, RecurringClue[]>(), other: RecurringClue[] = [];
  for (const clue of clues) {
    // Original merchant equality only; no translation, fuzzy identity, or alias binding.
    const targets = subscriptions.filter(item => item.merchant === clue.merchant);
    if (targets.length !== 1) { other.push(clue); continue; }
    const key = targets[0].subscription_id;
    matches.set(key, [...matches.get(key) || [], clue]);
  }
  return { matches, other };
}
