import type { ReactNode } from "react";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import "./customerResultDisclosure.css";

/** Extra result information is intentionally closed, including after a new
 * source/version is rendered. Native disclosure supplies keyboard semantics
 * and removes its hidden action buttons from the tab sequence. */
export function CustomerResultDisclosure({ children, scopeKey, context, label, className = "" }: {
  children: ReactNode; scopeKey: string; context?: string; label?: string; className?: string;
}) {
  const { locale } = useCustomerLanguage();
  const summary = label || (locale === "en" ? "View details" : "查看详情");
  return <details key={scopeKey} className={`customer-result-disclosure ${className}`} data-result-disclosure="true">
    <summary aria-label={context ? `${summary}：${context}` : summary}>{summary}</summary>
    <div className="customer-result-disclosure-body">{children}</div>
  </details>;
}
