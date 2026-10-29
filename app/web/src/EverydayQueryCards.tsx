// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText, localizeFinancialValue } from "./customerLanguage";
import { Icon, money, dateText, type IconName } from "./components";
import { customerName } from "./customerPresentation";
import { currentCardIdentity, validSimulationCardNumber } from "./cardIdentity";
import { exactMajorAmount } from "./typedMoney";
import { subscriptionDueLabel } from "./customerContinuation";
import { cardQueryPresentation, cardQueryTitle } from "./cardQueryPresentation";
import { useFxReferenceFreshness } from "./useFxReferenceFreshness";
import { queryRecordKeys } from "./queryRecordIdentity";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { subscriptionDate } from "./subscriptionCommercePresentation";
import { CreditCards } from "./CreditCards";
import { wealthQueryPresentation } from "./wealthQueryPresentation";
import type { TraceNode } from "./types";
import type { ReactNode } from "react";
import "./everydayQueryCards.css";

type Data = Record<string, unknown>;
export type EverydayQueryProps = {
  node: TraceNode;
  onPrompt?: (message: string) => void;
  headingInParent?: boolean;
  queryFocus?: "CARD_LIMIT" | "CARD_BALANCE_LIMIT" | null;
  suppressSharedWealthNotice?: boolean;
};
const queryTitles: Record<string, [string, IconName]> = {
  SUBSCRIPTION_LIST: ["订阅与自动续费", "subscription"],
  CARD_STATUS: ["银行卡状态", "card"],
  WEALTH_COMPARE: ["理财产品对比", "wealth"],
  FX_QUOTE: ["汇率与参考换算", "transfer"],
};
export const supportsEverydayQuery = (action: string) =>
  Object.hasOwn(queryTitles, action);
const object = (value: unknown): Data | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Data)
    : null;
const rows = (value: unknown): Data[] | null =>
  Array.isArray(value) && value.every((item) => object(item))
    ? (value as Data[])
    : null;
const integer = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value);
const text = (value: unknown) =>
  typeof value === "string" && value.trim() ? value : null;
const name = (value: unknown, fallback: string) =>
  text(value) ? customerName(value as string) : fallback;
const date = (value: unknown) =>
  typeof value === "string" && Number.isFinite(Date.parse(value))
    ? dateText(value, true)
    : "日期未返回";
const amount = (value: unknown, currency: unknown) =>
  !integer(value)
    ? "金额未返回"
    : typeof currency !== "string" || !currency.trim()
      ? "币种未返回"
      : `${money(value, { currency })}${currency === "CNY" ? " 人民币" : currency === "USD" ? " 美元" : ""}`;
const level = (value: unknown) =>
  integer(value) && value >= 1 && value <= 5 ? `R${value}` : "等级未返回";
const nonnegativeCount = (value: unknown) =>
  integer(value) && value >= 0 ? String(value) : "未返回";
