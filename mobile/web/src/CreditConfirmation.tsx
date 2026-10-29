import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { money } from "./components";

type Data=Record<string,unknown>;
const object=(value:unknown):Data=>value!==null&&typeof value==="object"&&!Array.isArray(value)?value as Data:{};
export function CreditConfirmation({action,preview}:{action:string;preview:unknown}) {
  const {locale}=useCustomerLanguage();const en=locale==="en";const data=object(preview);
  const amount=(value:unknown,currency:unknown)=>typeof value==="number"&&Number.isSafeInteger(value)&&["CNY","USD"].includes(String(currency))
    ?money(value,{currency:String(currency)}):en?"Not verified":"待核验";
  const schedules=Array.isArray(data.schedule)?data.schedule.map(object):[];
  return <section className="credit-confirmation" aria-label={en?"Credit service review":"信用业务核对"}>
    {typeof data.card_number==="string"&&<p>{en?"Credit card: ":"信用卡："}{data.card_number}</p>}
    {typeof data.amount_minor==="number"&&<p><strong>{en?"Amount: ":"本次金额："}{amount(data.amount_minor,data.currency)}</strong></p>}
    {typeof data.source_card_last4==="string"&&<p>{en?"Pay from debit card ending ":"从借记卡尾号"}{data.source_card_last4}{en?"":"付款"}</p>}
    {typeof data.cash_debit_minor==="number"&&<p>{en?"CNY cash debit at the locked quote: ":"按本次锁定报价扣减人民币现金："}{amount(data.cash_debit_minor,data.source_currency)}</p>}
    {typeof data.rate==="string"&&<p>{en?"Locked USD/CNY reference rate: ":"本次USD/CNY锁定参考汇率："}{data.rate}</p>}
    {typeof data.limit_minor==="number"&&<p>{en?"New per-transaction limit: ":"拟设单笔消费限额："}{amount(data.limit_minor,"CNY")}</p>}
    {typeof data.requested_limit_minor==="number"&&<p>{en?"Requested shared credit limit: ":"申请共享授信额度："}{amount(data.requested_limit_minor,"CNY")}</p>}
    {typeof data.fee_minor==="number"&&<p>{en?"Total registered installment fee: ":"登记分期总费用："}{amount(data.fee_minor,data.currency)}</p>}
    {typeof data.total_fee_bps==="number"&&<p>{en?"Total fee rate (not annualized): ":"总费率（非年化）："}{data.total_fee_bps/100}%</p>}
    {schedules.length>0&&<table><thead><tr><th>{en?"Period":"期次"}</th><th>{en?"Principal":"本金"}</th><th>{en?"Fee":"费用"}</th><th>{en?"Due date":"应还日期"}</th></tr></thead>
      <tbody>{schedules.map((row,index)=><tr key={index}><td>{typeof row.period==="number"?row.period:"—"}</td><td>{amount(row.principal_minor,data.currency)}</td><td>{amount(row.fee_minor,data.currency)}</td><td>{typeof row.due_at==="string"?row.due_at.slice(0,10):"—"}</td></tr>)}</tbody></table>}
    {data.fee_refund_policy==="RETAIN_DISCLOSED_ORIGINATION_FEE"&&<p>{en?"A purchase refund does not automatically waive the disclosed origination fee.":"消费退款不自动免除本次已登记的分期发起费用。"}</p>}
    {["CREDIT_APPLY","CREDIT_LIMIT_REQUEST"].includes(action)&&<p>{en?"Confirmation submits an application for review. Approval and activation are separate.":"确认只提交申请，等待审核；不代表批准授信。发卡后仍须本人激活。"}</p>}
    {action==="CREDIT_REPAY"&&data.repayment_mode==="MINIMUM"&&<p>{en?"Minimum payment does not clear all debt; the remainder still uses credit.":"最低还款不等于全额还清，剩余欠款仍占授信。"}</p>}
    {action==="CREDIT_SETTLE_INSTALLMENT"&&<p>{en?"Settle the remaining principal and original registered fees; no additional early-settlement penalty.":"偿还该分期剩余本金与原登记费用，不新增提前结清罚费。"}</p>}
  </section>;
}
