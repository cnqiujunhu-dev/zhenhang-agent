import type { ConversationAcknowledgement,PendingConversationRequest } from "./conversationTypes";
export const SENSITIVE_PENDING_PLACEHOLDER = "这条消息含安全凭据信息，原文不保存在浏览器；请查回受理记录。";
const bareKey=/(?:^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])/i;
const bearer=/\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}/i;
const labelledAscii=/(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password)\s*(?:[:：=]|\bis\b)\s*([^\s，。；;]{4,256})/i;
const labelledChinese=/(?:验证码|校验码|动态码|密码|密钥|口令|OTP)\s*(?:是不是|会不会是|收到的是|填的是|需要填|要填|应该填|设置为|改成|改为|换成|是|为|[:：=])?\s*([A-Za-z0-9_-]{4,256})/i;
const labelledUnicode=/(?:密码|密钥|口令)\s*(?:设置为|改成|改为|换成|是|为|[:：=])\s*([^\s，。；;]{4,256})/i;
export function containsCredentialValue(value: string): boolean {
  if(bareKey.test(value)||bearer.test(value))return true;
  const ascii=value.match(labelledAscii)?.[1];
  if(ascii && !( /\b(?:why|how|what|when|is|does)\b/i.test(value) && /^(?:required|optional|secure|expired|enabled|disabled)[?.]?$/i.test(ascii)))return true;
  if(labelledChinese.test(value))return true;
  const unicode=value.match(labelledUnicode)?.[1];
  return Boolean(unicode && !(/^(?:怎么|如何|什么|为什么|为何|用来|用于|干什么)/.test(unicode)&&!/[A-Za-z0-9]/.test(unicode)));
}

/** Projection only; the actual POST still uses its untouched local raw payload. */
export function persistedConversationPending(record: PendingConversationRequest): PendingConversationRequest {
  if(!record.lookup_only&&!containsCredentialValue(record.payload.message)&&!containsCredentialValue(record.title))return record;
  return { scope:record.scope,conversation_id:record.conversation_id,conversation_request_id:record.conversation_request_id,
    title:"安全内容受理记录",payload:{message:SENSITIVE_PENDING_PLACEHOLDER,client_request_id:record.payload.client_request_id,context_policy:record.payload.context_policy},
    created_at:record.created_at,lookup_only:true };
}
export function acknowledgementMatchesPending(found:ConversationAcknowledgement,original:PendingConversationRequest):boolean{
  if(found?.read_only!==true||found.bank_confirmation_permitted!==false||found.client_request_id!==original.payload.client_request_id||
    !["ACCEPTED","UNDETERMINED"].includes(found.status))return false;
  if(found.status==="UNDETERMINED")return found.turn===null;
  return Boolean(found.turn&&found.conversation&&typeof found.turn.turn_id==="string"&&found.turn.turn_id&&
    typeof found.conversation.conversation_id==="string"&&found.conversation.conversation_id&&
    found.turn.conversation_id===found.conversation.conversation_id&&
    (!original.conversation_id||found.turn.conversation_id===original.conversation_id)&&
    Number.isSafeInteger(found.turn.sequence)&&found.turn.sequence>0&&
    ["QUEUED","LEASED","ANSWERED","FAILED"].includes(found.turn.status)&&typeof found.turn.message==="string"&&Array.isArray(found.turn.sources));
}
