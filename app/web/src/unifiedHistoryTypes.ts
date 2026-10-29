export type HistoryActor = {user_id:string;sandbox_id:string;role:string};
export type UnifiedHistoryEntry = {entry_id:string;kind:"CONVERSATION"|"TASK";conversation_id:string|null;task_id:string|null;
  title:string;preview:string;created_at:string;updated_at:string;task_count:number;task_state_counts:Record<string,number>;latest_task_state:string|null};
export type UnifiedHistoryPage = {schema_version:"customer-history-v1";actor:HistoryActor;query:string;items:UnifiedHistoryEntry[];has_more:boolean;next_cursor:string|null};
export type HistoryTaskResolution={schema_version:"customer-history-v1";actor:HistoryActor;task_id:string;kind:"CONVERSATION"|"TASK";conversation_id:string|null;entry_id:string};
export const HISTORY_PAGE_LIMIT=30;
export const HISTORY_QUERY_LIMIT=120;
const customerRoles=new Set(["CUSTOMER","OWNER","DEMO_OWNER","DEV_TEST","FAMILY_HELPER"]);
export const supportedHistoryRole=(role:unknown):role is string=>typeof role==="string"&&customerRoles.has(role);
const states=new Set(["QUEUED","UNDERSTANDING","PLANNING","WAITING","WAITING_TIME","NEED_CLARIFICATION","PLANNED","POLICY_CHECKED","AWAITING_CONFIRMATION","WAITING_CONFIRMATION","AUTHORIZED","EXECUTING","PARTIAL","UNKNOWN","COMPENSATING","SUCCEEDED","FAILED","CLOSED","CANCELLED","CANCELED"]);
const text=(value:unknown,max=2000):value is string=>typeof value==="string"&&value.length<=max;
const id=(value:unknown):value is string=>text(value,200)&&value.trim().length>0&&value.trim()===value&&!/[\u0000-\u001f\u007f]/.test(value);
// Opaque server cursor, not an identity or authorization grant. Its signed
// actor/query payload is intentionally longer than a bank/chat identifier.
const opaqueCursor=(value:unknown):value is string=>text(value,4096)&&value.trim().length>0&&value.trim()===value&&!/[\u0000-\u001f\u007f]/.test(value);
const integer=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;
const date=(value:unknown):value is string=>text(value,64)&&Number.isFinite(Date.parse(value));
export function parseHistoryPage(value:unknown,actorScope:string,query:string,pageLimit=HISTORY_PAGE_LIMIT,actorRole=""):UnifiedHistoryPage|null {
  if(!value||typeof value!=="object")return null;const page=value as UnifiedHistoryPage;
  if(page.schema_version!=="customer-history-v1"||!page.actor||!id(page.actor.user_id)||!id(page.actor.sandbox_id)||!customerRoles.has(page.actor.role)
    || (!supportedHistoryRole(actorRole) || page.actor.role!==actorRole)
    || `${page.actor.sandbox_id}:${page.actor.user_id}`!==actorScope||page.query!==query||!Array.isArray(page.items)||!integer(pageLimit)||pageLimit<1||page.items.length>pageLimit
    || typeof page.has_more!=="boolean"||(page.has_more ? !opaqueCursor(page.next_cursor) : page.next_cursor!==null))return null;
  for(const row of page.items) {
    if(!row||typeof row!=="object"||!id(row.entry_id)||!["CONVERSATION","TASK"].includes(row.kind)||!text(row.title)||!text(row.preview)
      || !date(row.created_at)||!date(row.updated_at)||!integer(row.task_count)||!row.task_state_counts||typeof row.task_state_counts!=="object"||Array.isArray(row.task_state_counts)
      || !Object.entries(row.task_state_counts).every(([state,count])=>states.has(state)&&integer(count))
      || Object.values(row.task_state_counts).reduce((sum,count)=>sum+count,0)!==row.task_count
      || (row.latest_task_state!==null&&!states.has(row.latest_task_state))
      || (row.task_count===0&&row.latest_task_state!==null) || (row.task_count>0&&row.latest_task_state===null))return null;
    if((row.kind==="CONVERSATION" ? !id(row.conversation_id)||row.task_id!==null : !id(row.task_id)||row.conversation_id!==null))return null;
  }
  if(new Set(page.items.map(row=>row.entry_id)).size!==page.items.length || new Set(page.items.map(row=>row.kind=== "CONVERSATION" ? `C:${row.conversation_id}` : `T:${row.task_id}`)).size!==page.items.length)return null;
  return page;
}
export function mergeHistoryEntries(previous:readonly UnifiedHistoryEntry[],incoming:readonly UnifiedHistoryEntry[]):UnifiedHistoryEntry[] {
  const result=[...previous],positions=new Map(result.map((row,index)=>[row.entry_id,index]));
  for(const row of incoming) {const index=positions.get(row.entry_id);if(index===undefined){positions.set(row.entry_id,result.length);result.push(row);}else result[index]=row;}
  return result;
}
export function historyActorEqual(left:HistoryActor,right:HistoryActor){return left.user_id===right.user_id&&left.sandbox_id===right.sandbox_id&&left.role===right.role;}
export function parseHistoryTaskResolution(value:unknown,actorScope:string,actorRole:string,taskId:string):HistoryTaskResolution|null {
  if(!value||typeof value!=="object")return null;const row=value as HistoryTaskResolution;
  if(row.schema_version!=="customer-history-v1"||!row.actor||!id(row.actor.user_id)||!id(row.actor.sandbox_id)||!supportedHistoryRole(actorRole)
    || row.actor.role!==actorRole||`${row.actor.sandbox_id}:${row.actor.user_id}`!==actorScope||row.task_id!==taskId)return null;
  if(row.kind==="CONVERSATION")return id(row.conversation_id)&&row.entry_id===`CONVERSATION:${row.conversation_id}` ? row : null;
  if(row.kind==="TASK")return row.conversation_id===null&&row.entry_id===`TASK:${taskId}` ? row : null;
  return null;
}
