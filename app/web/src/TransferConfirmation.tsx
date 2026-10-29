import type {JsonValue} from "./types";
import {useCustomerLanguage} from "./CustomerLanguageProvider";
import {exactMajorAmount} from "./typedMoney";
import {transferConfirmationFacts, transferConfirmationNotices} from "./confirmationPresentation";

/** Values first for a normal transfer. Authentication, expiry, permissions and
 * every write handler remain owned by the enclosing App confirmation. */
export function TransferConfirmation({scope, summary, result}: {scope:JsonValue|undefined; summary:JsonValue|undefined; result:JsonValue|undefined}) {
  const {locale}=useCustomerLanguage(), en=locale==="en";
  const facts=transferConfirmationFacts(scope), notices=transferConfirmationNotices(scope,summary,result);
  const cardText=(card:typeof facts.sourceCard)=>card.number || (card.conflict
    ? en?"Card identity needs checking":"卡号资料不一致，请重新核对"
    : card.tail?en?`Full card number not returned; verified ending ${card.tail}`:`完整卡号暂未返回；已核尾号 ${card.tail}`
    : en?"Card number not returned":"卡号暂未返回");
  return <div className="transfer-confirmation-review">
    <dl className="transfer-confirmation-fields">
      <div data-transfer-confirmation-field="recipient_display_name"><dt>{en?"Recipient":"收款人"}</dt>
        <dd>{facts.recipientName || (en?"Recipient name not returned":"收款人姓名暂未返回")}</dd></div>
      <div data-transfer-confirmation-field="source_card_number"><dt>{en?"Paying card":"转出卡号"}</dt>
        <dd>{facts.sourceCard.number?<code>{facts.sourceCard.number}</code>:cardText(facts.sourceCard)}</dd></div>
      <div data-transfer-confirmation-field="recipient_card_number"><dt>{en?"Receiving card":"收款卡号"}</dt>
        <dd>{facts.recipientCard.number?<code>{facts.recipientCard.number}</code>:cardText(facts.recipientCard)}</dd></div>
      <div data-transfer-confirmation-field="amount"><dt>{en?"Amount":"转账金额"}</dt>
        <dd>{facts.amountMinor!==null&&facts.currency
          ? `${exactMajorAmount(facts.amountMinor)} ${facts.currency}${en?"":facts.currency==="CNY"?" · 人民币":" · 美元"}`
          : en?"Amount or currency not returned":"金额或币种暂未返回"}</dd></div>
      <div data-transfer-confirmation-field="memo"><dt>{en?"Memo":"备注"}</dt>
        <dd>{facts.memo===null?en?"Memo not returned":"备注暂未返回":facts.memo || (en?"No memo":"无备注")}</dd></div>
    </dl>
    {notices.length>0&&<div className="transfer-confirmation-notices" role="alert">{notices.map((notice,index)=><p key={index}>{notice}</p>)}</div>}
  </div>;
}