function Metric({ label, value }: { label: string; value: ReactNode }) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  return (
    <div className="eqc-metric">
      <dt>{label}</dt>
      <dd>{typeof value === "string" ? localizeFinancialValue(value, locale) : value}</dd>
    </div>
  );
}
function Notice({
  children,
  warning = false,
}: {
  children: ReactNode;
  warning?: boolean;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  return (
    <p className={`eqc-notice ${warning ? "eqc-warning" : ""}`}>
      <Icon name="info" size={14} />
      {children}
    </p>
  );
}
function Draft({
  onPrompt,
  message,
  children,
  context,
}: {
  onPrompt?: EverydayQueryProps["onPrompt"];
  message: string;
  children: ReactNode;
  context?: string;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const label = typeof children === "string" ? children : typeof (children as { props?: { text?: unknown } })?.props?.text === "string" ? customerUiText((children as { props: { text: string } }).props.text) : null;
  return onPrompt ? (
    <button
      type="button"
      className="eqc-draft"
      title={customerUiText("只填入对话内容，不会自动办理或确认")}
      aria-label={context && label ? `${label}：${context}` : undefined}
      onClick={() => onPrompt(message)}
    >
      {children}
      <Icon name="arrow" size={14} />
    </button>
  ) : null;
}
const statusLabels: Record<string, string> = {
  ACTIVE: "生效中",
  CANCELLED: "已取消",
  LOCKED: "已临时锁定",
  LOST: "已挂失",
  AVAILABLE: "当前可供核验",
  SUSPENDED: "已暂停",
  EXPIRED: "已到期",
  DISABLED: "已停用",
  CLOSED: "已关闭",
};
function Status({ value, card = false }: { value: unknown; card?: boolean }) {
  const { t: customerUiText } = useCustomerLanguage();
  const state = typeof value === "string" ? value : "";
  const known = Object.hasOwn(statusLabels, state);
  return (
    <span className={`eqc-status ${known ? "" : "eqc-status-unknown"}`}>
      {card && state === "ACTIVE"
        ? customerUiText("正常")
        : known
          ? customerUiText(statusLabels[state])
          : customerUiText("状态待核实")}
    </span>
  );
}

function SubscriptionCards({ node, onPrompt, headingInParent }: EverydayQueryProps) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const RecordHeading = headingInParent ? "h3" : "h4";
  const subscriptions = rows(object(node.result)?.subscriptions);
  if (subscriptions === null)
    return (
      <Notice warning><CustomerText text="订阅清单尚未完整返回，不把缺失信息当作没有订阅。" /></Notice>
    );
  if (!subscriptions.length)
    return <Notice><CustomerText text="当前查询没有订阅记录，本次没有执行取消或关闭。" /></Notice>;
  const sourceLabels: Record<string, string> = {
    BANK_MANDATE: "银行代扣协议",
    MERCHANT_SIM: "商户订阅",
    EXTERNAL_UNCONNECTED: "外部服务 · 未接入取消能力",
  };
  const recordKeys = queryRecordKeys(node, subscriptions, "subscription_id");
  return (
    <>
      <div className="eqc-record-grid eqc-subscription-grid">
        {subscriptions.map((item, index) => {
          const merchant = text(item.merchant);
          const source = text(item.source_type);
          const calendarTime = text(item.calendar_time);
          const dataThrough = text(item.data_through);
          const paidEnd = subscriptionDate(item.entitlement_end_at) ? item.entitlement_end_at : null;
          const months = integer(item.billing_months) && item.billing_months > 0 ? item.billing_months : null;
          const connected =
            source === "BANK_MANDATE" || source === "MERCHANT_SIM";
          const inactiveBankMandate = node.status === "SUCCEEDED" &&
            source === "BANK_MANDATE" && item.auto_renew === false;
          const cancellable =
            connected &&
            item.can_cancel_here === true &&
            item.status === "ACTIVE" && item.auto_renew !== false &&
            Boolean(merchant);
          return (
            <article className="eqc-record" key={recordKeys[index]}>
              <header>
                <RecordHeading>{name(item.merchant, customerUiText("订阅名称未返回"))}</RecordHeading>
                <span className={`eqc-status ${item.auto_renew === false ? "eqc-status-neutral" : item.auto_renew === true && item.status === "ACTIVE" ? "" : "eqc-status-warning"}`}>
                  {item.auto_renew === false ? customerUiText("自动续费已关闭")
                    : item.auto_renew === true && item.status === "ACTIVE" ? locale === "en" ? "Auto-renewal on" : "自动续费开启"
                      : locale === "en" ? "Renewal needs verification" : "续费状态待核实"}
                </span>
              </header>
              <dl className={`eqc-metrics${inactiveBankMandate ? " eqc-subscription-bank-auto-off" : ""}`}>
                <Metric
                  label={inactiveBankMandate ? locale === "en" ? "Recorded amount per cycle" : "原登记每期金额"
                    : months && months > 1 ? locale === "en" ? `Fee per ${months} months` : `每${months}个月费用`
                      : months === 1 ? locale === "en" ? "Monthly fee" : "每月费用" : locale === "en" ? "Fee per billing cycle" : "每期费用"}
                  value={amount(
                    item.amount_minor,
                    item.currency === undefined ? "CNY" : item.currency,
                  )}
                />
                <Metric
                  label={customerUiText("权益有效至")}
                  value={paidEnd ? `${item.entitled === false ? locale === "en" ? "Expired · " : "已到期 · " : ""}${date(paidEnd)}`
                    : locale === "en" ? "Expiry date not provided" : "到期时间暂未提供"}
                />
              </dl>
              <CustomerResultDisclosure scopeKey={JSON.stringify(item)} context={name(item.merchant, customerUiText("该订阅"))}>
              <dl className="eqc-metrics">
                <Metric
                  label={subscriptionDueLabel(item.next_due_at, calendarTime ? Date.parse(calendarTime) : Number.NaN, locale,
                    { dueStatus: item.due_status, status: item.auto_renew === false ? "CANCELLED" : item.status })}
                  value={subscriptionDate(item.next_due_at) ? date(item.next_due_at) : locale === "en" ? "Payment date not provided" : "扣款时间暂未提供"}
                />
                {!paidEnd && <Metric label={locale === "en" ? "Paid access" : "付费权益"}
                  value={locale === "en" ? "Access status needs verification" : "权益状态待核实"}/>}
                {integer(item.monthly_price_minor) && !(months === 1 && item.monthly_price_minor === item.amount_minor)
                  && <Metric label={customerUiText("月费")} value={amount(item.monthly_price_minor,item.currency === undefined ? "CNY" : item.currency)} />}
                {item.status !== "ACTIVE" && Object.hasOwn(statusLabels, String(item.status))
                  && <Metric label={locale === "en" ? "Subscription status" : "订阅状态"} value={customerUiText(statusLabels[String(item.status)])} />}
                <Metric
                  label={customerUiText("扣款来源")}
                  value={customerUiText((source && sourceLabels[source]) || "来源尚未登记")}
                />
                {dataThrough && Number.isFinite(Date.parse(dataThrough)) && (
                  <Metric label={customerUiText("数据截止")} value={date(dataThrough)} />
                )}
              </dl>
              {cancellable && (
                <Draft onPrompt={onPrompt} message={locale === "en" ? `Cancel automatic renewal for ${merchant}` : `取消${merchant}自动续费`} context={name(item.merchant, customerUiText("该订阅"))}><CustomerText text="准备取消" /></Draft>
              )}
              {item.can_renew_here === true && merchant && <Draft onPrompt={onPrompt} message={locale === "en" ? `Renew ${merchant} for one month without automatic renewal` : `续期${merchant}一个月，不自动续费`} context={merchant}><CustomerText text="续期" /></Draft>}
              {item.can_start_here === true && merchant && <Draft onPrompt={onPrompt} message={locale === "en" ? `Restore ${merchant} for one month without automatic renewal` : `恢复${merchant}一个月，不自动续费`} context={merchant}><CustomerText text="恢复订阅" /></Draft>}
              {source === "EXTERNAL_UNCONNECTED" && item.can_cancel_here === false && item.status === "ACTIVE" && merchant && (
                <Draft
                  onPrompt={onPrompt}
                  message={locale === "en" ? `How can I cancel ${merchant} automatic renewal with the provider? Advice only; do not cancel it.` : `如何在服务商关闭${merchant}自动续费？只咨询，不执行取消。`}
                  context={name(item.merchant, customerUiText("该订阅"))}
                ><CustomerText text="了解服务商关闭方式" /></Draft>
              )}
              </CustomerResultDisclosure>
              {source === "EXTERNAL_UNCONNECTED" && item.can_cancel_here === false && item.status === "ACTIVE"
                && <p className="eqc-scope">{customerUiText("需到服务商核实并关闭，本平台未执行取消。")}</p>}
              {!cancellable && item.auto_renew !== false && item.status !== "CANCELLED" && !(source === "EXTERNAL_UNCONNECTED" && item.can_cancel_here === false && item.status === "ACTIVE")
                && <p className="eqc-scope eqc-warning-text">{customerUiText("当前取消能力或订阅状态尚未核实，不直接办理。")}</p>}
            </article>
          );
        })}
      </div>
    </>
  );
}

function CardStatusCards({ node, onPrompt, headingInParent,queryFocus }: EverydayQueryProps) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const presentation = cardQueryPresentation(locale);
  const RecordHeading = headingInParent ? "h3" : "h4";
  const cards = rows(object(node.result)?.cards);
  const credits = rows(object(node.result)?.credit_cards) || [];
  const requestedCurrencies = (value: unknown): ("CNY" | "USD")[] | null => Array.isArray(value) && value.length > 0
    && value.length <= 2 && new Set(value).size === value.length && value.every(item => item === "CNY" || item === "USD")
    ? [...value] as ("CNY" | "USD")[] : null;
  if (cards === null)
    return (
      <Notice warning><CustomerText text="银行卡状态信息尚未完整返回，不能据此认定卡片正常或余额为零。" /></Notice>
    );
  if (!cards.length && !credits.length)
    return (
      <Notice><CustomerText text="当前查询没有匹配的银行卡记录，未执行锁卡、解锁或挂失。" /></Notice>
    );
  if (cards.some(item => Object.hasOwn(item, "requested_balance_currencies") && !requestedCurrencies(item.requested_balance_currencies)))
    return <Notice warning>{locale === "en" ? "The requested balance currencies need verification; no balance is presented." : "余额查询币种范围尚未核实，不能据此展示余额。"}</Notice>;
  const recordKeys = queryRecordKeys(node, cards, "card_id");
  const sharedBalanceScopes = new Map<string, Set<string>>();
  const sharedBalanceAmounts = new Map<string, Set<number>>();
  for (const [index, item] of cards.entries()) {
    const scopes = object(item.balance_scope_refs), balances = object(item.balances_by_currency);
    for (const currency of ["CNY", "USD"]) {
      const scope = text(scopes?.[currency]);
      if (!scope || !integer(balances?.[currency])) continue;
      const key = JSON.stringify([currency, scope]);
      const owners = sharedBalanceScopes.get(key) || new Set<string>();
      owners.add(text(item.card_id) || text(item.card_number) || `record-${index}`);
      sharedBalanceScopes.set(key, owners);
      const amounts = sharedBalanceAmounts.get(key) || new Set<number>();
      amounts.add(balances![currency] as number); sharedBalanceAmounts.set(key, amounts);
    }
  }
  return (
    <>
      <div className="eqc-record-grid eqc-card-grid">
        {cards.map((item, index) => {
          const identity = currentCardIdentity(
            item as unknown as Parameters<typeof currentCardIdentity>[0],
          );
          const tail = /^[0-9a-f]{4}$/i.test(identity.displayTail)
            ? identity.displayTail
            : "未返回";
          const target = validSimulationCardNumber(item.card_number)
            ? `卡号${item.card_number}`
            : /^[0-9a-f]{4}$/i.test(tail)
              ? `尾号${tail}`
              : null;
          const balances = object(item.balances_by_currency);
          const scopes = object(item.balance_scope_refs);
          const supported = Array.isArray(item.supported_currencies) ? item.supported_currencies : [];
          const requested = requestedCurrencies(item.requested_balance_currencies);
          const unsupported = Array.isArray(item.unsupported_balance_currencies) ? item.unsupported_balance_currencies : [];
          const balanceCurrencies: readonly ("CNY" | "USD")[] = requested || (item.currency_mode === "CNY_USD" || item.fx_card === true
            || supported.includes("USD") || balances && Object.hasOwn(balances, "USD") ? ["CNY", "USD"] : ["CNY"]);
          const sharedCurrencies = balanceCurrencies.filter(currency => {
            const scope = text(scopes?.[currency]);
            const key = JSON.stringify([currency, scope]);
            return scope && integer(balances?.[currency]) && (sharedBalanceScopes.get(key)?.size || 0) > 1 && sharedBalanceAmounts.get(key)?.size === 1;
          });
          const balanceConflict = balanceCurrencies.some(currency => {
            const scope = text(scopes?.[currency]);
            return scope && (sharedBalanceAmounts.get(JSON.stringify([currency, scope]))?.size || 0) > 1;
          });
          return (
            <article className="eqc-record eqc-card-record" key={recordKeys[index]} data-balance-currency-count={balanceCurrencies.length}>
              <header>
                <RecordHeading><CustomerText text="银行卡 ·" />{tail === "未返回" ? customerUiText("尾号未返回") : customerUiText(`尾号 ${tail}`)}
                {typeof item.card_tier_name === "string" && typeof item.card_tier_code === "string" && <span className="eqc-card-tier">{item.card_tier_name}</span>}
                </RecordHeading>
                <Status value={item.status} card />
              </header>
              {(!requested || requested.includes("CNY")) && ["CARD_LIMIT","CARD_BALANCE_LIMIT"].includes(queryFocus || "") && <dl className="eqc-card-limit"><Metric label={presentation.limitLabel} value={integer(item.limit_minor) && item.limit_minor>=0 && ["CNY","USD"].includes(String(item.limit_currency===undefined ? "CNY" : item.limit_currency)) ? amount(item.limit_minor,item.limit_currency === undefined ? "CNY" : item.limit_currency) : locale==="en" ? "Limit or currency unverified" : "限额或币种待核实"} /></dl>}
              {queryFocus !== "CARD_LIMIT" && <dl className="eqc-metrics eqc-card-balances">
                {balanceCurrencies.map(currency => <Metric key={currency}
                  label={presentation.balanceLabels[currency]}
                  value={requested && unsupported.includes(currency) && !supported.includes(currency) ? (locale === "en" ? "Not supported by this card" : "此卡不支持该币种")
                    : integer(balances?.[currency]) ? amount(balances![currency], currency) : presentation.balanceMissing} />)}
              </dl>}
              {queryFocus !== "CARD_LIMIT" && sharedCurrencies.length > 0 && <p className="eqc-scope" data-shared-account-balance="true">
                {sharedCurrencies.join(" / ")} {presentation.sharedBadge}
              </p>}
              {balanceConflict && <Notice warning>{presentation.conflictingBalance}</Notice>}
              {balances && Object.keys(balances).some(currency => !["CNY", "USD"].includes(currency))
                && <Notice warning>{presentation.unknownBalanceCurrency}</Notice>}
              <CustomerResultDisclosure scopeKey={JSON.stringify(item)} context={customerUiText(`尾号 ${tail}`)}>
              {queryFocus === "CARD_LIMIT" ? <dl className="eqc-metrics eqc-card-balances">{balanceCurrencies.map(currency=><Metric key={currency} label={presentation.balanceLabels[currency]} value={integer(balances?.[currency]) ? amount(balances![currency],currency) : presentation.balanceMissing} />)}</dl> : queryFocus === "CARD_BALANCE_LIMIT" || requested && !requested.includes("CNY") ? null : <dl className="eqc-card-limit">
                <Metric
                  label={presentation.limitLabel}
                  value={amount(
                    item.limit_minor,
                    item.limit_currency === undefined
                      ? "CNY"
                      : item.limit_currency,
                  )}
                />
              </dl>}
              {onPrompt && target && ["ACTIVE", "LOCKED"].includes(String(item.status)) && (
                <div className="eqc-card-services" aria-label={`${presentation.moreServices}: ${customerUiText(`尾号 ${tail}`)}`}>
                  {item.status === "ACTIVE" ? (
                    <Draft onPrompt={onPrompt} message={locale === "en" ? `Temporarily lock ${validSimulationCardNumber(item.card_number) ? `card ${item.card_number}` : `card ending ${tail}`}` : `临时锁卡，${target}`} context={customerUiText(`尾号 ${tail}`)}><CustomerText text="准备临时锁卡" /></Draft>
                  ) : (
                    <Draft onPrompt={onPrompt} message={locale === "en" ? `Unlock ${validSimulationCardNumber(item.card_number) ? `card ${item.card_number}` : `card ending ${tail}`}` : `解锁，${target}`} context={customerUiText(`尾号 ${tail}`)}><CustomerText text="准备解锁" /></Draft>
                  )}
                </div>
              )}
              {item.status === "LOST" && (
                <p className="eqc-scope"><CustomerText text="挂失卡不能按临时锁定方式解锁；补卡需另行核对办理条件。" /></p>
              )}
              </CustomerResultDisclosure>
              {!Object.hasOwn(statusLabels, String(item.status)) && (
                <Notice warning><CustomerText text="当前卡状态没有被明确核实，不据此建议解锁或付款。" /></Notice>
              )}
            </article>
          );
        })}
      </div>
      {credits.length > 0 && <CreditCards node={{node_id: `${node.node_id}:credit`, action: "CREDIT_VIEW", result: {cards: credits}}} onPrompt={onPrompt}/>}
    </>
  );
}

