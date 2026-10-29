import {useCustomerLanguage} from "./CustomerLanguageProvider";
import {money} from "./components";
import {ownTransferFieldsValid,recordedOwnAccountTransfer} from "./ownAccountTransferPresentation";
import "./creditCards.css";

type Data=Record<string,unknown>;
const data=(value:unknown):Data=>value!==null&&typeof value==="object"&&!Array.isArray(value)?value as Data:{};

function OwnTransferFacts({value}:{value:Data}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  return <dl><div><dt>{en?"From your debit card":"转出借记卡尾号"}</dt><dd>{String(value.source_card_last4)}</dd></div>
    <div><dt>{en?"To your debit card":"转入借记卡尾号"}</dt><dd>{String(value.destination_card_last4)}</dd></div>
    <div><dt>{en?"Amount (CNY)":"转存金额（人民币）"}</dt><dd>{money(value.amount_minor as number,{currency:"CNY"})}</dd></div></dl>;
}

export function OwnAccountTransferConfirmation({preview}:{preview:unknown}) {
  const {locale}=useCustomerLanguage(),en=locale==="en",value=data(preview);
  if(!ownTransferFieldsValid(value)||value.bank_state_changed!==false)return <p>{en?"Review the two owned debit cards and amount.":"请重新核对本人两张借记卡和转存金额。"}</p>;
  return <section className="credit-card credit-card-compact" aria-label={en?"Review own-account transfer":"核对本人账户转存"}>
    <OwnTransferFacts value={value}/><p>{en?"After confirmation, cash moves only between these two owned accounts; no payment to another person.":"确认后仅在本人这两个资金账户间转存，不向他人付款。"}</p>
  </section>;
}

export function OwnAccountTransferReceipt({result}:{result:unknown}) {
  const {locale}=useCustomerLanguage(),en=locale==="en",value=data(result);
  if(!recordedOwnAccountTransfer(value))return <p>{en?"No confirmed own-account transfer receipt is available.":"尚未取得可确认的本人账户转存回执。"}</p>;
  return <section className="credit-card credit-card-compact" aria-label={en?"Own-account transfer receipt":"本人账户转存回执"}>
    <h3>{en?"Your account transfer was posted":"本人账户转存已完成"}</h3><OwnTransferFacts value={value}/>
    <p>{en?"Total owned cash is unchanged. This is internal movement, not new spending or an external payment.":"本人现金总额不变；这是内部资金搬移，不新增消费，也不是向他人付款。"}</p>
  </section>;
}
