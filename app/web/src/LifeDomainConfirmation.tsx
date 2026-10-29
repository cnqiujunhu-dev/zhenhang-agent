/** Finite server-audited scope, not payment or authorization authority. */
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import "./lifeService.css";
type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const instant = (value: unknown): value is string => text(value) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const timezone = (value:unknown) => {if(!text(value))return false;try{new Intl.DateTimeFormat("en",{timeZone:value}).format(0);return true;}catch{return false;}};
export function lifeReservationAdjustmentReady(value:unknown):value is Data {
  const fields=["version","previous_reserved_minor","new_reserved_minor","additional_reserved_minor","released_difference_minor","currency","schedule_preserved","synchronous_at_confirmation","cash_debit_minor"];
  return object(value)&&Object.keys(value).length===fields.length&&fields.every(key=>key in value)
    &&value.version==="life-reservation-adjustment-v1"&&value.currency==="CNY"&&value.schedule_preserved===true
    &&value.synchronous_at_confirmation===true&&value.cash_debit_minor===0
    &&[value.previous_reserved_minor,value.new_reserved_minor,value.additional_reserved_minor,value.released_difference_minor].every(v=>integer(v)&&v<=100000000)
    &&Number(value.previous_reserved_minor)>0&&Number(value.new_reserved_minor)>0&&value.previous_reserved_minor!==value.new_reserved_minor
    &&value.additional_reserved_minor===Math.max(Number(value.new_reserved_minor)-Number(value.previous_reserved_minor),0)
    &&value.released_difference_minor===Math.max(Number(value.previous_reserved_minor)-Number(value.new_reserved_minor),0);
}
function adjustmentText(change:Data,en:boolean) {
  const effect=Number(change.additional_reserved_minor)>0?(en?"additional reservation":"追加预留"):(en?"difference released":"释放差额");
  const amount=Number(change.additional_reserved_minor)||Number(change.released_difference_minor);
  return en?`Reservation ${money(change.previous_reserved_minor)} → ${money(change.new_reserved_minor)}; ${effect} ${money(amount)}. Preserve the original ordering and delivery schedule. This adjustment makes no payment.`
    :`原预留${money(change.previous_reserved_minor)} → ${money(change.new_reserved_minor)}，${effect}${money(amount)}；保留原下单和配送排期。本次调整不付款。`;
}
export const lifeConfirmationActions = new Set(["MERCHANT_PURCHASE", "OCCASION_MEMBER_SET", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL"]);
export function lifeConfirmationTitle(action: string, locale: string) {
  const titles: Record<string, [string, string]> = {MERCHANT_PURCHASE:["核对商品付款", "Review product payment"],
    OCCASION_SET:["核对生日资料", "Review birthday details"], LIFE_PLAN_AUTHORIZE:["核对本次生日计划授权", "Review this birthday plan authorization"],
    OCCASION_MEMBER_SET:["核对家庭成员资料", "Review family directory details"],
    LIFE_PLAN_CANCEL:["核对撤销生活计划", "Review life plan cancellation"]};
  return titles[action]?.[locale === "en" ? 1 : 0] || (locale === "en" ? "Review this action" : "核对本次操作");
}
export function lifeConfirmationReady(action: string, value: unknown): value is Data {
  if (!lifeConfirmationActions.has(action) || !object(value) || value.schema_version !== "customer-life-confirmation-v1"
      || value.scope_verified !== true || value.action !== action) return false;
  if (value.replacement !== undefined && (!object(value.replacement) || action !== "LIFE_PLAN_AUTHORIZE"
      || !text(value.replacement.old_plan_name) || value.replacement.replace_only_after_confirmation !== true
      || value.replacement.release_old_unused_reservation !== true || value.replacement.old_plan_unchanged_during_preparation !== true)) return false;
  if(object(value.replacement)&&value.replacement.reservation_adjustment!==undefined
      &&(!lifeReservationAdjustmentReady(value.replacement.reservation_adjustment)
        ||value.replacement.reservation_adjustment.new_reserved_minor!==value.budget_minor))return false;
  if (action === "OCCASION_MEMBER_SET") return text(value.family_member_name) && text(value.family_relationship)
    && ["CREATE", "UPDATE"].includes(String(value.operation)) && value.money_operation === false && value.account_access_granted === false;
  if (action === "OCCASION_SET") {
    const birthday = value.birthday;
    return text(value.family_member_name) && object(birthday) && integer(birthday.month) && birthday.month >= 1 && birthday.month <= 12
      && integer(birthday.day) && birthday.day >= 1 && birthday.day <= 31 && ["GREGORIAN", "LUNAR"].includes(String(birthday.calendar))
      && timezone(birthday.timezone) && value.money_operation === false && value.account_access_granted === false;
  }
  const payment = value.payment, recipient = value.recipient, items = value.items;
  if (!object(payment) || !["DEBIT", "CREDIT"].includes(String(payment.instrument_type)) || !text(payment.display_last4)
      || !/^[a-f\d]{4}$/i.test(payment.display_last4) || payment.currency !== "CNY" || value.currency !== "CNY"
      || !object(recipient) || !text(recipient.name) || !text(recipient.address)
      || !Array.isArray(items) || !items.length || items.length > 20 || !items.every(item => object(item)
        && text(item.product_name) && text(item.merchant_name) && integer(item.quantity) && item.quantity > 0
        && integer(item.unit_price_minor) && item.unit_price_minor > 0 && integer(item.amount_minor)
        && item.amount_minor === item.unit_price_minor * item.quantity && item.currency === "CNY")
      || !integer(value.total_amount_minor) || value.total_amount_minor !== items.reduce((sum,item) => sum + item.amount_minor, 0)
      || !instant(value.delivery_start) || !instant(value.delivery_end) || Date.parse(value.delivery_end) <= Date.parse(value.delivery_start)
      || !timezone(value.timezone) || value.payment_is_delivery !== false) return false;
  if (action === "MERCHANT_PURCHASE") return ["DELIVERY", "ON_SITE"].includes(String(value.fulfilment_mode))
    && (value.fulfilment_mode !== "DELIVERY" || text(recipient.phone));
  return text(value.plan_name) && text(value.family_member_name) && text(value.birthday) && text(recipient.phone)
    && payment.instrument_type === "DEBIT" && integer(value.budget_minor) && value.budget_minor > 0
    && integer(value.budget_limit_minor ?? 100000) && Number(value.budget_limit_minor ?? 100000) > 0
    && Number(value.budget_limit_minor ?? 100000) <= 100000000 && value.budget_minor <= Number(value.budget_limit_minor ?? 100000)
    && value.budget_minor >= value.total_amount_minor
    && instant(value.reserve_at) && instant(value.order_at) && instant(value.order_window_end) && instant(value.expires_at)
    && value.maximum_occurrences === 1 && value.cash_reservation_is_card_lock === false && value.change_requires_new_confirmation === true
    && value.failure_policy === "REFUND_UNFULFILLED_AND_RELEASE_UNUSED" && value.cancellation_policy === "REFUND_UNFULFILLED_TO_ORIGINAL_SOURCE";
}
const money = (value: unknown) => integer(value) ? `${Math.floor(value / 100)}.${String(value % 100).padStart(2, "0")} CNY` : "—";
function date(value: unknown, zone: unknown, en: boolean) {
  if (!instant(value) || !text(zone)) return "—";
  try { return new Intl.DateTimeFormat(en ? "en-GB" : "zh-CN", {timeZone:zone, year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false}).format(new Date(value)); }
  catch { return "—"; }
}
export function lifeConfirmationSpeech(action:string,scope:unknown,locale:string) {
  const en=locale==="en";
  if(!lifeConfirmationReady(action,scope))return en?"The scope is unavailable. Do not confirm.":"确认范围尚未核对完整，请勿确认。";
  if(action==="OCCASION_MEMBER_SET") return en?`Save family display name ${scope.family_member_name}, relationship ${scope.family_relationship}. No login, account or banking permission is created.`
    :`请核对家庭成员显示名${scope.family_member_name}，关系${scope.family_relationship}。本次仅保存资料，不开通登录、账户或银行权限。`;
  if(action==="OCCASION_SET") {
    const birthday=scope.birthday as Data;
    return en?`Review ${scope.family_member_name}'s birthday, ${birthday.month}/${birthday.day}, ${birthday.calendar==="LUNAR"?"lunar":"Gregorian"} calendar. No payment or account access.`
      :`请核对${scope.family_member_name}的${birthday.calendar==="LUNAR"?"农历":"公历"}生日，${birthday.month}月${birthday.day}日。本次不付款、不授权账户权限。`;
  }
  const payment=scope.payment as Data,recipient=scope.recipient as Data,items=scope.items as Data[];
  const names=items.map(item=>`${item.product_name} ${item.quantity}${en?" item":"件"}`).join(en?", ":"、");
  const adjusted=object(scope.replacement)&&lifeReservationAdjustmentReady(scope.replacement.reservation_adjustment);
  const reserveWhen=scope.reserve_policy==="ON_CONFIRMATION"&&!adjusted?(action==="LIFE_PLAN_CANCEL"?(en?"originally reserved after confirmation":"原计划为确认后预留"):(en?"after your confirmation":"确认后立即预留")):date(scope.reserve_at,scope.timezone,en);
  const plan = action.startsWith("LIFE_PLAN_") ? en?` Budget ${money(scope.budget_minor)}; ${adjusted?"original completed reservation":"reserve"} ${reserveWhen}, order ${date(scope.order_at,scope.timezone,true)}.`
    :` 本次预算${money(scope.budget_minor)}，${adjusted?"原已完成预留时间":"预留安排"}${reserveWhen}，下单时间${date(scope.order_at,scope.timezone,false)}。` : "";
  const replacement=object(scope.replacement)?lifeReservationAdjustmentReady(scope.replacement.reservation_adjustment)
    ?adjustmentText(scope.replacement.reservation_adjustment,en):en?" Confirming replaces the original arrangement and releases its unused reservation.":" 确认后替换原安排并释放原未用预留。":"";
  return en?`${lifeConfirmationTitle(action,locale)}. ${names}. Total ${money(scope.total_amount_minor)}. ${payment.instrument_type==="CREDIT"?"Credit":"Debit"} card ending ${payment.display_last4}. Recipient ${recipient.name}; address ${recipient.address}. ${date(scope.delivery_start,scope.timezone,true)} to ${date(scope.delivery_end,scope.timezone,true)}.${plan}${replacement} Review the full scope before confirming; payment is not delivery.`
    :`请核对${names}，清单总价${money(scope.total_amount_minor)}，${payment.instrument_type==="CREDIT"?"信用卡":"借记卡"}尾号${payment.display_last4}。收件人${recipient.name}，地址${recipient.address}，时间${date(scope.delivery_start,scope.timezone,false)}至${date(scope.delivery_end,scope.timezone,false)}。${plan}${replacement}请阅读完整范围后再确认；付款不等于送达。`;
}
export function LifeDomainConfirmation({action, scope}: {action:string; scope:unknown}) {
  const {locale} = useCustomerLanguage(); const en = locale === "en";
  if (!lifeConfirmationReady(action, scope)) return <p role="alert">{en ? "The confirmation scope is unavailable. Prepare a new request; nothing has been executed." : "本次确认范围尚未核对完整，请重新准备；尚未执行。"}</p>;
  if (action === "OCCASION_MEMBER_SET") return <section aria-label={lifeConfirmationTitle(action,locale)}><dl>
    <dt>{en?"Display name":"显示名"}</dt><dd>{String(scope.family_member_name)}</dd>
    <dt>{en?"Relationship":"关系标签"}</dt><dd>{String(scope.family_relationship)}</dd>
    </dl><p>{en?"Only save family directory details. No login, bank account or delegation is created; birthday details need separate confirmation.":"本次仅保存家庭资料，不开通登录、银行账户或家庭协助权限；生日资料另行确认。"}</p></section>;
  if (action === "OCCASION_SET") {
    const birthday = scope.birthday as Data;
    const leap:Record<string,string> = {FEB28:en?"February 28":"2月28日", MAR1:en?"March 1":"3月1日", LEAP_YEARS_ONLY:en?"Leap years only":"仅闰年办理"};
    return <section aria-label={lifeConfirmationTitle(action,locale)}><dl>
      <dt>{en?"Family member":"家庭成员"}</dt><dd>{String(scope.family_member_name)}</dd>
      <dt>{en?"Birthday":"生日"}</dt><dd>{birthday.calendar === "LUNAR" ? en?"Lunar calendar":"农历" : en?"Gregorian calendar":"公历"} · {String(birthday.month)}{en?"/":"月"}{String(birthday.day)}{en?"":"日"}{birthday.lunar_leap_month === true ? en?" · leap month":" · 闰月":""}</dd>
      <dt>{en?"Time zone":"使用时区"}</dt><dd>{String(birthday.timezone)}</dd>
      {text(birthday.confirmed_solar_date)&&<><dt>{en?"Confirmed Gregorian date":"已确认对应公历日期"}</dt><dd>{birthday.confirmed_solar_date}</dd></>}
      {text(birthday.leap_day_policy)&&<><dt>{en?"Non-leap-year arrangement":"非闰年安排"}</dt><dd>{leap[birthday.leap_day_policy] || "—"}</dd></>}
    </dl><p>{en?"Only save these birthday details. No payment, credit or account access is granted.":"本次仅保存这份生日资料，不付款，也不授予账户或信用权限。"}</p></section>;
  }
  const payment = scope.payment as Data, recipient = scope.recipient as Data, items = scope.items as Data[];
  const long = action.startsWith("LIFE_PLAN_");
  const adjusted=object(scope.replacement)&&lifeReservationAdjustmentReady(scope.replacement.reservation_adjustment);
  return <section className="life-confirmation-review" aria-label={lifeConfirmationTitle(action,locale)}>
    {object(scope.replacement)&&<p role="note">{lifeReservationAdjustmentReady(scope.replacement.reservation_adjustment)
      ?adjustmentText(scope.replacement.reservation_adjustment,en):en?`Replace “${scope.replacement.old_plan_name}” after confirmation and release its unused reservation. The original plan has not changed yet.`
      :`确认后替换“${scope.replacement.old_plan_name}”，释放原未用预留；原安排尚未改变。`}</p>}
    {long && <p className="life-confirmation-context">{String(scope.plan_name)} · {String(scope.family_member_name)} · {String(scope.birthday)}</p>}
    <section className="life-confirmation-block" aria-label={en?"Products and budget":"商品与预算"}>
      <h4>{en?"Products and budget":"商品与预算"}</h4>
      <ul className="life-confirmation-items">{items.map((item,index)=><li key={index}>
        <strong>{String(item.product_name)}</strong><span>{String(item.merchant_name)}{item.fulfilment_mode === "ON_SITE" ? en?" · On-site service":" · 到店服务":""}</span>
        <span>{en?"Quantity":"数量"} {String(item.quantity)} · {en?"Unit price":"单价"} {money(item.unit_price_minor)} · {en?"Subtotal":"小计"} {money(item.amount_minor)}</span>
      </li>)}</ul>
      <dl><div><dt>{long ? en?"Item price ceiling":"商品清单价格上限" : en?"Total payment":"本次付款总额"}</dt><dd>{money(scope.total_amount_minor)}</dd></div>
        {long && <div><dt>{en?"Cash reservation budget":"本次现金预留预算"}</dt><dd>{money(scope.budget_minor)}</dd></div>}
      </dl>
    </section>
    <section className="life-confirmation-block" aria-label={en?"Execution schedule":"执行安排"}>
      <h4>{en?"Execution schedule":"执行安排"}</h4>
      <dl><div><dt>{action === "LIFE_PLAN_CANCEL" ? en?"Original payment source":"原付款工具" : en?"Selected payment source":"所选付款工具"}</dt><dd>{payment.instrument_type === "CREDIT" ? en?"Credit card":"信用卡" : en?"Debit card":"借记卡"} · {en?"ending ":"尾号 "}{String(payment.display_last4)}</dd></div>
        {long&&<><div><dt>{adjusted?(en?"Original completed reservation":"原已完成预留日期"):(en?"Reservation":"预留安排")}</dt><dd>{scope.reserve_policy==="ON_CONFIRMATION"&&!adjusted?(action==="LIFE_PLAN_CANCEL"?(en?"Originally reserved after confirmation":"原计划为确认后预留"):(en?"Immediately after confirmation":"确认后立即预留")):date(scope.reserve_at,scope.timezone,en)}</dd></div>
          <div><dt>{scope.schedule_policy === "EXPLICIT" ? en?"Authorized ordering window":"本次下单时间窗" : en?"Order two days before birthday":"生日前两天下单"}</dt><dd>{date(scope.order_at,scope.timezone,en)} — {date(scope.order_window_end,scope.timezone,en)}</dd></div></>}
        <div><dt>{en?"Delivery / service window":"配送／服务时间窗"}</dt><dd>{date(scope.delivery_start,scope.timezone,en)} — {date(scope.delivery_end,scope.timezone,en)}</dd></div>
        {long&&<div><dt>{en?"Authorization expires":"授权截止"}</dt><dd>{date(scope.expires_at,scope.timezone,en)}</dd></div>}
        <div><dt>{en?"Time zone":"时区"}</dt><dd>{String(scope.timezone)}</dd></div>
      </dl>
    </section>
    <section className="life-confirmation-block" aria-label={en?"Recipient details":"收件信息"}>
      <h4>{en?"Recipient details":"收件信息"}</h4>
      <dl><div><dt>{scope.fulfilment_mode === "ON_SITE" ? en?"Service recipient":"服务使用人" : en?"Recipient":"收件人"}</dt><dd>{String(recipient.name)}</dd></div>
        {text(recipient.phone)&&<div><dt>{en?"Phone":"联系电话"}</dt><dd>{recipient.phone}</dd></div>}
        <div><dt>{en?"Address":"地址"}</dt><dd>{String(recipient.address)}</dd></div>
      </dl>
    </section>
    {long && <p className="life-confirmation-boundary">{action === "LIFE_PLAN_CANCEL"
      ?en?"Cancel unexecuted arrangements and release unused reservations; check paid orders and refunds separately.":"取消未执行安排并释放剩余预留；已付款与退款按订单状态核对。"
      :en?"Automatic processing applies only to this confirmed scope; changes require a new confirmation.":"仅本次范围自动办理，变更需重新确认。"}</p>}
    <p className="life-confirmation-delivery">{en?"Payment is not delivery; check the order's fulfilment status separately.":"付款不等于送达，配送／履约状态以订单为准。"}</p>
  </section>;
}
