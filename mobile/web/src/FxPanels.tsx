// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { useState } from "react";
import { dateText, Icon, money, Skeleton, StateBadge } from "./components";
import { useEndpoint } from "./useEndpoint";
import { minorFromYuan } from "./admin/moneyInput";
import { exactMajorAmount, isMoneyCurrency, type MoneyCurrency } from "./typedMoney";
import type { Card, JsonValue } from "./types";
import { FxOwnStatements } from "./FxOwnStatements";
import { sourceCardSnapshot, currentCardIdentity } from "./cardIdentity";

export interface FxWalletSnapshot {
  synthetic: boolean;
  wallets: {
    wallet_id: string;
    currency: "USD";
    balance_minor: number;
    version: number;
    card_id: string;
    card_last4: string | null;
    card_status: string | null;
    profile_version: number;
    ledger_balance_minor: number;
    balanced: boolean;
  }[];
  cny_accounts: {
    account_id: string;
    currency: "CNY";
    balance_minor: number;
    version: number;
  }[];
  fees: { fee_minor: number; source: string };
}
export interface FxMarketRate {
  base_currency: "USD";
  quote_currency: "CNY";
  rate: string;
  market_date: string;
  provider: string;
  source_url: string;
  fetched_at: string;
  expires_at: string;
  max_age_days: number;
  reference_only: boolean;
  simulated_execution_price: boolean;
  fee_policy: { fee_minor: number; source: string };
}
const currencyNames = { CNY: "人民币", USD: "美元" };
export function FxPanels({
  token,
  revision,
  onPrompt,
  ownedCards = [],
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
  ownedCards?: Card[];
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const wallet = useEndpoint<FxWalletSnapshot>(
    "/api/v2/fx/wallets",
    token,
    revision,
  );
  const [rateRequested, setRateRequested] = useState(false);
  const market = useEndpoint<FxMarketRate>(
    rateRequested ? "/api/v2/fx/rate" : null,
    token,
  );
  const [source, setSource] = useState<MoneyCurrency>("USD");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const target: MoneyCurrency = source === "USD" ? "CNY" : "USD";
  const wallets = wallet.data?.wallets || [];
  function prepare() {
    setError("");
    try {
      const minor = minorFromYuan(amount);
      if (minor <= 0) throw new Error("请输入大于零的明确换汇金额。");
      if (!wallets.length)
        throw new Error(
          "当前没有已读取的双币卡美元钱包，不能直接生成可执行资金方案。",
        );
      onPrompt(
        `将${exactMajorAmount(minor)}${currencyNames[source]}兑换成${currencyNames[target]}`,
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "换汇需求未通过输入核对",
      );
    }
  }
  return (
    <section className="panel fx-panel" aria-label={customerUiText("美元钱包与换汇资料")}>
      <div className="small-panel-heading">
        <div>
          <h2><CustomerText text="美元钱包与换汇" /></h2>
          <span className="fx-section-label"><CustomerText text="独立币种账本 · 分币种资金" /></span>
        </div>
        <button
          className="icon-button"
          aria-label={customerUiText("刷新本人美元钱包")}
          onClick={wallet.refresh}
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>
      <div className="fx-panel-body">
        {wallet.loading && !wallet.data && <Skeleton lines={3} compact />}
        {wallet.error && (
          <p className="error-inline" role="alert"><CustomerText text="美元钱包暂未加载：" />{wallet.error}
          </p>
        )}
        {wallet.data &&
          (wallets.length ? (
            wallets.map((item) => {
              const linked = ownedCards.find(
                (card) => card.card_id === item.card_id,
              );
              const currentIdentity = linked
                ? currentCardIdentity(linked)
                : null;
              return (
                <article
                  className="fx-wallet-card"
                  key={item.wallet_id}
                  data-currency={item.currency}
                >
                  <div>
                    <span><CustomerText text="USD · 美元钱包" /></span>
                    <StateBadge
                      state={item.card_status || "UNKNOWN"}
                      label={!item.card_status ? customerUiText("关联卡状态未核验") : undefined}
                    />
                  </div>
                  <strong>
                    {money(item.balance_minor, { currency: item.currency })}
                  </strong>
                  <p>
                    {currentIdentity?.number
                      ? `当前关联银行卡标识 ${currentIdentity.number}`
                      : `关联卡旧兼容尾号 ${item.card_last4 || customerUiText("未返回")}`}
                  </p>
                  <small>
                    {item.balanced ? customerUiText("美元账本核对一致") : customerUiText("美元账本待核验")}<CustomerText text="· 不计入人民币可用余额" /></small>
                </article>
              );
            })
          ) : (
            <p className="extension-empty"><CustomerText text="当前没有美元钱包。人民币现金仍按原账户单独展示。" /></p>
          ))}
        <p className="fx-separation-note"><CustomerText text="USD与CNY不直接相加；人民币理财、目标预留和原账单统计仍使用CNY口径。" /></p>
        <button
          className="button secondary compact fx-rate-query"
          onClick={() => {
            if (rateRequested) market.refresh();
            else setRateRequested(true);
          }}
          aria-label={customerUiText("只读查询USD兑CNY参考汇率")}
        >
          <Icon name="eye" size={15} />
          {market.loading ? customerUiText("正在查询参考数据…") : customerUiText("只读查询 USD/CNY 参考汇率")}
        </button>
        {market.loading && !market.data && <Skeleton lines={2} compact />}
        {market.error && (
          <p className="error-inline" role="alert"><CustomerText text="参考汇率未取得：" />{market.error}<CustomerText text="。不会使用猜测汇率。" /></p>
        )}
        {market.data && (
          <div className="fx-market-rate" aria-label={customerUiText("当前返回的参考汇率")}>
            <strong>1 USD = {market.data.rate} CNY</strong>
            <span><CustomerText text="市场观察日" />{market.data.market_date} · {market.data.provider}
            </span>
            <small><CustomerText text="数据读取" />{dateText(market.data.fetched_at, true)}</small>
            <small><CustomerText text="参考数据缓存有效至" />{dateText(market.data.expires_at, true)}
            </small>
            <p><CustomerText text="参考数据用于平台参考价格，不是银行实时成交承诺；执行必须重新核对120秒有效的资金方案。" /></p>
            <span><CustomerText text="手续费" />{" "}
              {market.data.fee_policy.fee_minor === 0
                ? customerUiText("0（平台费用规则，非外部银行优惠）")
                : customerUiText("以方案返回的费用为准")}
            </span>
            <SourceReference url={market.data.source_url} />
          </div>
        )}
        <details className="fx-exchange-draft">
          <summary><CustomerText text="准备本人换汇需求" /></summary>
          <div>
            <label><CustomerText text="原输入金额币种" /><select
                aria-label={customerUiText("本人换汇输入币种")}
                value={source}
                onChange={(event) =>
                  setSource(event.target.value as MoneyCurrency)
                }
              >
                <option value="USD"><CustomerText text="USD · 美元资金" /></option>
                <option value="CNY"><CustomerText text="CNY · 人民币资金" /></option>
              </select>
            </label>
            <label><CustomerText text="原输入金额" /><input
                aria-label={customerUiText("本人换汇原输入金额")}
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder={customerUiText("请输入明确金额")}
              />
            </label>
            <p><CustomerText text="输入" />{source}<CustomerText text="，兑换为" />{target}<CustomerText text="。不从参考价预算收款金额，实际扣款和入账以报价方案为准。" /></p>
            {error && (
              <p className="error-inline" role="alert">
                {error}
              </p>
            )}
            <button
              className="button secondary compact"
              disabled={!amount.trim() || !wallets.length}
              onClick={prepare}
            ><CustomerText text="生成本人换汇需求" /><Icon name="arrow" size={14} />
            </button>
          </div>
        </details>
        <p className="fx-unsupported-note"><CustomerText text="本期美元不支持定时转账、AA或家庭委托，不能自动改为人民币操作。" /></p>
        <FxOwnStatements token={token} revision={revision} />
      </div>
    </section>
  );
}
function SourceReference({ url }: { url: unknown }) {
  const { t: customerUiText } = useCustomerLanguage();
  if (typeof url !== "string") return null;
  let valid = false;
  try {
    const parsed = new URL(url);
    valid = ["http:", "https:"].includes(parsed.protocol);
  } catch {
    /* Unknown provenance is not made up. */
  }
  return valid ? (
    <a
      className="fx-source-reference"
      href={url}
      target="_blank"
      rel="noreferrer"
    ><CustomerText text="查看参考数据来源" /><Icon name="arrow" size={12} />
    </a>
  ) : (
    <small><CustomerText text="来源链接未返回可访问地址" /></small>
  );
}
export function FxQuoteDetails({
  data,
  technical = false,
}: {
  data: JsonValue | undefined | null;
  technical?: boolean;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const quote =
    data.fx_quote &&
    typeof data.fx_quote === "object" &&
    !Array.isArray(data.fx_quote)
      ? data.fx_quote
      : data;
  if (
    !quote.source_currency ||
    !quote.target_currency
  )
    return null;
  const executionExpiry =
    typeof quote.expiry === "string"
      ? quote.expiry
      : typeof quote.expires_at === "string"
        ? quote.expires_at
        : undefined;
  const sourceIdentity = sourceCardSnapshot(quote);
  const amount = (key: string, currency: JsonValue | undefined) =>
    typeof quote[key] === "number" && Number.isSafeInteger(quote[key]) && isMoneyCurrency(currency)
      ? money(quote[key] as number, { currency })
      : customerUiText("金额或币种未返回");
  // Presentation-only route descriptions use verified currency enums, not
  // arbitrary provider notices, and never imply a preview has already posted.
  const fundingNotice = quote.source_currency === "USD" ? "Funding source: USD wallet."
    : quote.source_currency === "CNY" ? "Funding source: CNY account."
    : "Funding currency was not verified. Review the original proposal before confirming.";
  const settlementNotice = quote.target_currency === "USD" ? "Receiving currency: USD, accounted for separately from CNY."
    : quote.target_currency === "CNY" ? "Receiving currency: CNY, accounted for separately from USD."
    : "Receiving currency was not verified. Do not assume conversion or receipt.";
  return (
    <div
      className="fx-quote-details"
      aria-label={customerUiText("外汇资金方案实际币种与金额")}
      data-quote-id={typeof quote.quote_id === "string" ? quote.quote_id : undefined}
    >
      <h4>
        <Icon name="transfer" size={16} /><CustomerText text="金额与币种" /></h4>
      <dl>
        <div>
          <dt><CustomerText text="实际入账对象" /></dt>
          <dd>
            {typeof quote.recipient_username === "string"
              ? quote.recipient_username
              : customerUiText("未返回")}
          </dd>
        </div>
        <div>
          <dt>
            {technical ? customerUiText("实际付款卡（记录时点）") : customerUiText("付款卡尾号")}
          </dt>
          <dd>
            {sourceIdentity.number || sourceIdentity.legacyTail
              ? technical ? sourceIdentity.number || sourceIdentity.legacyTail : sourceIdentity.displayTail || sourceIdentity.legacyTail || customerUiText("尾号未返回")
              : customerUiText("未返回，不推测来源卡")}
          </dd>
        </div>
        {technical && sourceIdentity.displayTail && (
          <div>
            <dt><CustomerText text="付款卡HEX尾码（当时快照）" /></dt>
            <dd>{sourceIdentity.displayTail}</dd>
          </div>
        )}
        <div>
          <dt><CustomerText text="原输入金额" /></dt>
          <dd>{amount("requested_amount_minor", quote.requested_currency)}</dd>
        </div>
        <div>
            <dt><CustomerText text="付款资金扣减 ·" />{isMoneyCurrency(quote.source_currency) ? quote.source_currency : customerUiText("币种未核验")}</dt>
          <dd>{amount("debit_minor", quote.source_currency)}</dd>
        </div>
        <div>
            <dt><CustomerText text="收款资金入账 ·" />{isMoneyCurrency(quote.target_currency) ? quote.target_currency : customerUiText("币种未核验")}</dt>
          <dd>{amount("credit_minor", quote.target_currency)}</dd>
        </div>
        <div>
          <dt><CustomerText text="USD/CNY参考基准" /></dt>
          <dd>
            {typeof quote.rate === "string"
              ? `1 USD = ${quote.rate} CNY`
              : customerUiText("未返回汇率，不推算")}
          </dd>
        </div>
        <div>
          <dt><CustomerText text="平台手续费" /></dt>
          <dd>
            {quote.fee_minor === 0
              ? customerUiText("0 · 平台费用规则（不是外部银行优惠承诺）")
              : customerUiText("费用待核对")}
          </dd>
        </div>
        <div>
          <dt><CustomerText text="市场观察日" /></dt>
          <dd>
            {typeof quote.market_date === "string"
              ? quote.market_date
              : customerUiText("未返回")}
          </dd>
        </div>
        <div>
          <dt><CustomerText text="数据来源" /></dt>
          <dd>
            {typeof quote.provider === "string" ? quote.provider : customerUiText("未返回")}
          </dd>
        </div>
        {technical && <div>
          <dt><CustomerText text="参考数据读取时间" /></dt>
          <dd>
            {typeof quote.fetched_at === "string"
              ? dateText(quote.fetched_at, true)
              : customerUiText("未返回")}
          </dd>
        </div>}
        <div>
          <dt><CustomerText text="成交方案有效截止" /></dt>
          <dd>
            {executionExpiry ? dateText(executionExpiry, true) : customerUiText("期限未返回")}
          </dd>
        </div>
        {technical && <div>
          <dt><CustomerText text="成交方案编号" /></dt>
          <dd>{String(quote.quote_id)}</dd>
        </div>}
      </dl>
      {locale === "en" ? <p>{fundingNotice}</p> : typeof quote.funding_notice === "string" && (
        <p>{quote.funding_notice}</p>
      )}
      {locale === "en" ? <p>{settlementNotice}</p> : typeof quote.settlement_notice === "string" && (
        <p>{quote.settlement_notice}</p>
      )}
      <p><CustomerText text="请核对付款与到账币种、金额及费用。参考汇率不是银行实时成交价。" /></p>
      <SourceReference url={quote.source_url} />
    </div>
  );
}