function WealthCards({ node, onPrompt, suppressSharedWealthNotice }: EverydayQueryProps) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const result = object(node.result);
  const products = rows(result?.products);
  if (products === null)
    return (
      <Notice warning><CustomerText text="理财产品资料尚未完整返回，未据此做出投资建议或交易。" /></Notice>
    );
  if (!products.length)
    return <Notice><CustomerText text="当前查询没有可展示的产品记录，本次没有申购或赎回。" /></Notice>;
  const reasons: Record<string, string> = {
    RISK_ASSESSMENT_REQUIRED: locale === "en" ? "Complete your own risk assessment." : "需本人独立完成风险测评",
    RISK_PROFILE_EXPIRED: locale === "en" ? "Your risk assessment has expired." : "风险测评已过期",
    PRODUCT_RISK_EXCEEDS_PROFILE: locale === "en" ? "The product risk exceeds your current assessment." : "产品风险等级超出当前测评",
    PRODUCT_UNAVAILABLE: locale === "en" ? "This product is currently unavailable for trading." : "产品当前不可交易",
  };
  const shared = wealthQueryPresentation(node, locale);
  const recordKeys = queryRecordKeys(node, products, "product_id");
  // Suitability is a returned bank fact, not a personalized ranking inferred
  // from a product name. Keep every product and every risk restriction visible.
  const ordered = products.map((item,index)=>({item,index})).sort((left,right)=>
    Number(right.item.suitable === true && right.item.status === "AVAILABLE") - Number(left.item.suitable === true && left.item.status === "AVAILABLE"));
  const productCards=ordered.map(({item, index}) => {
          const terms = object(item.terms);
          const rawName = text(item.name);
          const reasonCodes = Array.isArray(item.suitability_reason_codes)
            ? [...new Set(item.suitability_reason_codes)].filter(code => !shared.commonRiskCodes.includes(String(code)))
            : [];
          const validPrice = integer(item.unit_price_minor) && item.unit_price_minor > 0 && ["CNY", "USD"].includes(String(item.currency));
          const unverified = [
            !validPrice ? locale === "en" ? "Price or currency needs verification; not ready for a trade." : "价格或币种待核实，暂不能据此办理。" : null,
            item.status !== "AVAILABLE" && !reasonCodes.includes("PRODUCT_UNAVAILABLE")
              ? locale === "en" ? "Product state needs verification; not ready for a trade." : "产品状态待核实，暂不能据此办理。" : null,
            item.suitable !== true && item.suitable !== false ? customerUiText("适配结果尚未返回，不能自行认定可交易。") : null,
            item.suitable === false && !reasonCodes.length && !shared.commonRiskCodes.length ? customerUiText("当前不符合交易准备条件。") : null,
          ].filter(Boolean);
          return (
            <article className="eqc-record" key={recordKeys[index]}>
              <header>
                <h4>{name(item.name, customerUiText("产品名称未返回"))}</h4>
                {item.status !== "AVAILABLE" && <Status value={item.status} />}
              </header>
              <dl className="eqc-metrics">
                <Metric label={customerUiText("产品风险等级")} value={level(item.risk_level)} />
                <Metric label={customerUiText("产品期限")} value={text(terms?.term) || customerUiText("期限未返回")} />
                <Metric label={customerUiText("赎回到账规则")} value={text(terms?.redemption_timing) || customerUiText("规则未返回")} />
              </dl>
              {terms?.capital_guaranteed === false && !shared.sharedCapitalRisk && <p className="eqc-scope"><CustomerText text="本金无保证。" /></p>}
              {unverified.length > 0 && <p className="eqc-scope eqc-warning-text">{unverified.join(locale === "en" ? " " : "")}</p>}
              {reasonCodes.length > 0 && <ul className="eqc-reasons">{reasonCodes.map((code, key) => <li key={key}>{customerUiText(typeof code === "string" && reasons[code]
                || (locale === "en" ? "Suitability restrictions need verification; query again or complete your own assessment." : "有待核实的适配限制，请重新查询或完成本人测评。"))}</li>)}</ul>}
              <CustomerResultDisclosure scopeKey={JSON.stringify(item)} context={name(item.name, customerUiText("该产品"))}>
              <dl className="eqc-metrics">
                <Metric label={customerUiText("当前每份参考价格")} value={amount(item.unit_price_minor, item.currency)} />
                <Metric
                  label={customerUiText("本人持有份额")}
                  value={nonnegativeCount(item.holding_units)}
                />
                <Metric
                  label={customerUiText("平台手续费")}
                  value={amount(terms?.fee_minor, item.currency)}
                />
              </dl>
              {rawName && (
                <Draft
                  onPrompt={onPrompt}
                  message={locale === "en" ? `Explain the terms and purchase conditions for ${rawName}. Advice only; do not submit a trade.` : `了解${rawName}的产品条款与申购条件，只咨询，不提交交易。`}
                  context={name(item.name, customerUiText("该产品"))}
                ><CustomerText text="了解条款与申购条件" /></Draft>
              )}
              </CustomerResultDisclosure>
            </article>
          );
        });
  const hiddenRestrictions=ordered.slice(3).some(({item})=>item.status!=="AVAILABLE"
    || !integer(item.unit_price_minor) || item.unit_price_minor<=0 || !["CNY","USD"].includes(String(item.currency))
    || item.suitable!==true && item.suitable!==false
    || item.suitable===false && (!shared.commonRiskCodes.length || !Array.isArray(item.suitability_reason_codes)
      || item.suitability_reason_codes.some(code=>!shared.commonRiskCodes.includes(String(code)))));
  return (
    <>
      {shared.notice && !suppressSharedWealthNotice && <p className="eqc-scope eqc-wealth-shared-notice">{shared.notice}</p>}
      <div className="eqc-record-grid eqc-wealth-comparison">{productCards.slice(0,3)}</div>
      {hiddenRestrictions && <Notice warning>{locale === "en" ? "Other products include unsuitable, unavailable or unverified conditions. Check their restrictions in the full list." : "其他产品包含不适配、不可用或尚未核实的条件，请在完整清单里查看限制。"}</Notice>}
      {productCards.length>3 && <CustomerResultDisclosure scopeKey={JSON.stringify(products)} label={locale === "en" ? "More products and restrictions" : "更多产品与适配限制"}>
        <div className="eqc-record-grid eqc-wealth-comparison">{productCards.slice(3)}</div>
      </CustomerResultDisclosure>}
    </>
  );
}

