import type { CustomerLocale } from "./customerLanguage";

/** A short idle hint; capabilities and authorization remain in their own controls. */
export function composerPlaceholder(locale: CustomerLocale, options: {ready: boolean; helper: boolean}) {
  if (!options.ready) return locale === "en" ? "Preparing…" : "准备中…";
  if (options.helper) return locale === "en" ? "Review help…" : "核对委托…";
  return locale === "en" ? "Ask me…" : "说说需求…";
}
