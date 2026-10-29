/** Bill-v2 public projection only; server source checks remain authoritative. */
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerName } from "./customerPresentation";

type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown, signed = false): value is number => typeof value === "number" && Number.isSafeInteger(value) && (signed || value >= 0);
const money = (value: unknown, currency: unknown) => {
  if (!integer(value, true) || (currency !== "CNY" && currency !== "USD")) return "—";
  const magnitude = Math.abs(value);
  return `${value < 0 ? "−" : ""}${Math.floor(magnitude / 100)}.${String(magnitude % 100).padStart(2, "0")} ${currency}`;
};
const date = (value: unknown, exclusiveEnd = false) => typeof value === "string" && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(Date.parse(value) - (exclusiveEnd ? 1 : 0))) : "—";

/** One truthful scope line; only exact calendar-month boundaries get a month name. */
export function ownedBillScopeLabel(value: unknown, locale: "zh-CN" | "en" = "zh-CN"): string {
  if (!object(value) || !object(value.billing_scope)) return locale === "en" ? "Statement scope needs verification" : "账单范围待核对";
  const en = locale === "en", scope = value.billing_scope;
  const left = typeof value.period_start === "string" ? Date.parse(value.period_start) : Number.NaN;
  const right = typeof value.period_end === "string" ? Date.parse(value.period_end) : Number.NaN;
  const parts = (timestamp: number) => Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone:"Asia/Shanghai",
    year:"numeric",month:"numeric",day:"numeric",hour:"numeric",minute:"numeric",second:"numeric",hourCycle:"h23" })
    .formatToParts(new Date(timestamp)).filter(part=>part.type!=="literal").map(part=>[part.type,Number(part.value)]));
  let period = en ? "Recorded statement" : "已有账单";
  if (Number.isFinite(left) && Number.isFinite(right) && left < right) {
    const start=parts(left),end=parts(right);
    const midnight=(part:Record<string,number>,timestamp:number)=>part.hour===0 && part.minute===0 && part.second===0 && timestamp%1000===0;
    if (start.day===1 && end.day===1 && midnight(start,left) && midnight(end,right)
      && end.year*12+end.month===start.year*12+start.month+1) {
      period=en ? new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Shanghai",month:"long",year:"numeric"}).format(new Date(left))
        : `${start.year}年${start.month}月`;
    } else if (midnight(start,left) && midnight(end,right)) {
      period=`${date(value.period_start)} — ${date(value.period_end,true)}`;
    } else {
      const format=new Intl.DateTimeFormat(en?"en-GB":"zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
      period=`${format.format(new Date(left))} — ${format.format(new Date(right))}${en?" (Shanghai time)":"（北京时间）"}`;
    }
  } else if (value.period_start !== null && value.period_start !== undefined || value.period_end !== null && value.period_end !== undefined) {
    period=en ? "Statement period needs verification" : "统计期间待核对";
  }
  const labels=[period];
  if (object(scope.card_filter)) labels.push(typeof scope.card_filter.display_last4 === "string" && /^[0-9a-f]{4}$/i.test(scope.card_filter.display_last4)
    ? en ? `Card ending ${scope.card_filter.display_last4}` : `尾号${scope.card_filter.display_last4}`
    : en ? "Card identity needs verification" : "卡片资料待核对");
  if (scope.currency_filter === "CNY") labels.push(en?"CNY":"人民币");
  else if (scope.currency_filter === "USD") labels.push(en?"USD":"美元");
  else if (scope.currency_filter !== undefined && scope.currency_filter !== null) labels.push(en?"Currency needs verification":"币种待核对");
  if (object(value.category_filter) && typeof value.category_filter.category === "string") labels.push(en
    ? `${customerName(value.category_filter.category)} category only` : `仅${customerName(value.category_filter.category)}分类`);
  return labels.join(" · ");
}

