/** Read-only projections. Buttons request the ordinary confirmation flow. */
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { lifeReservationAdjustmentReady } from "./LifeDomainConfirmation";
import { lifePlanNotices, lifePlanState, lifePlanTime } from "./lifePlanPresentation";
import "./lifeService.css";
import { lifeExecutionReview } from "./lifeExecutionReview";
type PlanItem = { merchant_name?: string; product_name?: string; quantity?: number; unit_price_minor?: number; amount_minor?: number };
type Order = { order_id: string; status: string; amount_minor?: number; status_source?: string };
type Occurrence = { occurrence_id: string; date: string; status: string; reserve_at?: string; order_at?: string;
  orders?: Order[]; nodes?: { job_id: string; kind: string; status: string; error?: string | null }[] };
export type LifePlanProjection = { plan_id: string; name: string; version: number; status: string;
  pause_reason?: string | null; budget_minor?: number; reserved_minor?: number; spent_minor?: number;
  refunded_minor?: number; net_cash_debit_minor?: number;
  execution_review?: unknown;
  scope?: { birthday?: string; currency?: string; items?: PlanItem[]; delivery_start?: string; delivery_end?: string;
    reserve_at?: string; reserve_policy?: string; order_at?: string; order_window_end?: string; expires_at?: string; timezone?: string;
    instrument?: { instrument_type?: string; card_number?: string; display_last4?: string; currency?: string };
    recipient?: { name?: string; phone?: string; address?: string }; recurring?: boolean };
  occurrences?: Occurrence[] };
export type LifePlanResult = { plans?: LifePlanProjection[]; replaced_plan?: {name?:string;released_reserved_minor?:number}; reservation_adjustment?:unknown };

const money = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value)
  ? `¥${(value / 100).toFixed(2)}` : "暂未提供";