function FxReferenceCard({ node, onPrompt }: EverydayQueryProps) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const freshness = useFxReferenceFreshness(node);
  const result = object(node.result);
  const rate = object(result?.market_rate);
  if (!rate)
    return (
      <Notice warning><CustomerText text="参考汇率尚未返回，不使用猜测价格或模型估计换算。" /></Notice>
    );
  const rateText = text(rate.rate);
  const rateValid =
    rate.base_currency === "USD" &&
    rate.quote_currency === "CNY" &&
    Boolean(
      rateText &&
      /^\d+(?:\.\d+)?$/.test(rateText) &&
      Number(rateText) >= 1 &&
      Number(rateText) <= 20,
    );
  const expiry =
    typeof rate.expires_at === "string" ? Date.parse(rate.expires_at) : NaN;
  const expired = freshness.expired;
  const conversion = object(result?.conversion);
  const fee = object(rate.fee_policy);
  const source =
    rate.provider === "FRANKFURTER_ECB"
      ? "ECB 日频参考数据"
      : rate.provider === "TEST_STUB"
        ? "测试参考数据"
        : "数据来源未登记";
  const sourceCurrency = text(conversion?.source_currency),
    targetCurrency = text(conversion?.target_currency);
  const inputAmount = conversion?.input_amount_minor;
  const canPrepareSourceDraft = freshness.canPrepare;
  const currencyName: Record<string, string> = { USD: "美元", CNY: "人民币" };
  return (
    <>
      <article className="eqc-record eqc-fx-reference">
        <h4><CustomerText text="美元 / 人民币（USD / CNY）参考汇率" /></h4>
        <strong className="eqc-reference-price">
          {rateValid ? `1 美元 = ${rateText} 人民币` : customerUiText("参考汇率或币种未完整返回")}
        </strong>
        {(expired || !Number.isFinite(expiry) || !rateValid) && (
          <Notice warning>
            <span role={freshness.warning === "EXPIRED" ? "alert" : "status"}>
            {expired
              ? customerUiText("参考数据已过期，以下仅保留当时记录；办理前请重新查询。")
              : customerUiText("参考数据完整性或有效期限尚未核实，不能直接用于办理。")}
            </span>
          </Notice>
        )}
        {freshness.warning && freshness.warning !== "EXPIRED" && <Notice warning>
          <span role="alert">{locale === "en"
            ? "This reference record or its amount conditions have changed. Query again and review the details; no exchange draft was filled."
            : "这条参考记录或金额条件已变化，请重新查询后核对；未填入换汇草稿。"}</span>
        </Notice>}
        {conversion && (
          <div className="eqc-conversion">
            <h4>
              {conversion.amount_role === "TARGET"
                ? customerUiText("按目标金额参考估算所需资金")
                : customerUiText("参考金额换算")}
            </h4>
            <dl className="eqc-metrics">
              <Metric
                label={
                  conversion.amount_role === "TARGET"
                    ? customerUiText("输入目标金额")
                    : customerUiText("原输入金额")
                }
                value={amount(
                  conversion.input_amount_minor,
                  conversion.input_currency,
                )}
              />
              <Metric
                label={
                  conversion.amount_role === "TARGET"
                    ? customerUiText("参考所需资金")
                    : customerUiText("参考可换金额")
                }
                value={amount(
                  conversion.output_amount_minor,
                  conversion.output_currency,
                )}
              />
            </dl>
            <p className="eqc-scope"><CustomerText text="换算金额由服务端参考规则返回，不是实际扣款、到账或已确认的成交价格。" /></p>
          </div>
        )}
        {rate.provider === "TEST_STUB" && (
          <Notice warning><CustomerText text="当前为测试来源，不作为外部银行实时价格。" /></Notice>
        )}
        <CustomerResultDisclosure scopeKey={JSON.stringify(result)} context={customerUiText("美元 / 人民币（USD / CNY）参考汇率")}>
        <dl className="eqc-metrics">
          <Metric label={customerUiText("市场观察日")} value={date(rate.market_date)} />
          <Metric label={customerUiText("参考数据来源")} value={source} />
          <Metric label={customerUiText("数据读取时间")} value={date(rate.fetched_at)} />
          <Metric label={customerUiText("参考记录有效至")} value={date(rate.expires_at)} />
          <Metric label={customerUiText("平台费用规则")} value={integer(fee?.fee_minor) && fee.fee_minor === 0 ? customerUiText("已记录为零手续费") : customerUiText("费用尚未完整返回，以办理方案为准")} />
        </dl>
        <div className="eqc-actions">
          <Draft onPrompt={onPrompt} message={customerUiText("查询美元兑人民币汇率")}><CustomerText text="重新查询汇率" /></Draft>
          {canPrepareSourceDraft && (
            <Draft
              onPrompt={onPrompt ? message => { if (freshness.checkCurrent()) onPrompt(message); } : undefined}
              message={`将${exactMajorAmount(inputAmount as number)}${currencyName[sourceCurrency!]}兑换成${currencyName[targetCurrency!]}`}
            ><CustomerText text="准备本人换汇" /></Draft>
          )}
        </div>
        </CustomerResultDisclosure>
      </article>
    </>
  );
}