export function ownedBillingDisplayReady(value: unknown): value is Data & { totals_by_currency: Data[] } {
  if (!object(value) || value.billing_schema_version !== "owned-bill-analysis-v2" || value.currency !== "CNY"
      || !object(value.billing_scope) || value.billing_scope.currencies_combined !== false || value.billing_scope.scalar_fields_currency !== "CNY"
      || !object(value.financial_insights) || value.financial_insights.version !== "owned-bill-analysis-v2"
      || value.financial_insights.read_only !== true || value.financial_insights.bank_state_changed !== false || value.financial_insights.model_calculated_money !== false
      || !object(value.coverage) || !["COMPLETE", "PARTIAL", "OBSERVED_HISTORY", "UNKNOWN", "INSUFFICIENT"].includes(String(value.coverage.status))
      || !integer(value.transaction_count) || !Array.isArray(value.totals_by_currency) || !value.totals_by_currency.every(object)) return false;
  const totals = value.totals_by_currency;
  if (new Set(totals.map(row => row.currency)).size !== totals.length) return false;
  for (const row of totals) {
    if (!["CNY", "USD"].includes(String(row.currency)) || !["row_count", "consumption_minor", "fees_minor", "economic_spending_minor", "refund_minor", "income_minor", "movement_in_minor", "movement_out_minor", "reclassified_principal_minor"].every(key => integer(row[key]))
        || !integer(row.net_economic_spending_minor, true) || !integer(row.net_economic_change_minor, true)
        || (row.cash_flow_net_minor !== null && !integer(row.cash_flow_net_minor, true)) || !object(row.category_spending_minor)) return false;
    const categories = Object.values(row.category_spending_minor);
    if (!categories.every(value => integer(value)) || categories.reduce((sum, amount) => sum + amount, 0) !== row.economic_spending_minor
        || (row.consumption_minor as number) + (row.fees_minor as number) !== row.economic_spending_minor
        || (row.economic_spending_minor as number) - (row.refund_minor as number) !== row.net_economic_spending_minor
        || (row.income_minor as number) - (row.net_economic_spending_minor as number) !== row.net_economic_change_minor) return false;
  }
  if (totals.reduce((sum, row) => sum + (row.row_count as number), 0) !== value.transaction_count) return false;
  const scope = value.billing_scope;
  if (scope.card_filter !== null && scope.card_filter !== undefined && (!object(scope.card_filter)
      || !["DEBIT", "CREDIT"].includes(String(scope.card_filter.instrument_type))
      || typeof scope.card_filter.display_last4 !== "string" || scope.card_filter.display_last4.length !== 4)) return false;
  if (scope.currency_filter !== null && scope.currency_filter !== undefined
      && (!["CNY", "USD"].includes(String(scope.currency_filter)) || totals.some(row => row.currency !== scope.currency_filter))) return false;
  const cny = totals.find(row => row.currency === "CNY");
  for (const [field, canonical] of [["spending_minor", "economic_spending_minor"], ["refund_minor", "refund_minor"], ["income_minor", "income_minor"], ["movement_in_minor", "movement_in_minor"], ["movement_out_minor", "movement_out_minor"]])
    if (!integer(value[field]) || value[field] !== (cny?.[canonical] ?? 0)) return false;
  const selected = value.category_filter;
  const insightFilter = value.financial_insights.category_filter;
  if (selected !== null && selected !== undefined && (!object(selected) || selected.version !== "bill-category-filter-v1"
      || selected.basis !== "CATEGORY_EXACT_MATCH" || selected.currency !== "SEPARATE_BY_CURRENCY" || typeof selected.category !== "string"
      || selected.category.length < 1 || selected.category.length > 32 || selected.read_only !== true || selected.bank_state_changed !== false
      || !object(insightFilter) || Object.keys(selected).length !== Object.keys(insightFilter).length
      || !Object.keys(selected).every(key => selected[key] === insightFilter[key]))) return false;
  return true;
}

export function ownedBillInstrumentGroups(value: unknown): Data[] | null {
  if (!object(value) || !object(value.financial_insights) || !Array.isArray(value.totals_by_currency)) return null;
  const projection=value.financial_insights.instrument_breakdown;
  if (!object(projection) || projection.version!=="owned-bill-instrument-breakdown-v1" || projection.status!=="RECORDED"
      || projection.currencies_combined!==false || !Array.isArray(projection.groups) || projection.groups.length>6
      || !projection.groups.every(object)) return null;
  const rows=projection.groups,fields=["row_count","consumption_minor","fees_minor","refund_minor"];
  if (rows.some(row=>!["DEBIT","CREDIT","UNKNOWN"].includes(String(row.instrument_type))
      || !["CNY","USD"].includes(String(row.currency)) || !fields.every(key=>integer(row[key])) || Number(row.row_count)<1)
      || new Set(rows.map(row=>`${row.currency}:${row.instrument_type}`)).size!==rows.length) return null;
  const totals=value.totals_by_currency;
  if (!totals.every(object) || new Set(rows.map(row=>row.currency)).size!==totals.length
      || totals.some(total=>!fields.every(key=>integer(total[key])
        && rows.filter(row=>row.currency===total.currency).reduce((sum,row)=>sum+Number(row[key]),0)===total[key]))) return null;
  return rows;
}

