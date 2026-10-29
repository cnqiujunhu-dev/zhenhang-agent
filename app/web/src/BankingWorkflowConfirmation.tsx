/** Finite reviewed scope only; never derive authorization from narration. */
import {useCustomerLanguage} from "./CustomerLanguageProvider";
import {Icon} from "./components";
import "./scheduledTransferReview.css";

type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const keys = (value: Data, required: string[], optional: string[] = []) => required.every(key => Object.hasOwn(value,key))
  && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value);
const positive = (value: unknown): value is number => integer(value) && value > 0;
const text = (value: unknown, maximum = 100): value is string => typeof value === "string" && value.trim().length > 0
  && value.length <= maximum && !/[\u0000-\u001f\u007f]/.test(value)
  && !/(?:zhenhang|xiaozhao)[a-f\d]{8}|\b[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}\b/i.test(value);
const tail = (value: unknown): value is string => typeof value === "string" && /^[a-f\d]{4}$/i.test(value);
// Preserve microseconds when matching the actual confirmation deadline; Date
// alone would silently consider two different sub-millisecond instants equal.
function instant(value: unknown): bigint | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return null;
  const [,y,m,d,h,minute,s,fraction,offset]=match;
  const year=Number(y),month=Number(m),day=Number(d),hour=Number(h),minutes=Number(minute),seconds=Number(s);
  const days=[31,year%4===0&&(year%100!==0||year%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
  if(year<1||month<1||month>12||day<1||day>days[month-1]||hour>23||minutes>59||seconds>59)return null;
  const milliseconds=Date.parse(`${y}-${m}-${d}T${h}:${minute}:${s}${offset}`);
  return Number.isSafeInteger(milliseconds) ? BigInt(milliseconds)*1000n+BigInt((fraction||"").padEnd(6,"0")) : null;
}
const instrument = (value: unknown, named = false): value is Data => object(value)
  && keys(value,named?["username","display_name","display_last4","instrument_type","currency"]:["display_last4","instrument_type","currency"])
  && value.instrument_type === "DEBIT" && value.currency === "CNY" && tail(value.display_last4)
  && (!named || text(value.username,32) && text(value.display_name));
const common = ["schema_version","action","scope_verified","currency","confirmation_expires_at"];
export const bankingWorkflowConfirmationActions = new Set(["TRANSFER_SCHEDULE","AA_REQUEST"]);
export function bankingWorkflowConfirmationTitle(action: string, locale: string) {
  return action === "TRANSFER_SCHEDULE" ? locale === "en" ? "Review scheduled transfers" : "核对预约转账"
    : action === "AA_REQUEST" ? locale === "en" ? "Review AA invitations" : "核对AA收款邀请"
      : locale === "en" ? "Review this action" : "核对本次操作";
}
export function bankingWorkflowConfirmationReady(action: string, value: unknown, confirmationExpiresAt: unknown): value is Data {
  if(!bankingWorkflowConfirmationActions.has(action)||!object(value)||value.schema_version!=="customer-banking-confirmation-v1"
    ||value.action!==action||value.scope_verified!==true||value.currency!=="CNY")return false;
  const confirmation=instant(confirmationExpiresAt),deadline=instant(value.confirmation_expires_at);
  if(confirmation===null||deadline===null||confirmation!==deadline)return false;
  if(action==="TRANSFER_SCHEDULE"){
    if(!keys(value,[...common,"source","recipient","amount_minor","due_at","timezone","clock_basis","frequency","total_occurrences",
      "end_at","last_due_at","total_budget_minor","funds_reserved","future_execution_revalidation","failure_policy","month_end_policy","memo"],["occurrence_dates","replaces_future_occurrences","default_execution_time"])
      ||!instrument(value.source)||!instrument(value.recipient,true)||!positive(value.amount_minor)
      ||value.timezone!=="Asia/Shanghai"||!["CALENDAR_REAL","BANK_CONTROLLED"].includes(String(value.clock_basis))
      ||!["ONCE","DAILY","WEEKLY","MONTHLY","EXPLICIT"].includes(String(value.frequency))||!positive(value.total_occurrences)||value.total_occurrences>366
      ||!positive(value.total_budget_minor)||!Number.isSafeInteger(value.amount_minor*value.total_occurrences)
      ||value.total_budget_minor!==value.amount_minor*value.total_occurrences||value.funds_reserved!==false
      ||value.future_execution_revalidation!==true||value.failure_policy!=="STOP_REMAINING"
      ||typeof value.memo!=="string"||value.memo.length>160
      ||Object.hasOwn(value,'replaces_future_occurrences')&&value.replaces_future_occurrences!==true
      ||Object.hasOwn(value,'default_execution_time')&&typeof value.default_execution_time!=='boolean')return false;
    const first=instant(value.due_at),last=instant(value.last_due_at);
    if(first===null||last===null||last<first||value.total_occurrences===1&&last!==first||value.total_occurrences>1&&last<=first)return false;
    if(value.frequency==="EXPLICIT"){
      if(!Array.isArray(value.occurrence_dates)||value.occurrence_dates.length!==value.total_occurrences||value.total_occurrences<(value.replaces_future_occurrences===true?1:2)||value.total_occurrences>(value.replaces_future_occurrences===true?366:12))return false;
      const dates=value.occurrence_dates.map(instant);
      if(dates.some(item=>item===null)||dates[0]!==first||dates.at(-1)!==last||dates.some((item,index)=>index>0&&(item as bigint)<=(dates[index-1] as bigint)))return false;
      return instant(value.end_at)===last&&value.month_end_policy===null;
    }
    if(Object.hasOwn(value,"occurrence_dates"))return false;
    if(value.frequency==="ONCE")return value.total_occurrences===1&&value.end_at===null&&last===first&&value.month_end_policy===null;
    return instant(value.end_at)===last && value.month_end_policy===(value.frequency==="MONTHLY"?"CLAMP_ORIGINAL_DAY_NO_DRIFT":null);
  }
  if(!keys(value,[...common,"collector","total_minor","participant_count","participants","expires_at","remainder_rule","invitation_only","automatic_debit","timezone"],["memo"])
    || Object.hasOwn(value,"memo") && (typeof value.memo!=="string" || value.memo.length>160 || /[\u0000-\u001f]/.test(value.memo))
    ||!instrument(value.collector,true)||!positive(value.total_minor)||!integer(value.participant_count)||value.participant_count<2||value.participant_count>20
    ||!Array.isArray(value.participants)||value.participants.length!==value.participant_count||value.remainder_rule!=="LIST_ORDER_EXTRA_CENT"
    ||value.invitation_only!==true||value.automatic_debit!==false||instant(value.expires_at)===null
    ||value.timezone!=="Asia/Shanghai")return false;
  const base=Math.floor(value.total_minor/value.participant_count),remainder=value.total_minor%value.participant_count;
  if(base<1||!value.participants.every((row,index)=>object(row)&&keys(row,["username","display_name","amount_minor"])
    &&text(row.username,32)&&text(row.display_name)&&positive(row.amount_minor)&&row.amount_minor===base+(index<remainder?1:0)))return false;
  return new Set(value.participants.map(row=>(row as Data).username)).size===value.participant_count;
}
const money = (value: unknown) => positive(value) ? `${Math.floor(value/100)}.${String(value%100).padStart(2,"0")} CNY` : "—";
const person = (value: Data) => value.username===value.display_name ? String(value.display_name) : `${String(value.display_name)} (${String(value.username)})`;
function date(value: unknown, zone: string, en: boolean) {
  if(instant(value)===null)return "—";
  return new Intl.DateTimeFormat(en?"en-GB":"zh-CN",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value as string));
}
const frequency = (value: unknown, en: boolean) => ({ONCE:en?"Once":"仅一次",DAILY:en?"Daily":"每天",WEEKLY:en?"Weekly":"每周",MONTHLY:en?"Monthly":"每月",EXPLICIT:en?"Specified times":"指定时点"} as Record<string,string>)[String(value)]||"—";
const reviewMoney=(value:unknown,en:boolean)=>positive(value)
  ? new Intl.NumberFormat(en?'en-US':'zh-CN',{style:'currency',currency:'CNY',currencyDisplay:'narrowSymbol',minimumFractionDigits:2}).format(value/100):'—';