export function LifePlanCards({ result, onAuthorize, onCancel }: { result: LifePlanResult;
  onAuthorize?: (planId: string) => void; onCancel?: (planId: string) => void }) {
  const {locale}=useCustomerLanguage();const en=locale==="en";
  if(result.reservation_adjustment!==undefined&&!lifeReservationAdjustmentReady(result.reservation_adjustment))
    return <p role="alert">{en?"The reservation adjustment has not been verified. Query the original plan.":"预留调整尚未核验，请查询原计划。"}</p>;
  if (!result.plans?.length) return <p>{en?"No registered life plans yet.":"目前没有已登记的生活计划。"}</p>;
  return <section aria-label={en?"Birthday plans":"生日生活计划"} className="life-plan-cards">
    {lifeReservationAdjustmentReady(result.reservation_adjustment)?<p>{en?"Reservation ":"原预留"}{money(result.reservation_adjustment.previous_reserved_minor)}{en?" adjusted to ":"已调整为"}{money(result.reservation_adjustment.new_reserved_minor)}；{Number(result.reservation_adjustment.additional_reserved_minor)>0?(en?"additional reservation ":"追加预留"):(en?"difference released ":"释放差额")}{money(Number(result.reservation_adjustment.additional_reserved_minor)||Number(result.reservation_adjustment.released_difference_minor))}{en?". Original schedule preserved; this adjustment makes no payment.":"。原排期保留，本次调整未扣款。"}</p>
      :result.replaced_plan&&<p>{en?"The new authorization replaces ":"本次新授权已替换“"}{result.replaced_plan.name || (en?"the original arrangement":"原安排")}{en?"; unused reservation released: ":"”；原未用预留已释放"}{money(result.replaced_plan.released_reserved_minor)}。</p>}
    {result.plans.map(plan => {
      const scope=plan.scope,unknown=en?"Not provided":"暂未提供",time=(value?:string)=>lifePlanTime(value,scope?.timezone,en);
      const instrument=scope?.instrument;
      const card=instrument?.card_number && /^(?:zhenhang|xiaozhao)[0-9a-f]{8}$/i.test(instrument.card_number)
        && (!instrument.display_last4 || instrument.card_number.slice(-4).toLowerCase()===instrument.display_last4.toLowerCase())
        ? instrument.card_number : instrument?.display_last4 && /^[0-9a-f]{4}$/i.test(instrument.display_last4)
          ? `${en?"ending ":"尾号 "}${instrument.display_last4}` : unknown;
      const review=lifeExecutionReview(plan,en);
      const notices=lifePlanNotices(plan,en).filter(note=>!review||
        ![en?"This arrangement is paused.":"安排已暂停",en?"Some steps are incomplete.":"部分步骤未完成"].some(prefix=>note.startsWith(prefix)));
      return <article key={plan.plan_id} className="life-plan-card">
      <header><h3>{plan.name}</h3><p>{lifePlanState(plan.status,en)}</p></header>
      <dl className="life-plan-amounts"><div><dt>{en?"Budget":"本次预算"}</dt><dd>{money(plan.budget_minor)}</dd></div>
        <div><dt>{en?"Reserved":"尚在预留"}</dt><dd>{money(plan.reserved_minor)}</dd></div><div><dt>{en?"Paid":"已付款"}</dt><dd>{money(plan.spent_minor)}</dd></div>
        {typeof plan.refunded_minor === "number" && <div><dt>{en?"Verified refunds":"已原路退款"}</dt><dd>{money(plan.refunded_minor)}</dd></div>}
        {typeof plan.net_cash_debit_minor === "number" && <div><dt>{en?"Net paid":"实付净额"}</dt><dd>{money(plan.net_cash_debit_minor)}</dd></div>}
      </dl>
      {review&&<section aria-label={en?"Execution review":"执行核对"} className="life-plan-execution-review">
        <p><strong>{review.reason}</strong> {review.effect}</p><p>{review.next}</p>
      </section>}
      <ul className="life-plan-items">{scope?.items?.map((item, index) => <li key={index}>
        <strong>{item.product_name || (en?"Selected product":"已选商品")}{typeof item.quantity === "number" ? ` × ${item.quantity}` : ""}</strong>
        <span>{item.merchant_name || (en?"Selected merchant":"已选商户")}{typeof item.unit_price_minor === "number" ? ` · ${en?"Unit price":"单价"} ${money(item.unit_price_minor)}` : ""} · {en?"Subtotal":"小计"} {money(item.amount_minor)}</span>
      </li>)}</ul>
      {notices.map(note=><p className="life-plan-status-note" role="status" key={note}>{note}</p>)}
      <details><summary>{en?"Schedule and recipient details":"安排与收件信息"}</summary>
        <dl className="life-plan-arrangement">
          <div><dt>{en?"Birthday":"生日"}</dt><dd>{scope?.birthday || unknown}</dd></div>
          <div><dt>{en?"Payment card":"付款卡"}</dt><dd>{card}{instrument?.currency?` · ${instrument.currency}`:""}</dd></div>
          <div><dt>{en?"Reservation":"预留安排"}</dt><dd>{scope?.reserve_policy==="ON_CONFIRMATION"?(en?"Immediately after confirmation":"确认后立即预留"):time(scope?.reserve_at)}</dd></div>
          <div><dt>{en?"Order window":"下单时间窗"}</dt><dd>{time(scope?.order_at)}{scope?.order_window_end?` — ${time(scope.order_window_end)}`:""}</dd></div>
          <div><dt>{en?"Delivery / service window":"配送／服务时间窗"}</dt><dd>{time(scope?.delivery_start)} — {time(scope?.delivery_end)}</dd></div>
          <div><dt>{en?"Authorization expires":"授权截止"}</dt><dd>{time(scope?.expires_at)}</dd></div>
          {scope?.timezone&&<div><dt>{en?"Time zone":"时区"}</dt><dd>{scope.timezone}</dd></div>}
          <div><dt>{en?"Recipient":"收件人"}</dt><dd>{scope?.recipient?.name || unknown}</dd></div>
          <div><dt>{en?"Phone":"联系电话"}</dt><dd>{scope?.recipient?.phone || unknown}</dd></div>
          <div><dt>{en?"Address":"地址"}</dt><dd>{scope?.recipient?.address || unknown}</dd></div>
        </dl>
        {plan.occurrences?.map(occ => <section key={occ.occurrence_id} className="life-plan-orders" aria-label={en?"Execution record":"本次执行记录"}>
          <p>{lifePlanState(occ.status,en)}
            {occ.reserve_at && occ.reserve_at!==scope?.reserve_at ? ` · ${en?"Reserved on":"预留时间"} ${time(occ.reserve_at)}` : ""}
            {occ.order_at && occ.order_at!==scope?.order_at ? ` · ${en?"Order on":"下单时间"} ${time(occ.order_at)}` : ""}</p>
          <ul>{occ.orders?.map(order => <li key={order.order_id}>{lifePlanState(order.status,en)} · {money(order.amount_minor)}</li>)}</ul>
        </section>)}
      </details>
      {(plan.status === "DRAFT" || plan.status === "PAUSED_RECONFIRM") && onAuthorize
        && <button type="button" onClick={() => onAuthorize(plan.plan_id)}>{en?"Review this authorization":"核对本次授权"}</button>}
      {!["SUCCEEDED", "CANCELLED", "COMPENSATED"].includes(plan.status) && onCancel
        && <button type="button" onClick={() => onCancel(plan.plan_id)}>{en?"Cancel unexecuted plan":"取消未执行计划"}</button>}
    </article>;})}
  </section>;
}