export function EverydayQueryCards(props: EverydayQueryProps) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const { node } = props;
  if (!supportsEverydayQuery(node.action)) return null;
  const [title, icon] = queryTitles[node.action];
  const heading = node.action === "CARD_STATUS"
    ? node.status === "SUCCEEDED" ? cardQueryTitle(node.result, locale) : cardQueryPresentation(locale).title
    : customerUiText(title);
  return (
    <section className="everyday-query-cards" aria-label={props.headingInParent ? undefined : heading}>
      {!props.headingInParent && <div className="eqc-heading">
        <Icon name={icon} size={19} />
        <h3>{heading}</h3>
      </div>}
      {node.status !== "SUCCEEDED" ? (
        <Notice warning>
          {node.status === "UNKNOWN"
            ? customerUiText("这次查询的结果尚未确定，不能把旧资料当作当前结果。")
            : ["FAILED", "CANCELLED", "CLOSED"].includes(node.status)
              ? customerUiText("这次查询尚未完成，暂没有已确认的业务资料。")
              : customerUiText("正在核对查询信息，结果返回后会直接展示在这里。")}
        </Notice>
      ) : node.action === "SUBSCRIPTION_LIST" ? (
        <SubscriptionCards {...props} />
      ) : node.action === "CARD_STATUS" ? (
        <CardStatusCards {...props} />
      ) : node.action === "WEALTH_COMPARE" ? (
        <WealthCards {...props} />
      ) : (
        <FxReferenceCard {...props} />
      )}
    </section>
  );
}