function reviewDate(value:unknown,en:boolean){
  const precise=new Date(value as string);
  return new Intl.DateTimeFormat(en?'en-GB':'zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',...(precise.getUTCSeconds()?{second:'2-digit' as const}:{}),hourCycle:'h23'}).format(precise);
}
export function bankingWorkflowConfirmationSpeech(action: string, scope: unknown, expiry: unknown, locale: string) {
  const en=locale==="en";
  if(!bankingWorkflowConfirmationReady(action,scope,expiry))return en?"The scope is incomplete. Do not confirm.":"确认范围尚未核对完整，请勿确认。";
  if(action==="TRANSFER_SCHEDULE"){
    const source=scope.source as Data,recipient=scope.recipient as Data;
    if(scope.frequency==="ONCE")return en
      ? `Review a scheduled transfer from your debit card ending ${source.display_last4} to ${person(recipient)}, debit card ending ${recipient.display_last4}. ${money(scope.amount_minor)}, due ${date(scope.due_at,String(scope.timezone),true)}, Shanghai time.${scope.memo?` Memo: ${scope.memo}.`:""} Executes when due; no funds are reserved now.${scope.clock_basis==="BANK_CONTROLLED"?" Timing uses the bank business clock, not the current calendar.":""}`
      : `请核对预约转账。付款人为本人，借记卡尾号${source.display_last4}；收款人${person(recipient)}，借记卡尾号${recipient.display_last4}。金额${money(scope.amount_minor)}，执行时间${date(scope.due_at,String(scope.timezone),false)}，北京时间。${scope.memo?`备注${scope.memo}。`:""}到期执行，本次不预留资金。${scope.clock_basis==="BANK_CONTROLLED"?"时间依据为银行业务时钟，不是当前日历时间。":""}`;
    return en?`Review scheduled transfers from your debit card ending ${source.display_last4} to ${person(recipient)}, debit card ending ${recipient.display_last4}. Each ${money(scope.amount_minor)}. First ${date(scope.due_at,String(scope.timezone),true)}, Asia/Shanghai. ${frequency(scope.frequency,true)}, ${scope.total_occurrences} occurrence(s), ending ${date(scope.last_due_at,String(scope.timezone),true)}, maximum total ${money(scope.total_budget_minor)}.${scope.memo?` Memo: ${scope.memo}.`:""} Funds are not reserved. Each due transfer is checked again.`
      :`请核对预约转账。付款人为本人，借记卡尾号${source.display_last4}；收款人${person(recipient)}，借记卡尾号${recipient.display_last4}。每次${money(scope.amount_minor)}，首次${date(scope.due_at,String(scope.timezone),false)}，上海时间。${frequency(scope.frequency,false)}，共${scope.total_occurrences}次，最后一期${date(scope.last_due_at,String(scope.timezone),false)}，累计上限${money(scope.total_budget_minor)}。${scope.memo?`备注${scope.memo}。`:""}本次不预留资金，到期逐次重新核对。`;
  }
  const collector=scope.collector as Data,parts=scope.participants as Data[];
  return en?`Review AA invitations totalling ${money(scope.total_minor)}, to ${person(collector)}, debit card ending ${collector.display_last4}. ${parts.map(row=>`${person(row)} ${money(row.amount_minor)}`).join("; ")}. Collection deadline ${date(scope.expires_at,"Asia/Shanghai",true)}, Asia/Shanghai. Sending invitations does not debit participants. Each participant must choose a card, verify and confirm.`
    :`请核对AA收款邀请，合计${money(scope.total_minor)}，收款人为${person(collector)}，借记卡尾号${collector.display_last4}。${parts.map(row=>`${person(row)}承担${money(row.amount_minor)}`).join("；")}。收款截止${date(scope.expires_at,"Asia/Shanghai",false)}，上海时间。发出邀请不代参与者扣款，各参与者须自行选卡、验证并确认。`;
}

