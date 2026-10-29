// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { useState } from "react";
import { dateText, Icon, money, Skeleton } from "./components";
import { useEndpoint } from "./useEndpoint";
import type { MoneyCurrency } from "./typedMoney";
import { customerNarrative } from "./customerPresentation";
import { checkedOwnFxHistory, useOwnHistoryReadGate } from "./ownHistoryRead";
import { AccessibleReloadButton } from "./AccessibleReloadButton";
export function FxOwnStatements({
  token,
  revision,
}: {
  token: string;
  revision: number;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const copy = (zh: string, en: string) => locale === "en" ? en : zh;
  const [requested, setRequested] = useState(false);
  const [currency, setCurrency] = useState<MoneyCurrency>("USD");
  const endpoint = useEndpoint<unknown>(
    requested ? "/api/v2/fx/history" : null,
    token,
    revision,
  );
  const read = useOwnHistoryReadGate(endpoint, token, revision, requested);
  const history = read.current ? checkedOwnFxHistory(endpoint.data) : null;
  const safeScope =
    history?.scope === "OWN_FX_STATEMENTS" &&
    history.counterparty_balances_disclosed === false &&
    history.counterparty_tasks_disclosed === false;
  const rows = safeScope
    ? history.statements.filter((row) => row.currency === currency)
    : [];
  return (
    <details
      className="fx-own-statements"
      onToggle={(event) => {
        if (event.currentTarget.open) setRequested(true);
      }}
    >
      <summary><CustomerText text="按币种查看本人外汇流水" /></summary>
      <div className="fx-statement-toolbar">
        <label><CustomerText text="本人的流水币种" /><select
            aria-label={customerUiText("本人外汇流水币种")}
            value={currency}
            onChange={(event) =>
              setCurrency(event.target.value as MoneyCurrency)
            }
          >
            <option value="USD"><CustomerText text="USD · 美元独立账本" /></option>
            <option value="CNY"><CustomerText text="CNY · 人民币外汇资金腿" /></option>
          </select>
        </label>
        <AccessibleReloadButton
          className="icon-button"
          label={customerUiText("刷新本人外汇流水")}
          scopeKey={read.scopeKey}
          blocked={!token || !read.visible || endpoint.loading}
          onReload={() => { if (read.invalidate()) endpoint.refresh(); }}
        >
          <Icon name="refresh" size={14} />
        </AccessibleReloadButton>
      </div>
      {endpoint.loading && <div role="status"><Skeleton lines={3} compact />
        <p>{copy("正在读取本人外汇流水…", "Loading my FX statements…")}</p>
        <button className="text-button" onClick={() => { if (read.invalidate()) endpoint.cancel(); }}>
          {copy("取消读取", "Cancel read")}
        </button>
      </div>}
      {endpoint.error && (
        <p className="error-inline" role="alert"><CustomerText text="本人外汇流水暂未加载：" />{endpoint.error}
        </p>
      )}
      {endpoint.cancelled && <p role="status">{copy("本次本人外汇流水读取已取消，最新资料尚未核验。请选择刷新。", "This FX-statement read was cancelled. Latest records are unverified. Select Refresh to retry.")}</p>}
      {endpoint.paused && <p role="status">{copy("本人外汇流水已暂停读取，返回后会重新核验。", "FX-statement reading is paused. Records will be rechecked when you return.")}</p>}
      {read.current && !safeScope && (
        <p className="error-inline" role="alert">{copy("本人范围或外汇流水尚未完整核验，未展示金额或空记录结论。请刷新重读。", "The own-account scope or FX statements could not be fully verified. Amounts and empty-result claims are unavailable. Refresh to retry.")}</p>
      )}
      {requested && !read.current && !endpoint.loading && !endpoint.error && !endpoint.cancelled && !endpoint.paused
        && <p role="status">{copy("本人外汇流水尚未核验，请刷新读取。", "My FX statements have not been verified. Select Refresh to read them.")}</p>}
      {safeScope && (
        <div className="fx-own-statement-list">
          {rows.map((row) => (
            <article
              key={row.statement_id}
              data-statement-id={row.statement_id}
              data-currency={row.currency}
            >
              <div>
                <span>
                  {row.direction === "INCOMING"
                    ? customerUiText("转入")
                    : row.direction === "OUTGOING"
                      ? customerUiText("转出")
                      : customerUiText("资金方向待核对")}{" "}
                  · {row.currency}
                </span>
                <strong>
                  {money(row.amount_minor, {
                    currency: row.currency,
                    signed: true,
                  })}
                </strong>
              </div>
              <p>
                {row.kind === "INITIAL_FUNDING"
                  ? customerUiText("美元初始入账（不计人民币收入）")
                  : customerNarrative(row.merchant)}
              </p>
              <small>{dateText(row.occurred_at, true)}</small>
            </article>
          ))}
          {!rows.length && (
            <p className="extension-empty"><CustomerText text="当前币种暂无本人外汇流水。" /></p>
          )}
        </div>
      )}
      <p className="fx-separation-note"><CustomerText text="这里只展示本人分币种资金腿，不把美元流水加入人民币账单收入。" /></p>
    </details>
  );
}
