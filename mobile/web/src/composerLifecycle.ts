import type {Conversation} from './conversationTypes';
import type {TaskTrace} from './types';

/** Current displayed-request navigation only; never completion/approval or slots. */
export function composerLifecycleTaskBinding(input: {
  message:string; conversation:Conversation|null; currentId:string; trace:TaskTrace|null;
  activeId:string; policy:'AUTO'|'INDEPENDENT'; scopeKey:string; blocked:boolean;
}) {
  const {message,conversation,trace}=input;
  if(input.blocked || !input.scopeKey || input.policy!=='AUTO' || !conversation || conversation.conversation_id!==input.currentId
    || !trace || trace.task_id!==input.activeId || conversation.current_task_id!==trace.task_id
    || !Number.isSafeInteger(trace.version) || trace.version<1) return null;
  // Only unqualified singular status questions; names, plural/historical
  // references, conditions and instructions use the original server routing.
  if(!/^(?:这(?:一)?(?:笔|项)|当前(?:任务|请求|业务))[^\r\n，,；;。]{0,45}(?:吗|吧|没)[？?。]?$/.test(message.trim())
    && !/^(?:has|is|did)\s+this\s+(?:request|task|transfer|payment|operation|transaction)\s+[^,;\r\n]{1,50}\?$/i.test(message.trim())) return null;
  if(/引用|示例|如果|假如|先|再|重新|取消|撤销|确认|同意|验证码|密码|怎么办|[“”"「」]|\b(?:if|then|again|resend|retry|cancel|confirm|proceed|password|otp|quote|example)\b/i.test(message)) return null;
  if(!/转出|转走|到账|执行|办完|办好|完成|成功|处理完|\b(?:sent|paid|executed|completed|finished|done|received)\b/i.test(message)) return null;
  if(/转出|转走|\b(?:transfer|transferred)\b/i.test(message)
    && !trace.nodes.some(node=>['TRANSFER','TRANSFER_SCHEDULE','FX_TRANSFER','AA_REQUEST'].includes(node.action))) return null;
  if(/赎回|申购/i.test(message) && !trace.nodes.some(node=>['WEALTH_REDEEM','WEALTH_BUY'].includes(node.action))) return null;
  const latest=[...conversation.turns].sort((a,b)=>b.sequence-a.sequence)[0];
  if(!latest || latest.conversation_id!==input.currentId || latest.status!=='ANSWERED' || latest.error_code
    || !['NEW_TASK','REVISE','CLARIFY','STATUS'].includes(latest.mode||'')
    || latest.task_link?.task_id!==trace.task_id || trace.version<latest.task_link.version) return null;
  return {taskId:trace.task_id,version:trace.version};
}