export function FamilyOccasionCards({ result }: { result: { occasions?: { occasion_id: string; month: number;
  family_member_name?: string; day: number; calendar: string; timezone: string; confirmed_solar_date?: string | null; adult_subject_confirmed?: boolean }[] } }) {
  const {locale}=useCustomerLanguage();const en=locale === "en";
  if (!Array.isArray(result.occasions)) return <p>{en ? "Birthday details have not been returned yet. Please query again." : "生日资料尚未完整返回，请重新查询。"}</p>;
  if (!result.occasions.length) return <p>{en ? "No confirmed family birthdays yet. You can provide birthday details in the main chat." : "目前没有已确认的家庭生日。你可以在主输入框提供生日资料。"}</p>;
  return <section aria-label={en ? "Family birthdays" : "家庭生日资料"}>{result.occasions.map(row => <article key={row.occasion_id}>
    {row.family_member_name&&<h3>{row.family_member_name}</h3>}
    <p>{row.calendar === "GREGORIAN" ? en ? "Gregorian" : "阳历" : row.calendar === "LUNAR" ? en ? "Lunar calendar" : "农历" : en ? "Calendar needs checking" : "历法待核验"} {en ? `${row.month}/${row.day}` : `${row.month}月${row.day}日`} · {row.timezone}</p>
    {row.calendar === "LUNAR" && <p>{row.confirmed_solar_date ? en ? `Confirmed Gregorian date this year: ${row.confirmed_solar_date}` : `本年已确认阳历日期：${row.confirmed_solar_date}` : en ? "Confirm this year's Gregorian date before making arrangements." : "需本人确认本年阳历日期后才能安排。"}</p>}
  </article>)}</section>;
}

/** The immutable save result, deliberately not a claim about today's data. */
export function FamilyOccasionSaveReceipt({result}:{result:unknown}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==="object"&&!Array.isArray(value);
  const rows=object(result)&&Array.isArray(result.occasions)?result.occasions:[];
  const row=rows.length===1&&object(rows[0])?rows[0]:null;
  const month=row?.month,day=row?.day;
  const ready=object(result)&&result.schema_version==="family-occasion-v1"&&result.occasion_state_changed===true&&result.bank_state_changed===false
    &&row&&typeof row.family_member_name==="string"&&row.family_member_name.trim().length>0
    &&typeof month==="number"&&Number.isSafeInteger(month)&&month>=1&&month<=12
    &&typeof day==="number"&&Number.isSafeInteger(day)&&day>=1&&day<=31
    &&["GREGORIAN","LUNAR"].includes(String(row.calendar))&&typeof row.timezone==="string"&&row.timezone.length>0;
  if(!ready||!row)return <p>{en?"The birthday-save result still needs checking.":"本次生日保存结果还需要核对。"}</p>;
  return <section aria-label={en?"This birthday save result":"本次生日资料保存结果"}>
    <h3>{en?"Birthday saved":"生日已保存"}</h3>
    <p>{String(row.family_member_name)} · {row.calendar==="LUNAR"?en?"Lunar calendar":"农历":en?"Gregorian":"公历"} · {en?`${month}/${day}`:`${month}月${day}日`}</p>
    {row.calendar==="LUNAR"&&<p>{typeof row.confirmed_solar_date==="string"?en?`Corresponding Gregorian date: ${row.confirmed_solar_date}`:`对应公历：${row.confirmed_solar_date}`:en?"A corresponding Gregorian date was not saved.":"尚未填写对应公历日期。"}</p>}
  </section>;
}
