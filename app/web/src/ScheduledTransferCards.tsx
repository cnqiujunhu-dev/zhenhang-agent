import {useCustomerLanguage} from './CustomerLanguageProvider';
import {CustomerResultDisclosure} from './CustomerResultDisclosure';
import {formatMoneyMinor} from './typedMoney';
import type {TraceNode} from './types';
import './personalServiceCards.css';
type Row = Record<string,unknown>;
const object = (v:unknown):v is Row => !!v && typeof v === 'object' && !Array.isArray(v);
const status = (v:unknown,en:boolean) => (({WAITING:en?'Scheduled':'待执行',ACTIVE:en?'Scheduled':'预约有效',
  COMPLETED:en?'Completed':'已完成',POSTED:en?'Transferred':'已转出',FAILED:en?'Failed':'未成功',CANCELLED:en?'Cancelled':'已取消',
  PENDING:en?'Scheduled':'待执行',EXECUTING:en?'Processing':'处理中'} as Record<string,string>)[String(v)] || (en?'Needs review':'状态待核对'));
const date = (v:unknown,en:boolean) => typeof v === 'string' && Number.isFinite(Date.parse(v))
  ? new Intl.DateTimeFormat(en?'en-GB':'zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(v)) : '—';
const frequency=(v:unknown,en:boolean)=>({DAILY:en?'Daily':'每天',WEEKLY:en?'Weekly':'每周',MONTHLY:en?'Monthly':'每月',EXPLICIT:en?'Selected dates':'指定日期'} as Record<string,string>)[String(v)]|| (en?'Once':'单次');
const errorText=(v:unknown,en:boolean)=>['INSUFFICIENT_FUNDS','TRANSFER_SOURCE_AVAILABLE_FUNDS','PAYMENT_INSTRUMENT_INSUFFICIENT_FUNDS'].includes(String(v))
  ? (en?'Insufficient funds in the selected card; remaining transfers stopped.':'指定付款卡余额不足，后续安排已停止。')
  :v?(en?'This transfer did not pass execution checks.':'本期转账未通过到期核验。'):'';
export function ScheduledTransferCards({node}:{node:TraceNode}) {
  const en=useCustomerLanguage().locale==='en',data=node.result;
  if(node.action!=='TRANSFER_SCHEDULE_VIEW'||node.status!=='SUCCEEDED'||node.error
    ||data?.schema_version!=='scheduled-transfers-read-v1'||data.bank_state_changed!==false||!Array.isArray(data.schedules)
    ||data.schedules.some(row=>!object(row)||typeof row.payee_name!=='string'||!Number.isSafeInteger(row.amount_minor)
      ||Number(row.amount_minor)<=0||row.currency!=='CNY'))return <p>{en?'Unable to verify scheduled transfers.':'暂时无法核对转账安排。'}</p>;
  return <section className="personal-service-results" aria-label={en?'Scheduled transfers':'定时转账安排'}>
    <h3>{en?'Scheduled transfers':'定时转账安排'}</h3>
    {!data.schedules.length&&<p>{en?'You have no scheduled transfers.':'目前没有已登记的定时转账安排。'}</p>}
    <div className="personal-result-grid">{(data.schedules as Row[]).map((row,index)=><article className="personal-result-card" key={String(row.schedule_id)}>
      <header><h4>{String(row.payee_name)}</h4><span className="personal-kind">{status(row.status,en)}</span></header>
      <dl><div><dt>{en?'Amount':'金额'}</dt><dd className="personal-primary-value">{formatMoneyMinor(Number(row.amount_minor),{currency:'CNY'})}</dd></div>
        <div><dt>{row.next_due_at?(en?'Next transfer':'下次执行'):(en?'Scheduled time':'约定时间')}</dt><dd>{date(row.next_due_at||row.due_at,en)}</dd></div>
        {typeof row.memo==='string'&&row.memo&&<div><dt>{en?'Memo':'备注'}</dt><dd>{row.memo}</dd></div>}</dl>
      {object(row.series)&&<p>{frequency(row.series.frequency,en)} · {en?'Remaining ':'剩余 '}{String(row.series.remaining_occurrences??'—')}{en?' transfers':' 次'}</p>}
      {row.settlement_status==='UNVERIFIED'&&<p>{en?'Transferred; arrival is awaiting verification.':'已转出，到账结果待核实。'}</p>}
      <CustomerResultDisclosure scopeKey={`schedules:${node.node_id}:${index}:${JSON.stringify(row)}`} label={en?'Details':'查看详情'}>
        <p>{en?'Shanghai time':'北京时间'}</p>
        {typeof row.source_last4==='string'&&<p>{en?'Payment card ending ':'付款卡尾号 '}{row.source_last4}</p>}
        {typeof row.recipient_last4==='string'&&<p>{en?'Receiving card ending ':'收款卡尾号 '}{row.recipient_last4}</p>}
        {object(row.timing_policy)&&row.timing_policy.time_origin==='DEFAULT_09_00'&&<p>{en?'Starts processing at 09:00 Shanghai time.':'默认北京时间09:00起处理。'}</p>}
        {object(row.series)&&typeof row.series.end_at==='string'&&<p>{en?'Last authorized date: ':'最后一期：'}{date(row.series.end_at,en)}</p>}
        {object(row.series)&&row.series.frequency==='MONTHLY'&&<p>{en?'Short months use the last day, then return to the original day.':'不足原日期的月份按月末执行，之后恢复原日期。'}</p>}
        {typeof row.error==='string'&&row.error&&<p>{errorText(row.error,en)}</p>}
        {object(row.series)&&Array.isArray(row.series.occurrences)&&<ul>{row.series.occurrences.filter(object).map((part,i)=><li key={i}>{date(part.due_at,en)} · {status(part.status,en)}{part.settlement_status==='UNVERIFIED'?(en?' · Arrival unverified':' · 到账待核实'):''}{part.error?<p>{errorText(part.error,en)}</p>:null}</li>)}</ul>}
      </CustomerResultDisclosure>
    </article>)}</div>
  </section>;
}
