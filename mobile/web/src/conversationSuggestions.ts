import type { ConversationSuggestion, ConversationTurn } from "./conversationTypes";

export const customerSuggestionCatalogVersion = "customer-capability-directory-20261019-family-life-v6";
const customerRoles = new Set(["DEMO_OWNER", "CUSTOMER", "DEV_TEST", "FAMILY_HELPER"]);
const suggestionModes = new Set(["DISCOVERY", "KNOWLEDGE", "CONSULT", "GREETING", "FEEDBACK", "OUT_OF_SCOPE"]);
const identifier = /^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){0,3}$/;
const fields = new Set(["id", "label", "prompt", "behavior", "capability_id", "read_only"]);
function reviewedShape(value: unknown): value is ConversationSuggestion {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return Object.keys(row).length === fields.size && Object.keys(row).every(key => fields.has(key))
    && typeof row.id === "string" && identifier.test(row.id) && row.id.length <= 80
    && typeof row.capability_id === "string" && identifier.test(row.capability_id) && row.capability_id.length <= 64
    && typeof row.label === "string" && row.label.trim().length > 0 && row.label.length <= 160
    && typeof row.prompt === "string" && row.prompt.trim().length > 0 && row.prompt.length <= 240
    && !/[\u0000-\u001f\u007f]/.test(row.label + row.prompt)
    && typeof row.read_only === "boolean"
    && (row.behavior === "FILL_COMPOSER" || row.behavior === "SEND_READ_ONLY" && row.read_only === true);
}

export type ConversationSuggestionContext = {
  currentTurnId?: string;
  conversationId?: string;
  actorRole?: string;
  scopeKey?: string;
  blocked?: boolean;
};

/** Fail closed on stale/foreign/unreviewed records. No answer text is parsed.
 * This is a display check, not a replacement for server-side authorization.
 */
export function currentConversationSuggestions(turn: ConversationTurn, context: ConversationSuggestionContext): ConversationSuggestion[] {
  const meta = turn.response_meta;
  if (context.blocked || !context.scopeKey || !context.actorRole || !customerRoles.has(context.actorRole)
    || context.currentTurnId !== turn.turn_id || context.conversationId !== turn.conversation_id
    || turn.status !== "ANSWERED" || turn.error_code || !suggestionModes.has(turn.mode || "")
    || turn.task_link || meta?.suggestion_source !== "CUSTOMER_CAPABILITY_CATALOG"
    || meta?.suggestion_catalog_version !== customerSuggestionCatalogVersion
    || meta?.suggestion_role !== context.actorRole || !Array.isArray(turn.suggestions)) return [];
  const seen = new Set<string>();
  return turn.suggestions.filter((row): row is ConversationSuggestion => {
    if (!reviewedShape(row) || seen.has(row.id)
      || context.actorRole === "FAMILY_HELPER" && !["family.view", "family.propose"].includes(row.id)) return false;
    seen.add(row.id);
    return true;
  }).slice(0, 8).map(row => ({ ...row }));
}

export function sameConversationSuggestion(a: ConversationSuggestion, b: ConversationSuggestion): boolean {
  return a.id === b.id && a.label === b.label && a.prompt === b.prompt && a.behavior === b.behavior
    && a.capability_id === b.capability_id && a.read_only === b.read_only;
}
