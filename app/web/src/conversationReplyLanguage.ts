import { isCustomerLocale, type CustomerLocale } from "./customerLanguage.ts";

/** Language hint only: no intent, recipient, amount or banking action parsing.
 * A deliberate language-button choice wins. Otherwise clear prose follows its
 * script; identifiers, currencies, amounts and short slot fragments stay auto.
 */
export function conversationReplyLanguage(message: string, explicit?: CustomerLocale): CustomerLocale | undefined {
  if (isCustomerLocale(explicit)) return explicit;
  const content = typeof message === "string" ? message.normalize("NFKC").trim() : "";
  if (!content) return undefined;
  const han = content.match(/\p{Script=Han}/gu)?.length || 0;
  const words = content.match(/\b[A-Za-z]{2,}\b/g)?.filter(word => !["USD", "CNY", "RMB"].includes(word.toUpperCase())) || [];
  const latin = content.match(/[A-Za-z]/g)?.length || 0;
  if (words.length >= 2 && latin >= 10 && (han === 0 || latin > han * 3)) return "en";
  if (han >= 2) return "zh-CN";
  return undefined;
}

/** Older display caches and server display preferences are not proof that a
 * human selected the reply language. Only the new explicit-choice marker is.
 */
export function explicitReplyLanguage(initial: unknown, storedChoice: unknown): CustomerLocale | undefined {
  return isCustomerLocale(initial) ? initial : isCustomerLocale(storedChoice) ? storedChoice : undefined;
}
