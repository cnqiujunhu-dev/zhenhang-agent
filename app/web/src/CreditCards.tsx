import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { money } from "./components";
import "./creditCards.css";
import { creditFacilityDisplay } from "./creditFacilityDisplay";
import { CreditConsumptionDetails } from "./CreditConsumptionDetails";

type Data = Record<string, unknown>;
const object = (v: unknown): Data => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Data : {};
const rows = (v: unknown): Data[] => Array.isArray(v) ? v.filter(x => x && typeof x === "object" && !Array.isArray(x)) as Data[] : [];
const text = (v: unknown, fallback = "") => typeof v === "string" ? v : fallback;
const supported = new Set(["CREDIT_VIEW", "CREDIT_STATEMENT", "CREDIT_REPAY", "CREDIT_INSTALLMENT", "CREDIT_SETTLE_INSTALLMENT", "CREDIT_APPLY", "CREDIT_LIMIT_REQUEST", "CREDIT_ACTIVATE"]);
const creditDisplayVersion = "credit-status-presentation-v1";
export const supportsCreditCards = (action: string) => supported.has(action);

export function CreditCards({ node, onPrompt }: { node: {node_id: string; action: string; result?: unknown}; onPrompt?: (value: string) => void }) {
  const { locale } = useCustomerLanguage();
  const en = locale === "en", unknown = en ? "Not provided" : "暂未提供";
  const data = object(node.result);
  const amount = (v: unknown, currency: unknown) => typeof v === "number" && Number.isSafeInteger(v) && ["CNY","USD"].includes(String(currency))
    ? money(v, { currency: String(currency) }) : unknown;
  const dueDate = (value: unknown) => {
    if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return unknown;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? unknown : new Intl.DateTimeFormat(en ? "en-GB" : "zh-CN", {
      year:"numeric",month:"2-digit",day:"2-digit",timeZone:"Asia/Shanghai",
    }).format(date);
  };
  const scheduleTable = (value: unknown, currency: unknown) => <div className="credit-schedule-scroll"><table>
    <thead><tr><th>{en ? "Period" : "期次"}</th><th>{en ? "Principal" : "本金"}</th><th>{en ? "Fee" : "费用"}</th><th>{en ? "Due date" : "应还日期"}</th></tr></thead>
    <tbody>{rows(value).map((row,index) => <tr key={index}><td>{typeof row.period === "number" && Number.isSafeInteger(row.period) && row.period>0 ? row.period : unknown}</td>
      <td>{amount(row.principal_minor,currency)}</td><td>{amount(row.fee_minor,currency)}</td><td>{dueDate(row.due_at)}</td></tr>)}</tbody>
  </table></div>;
  const cardRows=rows(data.cards);
  const facilityDisplay=creditFacilityDisplay(data.cards);
  const facilityIndex=(card:Data)=>facilityDisplay?.groupByCard.get(text(card.card_id));
  const sharedWith=(card:Data)=>{
    const index=facilityIndex(card),group=index===undefined?null:facilityDisplay?.groups[index];
    return group && group.cardIds[0]!==card.card_id ? {tail:group.tails[0]} : null;
  };
  const sharedCurrencyCredit=cardRows.length>0 && (facilityDisplay!==null || cardRows.every(card=>
    card.instrument_type==="CREDIT" && card.base_currency==="CNY" && card.shared_credit_limit===true
      && Array.isArray(card.supported_currencies) && card.supported_currencies.length===2
      && card.supported_currencies.includes("CNY") && card.supported_currencies.includes("USD")));
  const tail=(card:Data)=>text(card.display_last4) || text(card.card_number).slice(-4) || unknown;
  const statuses: Record<string, string> = en ? { ACTIVE: "Active", LOCKED: "Locked", LOST: "Lost", ISSUED: "Awaiting activation", REPLACED: "Replaced", PENDING_REVIEW: "Pending review", POSTED: "Posted", SETTLED: "Settled" }
    : { ACTIVE: "正常", LOCKED: "已锁定", LOST: "已挂失", ISSUED: "待激活", REPLACED: "已换卡", PENDING_REVIEW: "待审核", POSTED: "已入账", SETTLED: "已结清" };
  // This is an authenticated display projection, never a payment permission.
  const display = object(data.credit_status_display);
  const checkedDisplay = display.schema_version === creditDisplayVersion && display.node_id === node.node_id
    && (display.status === "READY" || display.status === "UNAVAILABLE");
  const notice = (card:Data) => checkedDisplay && display.card_id === card.card_id
    && (display.status === "UNAVAILABLE" || display.card_status === card.status)
    ? text(object(display.notices)[locale]) : "";
  const applications = rows(data.applications);
  const applicationDescription = (application:Data) => {
    if (application.kind === "LIMIT") {
      if (application.status === "PENDING_REVIEW") return en ? "Credit limit request under review; this is not approval." : "调额申请审核中，尚不代表调额获批。";
      if (application.status === "APPROVED") return en ? "Credit limit request approved. Refer to the current shared credit pool for available credit; this historical request is not a current balance." : "调额申请已获批；当前可用额度以当前授信池为准，历史申请记录不代表当前可用金额。";
      if (application.status === "REJECTED") return en ? "Credit limit request rejected." : "调额申请已被拒绝。";
      return unknown;
    }
    if (application.kind !== "CARD") return en ? "The application type is not verified. Refer to the current query result." : "申请记录类型暂未核实，请以当前查询结果为准。";
    return application.status==="PENDING_REVIEW" ? en ? "Pending review; no card or limit approval yet." : "待审核，尚不代表发卡或授信获批。" : application.status==="APPROVED" ? en ? "Application approved; card activation is separate." : "申请已批准；发卡与本人激活仍为不同步骤。" : application.status==="REJECTED" ? en ? "Application rejected." : "申请已拒绝。" : unknown;
  };
  const applicationCards = applications.map(application=><article className="credit-card" key={text(application.application_id)}><h4>{application.kind==="LIMIT" ? en ? "Credit limit request" : "授信调额申请" : application.kind==="CARD" ? en ? "Card application" : "信用卡申请" : en ? "Application record" : "申请记录"}</h4>
    <p>{applicationDescription(application)}</p>
  </article>);
  const specifiedCard = cardRows.length === 1 && typeof object(data.query).card_reference === "string"
    && text(object(data.query).card_reference).trim().length > 0;
  if (node.action === "CREDIT_VIEW") return <section className="credit-results" aria-label={en ? "Credit cards" : "信用卡"}>
    <h3>{en ? "Credit cards" : "信用卡"}</h3>
    {!rows(data.cards).length && <p>{en ? "No credit cards." : "目前没有信用卡。"}</p>}
    {sharedCurrencyCredit && <p className="credit-shared-pool">{en ? "CNY and USD share a credit limit." : "人民币/美元共享额度。"}
      {facilityDisplay && facilityDisplay.groups.length>1 && (en ? " Separate facilities are not combined." : "不同授信不合并。")}</p>}
    {cardRows.map(card => <article className="credit-card credit-card-compact" key={text(card.card_id)}>
      <header><h4>{en ? "Credit card · ending " : "信用卡 · 尾号 "}{tail(card)}</h4>
        <span className="credit-card-currency">{Array.isArray(card.supported_currencies) && card.supported_currencies.includes("CNY") && card.supported_currencies.includes("USD")
          || rows(card.balances).some(balance=>balance.currency==="CNY") && rows(card.balances).some(balance=>balance.currency==="USD") ? "CNY / USD" : unknown}</span>
        <span className="credit-card-status">{notice(card) && display.status === "UNAVAILABLE"
          ? en ? "Status unverified" : "状态待核验" : statuses[text(card.status)] || unknown}</span></header>
      {notice(card) && <p className="credit-card-use-notice" role="note">{notice(card)}</p>}
      {sharedWith(card) ? <p className="credit-shared-pool">{en
        ? `Shares the limit with card ending ${sharedWith(card)!.tail}.`
        : `与尾号${sharedWith(card)!.tail}共享额度。`}</p> : <dl className="credit-card-summary">
        <div><dt>{en ? "Available credit (CNY)" : "可用额度（人民币）"}</dt><dd>{amount(card.available_base_credit_minor,"CNY")}</dd></div>
        {rows(card.balances).map(balance=><div key={text(balance.currency)}><dt>{text(balance.currency)} {en ? "outstanding" : "未还金额"}</dt><dd>{amount(balance.outstanding_minor,balance.currency)}</dd></div>)}
      </dl>}
      <details className="credit-card-details"><summary>{en ? "Card details" : "查看详情"}</summary>
        <dl><div><dt>{en ? "Card number" : "完整卡号"}</dt><dd>{text(card.card_number,unknown)}</dd></div></dl>
        {!sharedWith(card) && <><dl className="credit-metrics"><div><dt>{en ? "Shared limit (CNY)" : "共享授信额度（人民币）"}</dt><dd>{amount(card.base_credit_limit_minor,"CNY")}</dd></div>
          <div><dt>{en ? "Used credit (CNY equivalent)" : "已用授信（折合人民币）"}</dt><dd>{amount(card.used_base_exposure_minor,"CNY")}</dd></div>
          <div><dt>{en ? "Authorization hold (CNY equivalent)" : "预授权占用（折合人民币）"}</dt><dd>{amount(card.authorization_base_exposure_minor,"CNY")}</dd></div></dl>
          <dl>{rows(card.balances).map(balance=><div key={text(balance.currency)}><dt>{text(balance.currency)} {en ? "credit balance" : "溢缴款"}</dt><dd>{amount(balance.credit_balance_minor,balance.currency)}</dd></div>)}</dl></>}
        {onPrompt && <button type="button" onClick={() => onPrompt(en ? `Show the statement and amount due for credit card ${text(card.card_number)}.` : `查询信用卡${text(card.card_number)}的账单和应还金额`)}>{en ? "View statement" : "查看账单"}</button>}
      </details>
    </article>)}
    {specifiedCard && applications.length > 0
      ? <details className="credit-card-details credit-application-history" key={`${node.node_id}:${String(display.task_version ?? "recorded")}:${creditDisplayVersion}:applications`}>
          <summary>{en ? `Credit-pool application history (${applications.length})` : `该授信池申请历史（${applications.length}）`}</summary>
          {applicationCards}
        </details>
      : applicationCards}
  </section>;
  if (node.action === "CREDIT_STATEMENT") return <section className="credit-results" aria-label={en ? "Credit statements" : "信用卡账单"}>
    <h3>{en ? "Credit statements" : "信用卡账单"}</h3><p>{en ? "Card ending " : "卡片尾号 "}{tail(data)}</p>
    {typeof data.card_number === "string" && <details className="credit-card-details"><summary>{en ? "Card number" : "查看完整卡号"}</summary><p>{data.card_number}</p></details>}
    <div className="credit-currencies">{rows(data.statements_by_currency).map(statement => <article className="credit-card" key={text(statement.currency)}>
      <h4>{text(statement.currency)}</h4><dl>
        <div><dt>{en ? "Total outstanding" : "全部未还欠款"}</dt><dd>{amount(statement.outstanding_minor, statement.currency)}</dd></div>
        <div><dt>{en ? "Current billed due" : "本期已出账应还"}</dt><dd>{amount(statement.current_due_minor, statement.currency)}</dd></div>
        <div><dt>{en ? "Minimum due" : "最低应还"}</dt><dd>{amount(statement.minimum_due_minor, statement.currency)}</dd></div></dl>
      <details className="credit-card-details"><summary>{en ? "Statement details" : "查看账单详情"}</summary><dl>
        <div><dt>{en ? "Unbilled principal" : "未出账本金"}</dt><dd>{amount(statement.unbilled_principal_minor, statement.currency)}</dd></div>
        <div><dt>{en ? "Fees" : "未还费用"}</dt><dd>{amount(statement.fees_minor, statement.currency)}</dd></div>
        <div><dt>{en ? "Interest" : "未还利息"}</dt><dd>{amount(statement.interest_minor, statement.currency)}</dd></div></dl>
      <p>{en ? "Minimum payment does not clear the remaining debt or release all used credit." : "最低还款不等于还清，剩余欠款仍占用授信。"}</p></details>
      <CreditConsumptionDetails value={statement.consumption_details} currency={text(statement.currency)} />
    </article>)}</div>
    {Array.isArray(data.installments) && <section aria-label={en ? "Existing installments" : "已有分期"}>
      <h4>{en ? "Existing installments" : "已有分期"}</h4>
      {!rows(data.installments).length && <p>{en ? "No installment records." : "暂无分期记录。"}</p>}
      {rows(data.installments).map((item,index)=><article className="credit-card credit-card-compact" key={index}>
        <header><h5>{en ? "Installment" : "分期"}{typeof item.periods === "number" && Number.isSafeInteger(item.periods) && item.periods>0
          ? en ? ` · ${item.periods} periods` : ` · ${item.periods}期` : ""}</h5>
          <span>{item.status==="ACTIVE" ? en ? "In progress" : "分期中" : item.status==="SETTLED" ? en ? "Settled" : "已结清" : en ? "Status unverified" : "状态待核验"}</span></header>
        <dl><div><dt>{en ? "Remaining principal" : "未还本金"}</dt><dd>{amount(item.remaining_principal_minor,item.currency)}</dd></div>
          <div><dt>{en ? "Remaining fees" : "未还费用"}</dt><dd>{amount(item.remaining_fee_minor,item.currency)}</dd></div></dl>
        <details className="credit-card-details"><summary>{en ? "View installment arrangement" : "查看分期安排"}</summary>
          <dl><div><dt>{en ? "Original principal" : "原分期本金"}</dt><dd>{amount(item.principal_minor,item.currency)}</dd></div>
            <div><dt>{en ? "Total agreed fee" : "约定总费用"}</dt><dd>{amount(item.fee_minor,item.currency)}</dd></div></dl>
          <p>{en ? "Original installment schedule" : "原分期计划"}</p>
          {rows(item.schedule).length ? scheduleTable(item.schedule,item.currency) : <p>{unknown}</p>}
        </details>
      </article>)}
    </section>}
  </section>;
  return <section className="credit-results" aria-label={en ? "Credit service result" : "信用卡办理结果"}><h3>{en ? "Credit service result" : "信用卡办理结果"}</h3>
    {typeof data.card_number === "string" && <><p>{en ? "Card ending " : "卡片尾号 "}{tail(data)}</p><details className="credit-card-details"><summary>{en ? "Card number" : "查看完整卡号"}</summary><p>{data.card_number}</p></details></>}
    <p>{statuses[text(data.status)] || text(data.message, unknown)}</p>
    {typeof data.amount_minor === "number" && <p>{en ? "Amount: " : "办理金额："}{amount(data.amount_minor, data.currency)}</p>}
    {typeof data.limit_minor === "number" && <p>{en ? "Per-transaction limit: " : "单笔消费限额："}{amount(data.limit_minor, "CNY")}</p>}
    {typeof object(data.replacement_card).card_number === "string" && <p>{en ? "Replacement awaiting activation: " : "待激活补卡："}{text(object(data.replacement_card).card_number)}</p>}
    {typeof data.principal_minor === "number" && <p>{en ? "Installment principal: " : "分期本金："}{amount(data.principal_minor, data.currency)}</p>}
    {typeof data.fee_minor === "number" && <p>{en ? "Total installment fee: " : "分期总费用："}{amount(data.fee_minor, data.currency)}</p>}
    {rows(data.schedule).length > 0 && <details><summary>{en ? "Payment schedule" : "逐期本金与费用"}</summary>
      {scheduleTable(data.schedule,data.currency)}
    </details>}
    {data.credit_limit_changed === false && <p>{en ? "Application submitted; no credit limit has changed." : "仅提交申请，授信额度尚未改变。"}</p>}
  </section>;
}
