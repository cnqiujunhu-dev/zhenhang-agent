// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText, localizeFinancialValue } from "./customerLanguage";
import { dateText, Icon, money, stateLabels } from "./components";
import { customerName, customerNarrative } from "./customerPresentation";
import { customerActionLabel } from "./customerTaskPresentation";
import { FxQuoteDetails } from "./FxPanels";
import type { JsonValue, TraceNode } from "./types";
import "./customerReply.css";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { recordedSubscriptionPayment, recordedActiveSubscription, recordedSubscriptionRestoration, subscriptionDate } from "./subscriptionCommercePresentation";
import { compactTransferResult } from "./transferResultPresentation";
import "./transferResult.css";

const writes = new Set(["TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT",
  "WEALTH_BUY", "WEALTH_REDEEM", "SUBSCRIPTION_CANCEL", "SUBSCRIPTION_START", "SUBSCRIPTION_RENEW", "FAMILY_GRANT", "FAMILY_REVOKE", "FAMILY_PROPOSE", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE",
  "CARBON_CLAIM", "CARBON_REDEEM", "FX_TRANSFER", "FX_EXCHANGE", "CONTACT_BIND"]);
export const supportsBusinessResult = (action: string) => writes.has(action);
function object(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function text(value: JsonValue | undefined): string | null {
  return typeof value === "string" && value.trim() ? customerName(value) : null;
}
function amount(value: JsonValue | undefined, currency = "CNY") {
  return typeof value === "number" && Number.isSafeInteger(value) && ["CNY", "USD"].includes(currency)
    ? `${money(value, {currency})} ${currency === "USD" ? "美元" : "人民币"}` : "金额待核对";
}
export function BusinessResultCards({node}: {node: TraceNode}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  if (node.action === "TRANSFER") {
    const receipt = compactTransferResult(node);
    if (!receipt) return null;
    const label = (zh: string, en: string) => locale === "en" ? en : zh;
    const unknown = label("待核对", "Not available");
    return <article className="business-result-card transfer-result-card" aria-label={label("转账结果", "Transfer result")}
      data-transfer-state={receipt.status}>
      <dl className="business-result-facts">
        <div><dt>{label("收款人姓名", "Recipient name")}</dt><dd>{receipt.recipientName || unknown}</dd></div>
        <div><dt>{label("收款卡号", "Recipient card number")}</dt><dd>{receipt.recipientCardNumber || unknown}</dd></div>
        <div><dt>{label("备注", "Note")}</dt><dd className="transfer-result-memo">{receipt.memo === null ? label("未提供", "Not provided") : receipt.memo || label("无", "None")}</dd></div>
        <div><dt>{label("转账状态", "Transfer status")}</dt><dd className={`transfer-result-status-${receipt.status.toLowerCase()}`}>
          {receipt.status === "SUCCESS" ? label("成功", "Successful") : receipt.status === "FAILED" ? label("失败", "Failed") : label("待核实", "Unverified")}
        </dd></div>
      </dl>
    </article>;
  }
  if (node.status !== "SUCCEEDED" || !node.result || node.result.error || !supportsBusinessResult(node.action)) return null;
  const data = node.result;
  const subscriptionWrite = ["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"].includes(node.action);
  const subscriptionAlreadyActive = recordedActiveSubscription(node.action, data);
  const paidAccessPreserved = subscriptionAlreadyActive && data.no_effect_kind === "START_PAID_RIGHTS_ACTIVE";
  const subscriptionRestored = recordedSubscriptionRestoration(node.action,data);
  if (subscriptionWrite && !subscriptionAlreadyActive && !subscriptionRestored && !recordedSubscriptionPayment(node.action, data)) return null;
  const alias = object(data.contact_alias);
  const currency = typeof data.currency === "string" ? data.currency : "CNY";
  const fields: [string, string | null][] = [];
  const numeric = (label: string, key: string) => { if (typeof data[key] === "number" && Number.isSafeInteger(data[key])) fields.push([label, String(data[key])]); };
  const cash = (label: string, key: string) => { if (data[key] !== undefined) fields.push([label, amount(data[key], currency)]); };
  if (node.action === "TRANSFER_SCHEDULE") {
    fields.push(["收款对象", text(data.recipient_username) || text(data.payee_name)]);
    cash("金额", "amount_minor");
    if (data.due_at) fields.push(["约定时间", typeof data.due_at === "string" ? dateText(data.due_at, true) : null]);
    const posted = typeof data.transfer_id === "string" && data.transfer_id.length > 0 && typeof data.amount_minor === "number";
    if (!posted) fields.push(["预约状态", "预约已登记，到期时会再次核对；当前还未付款"]);
    if (posted && data.transfer_channel === "EXTERNAL_SIM") fields.push(["到账情况", "外部清算已记账，外部银行到账仍需核对"]);
    if (posted && data.transfer_channel === "INTERNAL_BANK") fields.push(["到账情况", "平台内双方账户已入账"]);
  } else if (node.action.startsWith("CARD_")) {
    const tail = text(data.display_last4) || (typeof data.card_number === "string" ? data.card_number.slice(-4) : null);
    fields.push(["卡片", tail ? `尾号 ${tail}` : null]);
    fields.push(["当前状态", typeof data.card_status === "string" ? stateLabels[data.card_status] || "状态待更新" : null]);
    if (node.action === "CARD_SET_LIMIT") cash("新限额", "limit_minor");
    if (node.action === "CARD_REPLACEMENT") fields.push(["申请进度", "补卡申请已提交，尚未制卡"]);
  } else if (["WEALTH_BUY", "WEALTH_REDEEM"].includes(node.action)) {
    fields.push(["交易", node.action === "WEALTH_BUY" ? "申购" : "赎回"]); cash("本次金额", "amount_minor"); numeric("剩余持仓份额", "holding_units");
  } else if (node.action === "SUBSCRIPTION_CANCEL") {
    fields.push(["服务名称",text(data.merchant)],["订阅状态", data.auto_renew === false && ["ACTIVE","CANCELLED"].includes(String(data.status)) ? "自动续费已关闭" : data.status === "CANCELLED" ? "已取消" : data.can_cancel_here === false ? "需到服务商核实并取消" : "请核对服务状态"]);
    if (subscriptionDate(data.entitlement_end_at)) fields.push(["权益有效至",dateText(data.entitlement_end_at,true)]);
    if (data.entitlement_preserved === true) fields.push(["原付费权益保留","是"]);
    if (data.refunded_minor === 0) fields.push(["本次未退款","是"]);
  } else if (subscriptionWrite) {
    fields.push(["服务名称",text(data.merchant)]);
    if (subscriptionRestored) fields.push(["订阅状态",locale === "en" ? "Automatic renewal restored" : "自动续费已恢复"],["本次未扣款","是"],["原付费权益保留","是"]);
    else if (subscriptionAlreadyActive) fields.push(["订阅状态",paidAccessPreserved ? locale === "en" ? "Existing paid access remains valid" : "原付费权益仍有效" : "订阅已启用"],["本次未扣款","是"]);
    else { cash("已扣款","charged_minor"); numeric("付费月数","months"); }
    if (subscriptionDate(data.entitlement_end_at)) fields.push(["权益有效至",dateText(data.entitlement_end_at,true)]);
    if (typeof data.auto_renew === "boolean") fields.push(["自动续费",data.auto_renew ? "已开启" : "未开启"]);
    if (data.auto_renew === true) { cash("后续自动扣款金额","mandate_amount_minor"); numeric("自动续费周期（月）","mandate_billing_months"); }
    if (subscriptionDate(data.effective_from)) fields.push([subscriptionRestored ? locale === "en" ? "Renewal starts after existing access ends" : "自动续费起点（原权益到期）" : "生效时间",dateText(data.effective_from,true)]);
    if (!subscriptionAlreadyActive && !subscriptionRestored) cash("月费","monthly_price_minor");
  } else if (node.action === "AA_REQUEST") {
    cash("待收总额", "total_minor"); cash("已收到", "received_minor"); fields.push(["收款进度", "已建立收款请求，不代表参与者已经付款"]);
  } else if (node.action === "CONTACT_BIND" || data.contact_alias) {
    fields.push(["我的联系人称呼", text(alias.alias_name)], ["对应账号", text(alias.recipient_username)], ["适用范围", "仅用于我的联系人，不代表已转账"]);
  } else if (node.action.startsWith("FAMILY_")) {
    fields.push(["办理状态", node.action === "FAMILY_PROPOSE" ? "建议已保存，等待本人审阅" : node.action === "FAMILY_REVOKE" ? "家庭授权已撤销" : "家庭授权已保存"]);
    const scopes = Array.isArray(data.scopes) ? data.scopes.map(scope => ({READ_BALANCE: "查看授权余额", PROPOSE_TRANSFER: "提出转账建议", PROPOSE_CARD_LOCK: "提出锁卡建议"} as Record<string,string>)[String(scope)]).filter(Boolean) : [];
    if (scopes.length) fields.push(["授权内容", scopes.join("、")]);
    if (node.action === "FAMILY_PROPOSE") fields.push(["下一步", "由账户本人核对并独立确认，协助人不能代付"]);
  } else if (node.action.startsWith("GOAL_")) {
    fields.push(["目标", text(data.goal_name)]); cash("目标预留", "reserved_minor"); cash("可用金额", "available_balance_minor");
    fields.push(["资金说明", "预留只改变可支配安排，不代表向外付款"]);
  } else if (node.action.startsWith("CARBON_")) {
    fields.push(["权益", text(data.reward_name)]); numeric("本次入账积分", "credited_points"); numeric("兑换消耗积分", "cost_points"); numeric("剩余积分", "wallet_points");
    if (typeof data.estimated_avoided_grams === "number") fields.push(["减排估算", `${data.estimated_avoided_grams} 克（估算值）`]);
    fields.push(["权益说明", "活动积分或权益，不是现金到账；估算减排不是核证碳资产"]);
  } else if (["FX_TRANSFER", "FX_EXCHANGE"].includes(node.action)) {
    const quote = data.fx_quote ? object(data.fx_quote) : data;
    fields.push(["收款对象", text(quote.recipient_username)],
      ["付款金额", amount(quote.debit_minor, typeof quote.source_currency === "string" ? quote.source_currency : "UNKNOWN")],
      ["到账金额", amount(quote.credit_minor, typeof quote.target_currency === "string" ? quote.target_currency : "UNKNOWN")],
      ["平台手续费", quote.fee_minor === 0 ? "0 · 平台费用规则（不是外部银行优惠承诺）" : "费用待核对"]);
  }
  const narrative = typeof data.message === "string" ? customerNarrative(data.message).replace(/\bSET_LIMIT\b/g, "调整限额").replace(/\bUNLOCK\b/g, "解锁").replace(/\bLOCK\b/g, "锁定").replace(/\bREPORT_LOSS\b/g, "挂失") : null;
  const titleId = `business-result-heading-${node.node_id}`;
  const compactLabels = new Set(["收款对象", "金额", "付款金额", "到账金额", "平台手续费", "约定时间", "预约状态", "到账情况", "卡片", "当前状态", "新限额", "申请进度", "交易", "本次金额", "订阅状态", "待收总额", "已收到", "收款进度", "我的联系人称呼", "对应账号", "办理状态", "目标", "目标预留", "权益", "本次入账积分", "兑换消耗积分", "剩余积分", "下一步", "适用范围", "资金说明", "权益说明", "服务名称", "已扣款", "付费月数", "权益有效至", "自动续费", "原付费权益保留", "本次未退款", "本次未扣款"]);
  // Keep outcome/unknown/settlement boundaries visible; only ancillary fields
  // and IDs may move into disclosure. No confirmation inputs live here.
  const visibleFields = fields.filter(([label, value]) => value !== null && (compactLabels.has(label) || ["后续自动扣款金额","自动续费周期（月）"].includes(label)));
  const extraFields = fields.filter(([label, value]) => value !== null && !compactLabels.has(label) && !["后续自动扣款金额","自动续费周期（月）"].includes(label));
  const renderFields = (records: typeof fields) => <dl className="business-result-facts">{records.map(([label,value]) => <div key={label}><dt>{locale === "en" && label === "付款金额" ? "Amount debited" : locale === "en" && label === "到账金额" ? "Amount credited" : customerUiText(label)}</dt><dd>{["收款对象", "目标", "权益", "我的联系人称呼", "对应账号"].includes(label) ? value : localizeFinancialValue(value!, locale)}</dd></div>)}</dl>;
  return <article className="business-result-card" aria-labelledby={titleId}>
    <h3 id={titleId} className="business-result-title"><Icon name={subscriptionAlreadyActive ? "info" : "check"} size={16}/>{paidAccessPreserved ? locale === "en" ? "Existing paid access remains valid" : "原付费权益仍有效" : subscriptionRestored ? locale === "en" ? "Automatic renewal restored" : "自动续费已恢复" : customerUiText(subscriptionAlreadyActive ? "订阅已启用" : customerActionLabel(node.action))}</h3>
    {/* The English registered title, verified facts and receipt intro already
        describe the outcome. Do not echo untranslated free-form tool text. */}
    {renderFields(visibleFields)}
    {(extraFields.length > 0 || locale !== "en" && narrative || typeof data.transfer_id === "string" || ["FX_TRANSFER", "FX_EXCHANGE"].includes(node.action)) && <CustomerResultDisclosure scopeKey={JSON.stringify(data)} context={subscriptionRestored ? locale === "en" ? "Automatic renewal restored" : "自动续费已恢复" : customerUiText(customerActionLabel(node.action))}>
      {locale !== "en" && narrative && <p className="business-result-note">{customerUiText(narrative)}</p>}
      {renderFields(extraFields)}
      {["FX_TRANSFER", "FX_EXCHANGE"].includes(node.action) && <FxQuoteDetails data={data}/>}
      {typeof data.transfer_id === "string" && <p><CustomerText text="转账编号：" />{data.transfer_id}</p>}
    </CustomerResultDisclosure>}
  </article>;
}