export function BankingWorkflowConfirmation({action,scope,expiresAt}:{action:string;scope:unknown;expiresAt:unknown}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  if(!bankingWorkflowConfirmationReady(action,scope,expiresAt))return <p role="alert">{en?"The confirmation scope is incomplete or inconsistent. Prepare a new request; nothing has been executed.":"本次确认范围尚未核对完整或内容不一致，请重新准备；尚未执行。"}</p>;
  if(action==="TRANSFER_SCHEDULE"){
    const source=scope.source as Data,recipient=scope.recipient as Data,once=scope.frequency==="ONCE";
    return <section className="schedule-confirmation-review" aria-label={bankingWorkflowConfirmationTitle(action,locale)}>
      <div className="schedule-review-amount"><div><span>{once?en?'Transfer amount':'转账金额':en?'Amount per transfer':'每期转账金额'} · {en?'CNY':'人民币'}</span>
        <strong>{reviewMoney(scope.amount_minor,en)}</strong></div><span className="schedule-review-frequency">{frequency(scope.frequency,en)}</span></div>
      <div className="schedule-review-accounts" aria-label={en?'Payment and receiving accounts':'收付款账户'}>
        <div className="schedule-review-account"><span className="schedule-review-account-icon"><Icon name="card" size={20}/></span><div>
          <span className="schedule-review-label">{en?'From':'付款账户'}</span><strong>{en?'You':'本人'}</strong>
          <span className="schedule-review-card">{en?'Debit card · ':'借记卡 · 尾号 '}<b>{String(source.display_last4)}</b></span></div></div>
        <span className="schedule-review-arrow" aria-hidden="true"><Icon name="arrow" size={19}/></span>
        <div className="schedule-review-account schedule-review-recipient"><span className="schedule-review-account-icon"><Icon name="card" size={20}/></span><div>
          <span className="schedule-review-label">{en?'To':'收款账户'}</span><strong>{String(recipient.display_name)}</strong>
          {recipient.display_name!==recipient.username&&<span className="schedule-review-username">{String(recipient.username)}</span>}
          <span className="schedule-review-card">{en?'Debit card · ':'借记卡 · 尾号 '}<b>{String(recipient.display_last4)}</b></span></div></div>
      </div>
      <div className="schedule-review-facts"><dl>
        <div><dt>{once?en?'Execution date':'执行时间':en?'First transfer':'首次执行'}</dt><dd><time dateTime={String(scope.due_at)}>{reviewDate(scope.due_at,en)}</time></dd></div>
        {!once&&<div><dt>{en?'Last transfer':'最后一期'}</dt><dd><time dateTime={String(scope.last_due_at)}>{reviewDate(scope.last_due_at,en)}</time></dd></div>}
      </dl><div className="schedule-review-timezone">{en?'Shanghai time':'北京时间'}{scope.default_execution_time===true?en?' · starts processing at 09:00':' · 默认09:00起处理':''}</div></div>
      {!once&&<div className="schedule-review-total"><span>{en?`${scope.total_occurrences} transfers`:`共${scope.total_occurrences}期`}</span>
        <span>{en?'Total authorization limit':'累计授权上限'} <strong>{reviewMoney(scope.total_budget_minor,en)}</strong></span></div>}
      {scope.frequency==='EXPLICIT'&&<div className="schedule-review-explicit"><span>{en?'All authorized times':'全部授权执行时点'}</span>
        <ol>{(scope.occurrence_dates as string[]).map(value=><li key={value}><time dateTime={value}>{reviewDate(value,en)}</time></li>)}</ol></div>}
      {typeof scope.memo==='string'&&scope.memo&&<div className="schedule-review-memo"><span>{en?'Memo':'备注'}</span><p>{scope.memo}</p></div>}
      {scope.clock_basis!=='CALENDAR_REAL'&&<p className="schedule-review-warning">{en?'Bank business clock, not the current calendar':'银行业务时钟，不是当前日历时间'}</p>}
      {scope.replaces_future_occurrences===true&&<p className="schedule-review-warning">{en?'Confirmation replaces only future occurrences. Completed transfers remain unchanged.':'确认后替换原安排的未执行期次，已完成转账保持不变。'}</p>}
      <div className="schedule-review-footer"><p>{en?'Executes when due; no funds are reserved now.':'到期执行，本次不预留资金。'}</p>
        <details key={JSON.stringify(scope)}><summary>{en?'Execution details':'执行说明'}</summary><div>
          <p>{en?'Each due transfer rechecks the selected cards, identity, limits and available funds.':'到期时重新核对收付款卡、身份、限额和可用余额。'}</p>
          <p>{en?'Processing time is not a guaranteed arrival time.':'约定时间用于开始处理，不代表保证该时刻到账。'}</p>
          {scope.frequency==='MONTHLY'&&<p>{en?"Short months use the last day, then return to the original day.":'月份不足原日期时按当月最后一天执行，之后仍以原日期为准。'}</p>}
          {!once&&<p>{en?'If an occurrence fails, remaining transfers stop; completed transfers are not reversed.':'任一期失败后停止余下安排；已完成的转账不会因此撤回。'}</p>}
        </div></details></div>
    </section>;
  }
  const collector=scope.collector as Data,zone=scope.timezone==="Asia/Shanghai"?"Asia/Shanghai":"UTC";
  return <section className="banking-workflow-review" aria-label={bankingWorkflowConfirmationTitle(action,locale)}><dl>
    {typeof scope.memo==="string"&&<><dt>{en?"Memo":"备注"}</dt><dd>{scope.memo}</dd></>}
    <dt>{en?"Collection total":"收款总额"}</dt><dd>{money(scope.total_minor)}</dd>
    <dt>{en?"Collector":"收款人"}</dt><dd>{person(collector)}</dd>
    <dt>{en?"Receiving debit card":"收款借记卡尾号"}</dt><dd>{String(collector.display_last4)}</dd>
    <dt>{en?"Collection deadline":"收款截止时间"}</dt><dd>{date(scope.expires_at,zone,en)} · {zone==="Asia/Shanghai" ? en?"Shanghai time":"北京时间" : "UTC"}</dd>
  </dl><p className="banking-participants-heading">{en ? `Participants (${scope.participant_count})` : `参与人（${scope.participant_count}人）`}</p>
    <ul>{(scope.participants as Data[]).map((row,index)=><li key={index}>{person(row)} · {money(row.amount_minor)}</li>)}</ul>
    {Number(scope.total_minor)%Number(scope.participant_count)!==0&&<p>{en?"Any remainder cents go to participants in the displayed order.":"不能整除的余分，按上方名单顺序各多分配一分。"}</p>}
    <p>{en?"Confirmation sends invitations; each participant pays their own share.":"确认后发出邀请，由每人自行付款。"}</p>
  </section>;
}
