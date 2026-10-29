import { pendingConversationForScope } from "./conversationState.ts";
import { persistedConversationPending,SENSITIVE_PENDING_PLACEHOLDER } from "./conversationPendingPrivacy.ts";
import type { PendingConversationRequest } from "./conversationTypes";
const id=(value:unknown,max=120):value is string=>typeof value==="string"&&value.length<=max&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(value);
export function inspectConversationPending(raw:string|null,scope:string):{
  record:PendingConversationRequest|null;incomplete:boolean;clear:boolean;
}{
  if(raw===null)return{record:null,incomplete:false,clear:false};
  let value:any;try{value=JSON.parse(raw);}catch{return{record:null,incomplete:true,clear:true};}
  if(!value||typeof value!=="object"||Array.isArray(value))return{record:null,incomplete:true,clear:true};
  if(value.scope!==scope)return{record:null,incomplete:typeof value.scope!=="string",clear:true};
  if(value.restore_incomplete===true)return{record:null,incomplete:true,clear:false};
  const payload=value.payload&&typeof value.payload==="object"&&!Array.isArray(value.payload)?value.payload:{};
  if(payload.client_request_id&&value.client_request_id&&payload.client_request_id!==value.client_request_id)
    return{record:null,incomplete:true,clear:true};
  const client=payload.client_request_id??value.client_request_id;
  const conversation=value.conversation_id==null?null:value.conversation_id;
  if(!id(client)||conversation!==null&&!id(conversation,36)||conversation===null&&!id(value.conversation_request_id))
    return{record:null,incomplete:true,clear:true};
  const regular=pendingConversationForScope(value,scope);
  if(regular&&id(value.conversation_request_id)&&(!Object.hasOwn(value,"lookup_only")||typeof value.lookup_only==="boolean"))
    return{record:persistedConversationPending(regular),incomplete:false,clear:false};
  // Valid pointers can recover an incomplete legacy body, but never resend it.
  return{record:{scope,conversation_id:conversation,conversation_request_id:id(value.conversation_request_id)?value.conversation_request_id:"legacy-lookup",
    title:"旧消息受理记录",payload:{message:SENSITIVE_PENDING_PLACEHOLDER,client_request_id:client,context_policy:"INDEPENDENT"},
    created_at:typeof value.created_at==="string"?value.created_at:"",lookup_only:true},incomplete:false,clear:false};
}
