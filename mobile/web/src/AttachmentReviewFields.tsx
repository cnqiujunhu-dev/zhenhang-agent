import type { AttachmentView, DocumentField } from "./attachmentTypes";
import { documentFields } from "./attachmentTypes";
import { documentBoxStyle, documentInputValue } from "./attachmentState";
import { attachmentFieldLabel } from "./attachmentPresentation";
import type { CustomerLocale } from "./customerLanguage";
import "./attachmentReview.css";

const labels: Record<DocumentField, [string, string]> = {
  amount_minor: ["金额", "Amount"], currency: ["币种", "Currency"], date: ["日期", "Date"],
  merchant: ["商户 / 说明", "Merchant / description"], counterparty: ["对方称呼", "Other party"],
  recipient_card: ["收款卡号", "Recipient card"],
  status: ["图片中的状态", "Status in the image"], chat_text: ["聊天内容", "Conversation text"],
  role_context: ["对方的身份描述", "How they describe themselves"],
};

/** Field editing is presentation-only. The panel owns identity/version checks,
 * explicit selection, final consent and the single version-bound review POST. */
export function AttachmentReviewFields({ view, locale, values, selected, disabled, onFocus, onLocate, onSelect, onValue }: {
  view: AttachmentView; locale: CustomerLocale; values: Record<DocumentField, string>; selected: DocumentField[];
  disabled: boolean; onFocus: (field: DocumentField) => void; onLocate: (field: DocumentField) => void;
  onSelect: (field: DocumentField, checked: boolean) => void; onValue: (field: DocumentField, value: string) => void;
}) {
  const en = locale === "en";
  const primary: DocumentField[] = view.purpose === "FRAUD"
    ? ["chat_text", "counterparty", "role_context"] : view.extraction?.fields.recipient_card
      ? ["counterparty", "recipient_card", "amount_minor", "currency"] : ["amount_minor", "currency", "date", "merchant", "status"];
  const fieldRow = (field: DocumentField) => {
    const source = view.extraction?.evidence.filter(item => item.field === field) || [];
    const hasPosition = source.some(item => documentBoxStyle(item.box));
    const original = documentInputValue(field, view.extraction?.fields[field]);
    const id = `attachment-${encodeURIComponent(view.attachment_id)}-${field}`;
    const changed = values[field] !== original;
    return <div className="attachment-review-field" key={field} onFocus={() => onFocus(field)} data-review-field={field}>
      <label className="attachment-field-label" htmlFor={`${id}-selected`}>
        <input id={`${id}-selected`} type="checkbox" disabled={disabled} checked={selected.includes(field)}
          aria-label={`${en ? "Accept image field: " : "认可图片字段"}${attachmentFieldLabel(field, locale)}`}
          onChange={event => onSelect(field, event.target.checked)}/>
        <span>{labels[field][en ? 1 : 0]}</span>
      </label>
      {field === "currency" ? <select aria-label={`${en ? "Review image field: " : "核对图片字段"}${attachmentFieldLabel(field, locale)}`}
        aria-describedby={`${id}-source`} disabled={disabled} value={values[field]} onChange={event => onValue(field, event.target.value)}>
        <option value="">{en ? "Please choose" : "请选择"}</option><option value="CNY">CNY · {en ? "Chinese yuan" : "人民币"}</option><option value="USD">USD · {en ? "US dollar" : "美元"}</option>
      </select> : field === "chat_text" ? <textarea rows={4} aria-label={`${en ? "Review image field: " : "核对图片字段"}${attachmentFieldLabel(field, locale)}`}
        aria-describedby={`${id}-source`} disabled={disabled} value={values[field]} onChange={event => onValue(field, event.target.value)}/>
        : <input type={field === "date" ? "date" : "text"} inputMode={field === "amount_minor" ? "decimal" : undefined}
          aria-label={`${en ? "Review image field: " : "核对图片字段"}${attachmentFieldLabel(field, locale)}`}
          aria-describedby={`${id}-source`} disabled={disabled} value={values[field]} onChange={event => onValue(field, event.target.value)}/>}
      <div className="attachment-field-source" id={`${id}-source`}>
        {!original && <span>{en ? "Not recognized. Fill it in if needed, or leave it unchecked." : "这项没有识别清楚，可补充后勾选，也可以跳过。"}</span>}
        {changed && original && <span>{en ? "Originally recognized: " : "识别内容："}{original}</span>}
        {source.length > 0 && <details><summary>{en ? "Read the original text" : "查看原文"}</summary>
          {source.map((item, index) => <p key={index}>{item.text}</p>)}
        </details>}
        {hasPosition && <button type="button" className="text-button" disabled={disabled} onClick={() => onLocate(field)}>{en ? "Locate in image" : "在原图中定位"}</button>}
      </div>
    </div>;
  };
  return <div className="attachment-review-fields">
    <p className="attachment-review-guidance">{en ? "Check the details you want to use. Correct anything inaccurate, then select only the items you have checked." : "核对这次需要用到的信息；有误可以直接修改，只勾选你已确认的内容。"}</p>
    {primary.map(fieldRow)}
    <details className="attachment-additional-fields"><summary>{en ? "Other information in this image" : "图片中的其他信息"}</summary>
      {documentFields.filter(field => !primary.includes(field)).map(fieldRow)}
    </details>
  </div>;
}
