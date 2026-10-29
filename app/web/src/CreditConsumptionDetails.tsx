import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { money } from "./components";

type Row = { payment_id: string; merchant: string; currency: string; amount_minor: number;
  occurred_at: string; billing_status: "BILLED" | "UNBILLED"; statement_cycle: string | null;
  due_at: string | null; remaining_principal_minor: number; refunded_minor: number;
  net_purchase_minor: number; has_installment: boolean };
type Details = { schema_version: string; currency: string; total_count: number; returned_count: number;
  billed_count: number; unbilled_count: number; has_more: boolean; items: Row[] };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string,unknown> : null;
const integer = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const date = (value: unknown) => typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
export function creditConsumptionDetails(value: unknown, currency: string): Details | null {
  const data = record(value);
  if (!data || data.schema_version !== "credit-statement-consumption-v1" || data.scope !== "SELECTED_CARD_PURCHASES"
      || !["CNY","USD"].includes(currency) || data.currency !== currency || !Array.isArray(data.items)
      || ![data.total_count,data.returned_count,data.billed_count,data.unbilled_count].every(integer)
      || data.returned_count !== data.items.length || (data.total_count as number) < data.items.length
      || data.has_more !== ((data.total_count as number) > data.items.length)) return null;
  const ids = new Set<string>();
  for (const raw of data.items) {
    const row = record(raw);
    if (!row || typeof row.payment_id !== "string" || !row.payment_id || ids.has(row.payment_id)
        || typeof row.merchant !== "string" || !row.merchant.trim() || row.currency !== currency
        || ![row.amount_minor,row.remaining_principal_minor,row.refunded_minor,row.net_purchase_minor].every(integer)
        || !(Number(row.amount_minor)>0) || Number(row.remaining_principal_minor)>Number(row.amount_minor)
        || Number(row.refunded_minor)>Number(row.amount_minor)
        || row.net_purchase_minor !== Number(row.amount_minor)-Number(row.refunded_minor)
        || !date(row.occurred_at) || typeof row.has_installment !== "boolean"
        || !["BILLED","UNBILLED"].includes(String(row.billing_status))
        || (row.billing_status === "BILLED" && (typeof row.statement_cycle !== "string" || !row.statement_cycle || !date(row.due_at)))
        || (row.billing_status === "UNBILLED" && (row.statement_cycle !== null || row.due_at !== null))) return null;
    ids.add(row.payment_id);
  }
  if (data.billed_count !== data.items.filter(row=>row.billing_status === "BILLED").length
      || data.unbilled_count !== data.items.filter(row=>row.billing_status === "UNBILLED").length) return null;
  return data as unknown as Details;
}

export function CreditConsumptionDetails({value,currency}:{value:unknown;currency:string}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  if (value === undefined) return null; // Historical result without the new projection is not a zero-result query.
  const details=creditConsumptionDetails(value,currency);
  if (!details) return <p className="credit-consumption-unavailable" role="status">{en ? "Purchase details could not be verified. Please query again." : "消费明细暂未核验，请重新查询。"}</p>;
  const when=(value:string,withTime=false)=>new Intl.DateTimeFormat(en?"en-GB":"zh-CN",{
    timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",
    ...(withTime?{hour:"2-digit",minute:"2-digit"}:{}),
  }).format(new Date(value));
  const amount=(value:number)=>money(value,{currency});
  return <details className="credit-card-details credit-consumption-details">
    <summary>{en ? `Purchase details (${details.total_count})` : `本卡消费明细（${details.total_count}笔）`}
      <span className="credit-consumption-counts">{en ? `Billed ${details.billed_count} · Unbilled ${details.unbilled_count}` : `已出账 ${details.billed_count} · 未出账 ${details.unbilled_count}`}</span></summary>
    {!details.items.length && <p>{en ? "No credit purchases recorded for this card." : "本卡暂无信用消费记录。"}</p>}
    {details.has_more && <p>{en ? `Showing the latest ${details.returned_count} purchases.` : `当前显示最近${details.returned_count}笔消费。`}</p>}
    <ol className="credit-consumption-list">{details.items.map(row=><li key={row.payment_id}>
      <div className="credit-consumption-heading"><strong>{row.merchant}</strong><b>{amount(row.amount_minor)}</b></div>
      <div className="credit-consumption-meta"><time dateTime={row.occurred_at}>{when(row.occurred_at,true)}</time><span>{currency}</span>
        <span>{row.billing_status === "BILLED" ? en ? `Billed · ${row.statement_cycle}` : `已出账 · ${row.statement_cycle}` : en ? "Unbilled" : "未出账"}</span>
        {row.due_at && <span>{en ? "Due " : "应还 "}{when(row.due_at)}</span>}
        {row.has_installment && <span>{en ? "Linked installment" : "已关联分期"}</span>}</div>
      {(row.remaining_principal_minor !== row.amount_minor || row.refunded_minor>0) && <div className="credit-consumption-adjustments">
        <span>{en ? "Remaining principal " : "未还本金 "}{amount(row.remaining_principal_minor)}</span>
        {row.refunded_minor>0 && <span>{en ? "Refunded " : "已退款 "}{amount(row.refunded_minor)}</span>}</div>}
    </li>)}</ol>
  </details>;
}
