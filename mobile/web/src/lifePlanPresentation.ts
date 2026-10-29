type StateSource = {status:string; occurrences?:{status:string; orders?:{status:string}[]; nodes?:{status:string}[]}[]};
const states:Record<string,readonly [string,string]> = {
  DRAFT:["待本人授权","Awaiting your authorization"], ACTIVE:["已授权","Authorized"], PAUSED_RECONFIRM:["已暂停，待核对","Paused for review"],
  PARTIAL:["部分完成","Partially completed"], CANCELLING:["正在取消","Cancelling"], COMPENSATING:["退款处理中","Refund processing"],
  COMPENSATED:["原路退款已核验","Refund verified"], CANCELLED:["未执行部分已取消","Unexecuted arrangements cancelled"],
  SUCCEEDED:["计划步骤已完成","Plan steps completed"], PAID:["已付款，待履约","Paid, awaiting fulfilment"],
  FULFILLED:["商户履约已记录","Merchant fulfilment recorded"], REFUND_PENDING:["已申请退款，到账待核验","Refund requested; receipt unverified"],
  REFUNDED:["退款入账已核验","Refund receipt verified"], QUEUED:["等待约定时间","Waiting for the scheduled time"],
  FAILED:["未完成","Not completed"], WAITING_RESERVE:["等待预留","Waiting for reservation"],
  WAITING_ORDER:["等待下单","Waiting for ordering"], WAITING_DELIVERY:["等待履约","Awaiting fulfilment"],
};
export const lifePlanState = (state:string,en:boolean) => states[state]?.[en?1:0] || (en?"Status unverified":"状态待核验");
export function lifePlanTime(value:unknown,zone:unknown,en:boolean):string {
  if(typeof value!=="string"||!value.trim())return en?"Not provided":"暂未提供";
  if(typeof zone!=="string"||!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value)))return value;
  try{return new Intl.DateTimeFormat(en?"en-GB":"zh-CN",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value));}
  catch{return value;}
}
/** Finite actual states only; private pause reasons and future failures are not
 * narrated as events. Order completion never proves external delivery. */
export function lifePlanNotices(plan:StateSource,en:boolean):string[] {
  const occurrences=plan.occurrences||[],orders=occurrences.flatMap(row=>row.orders||[]);
  const statuses=[plan.status,...occurrences.map(row=>row.status),...orders.map(row=>row.status)];
  const notices:string[]=[];
  if(statuses.includes("PAUSED_RECONFIRM"))notices.push(en?"This arrangement is paused. Review the changes before confirming again.":"安排已暂停，请核对变更后重新确认。");
  if(statuses.includes("PARTIAL")||orders.some(row=>row.status==="FAILED"))
    notices.push(en?"Some steps are incomplete. Payments, refunds and remaining reservations are shown separately.":"部分步骤未完成；已付款、退款和剩余预留分别列示。");
  if(statuses.some(status=>["COMPENSATING","REFUND_PENDING"].includes(status)))notices.push(en?"The refund is processing; its amount will update once receipt is verified.":"退款正在处理中，到账核验后更新金额。");
  if(orders.some(row=>row.status==="FULFILLED"))notices.push(en?"Fulfilment is a merchant record; confirm actual delivery separately.":"履约为商户记录，实际配送情况仍需确认。");
  return notices;
}