export function OwnedBillingCards({ analysis }: { analysis: unknown }) {
  const { locale } = useCustomerLanguage();
  const en = locale === "en";
  if (!ownedBillingDisplayReady(analysis)) return <p>{en ? "The statement scope and amounts need verification." : "账单范围和金额还需要核对。"}</p>;
  const coverage = analysis.coverage as Data;
  if (["UNKNOWN", "INSUFFICIENT"].includes(String(coverage.status))) return <p>{coverage.scope_status === "USD_DEBIT_WALLET_SOURCE_UNAVAILABLE"
    ? en ? "This debit card's USD-wallet statement is not covered; missing records are not zero spending." : "这张借记卡的美元钱包账单尚未接入当前来源，没有流水不代表零消费。"
    : en ? "That period is not covered yet; an empty list does not mean zero spending." : "这段期间的资料尚未覆盖，空清单不代表零消费。"}</p>;
  const scope = analysis.billing_scope as Data;
  const card = object(scope.card_filter) ? scope.card_filter : null;
  const category = object(analysis.category_filter) ? analysis.category_filter.category : null;
  const fallback = object(analysis.period_fallback) ? analysis.period_fallback : null;
  const fallbackDates = fallback && object(fallback.requested) && object(fallback.displayed) ? { requested: fallback.requested, displayed: fallback.displayed } : null;
  if (fallback && (!fallbackDates || fallback.status !== "APPLIED" || fallback.read_only !== true || fallback.bank_state_changed !== false
      || !["REQUESTED_PERIOD_NOT_COVERED", "REQUESTED_PERIOD_ONLY_MOVEMENTS"].includes(String(fallback.reason))
      || fallbackDates.displayed.start !== analysis.period_start || fallbackDates.displayed.end !== analysis.period_end))
    return <p>{en ? "The alternative statement period needs verification." : "替代账单期间还需要核对。"}</p>;
  const transactions = Array.isArray(analysis.statement_transactions) ? analysis.statement_transactions.filter(object) : [];
  const anomalies = Array.isArray(analysis.anomalies) && analysis.anomalies.every(object) ? analysis.anomalies : null;
  const split=ownedBillInstrumentGroups(analysis),hasSplit=(analysis.financial_insights as Data).instrument_breakdown!==undefined;
  const typeName=(value:unknown)=>value==="DEBIT"?(en?"Debit card":"借记卡"):value==="CREDIT"?(en?"Credit card":"信用卡"):(en?"Card type unspecified":"未标明卡种");
  return <section className="owned-billing-cards" aria-label={card ? en ? "Selected card economic bill records" : "选中卡片账单" : en ? "All owned economic bill records" : "本人全部账户账单"}>
    <p className="bill-scope-label">{ownedBillScopeLabel(analysis,locale)}</p>
    {fallbackDates && <p>{en ? "Originally requested" : "原先询问期间"}：{date(fallbackDates.requested.start)} — {date(fallbackDates.requested.end,true)}；{en ? "showing the recorded period above" : "当前显示上方已有账单期间"}。</p>}
    {analysis.totals_by_currency.map(total => <article key={String(total.currency)}>
      <h4>{String(total.currency)}</h4><dl className="insight-metrics">
        <dt>{typeof category === "string" ? en ? "Category spending incl. fees" : "该分类消费及费用" : en ? "Spending incl. fees" : "消费及费用合计"}</dt><dd>{money(total.economic_spending_minor, total.currency)}</dd>
        {Number(total.refund_minor)>0 && <><dt>{en ? "Refunds" : "退款"}</dt><dd>{money(total.refund_minor, total.currency)}</dd></>}
      </dl></article>)}
    {!analysis.totals_by_currency.length && <p>{en ? "No recorded rows match this scope." : "本次范围没有匹配的已记录流水。"}</p>}
    {hasSplit && (split ? <details className="bill-instrument-breakdown"><summary>{en?"Spending, fees and refunds by card type":"按卡种查看消费、费用和退款"}</summary>
      <table><thead><tr><th>{en?"Card type":"卡种"}</th><th>{en?"Currency":"币种"}</th><th>{en?"Consumption":"消费"}</th><th>{en?"Recorded fees":"已登记费用"}</th><th>{en?"Refunds":"退款"}</th></tr></thead>
        <tbody>{split.map(row=><tr key={`${row.currency}:${row.instrument_type}`}><td>{typeName(row.instrument_type)}</td><td>{String(row.currency)}</td>
          <td>{money(row.consumption_minor,row.currency)}</td><td>{money(row.fees_minor,row.currency)}</td><td>{money(row.refund_minor,row.currency)}</td></tr>)}</tbody></table>
      {split.some(row=>row.instrument_type==="UNKNOWN")&&<p>{en?"Some records are not linked to a specific card.":"部分记录未关联到具体卡片。"}</p>}
      <p>{en?"Funds movements, including repayments and transfers, are not consumption. Currencies are separate; only records within this statement scope are included.":"资金搬移（含还款/划转）不计消费。各币种分开，仅统计本次账单范围内的记录。"}</p>
    </details>:<p role="alert">{en?"The card-type breakdown needs verification.":"卡种分项还需要核对。"}</p>)}
    {anomalies && anomalies.length > 0 ? <p className="insight-notice">{en ? `Payments to review: ${anomalies.length}. These are review clues, not confirmed fraud.`
      : `需要留意的扣款：${anomalies.length} 笔，仅为待核对线索，不代表已确认欺诈。`}</p>
      : anomalies === null && <p className="insight-notice">{en ? "Review clues have not been returned yet." : "异常线索尚未返回。"}</p>}
    {anomalies && anomalies.length > 0 && <ul className="bill-review-preview" aria-label={en ? "Payments to review, summary" : "待核对扣款摘要"}>
      {anomalies.slice(0, 3).map((row, index) => <li key={index}>
        <span>{typeof row.merchant === "string" ? customerName(row.merchant) : en ? "Name unavailable" : "商户待核实"}</span>
        <span>{money(row.amount_minor, row.currency)} · {date(row.occurred_at)}</span>
      </li>)}
    </ul>}
    {coverage.status === "PARTIAL" && <p className="bill-availability-note">{en ? "Only covered records, not a full-period total." : "仅统计已有覆盖记录，不代表完整期间总额。"}</p>}
    <details><summary>{en ? "Recorded transactions and review clues" : "查看逐笔账单和待核对线索"}</summary>
      <ul>{transactions.map((row,index) => <li key={index}>{typeof row.merchant === "string" ? customerName(row.merchant) : en ? "Name unavailable" : "名称待核实"} · {date(row.occurred_at)} · {money(row.amount_minor,row.currency)}
        {row.instrument_type === "CREDIT" ? en ? " · credit" : " · 信用" : en ? " · cash account" : " · 现金账户"}</li>)}</ul>
      {analysis.statement_transactions_truncated === true && <p>{en ? `Showing ${transactions.length} details; totals use all ${analysis.transaction_count} recorded rows.` : `明细仅展示${transactions.length}笔，汇总使用全部${analysis.transaction_count}笔已取得流水。`}</p>}
      {anomalies && anomalies.length > 0 && <><h4>{en ? "Clues to review, not confirmed fraud" : "待核对线索，不代表已确认欺诈"}</h4><ul>{anomalies.map((row,index) => <li key={index}>{typeof row.merchant === "string" ? customerName(row.merchant) : "—"} · {money(row.amount_minor,row.currency)} · {typeof row.reason === "string" ? row.reason : "—"}</li>)}</ul></>}
      <small>{en ? "No automatic card lock, cancellation or money operation." : "不会据此自动锁卡、取消订单或操作资金。"}</small>
    </details>
  </section>;
}
