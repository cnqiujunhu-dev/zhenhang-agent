// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { useState } from "react";
import { Icon, Skeleton } from "./components";
import { useEndpoint } from "./useEndpoint";
import { validSimulationCardNumber } from "./cardIdentity";
import { minorFromYuan } from "./admin/moneyInput";
import { exactMajorAmount } from "./typedMoney";
import type { JsonValue } from "./types";

interface ContactAlias {
  alias_id: string;
  alias_name: string;
  recipient_username: string;
  recipient_reference: string;
  status: string;
  version: number;
  available: boolean;
}
export function ContactAliasesPanel({
  token,
  revision,
  onPrompt,
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const endpoint = useEndpoint<{
    scope: string;
    aliases: ContactAlias[];
    synthetic: boolean;
    counterparty_balances_disclosed: boolean;
    mutations_require_confirmed_task: boolean;
  }>("/api/v2/contacts/aliases", token, revision);
  const [reference, setReference] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const safe =
    endpoint.data?.scope === "CURRENT_USER_CONTACT_ALIASES" &&
    endpoint.data.counterparty_balances_disclosed === false &&
    endpoint.data.mutations_require_confirmed_task === true;
  const aliases = safe ? endpoint.data!.aliases : [];
  const landlord = aliases.find(
    (item) => item.alias_name === "房东" && item.status === "ACTIVE",
  );
  function prepare(mode: "bind" | "replace" | "transfer") {
    setError("");
    try {
      const target = reference.trim();
      if (
        !validSimulationCardNumber(target) &&
        !/^[a-z][a-z0-9_]{2,31}$/i.test(target)
      )
        throw new Error("请填写完整平台用户名或16字符银行卡标识。");
      if (mode === "bind") onPrompt(`把${target}设置为我的房东`);
      else if (mode === "replace") onPrompt(`把我的房东改为${target}`);
      else {
        const minor = minorFromYuan(amount);
        if (minor <= 0) throw new Error("请明确大于零的人民币转账金额。");
        onPrompt(`向${target}账户的房东转${exactMajorAmount(minor)}元`);
      }
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "身份需求未通过输入核对",
      );
    }
  }
  return (
    <div className="contact-aliases-panel" aria-label={customerUiText("我的个人身份别名")}>
      <header>
        <div>
          <strong><CustomerText text="我的身份联系人" /></strong>
          <p><CustomerText text="“房东”是我个人的收款身份，不修改对方全局姓名。" /></p>
        </div>
        <button
          className="icon-button"
          aria-label={customerUiText("刷新我的个人身份别名")}
          onClick={endpoint.refresh}
        >
          <Icon name="refresh" size={15} />
        </button>
      </header>
      {endpoint.loading && !endpoint.data && <Skeleton lines={3} compact />}
      {endpoint.error && (
        <p className="error-inline" role="alert"><CustomerText text="个人身份未加载：" />{endpoint.error}<CustomerText text="。不会生成虚构绑定。" /></p>
      )}
      {endpoint.data && !safe && (
        <p className="error-inline" role="alert"><CustomerText text="接口未返回个人确认约束范围，未显示联系人数据。" /></p>
      )}
      {safe &&
        (aliases.length ? (
          <div className="contact-alias-list">
            {aliases.map((item) => (
              <article key={item.alias_id}>
                <strong>
                  {item.alias_name} → {item.recipient_username}
                </strong>
                <code>{item.recipient_reference}</code>
                <small>
                  {item.available
                    ? customerUiText("目标收款身份可用")
                    : customerUiText("目标暂不可用，需重新核对")}{" "}<CustomerText text="· 当前版本" />{item.version}
                </small>
              </article>
            ))}
          </div>
        ) : (
          <p className="extension-empty"><CustomerText text="尚未保存我的房东身份。" /></p>
        ))}
      <p className="personal-alias-boundary"><CustomerText text="个人内部身份绑定与账套原有的外部“房东”清算联系人不是同一个对象；不会自动替换已有身份或转账历史。" /></p>
      <div className="contact-alias-draft-form">
        <label><CustomerText text="目标平台用户名或完整银行卡标识" /><input
            aria-label={customerUiText("房东身份目标用户名或银行卡标识")}
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            placeholder={customerUiText("如wangwei，或16字符平台银行卡标识")}
            maxLength={32}
          />
        </label>
        <button
          className="button secondary compact"
          disabled={!reference.trim() || !safe}
          onClick={() => prepare("bind")}
        ><CustomerText text="准备设置我的房东" /><Icon name="arrow" size={14} />
        </button>
        {landlord && (
          <>
            <p><CustomerText text="当前已绑定" />{landlord.recipient_username}<CustomerText text="；不同目标需要明确改绑与独立确认，不会静默覆盖。" /></p>
            <button
              className="button secondary compact"
              disabled={!reference.trim() || !safe}
              onClick={() => prepare("replace")}
            ><CustomerText text="准备明确改绑我的房东" /></button>
          </>
        )}
        <details>
          <summary><CustomerText text="同时准备本次转账与个人身份确认" /></summary>
          <label><CustomerText text="本次人民币金额" /><input
              aria-label={customerUiText("房东身份转账人民币金额")}
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              inputMode="decimal"
              placeholder={customerUiText("填写金额，不直接转账")}
            />
          </label>
          <button
            className="button secondary compact"
            disabled={!reference.trim() || !amount.trim() || !safe}
            onClick={() => prepare("transfer")}
          ><CustomerText text="生成含房东身份的转账需求" /></button>
        </details>
        {error && (
          <p className="error-inline" role="alert">
            {error}
          </p>
        )}
        <p><CustomerText text="所有按钮只填对话草稿；保存或改绑必须进入Agent任务，经本人核对确认与验证，不会自动修改联系人。" /></p>
      </div>
    </div>
  );
}
export function AliasBindingNotice({
  data,
}: {
  data: JsonValue | undefined | null;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const fx =
    data.fx_quote &&
    typeof data.fx_quote === "object" &&
    !Array.isArray(data.fx_quote)
      ? data.fx_quote
      : null;
  const raw = data.alias_binding || fx?.alias_binding;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  return (
    <div className="alias-binding-notice" aria-label={customerUiText("本次个人身份绑定确认范围")}>
      <h4>
        <Icon name="family" size={16} /><CustomerText text="本次与今后的个人身份范围" /></h4>
      <p>
        {raw.change_kind === "REPLACE"
          ? `确认将把我的${String(raw.alias_name)}从${String(raw.previous_recipient_username || customerUiText("原目标"))}改为${String(raw.recipient_username)}`
          : raw.change_kind === "UNCHANGED"
            ? `本次继续使用已保存的我的${String(raw.alias_name)}身份`
            : `确认将保存我的${String(raw.alias_name)}身份为${String(raw.recipient_username)}`}
      </p>
      {typeof raw.notice === "string" && <p>{raw.notice}</p>}
      {raw.existing_external_contact_preserved === true && (
        <p className="alias-resolution-priority"><CustomerText text="同名旧外部联系人会保留；本次确认保存内部个人身份后，今后使用我的该身份时优先指向已绑定的内部目标。" /></p>
      )}
      <small><CustomerText text="仅改变我的身份联系人，不改对方全局姓名。与当前银行操作同一确认范围，未经确认不会保存或执行。" /></small>
    </div>
  );
}
