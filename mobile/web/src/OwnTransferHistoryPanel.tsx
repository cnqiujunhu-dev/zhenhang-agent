// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { dateText, Icon, money, Skeleton, StateBadge } from "./components";
import { useEndpoint } from "./useEndpoint";
import { customerName } from "./customerPresentation";
import type { OwnTransferReceipt } from "./types";
import { checkedOwnTransferHistory, useOwnHistoryReadGate } from "./ownHistoryRead";
import { AccessibleReloadButton } from "./AccessibleReloadButton";

export function OwnTransferHistoryPanel({
  token,
  revision,
}: {
  token: string;
  revision: number;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const copy = (zh: string, en: string) => locale === "en" ? en : zh;
  const endpoint = useEndpoint<unknown>("/api/v2/transfers/history", token, revision);
  const read = useOwnHistoryReadGate(endpoint, token, revision);
  const history = read.current ? checkedOwnTransferHistory(endpoint.data) : null;
  const safeScope =
    history?.scope === "OWN_TRANSFER_RECEIPTS" &&
    history.counterparty_balances_disclosed === false &&
    history.counterparty_tasks_disclosed === false;
  const rows = safeScope ? history.receipts : [];
  function amount(row: OwnTransferReceipt) {
    const signed =
      row.direction === "INCOMING"
        ? row.amount_minor
        : row.direction === "OUTGOING"
          ? -row.amount_minor
          : row.amount_minor;
    return money(signed, { currency: row.currency, signed: true });
  }
  return (
    <div className="own-transfer-history-panel" aria-label={customerUiText("本人收付款记录")}>
      <div className="own-transfer-history-heading">
        <div>
          <strong><CustomerText text="本人收付款记录" /></strong>
          <p><CustomerText text="查看本账户的转出和转入记录。" /></p>
        </div>
        <AccessibleReloadButton
          className="icon-button"
          label={customerUiText("刷新本人收付款记录")}
          scopeKey={read.scopeKey}
          blocked={!token || !read.visible || endpoint.loading}
          onReload={() => { if (read.invalidate()) endpoint.refresh(); }}
        >
          <Icon name="refresh" size={15} />
        </AccessibleReloadButton>
      </div>
      <p className="own-transfer-history-boundary"><CustomerText text="仅当前账号的最小交易记录，不展示对方余额，也不提供对方任务链接。" /></p>
      {endpoint.loading && <div role="status"><Skeleton lines={4} compact />
        <p>{copy("正在读取本人收付款记录…", "Loading my payment records…")}</p>
        <button className="text-button" onClick={() => { if (read.invalidate()) endpoint.cancel(); }}>
          {copy("取消读取", "Cancel read")}
        </button>
      </div>}
      {endpoint.error && (
        <p className="error-inline" role="alert"><CustomerText text="收付款记录暂未加载：" />{endpoint.error}
        </p>
      )}
      {endpoint.cancelled && <p role="status">{copy("本次本人收付款记录读取已取消，最新资料尚未核验。请选择刷新。", "This payment-history read was cancelled. Latest records are unverified. Select Refresh to retry.")}</p>}
      {endpoint.paused && <p role="status">{copy("本人收付款记录已暂停读取，返回后会重新核验。", "Payment-history reading is paused. Records will be rechecked when you return.")}</p>}
      {read.current && !safeScope && (
        <p className="error-inline" role="alert">{copy("本人范围或交易资料尚未完整核验，未展示金额、状态或空记录结论。请刷新重读。", "The own-account scope or payment records could not be fully verified. Amounts, statuses and empty-result claims are unavailable. Refresh to retry.")}</p>
      )}
      {!read.current && !endpoint.loading && !endpoint.error && !endpoint.cancelled && !endpoint.paused
        && <p role="status">{copy("本人收付款记录尚未核验，请刷新读取。", "My payment records have not been verified. Select Refresh to read them.")}</p>}
      {safeScope &&
        (rows.length ? (
          <div className="own-transfer-history-list">
            {rows.map((row) => {
              const postedVerified =
                row.status === "POSTED" && row.settlement_verified === true && row.transfer_channel !== "EXTERNAL_SIM";
              const external = row.transfer_channel === "EXTERNAL_SIM";
              return (
                <article
                  key={`${row.transfer_id}:${row.direction}:${row.currency}`}
                  className={`own-transfer-history-item ${row.direction.toLowerCase()}`}
                  data-transfer-id={row.transfer_id}
                  data-transfer-direction={row.direction}
                  data-settlement-verified={String(postedVerified)}
                  data-bookkeeping-verified={String(row.settlement_verified)}
                  data-currency={row.currency}
                  data-own-leg={`${row.transfer_id}:${row.direction}:${row.currency}`}
                >
                  <header>
                    <span>
                      <b className="history-currency-label">{row.currency}</b>{" "}
                      {row.direction === "INCOMING"
                        ? customerUiText("转入")
                        : row.direction === "OUTGOING"
                          ? customerUiText("转出")
                          : customerUiText("交易方向未返回")}
                    </span>
                    <strong
                      className={
                        postedVerified && row.direction === "INCOMING"
                          ? "verified-incoming"
                          : ""
                      }
                    >
                      {amount(row)}
                    </strong>
                  </header>
                  <p className="own-transfer-counterparty">
                    {row.direction === "INCOMING"
                      ? customerUiText("来自")
                      : row.direction === "OUTGOING"
                        ? customerUiText("转至")
                        : customerUiText("交易对方")}{" "}
                    <strong>
                      {customerName(row.counterparty || customerUiText("未返回"))}
                    </strong>
                  </p>
                  <div className="own-transfer-history-tags">
                    <span>
                      {row.transfer_channel === "INTERNAL_BANK"
                        ? customerUiText("平台内互转")
                        : row.transfer_channel === "EXTERNAL_SIM"
                          ? customerUiText("外部清算记录")
                          : ["FX_SIM", "INTERNAL_BANK_FX"].includes(
                                row.transfer_channel,
                              )
                            ? customerUiText("外汇双币记账")
                            : `${copy("渠道：", "Channel: ")}${row.transfer_channel}`}
                    </span>
                    <StateBadge
                      state={row.status}
                      label={row.status === "POSTED" ? external ? copy("本人出账已记账", "My debit entry posted") : customerUiText("已记账") : undefined}
                    />
                    <span
                      className={
                        postedVerified
                          ? "history-settlement verified"
                          : "history-settlement unverified"
                      }
                    >
                      {external ? row.settlement_verified
                        ? copy("本人出账已核验 · 外部到账未核验", "Own debit verified · external arrival unverified")
                        : copy("本人出账待核验 · 外部到账未核验", "Own debit needs verification · external arrival unverified")
                        : postedVerified ? customerUiText("账务已核验") : customerUiText("账务待核验")}
                    </span>
                  </div>
                  {typeof row.requested_amount_minor === "number" &&
                    row.requested_currency && (
                      <p className="own-transfer-memo"><CustomerText text="原请求" />{" "}
                        {money(row.requested_amount_minor, {
                          currency: row.requested_currency,
                        })}<CustomerText text="；上方为本人实际" />{row.currency}<CustomerText text="资金腿。" /></p>
                    )}
                  {row.memo && (
                    <p className="own-transfer-memo"><CustomerText text="备注：" />{row.memo}</p>
                  )}
                  <time>{dateText(row.created_at, true)}</time>
                  {row.transfer_channel === "EXTERNAL_SIM" && (
                    <small><CustomerText text="仅当前平台外部清算记录，未接入外部银行，外部到账尚未核验。" /></small>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="extension-empty"><CustomerText text="当前暂无本人收付款记录。" /></p>
        ))}
    </div>
  );
}
