// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { useRef, useState } from "react";
import { Icon, Skeleton } from "./components";
import { AccessibleReloadButton } from "./AccessibleReloadButton";
import { RECIPIENT_DIRECTORY_LIMIT, recipientPrefix, useRecipientDirectoryRead } from "./recipientDirectoryRead";
import { minorFromYuan } from "./admin/moneyInput";
import { exactMajorAmount, type MoneyCurrency } from "./typedMoney";
import type { JsonValue } from "./types";
import { validSimulationCardNumber } from "./cardIdentity";
import { customerName } from "./customerPresentation";

export function TransferRecipientPanel({
  token,
  revision,
  onPrompt,
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const copy = (zh: string, en: string) => locale === "en" ? en : zh;
  const [searchInput, setSearchInput] = useState({ owner: token, value: "" });
  const [applied, setApplied] = useState({ owner: token, prefix: "", requestKey: 0 });
  const prefix = applied.owner === token ? applied.prefix : "";
  const search = searchInput.owner === token ? searchInput.value : "";
  const { endpoint: directory, read, rows } = useRecipientDirectoryRead(token, revision, prefix, applied.requestKey);
  const ready = rows !== null;
  const emptyDraft = () => ({ owner: token, username: "", amount: "", memo: "", currency: "CNY" as MoneyCurrency, manualReference: "" });
  const [draft, setDraft] = useState(emptyDraft);
  const { username, amount, memo, currency, manualReference } = draft.owner === token ? draft : emptyDraft();
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");
  const recipients = rows || [];
  const recipient = recipients.find((item) => item.username === username);
  const frame = { token, key: read.scopeKey, receipt: directory.readReceipt, ready };
  const latest = useRef(frame); latest.current = frame;
  const issued = useRef<{ owner: string; prefix: string; expectedGeneration: number; scope: string } | null>(null);
  const currentFrame = () => latest.current.key === frame.key && latest.current.receipt === frame.receipt
    && latest.current.ready && document.visibilityState === "visible";
  const editDraft = (patch: Partial<ReturnType<typeof emptyDraft>>) => {
    if (latest.current.token !== token) return;
    setDraft(previous => ({ ...(previous.owner === token ? previous : emptyDraft()), ...patch, owner: token }));
  };
  function searchDirectory() {
    if (latest.current.key !== frame.key || !token || !read.visible || document.visibilityState !== "visible") return;
    const next = recipientPrefix(search);
    if (next === null) { setSearchError(copy("请使用最多32位的用户名开头，仅含字母、数字或下划线，并以字母开头。", "Enter a username prefix of up to 32 letters, digits or underscores, starting with a letter.")); return; }
    if (issued.current?.owner === token && issued.current.prefix === next && issued.current.scope === frame.key
      && (directory.loading || directory.getReadGeneration() < issued.current.expectedGeneration)) return;
    if (directory.loading && next === prefix) return;
    issued.current = { owner: token, prefix: next, scope: frame.key,
      expectedGeneration: Math.max(directory.readGeneration, directory.readReceipt) + 1 };
    read.invalidate(); setSearchError("");
    setApplied(previous => ({ owner: token, prefix: next, requestKey: previous.requestKey + 1 }));
  }
  function prepare() {
    if (!currentFrame() || draft.owner !== token) return;
    setError("");
    try {
      const target = manualReference.trim() || recipient?.username;
      if (!target)
        throw new Error("请从平台目录选择账号，或填写完整平台银行卡标识。");
      if (
        manualReference.trim() &&
        !validSimulationCardNumber(target) &&
        !/^[a-z][a-z0-9_]{2,31}$/i.test(target)
      )
        throw new Error("手输身份须为完整平台用户名或16字符银行卡标识。");
      const minor = minorFromYuan(amount);
      if (minor <= 0) throw new Error("转账金额必须大于零。");
      const yuan = exactMajorAmount(minor);
      onPrompt(
        `给${target}转${yuan}${currency === "USD" ? "美元" : "元"}${memo.trim() ? `，备注${memo.trim()}` : ""}`,
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "转账需求未通过输入核对",
      );
    }
  }
  return (
    <div className="registered-recipient-panel">
      <div className="recipient-directory-heading">
        <Icon name="family" size={17} />
        <div>
          <strong><CustomerText text="平台账户内互转" /></strong>
          <p><CustomerText text="只展示有效平台收款身份与银行卡标识，不展示对方余额。" /></p>
        </div>
        <AccessibleReloadButton
          className="icon-button"
          label={customerUiText("刷新平台收款目录")}
          scopeKey={read.scopeKey}
          blocked={!token || !read.visible || directory.loading}
          onReload={() => { if (read.invalidate()) directory.refresh(); }}
        >
          <Icon name="refresh" size={15} />
        </AccessibleReloadButton>
      </div>
      <form className="recipient-directory-search" onSubmit={event => { event.preventDefault(); searchDirectory(); }}>
        <label>{copy("按平台用户名前缀查找", "Search by platform username prefix")}<input
          aria-label={copy("收款账号用户名前缀", "Recipient username prefix")}
          value={search} maxLength={32} autoComplete="off"
          onChange={event => { if (latest.current.token !== token) return; setSearchInput({ owner: token, value: event.target.value }); setSearchError(""); }}
          placeholder={copy("例如：wan", "For example: wan")} /></label>
        <button type="submit" className="button secondary compact"
          aria-disabled={!token || !read.visible || directory.loading && recipientPrefix(search) === prefix}>
          {copy("查询目录", "Search directory")}
        </button>
      </form>
      <p className="recipient-safety-note">{copy(`每次最多展示${RECIPIENT_DIRECTORY_LIMIT}项匹配目录信息，不能据此确认账号全集；未显示不代表账号不存在或不能收款。`, `Each read shows at most ${RECIPIENT_DIRECTORY_LIMIT} matching directory entries. This limited view does not establish the complete directory; an absent entry does not mean the account is unavailable.`)}</p>
      <p>{copy("当前查询前缀：", "Current query prefix:")}{prefix || copy("未指定", "none")}</p>
      {searchError && <p className="error-inline" role="alert">{searchError}</p>}
      {directory.loading && <div role="status"><Skeleton lines={3} compact />{copy("正在读取匹配目录…", "Reading matching entries…")}
        <button className="text-button" onClick={() => { if (read.invalidate()) directory.cancel(); }}>{copy("取消读取", "Cancel read")}</button>
      </div>}
      {directory.error && (
        <p className="error-inline" role="alert"><CustomerText text="收款目录暂未加载：" />{directory.error}
        </p>
      )}
      {directory.cancelled && <p role="status">{copy("目录读取已取消，当前结果尚未核验。可刷新或提交新前缀。", "This directory read was cancelled. Current results are unverified. Refresh or submit a new prefix.")}</p>}
      {read.current && rows === null && <p className="error-inline" role="alert">{copy("目录范围或匹配资料尚未完整核验，暂不展示收款身份。请重读。", "The directory scope or matching entries could not be fully verified. Reload before using them.")}</p>}
      {ready && recipients.length === 0 && <p className="extension-empty">{copy("这次查询未返回匹配目录项。仍可填写完整用户名或银行卡标识，由Agent重新核对。", "This query returned no matching entries. You can still enter an exact username or card reference for the assistant to check.")}</p>}
          <div className="recipient-transfer-form">
            <label><CustomerText text="平台收款账号" /><select
                aria-label={customerUiText("平台内收款账号")}
                value={username}
                disabled={!ready}
                onChange={(event) => {
                  if (!currentFrame()) return;
                  editDraft({ username: event.target.value });
                  setError("");
                }}
              >
                <option value=""><CustomerText text="请选择有效平台账号" /></option>
                {username && !recipient && <option value={username} disabled>{username}{copy(" · 前次选择，需重新核验", " · previous selection, needs rechecking")}</option>}
                {recipients.map((item) => (
                  <option key={item.username} value={item.username}>
                    {item.username}
                    {item.display_name && item.display_name !== item.username
                      ? ` · ${customerName(item.display_name)}`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            {username && !recipient && <p role="status">{copy("先前选择保留在草稿中，本次目录未核验该目标。请重新找到并选择，或明确填写完整身份。", "The previous selection is preserved in the draft but unverified by this read. Find and select it again, or enter its exact reference.")}</p>}
            <label><CustomerText text="手输收款身份（可选，优先使用）" /><input
                aria-label={customerUiText("手输收款用户名或16字符银行卡标识")}
                value={manualReference}
                onChange={(event) => editDraft({ manualReference: event.target.value })}
                placeholder={customerUiText("完整平台用户名或银行卡标识")}
                maxLength={32}
              />
            </label>
            {manualReference.trim() && (
              <p className="recipient-safety-note"><CustomerText text="草稿将使用手输身份" />{manualReference.trim()}<CustomerText text="；实际归属与可收币种由Agent重新核对，目录中的其他选择不会被当作该目标。" /></p>
            )}
            {recipient && (
              <div className="recipient-identity">
                <strong><CustomerText text="平台账号" />{recipient.username}</strong>
                <span>
                  {recipient.primary_card_number
                    ? `平台主收款银行卡标识 ${recipient.primary_card_number}`
                    : `旧兼容账户标识 ${recipient.account_reference || customerUiText("未返回")}`}
                </span>
                {recipient.account_last4 && (
                  <small><CustomerText text="平台账号尾号" />{recipient.account_last4}</small>
                )}
                <small><CustomerText text="可接收币种" />{" "}
                  {(recipient.accepted_currencies || []).join(" / ")}
                  {currency === "USD" &&
                  !recipient.accepted_currencies?.includes("USD")
                    ? customerUiText("；不支持美元钱包直接入账，须核对方案实际收币")
                    : ""}
                </small>
                {Boolean(recipient.available_receiving_cards?.length) && (
                  <div className="recipient-available-cards">
                    {recipient.available_receiving_cards!.map((card) => (
                      <button
                        key={card.card_number}
                        type="button"
                        onClick={() => { if (currentFrame()) editDraft({ manualReference: card.card_number }); }}
                        aria-label={`选择收款银行卡标识${card.card_number}`}
                      >
                        <code>{card.card_number}</code>
                        <small><CustomerText text="HEX尾码" />{card.display_last4}<CustomerText text="· 可收" />{card.accepted_currencies.join("/")}
                        </small>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <label><CustomerText text="原输入币种" /><select
                aria-label={customerUiText("平台转账原输入币种")}
                value={currency}
                onChange={(event) =>
                  editDraft({ currency: event.target.value as MoneyCurrency })
                }
              >
                <option value="CNY"><CustomerText text="CNY · 人民币" /></option>
                <option value="USD"><CustomerText text="USD · 美元金额" /></option>
              </select>
            </label>
            <label><CustomerText text="转账原输入金额（" />{currency}）
              <input
                aria-label={customerUiText("平台内转账金额")}
                inputMode="decimal"
                value={amount}
                onChange={(event) => editDraft({ amount: event.target.value })}
                placeholder={customerUiText("请输入明确金额")}
              />
            </label>
            <label><CustomerText text="转账备注（可选）" /><input
                aria-label={customerUiText("平台内转账备注")}
                value={memo}
                onChange={(event) => editDraft({ memo: event.target.value })}
                maxLength={120}
                placeholder={customerUiText("说明本次用途")}
              />
            </label>
            {error && (
              <p className="error-inline" role="alert">
                {error}
              </p>
            )}
            <button
              className="button secondary compact"
              onClick={prepare}
              disabled={
                !ready || (!recipient && !manualReference.trim()) || !amount.trim()
              }
            ><CustomerText text="生成转账需求" /><Icon name="arrow" size={14} />
            </button>
            <p className="recipient-safety-note"><CustomerText text="这里只填写对话需求，不会立即付款。Agent核对事实后仍需你确认；平台双方入账或外部清算记录以实际渠道与执行回执为准，外部到账需另行核验。" />{currency === "USD" &&
                customerUiText("美元输入不等于美元扣款或美元到账；资金来源与收币必须看实际fx_quote，不能直接拿USD数字当人民币。")}
            </p>
          </div>
    </div>
  );
}

/** Describes actual scoped channel data; never derives a receiver balance or completion. */
export function TransferChannelInfo({
  scope,
}: {
  scope: Record<string, JsonValue> | null | undefined;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  if (
    !scope ||
    !["INTERNAL_BANK", "EXTERNAL_SIM"].includes(String(scope.transfer_channel))
  )
    return null;
  const internal = scope.transfer_channel === "INTERNAL_BANK";
  return (
    <div
      className={`transfer-channel-info ${internal ? "internal" : "external"}`}
      data-transfer-channel={String(scope.transfer_channel)}
    >
      <Icon name="transfer" size={16} />
      <div>
        <strong>
          {internal
            ? `${customerUiText("平台账号")} ${typeof scope.recipient_username === "string" ? scope.recipient_username : customerUiText("（身份信息未返回）")}`
            : customerUiText("外部清算转出记录")}
        </strong>
        {!internal && typeof scope.payee_name === "string" && (
          <span><CustomerText text="外部清算收款方" />{scope.payee_name}</span>
        )}
        {typeof scope.payee_last4 === "string" && (
          <small><CustomerText text="收款标识尾号" />{scope.payee_last4}</small>
        )}
        <p>
          {internal
            ? customerUiText("平台内互转，办理结果请查看业务回执。")
            : customerUiText("此渠道仅记录平台外部清算，尚未连接外部银行，外部到账未核验。")}
        </p>
      </div>
    </div>
  );
}
