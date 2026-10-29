import { useEffect, useState } from "react";
import { request } from "./api";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { dateText, Icon, money, Skeleton, stateLabels } from "./components";
import { subscriptionDueLabel } from "./customerContinuation";
import { partitionRecurringClues, type RecurringClue } from "./subscriptionPresentation";
import type { Subscription } from "./types";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { subscriptionDate } from "./subscriptionCommercePresentation";

export function SubscriptionResourcePanel({ subscriptions, token, revision, loading, onManage }: {
  subscriptions: Subscription[]; token: string; revision: number; loading: boolean; onManage: () => void;
}) {
  const { locale, t } = useCustomerLanguage();
  const [analysis, setAnalysis] = useState<{ token: string; candidates: RecurringClue[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController(); setError("");
    request<{ candidates: RecurringClue[] }>("/api/v2/subscriptions/analysis", token, "GET", undefined, undefined, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setAnalysis({ token, candidates: Array.isArray(value.candidates) ? value.candidates : [] }); })
      .catch(() => { if (!controller.signal.aborted) setError(locale === "en" ? "Recurring-payment clues could not be loaded. This does not mean there are none." : "周期线索暂未读到，不代表没有线索。"); });
    return () => controller.abort();
  }, [token, revision, locale]);
  const partition = partitionRecurringClues(subscriptions, analysis?.token === token ? analysis.candidates : []);
  const clueRow = (clue: RecurringClue, index: number) => <div className="recurring-item" key={index}>
    <div><strong>{clue.merchant}</strong><span>{money(clue.amount_minor)} / {locale === "en" ? clue.cycle.toLowerCase() : ({ WEEKLY: "每周", MONTHLY: "每月", QUARTERLY: "每季度", YEARLY: "每年" } as Record<string, string>)[clue.cycle] || clue.cycle}</span></div>
    <small>{clue.occurrence_count} {locale === "en" ? "patterned payments · transaction clues, not a new debit" : "次规律扣款 · 仅为流水线索，不是新扣款"}</small>
  </div>;
  return <>
    <section className="panel subscriptions-panel">
      <div className="small-panel-heading"><h2>{t("持续扣款")}</h2><span className="period-label">{subscriptions.filter(item => item.status === "ACTIVE").length} {t("项有效")}</span></div>
      {subscriptions.length ? subscriptions.map((item, index) => <div className="subscription-resource" key={item.subscription_id}>
        <div className="subscription-item"><span className={`merchant-icon merchant-${index % 3}`}><Icon name={index % 2 ? "subscription" : "bill"} size={17} /></span>
          <div><strong>{item.merchant}</strong><small>{t("权益有效至")} · {subscriptionDate(item.entitlement_end_at)
            ? dateText(item.entitlement_end_at) : locale === "en" ? "Expiry date not provided" : "到期时间暂未提供"}
            {item.auto_renew === false ? ` · ${t("自动续费已关闭")}` : item.status !== "ACTIVE" ? ` · ${t(["CANCELLED","CANCELED"].includes(item.status)?"已取消":stateLabels[item.status]||item.status)}` : ""}</small>
            <small>{subscriptionDueLabel(item.next_due_at,item.calendar_time ? Date.parse(item.calendar_time) : Number.NaN,locale,
              {dueStatus:item.due_status,status:item.auto_renew===false?"CANCELLED":item.status})} · {subscriptionDate(item.next_due_at)
                ? dateText(item.next_due_at) : locale==="en"?"Payment date not provided":"扣款时间暂未提供"}</small></div>
          <span>{money(item.amount_minor)}</span></div>
        {(item.data_through || partition.matches.get(item.subscription_id)?.length) && <CustomerResultDisclosure scopeKey={`${token}:${revision}:${JSON.stringify(item)}`} context={item.merchant}>
          {item.data_through && <small className="subscription-data-through">{t("数据截止")}: {dateText(item.data_through, true)}{locale === "en" ? " · subscription record time" : " · 订阅资料时点"}</small>}
          {partition.matches.get(item.subscription_id)?.length ? <div className="subscription-recurring-details"><strong>{t("匹配的周期扣费线索")}</strong>{partition.matches.get(item.subscription_id)!.map(clueRow)}</div> : null}
        </CustomerResultDisclosure>}
      </div>) : loading ? <Skeleton lines={3} /> : <div className="resource-empty"><p>{t("当前没有已读取的订阅")}</p></div>}
      <button type="button" className="full-width-link" disabled={!token} onClick={onManage}>{t("管理订阅与代扣")}<Icon name="arrow" size={15} /></button>
    </section>
    {error && <p className="error-inline">{error}</p>}
    {partition.other.length > 0 && <section className="panel recurring-panel"><div className="small-panel-heading"><h2>{t("其他周期扣费线索")}</h2><span className="period-label">{partition.other.length} {locale === "en" ? "clues" : "项线索"}</span></div><CustomerResultDisclosure scopeKey={`${token}:${revision}:${JSON.stringify(partition.other)}`} label={locale === "en" ? "View payment clues" : "查看扣费线索"}><p className="recurring-description">{t("先从流水发现规律，再核对是否存在可取消的银行代扣协议。")}</p>{partition.other.map(clueRow)}</CustomerResultDisclosure></section>}
  </>;
}
