import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { dateText, money } from "./components";
import { customerName } from "./customerPresentation";
import type { TraceNode } from "./types";
import "./personalServiceCards.css";

type Data = Record<string, unknown>;
const object = (v: unknown): Data => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Data : {};
const rows = (v: unknown): Data[] => Array.isArray(v) ? v.filter(x => x && typeof x === "object" && !Array.isArray(x)) as Data[] : [];
const text = (v: unknown, fallback = "") => typeof v === "string" ? v : fallback;
const amount = (v: unknown, currency: unknown, unknown: string) => typeof v === "number" && Number.isSafeInteger(v)
  ? money(v, { currency: text(currency, "CNY") }) : unknown;
const quantity = (v: unknown, unknown: string) => typeof v === "string" && /^-?\d+(?:\.\d+)?$/.test(v)
  ? v.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : unknown;
const supported = new Set(["ASSET_POSITIONS_READ", "MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW", "PREFERENCE_SET", "PREFERENCE_REMOVE"]);
export const supportsPersonalService = (action: string) => supported.has(action);

export function PersonalServiceCards({ node, onPrompt }: { node: Pick<TraceNode, "node_id" | "action" | "result">; onPrompt?: (value: string) => void }) {
  const { locale } = useCustomerLanguage();
  const en = locale === "en", unknown = en ? "Not provided" : "暂未提供";
  const data = object(node.result);
  const stamp = text(data.observed_at);
  const scope = `${node.node_id}:${JSON.stringify(node.result)}`;
  const units: Record<string, string> = en ? { UNIT: "units", SHARE: "shares", GRAM: "g" } : { UNIT: "份", SHARE: "股", GRAM: "克" };
  const classes: Record<string, string> = en ? { WEALTH: "Wealth", STOCK: "Stocks", PRECIOUS_METAL: "Precious metals" } : { WEALTH: "理财", STOCK: "股票", PRECIOUS_METAL: "贵金属" };
  if (node.action === "ASSET_POSITIONS_READ") {
    const positions = rows(data.positions), summary = object(data.summary);
    return <section className="personal-service-results" aria-label={en ? "My holdings" : "我的持仓"}>
      <h3>{en ? "My holdings" : "我的持仓"}</h3>
      {rows(summary.values_by_currency).length > 0 && <div className="personal-asset-totals">{rows(summary.values_by_currency).map(row => <div key={text(row.currency)}>
        <span>{["COMPLETE", "COMPLETE_RECORDED_PRICES"].includes(text(row.valuation_status)) ? en ? "Reference value" : "持仓参考价值" : en ? "Available reference value" : "已知持仓参考价值"}</span>
        <strong>{amount(row.market_value_minor, row.currency, unknown)}</strong>
      </div>)}</div>}
      {!positions.length && <p className="personal-empty">{en ? "No matching holdings." : "目前没有符合条件的持仓。"}</p>}
      <div className="personal-result-grid">{positions.map(row => <article className="personal-result-card" key={text(row.position_id)}>
        <header><h4>{customerName(text(row.product_name))}</h4><span className="personal-kind">{classes[text(row.asset_class)] || text(row.asset_class)}</span></header>
        <dl><div><dt>{en ? "Held" : "当前持有"}</dt><dd className="personal-primary-value">{quantity(row.total_quantity, unknown)} {units[text(row.quantity_unit)] || text(row.quantity_unit)}</dd></div>
          <div><dt>{en ? "Reference value" : "参考价值"}</dt><dd>{amount(row.market_value_minor, row.currency, unknown)}</dd></div></dl>
        <CustomerResultDisclosure scopeKey={`${scope}:${text(row.position_id)}`} context={customerName(text(row.product_name))}>
          <dl><div><dt>{en ? "Available" : "可用数量"}</dt><dd>{quantity(row.available_quantity, unknown)}</dd></div>
            <div><dt>{en ? "Frozen" : "冻结数量"}</dt><dd>{quantity(row.frozen_quantity, unknown)}</dd></div>
            <div><dt>{en ? "Reference unit price" : "参考单价"}</dt><dd>{quantity(row.reference_price, unknown)} {text(row.currency)} / {units[text(row.quantity_unit)]}</dd></div>
            <div><dt>{en ? "Price time" : "价格时间"}</dt><dd>{text(row.price_observed_at) ? dateText(text(row.price_observed_at), true) : unknown}</dd></div>
            <div><dt>{en ? "Remaining cost" : "持仓成本"}</dt><dd>{amount(row.remaining_cost_minor, row.currency, unknown)}</dd></div>
            <div><dt>{en ? "Unrealized P/L" : "浮动盈亏"}</dt><dd>{amount(row.unrealized_pnl_minor, row.currency, unknown)}</dd></div></dl>
        </CustomerResultDisclosure>
      </article>)}</div>
      {stamp && <p className="personal-asof">{en ? "Holdings checked at " : "持仓查询于 "}{dateText(stamp, true)}</p>}
    </section>;
  }
  if (node.action === "MEMBERSHIP_VIEW") {
    const membership = object(data.membership), risk = object(data.risk_profile);
    return <section className="personal-service-results" aria-label={en ? "Membership and cards" : "客户与卡片等级"}>
      <h3>{en ? "Membership and cards" : "客户与卡片等级"}</h3>
      <div className="personal-asset-totals"><div><span>{en ? "Customer tier" : "客户等级"}</span><strong>{text(membership.tier_name, unknown)}</strong></div>
        <div><span>{en ? "Risk assessment" : "风险测评"}</span><strong>{risk.risk_level == null ? unknown : `R${String(risk.risk_level)}`}{risk.risk_level != null && risk.valid === false ? en ? " · Needs renewal" : " · 需重新测评" : ""}</strong></div></div>
      <div className="personal-result-grid">{rows(data.cards).map(card => <article className="personal-result-card" key={text(card.card_id)}>
        <header><h4>{en ? "Card ending " : "银行卡 · 尾号 "}{text(card.last4)}</h4><span className="personal-kind">{text(card.card_tier_name, unknown)}</span></header>
      </article>)}</div>
    </section>;
  }
  if (node.action === "BENEFITS_VIEW") return <section className="personal-service-results" aria-label={en ? "Offers and benefits" : "优惠活动与权益"}>
    <h3>{en ? "Offers and benefits" : "优惠活动与权益"}</h3>
    {!rows(data.activities).length && <p className="personal-empty">{en ? "No matching offers right now." : "暂时没有符合条件的活动。"}</p>}
    <div className="personal-result-grid">{rows(data.activities).map(activity => {
      const eligibility = object(activity.eligibility);
      const conditions = object(activity.conditions);
      const tierNames: Record<string, string> = en
        ? { STANDARD: "Standard", SILVER: "Silver", GOLD: "Gold", PLATINUM: "Platinum" }
        : { STANDARD: "普通", SILVER: "银级", GOLD: "金级", PLATINUM: "白金" };
      const conditionNames: Record<string, string> = en
        ? { ACTIVITY_ACTIVE: "Activity enabled", ACTIVITY_STARTED: "Activity started", ACTIVITY_UNEXPIRED: "Within valid dates", CUSTOMER_TIER: "Customer tier requirement", CARD_TIER_AND_STATE: "Card tier and status requirement" }
        : { ACTIVITY_ACTIVE: "活动启用状态", ACTIVITY_STARTED: "活动开始时间", ACTIVITY_UNEXPIRED: "活动有效期", CUSTOMER_TIER: "客户等级要求", CARD_TIER_AND_STATE: "卡片等级与状态要求" };
      const requiredTiers = (value: unknown) => Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string").map(item => tierNames[item] || item).join(" / ") : "";
      return <article className="personal-result-card" key={text(activity.activity_key)}>
        <header><h4>{text(activity.name)}</h4><span className={`personal-kind ${eligibility.eligible === true ? "is-eligible" : ""}`}>{eligibility.eligible === true ? en ? "Eligible" : "符合条件" : eligibility.eligible === false ? en ? "Not eligible" : "暂不符合" : en ? "To be checked" : "待核实"}</span></header>
        <p>{text(activity.description)}</p>
        <p className="personal-asof">{en ? "Ends " : "有效至 "}{text(activity.ends_at) ? dateText(text(activity.ends_at), true) : unknown}</p>
        <CustomerResultDisclosure scopeKey={`${scope}:${text(activity.activity_key)}`} context={text(activity.name)} label={en ? "Offer conditions" : "查看活动条件"}>
          {requiredTiers(conditions.customer_tiers) && <p>{en ? "Customer tiers: " : "适用客户等级："}{requiredTiers(conditions.customer_tiers)}</p>}
          {requiredTiers(conditions.card_tiers) && <p>{en ? "Card tiers: " : "适用卡片等级："}{requiredTiers(conditions.card_tiers)}</p>}
          <ul>{rows(eligibility.reasons).map((reason, index) => <li key={index}>
            {conditionNames[text(reason.code)] || text(reason.message)} · {reason.met === true ? en ? "Met" : "已满足" : reason.met === false ? en ? "Not met" : "未满足" : en ? "To be checked" : "待核实"}
          </li>)}</ul>
          {rows(eligibility.matching_cards).length > 0 && <p>{en ? "Matching cards: " : "适用卡片："}{rows(eligibility.matching_cards).map(card => text(card.last4)).join("、")}</p>}
        </CustomerResultDisclosure>
      </article>;
    })}</div>
  </section>;
  if (node.action === "PREFERENCE_VIEW") return <section className="personal-service-results" aria-label={en ? "My preferences" : "我的偏好"}>
    <h3>{en ? "My preferences" : "我的偏好"}</h3>
    {!rows(data.preferences).length && <p className="personal-empty">{en ? "No preferences saved yet. You can tell me what you like." : "还没有保存偏好，可以直接告诉我你的习惯。"}</p>}
    <div className="personal-result-grid">{rows(data.preferences).map(preference => <article className="personal-result-card" key={text(preference.kind)}>
      <header><h4>{text(preference.kind_label)}</h4></header><p className="personal-primary-value">{text(preference.value)}</p>
      {onPrompt && <button className="text-button" onClick={() => onPrompt(en ? `Remove my ${preference.kind === "TRAVEL" ? "travel" : "spending"} preference.` : `不再记住我的${preference.kind === "TRAVEL" ? "出行" : "消费"}偏好。`)}>{en ? "Remove this preference" : "不再记住"}</button>}
    </article>)}</div>
    {rows(data.suggestions).length > 0 && <CustomerResultDisclosure scopeKey={scope} label={en ? "Suggested preferences" : "可供确认的偏好建议"}>
      {rows(data.suggestions).map((suggestion, index) => <p key={index}>{text(suggestion.value)} · {text(suggestion.reason)}</p>)}
    </CustomerResultDisclosure>}
  </section>;
  return <section className="personal-service-results"><h3>{node.action === "PREFERENCE_REMOVE" ? en ? "Preference removed" : "已撤回偏好" : en ? "Preference updated" : "偏好已更新"}</h3>
    <p>{text(data.kind_label)}{node.action === "PREFERENCE_SET" && text(object(data.preference).value) ? `：${text(object(data.preference).value)}` : ""}</p></section>;
}
