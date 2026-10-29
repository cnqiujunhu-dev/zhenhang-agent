import type {LifePlanProjection} from "./LifePlanCards";

const reasons:Record<string,readonly [string,string]>={
  PRICE_CHANGED:["指定商品价格或条款已变化。","A selected item's price or terms changed."],
  ITEM_UNAVAILABLE:["指定商品暂时缺货或不可用。","A selected item is unavailable."],
  AUTHORIZATION_CHANGED:["本次安排与原授权不一致。","This arrangement no longer matches its authorization."],
  PAYMENT_CONTROL_CHANGED:["付款卡或预留资金条件已变化。","Payment-card or reserved-fund conditions changed."],
  BUDGET_EXCEEDED:["本次下单将超出已授权预算。","This order would exceed the authorized budget."],
  FULFILMENT_UNVERIFIED:["约定时间内未取得完整履约记录。","Complete fulfilment was not recorded within the agreed window."],
  REVIEW_REQUIRED:["执行条件未通过核验。","Execution conditions could not be verified."],
};
const integer=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;

/** Strict server checkpoint projection. No raw error, identifier or model text. */
export function lifeExecutionReview(plan:LifePlanProjection,en:boolean):{reason:string;effect:string;next:string}|null{
  const value=plan.execution_review;
  if(!value||typeof value!=="object"||Array.isArray(value))return null;
  const r=value as Record<string,unknown>;
  const keys=["schema_version","plan_version","plan_status","reason","payment_count","paid_minor","refunded_minor","reserved_minor","source"];
  if(Object.keys(r).length!==keys.length||keys.some(k=>!(k in r))
    ||r.schema_version!=="life-execution-review-v1"||r.source!=="LOCAL_BANK_AND_VIRTUAL_MERCHANT"
    ||!integer(plan.version)||r.plan_version!==plan.version||r.plan_status!==plan.status
    ||!["PAUSED_RECONFIRM","PARTIAL","COMPENSATING"].includes(plan.status)
    ||typeof r.reason!=="string"||!Object.hasOwn(reasons,r.reason)
    ||![r.payment_count,r.paid_minor,r.refunded_minor,r.reserved_minor].every(integer)
    ||r.paid_minor!==plan.spent_minor||r.refunded_minor!==plan.refunded_minor||r.reserved_minor!==plan.reserved_minor
    ||Number(r.refunded_minor)>Number(r.paid_minor)
    ||(r.payment_count===0)!==(r.paid_minor===0))return null;
  const noPayment=r.payment_count===0;
  return {reason:reasons[r.reason][en?1:0],
    effect:noPayment?(en?"No payment has been recorded for this plan. Reserved funds are listed separately.":"本计划尚无付款记录；尚在预留的资金单独列示。")
      :(en?`${r.payment_count} payment(s) have been recorded. Payments and verified refunds remain listed separately.`:`本计划已有${r.payment_count}笔付款记录；已付款和已核验退款分别保留。`),
    next:noPayment?(en?"Review the arrangement and confirm a new authorization, or cancel to release unused reservations.":"请核对安排后重新授权，或取消未执行计划以释放剩余预留。")
      :(en?"Check existing orders and refund progress before making another arrangement.":"请先核对已有订单和退款进度，再决定后续安排。")};
}
