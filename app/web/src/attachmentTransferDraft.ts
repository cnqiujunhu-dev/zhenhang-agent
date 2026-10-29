import type { AttachmentView, ReviewedDocument } from './attachmentTypes';
import { exactMajorAmount } from './typedMoney';

const required=['counterparty','recipient_card','amount_minor','currency'] as const;
function facts(fields: Partial<ReviewedDocument['fields']>) {
  const name=fields.counterparty,card=fields.recipient_card,amount=fields.amount_minor,currency=fields.currency;
  if(typeof name!=='string'||!/^\p{L}[\p{L}\d_· -]{0,79}$/u.test(name)
    ||typeof card!=='string'||!/^(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}$/.test(card)
    ||typeof amount!=='number'||!Number.isSafeInteger(amount)||amount<=0||!['CNY','USD'].includes(String(currency)))return null;
  return {name,card,amount:exactMajorAmount(amount),currency:currency==='USD'?'美元':'人民币'};
}
export function imageTransferQuestion(view: AttachmentView) {
  if(view.purpose!=='BILLING'||!['EXTRACTED','REVIEWED'].includes(view.state)||!view.extraction)return null;
  const value=facts(view.extraction.fields);if(!value)return null;
  return `我识别到收款人是${value.name}，收款卡尾号${value.card.slice(-4)}，金额${value.amount}${value.currency}。你想按图片上的信息准备转账吗？请先核对下面四项信息。`;
}
export function reviewedImageTransferDraft(view: AttachmentView) {
  const review=view.latest_review;
  if(view.purpose!=='BILLING'||view.state!=='REVIEWED'||!review?.user_confirmed
    ||review.sha256!==view.sha256||review.attachment_version!==view.version
    ||!required.every(field=>review.selected_fields.includes(field)))return null;
  const value=facts(review.fields);if(!value)return null;
  // Only explicit reviewed scalar values enter an editable customer draft.
  // No image instruction, source-card guess, submission or payment is made.
  return `向${value.name}的${value.card}转${value.amount}${value.currency}。`;
}
