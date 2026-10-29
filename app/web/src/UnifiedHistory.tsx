import {useEffect,useRef,useState} from "react";
import {ApiError,request} from "./api";
import {useCustomerLanguage} from "./CustomerLanguageProvider";
import {AccessibleReloadButton} from "./AccessibleReloadButton";
import {Icon,Skeleton} from "./components";
import {useUnifiedHistory} from "./useUnifiedHistory";
import type {UnifiedHistoryEntry} from "./unifiedHistoryTypes";
import {HISTORY_QUERY_LIMIT,parseHistoryTaskResolution} from "./unifiedHistoryTypes";
import "./unifiedHistory.css";
export function UnifiedHistory({token,actorScope,actorRole,enabled,openDisabled,navigationRevision,onConversationOpen,onTaskOpen}:{token:string;actorScope:string;actorRole:string;enabled:boolean;openDisabled:boolean;navigationRevision:number;onConversationOpen:(id:string)=>void;onTaskOpen:(id:string)=>void}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  const history=useUnifiedHistory(token,actorScope,enabled,locale,actorRole);
  const inputScope=JSON.stringify([token,actorScope,actorRole]);
  const [input,setInput]=useState({scope:inputScope,value:""});const queryInput=input.scope===inputScope ? input.value : "";
  const navigationScope=JSON.stringify([history.scopeKey,navigationRevision]);
  const authority=useRef({token,actorScope,enabled,openDisabled,scopeKey:history.scopeKey,navigationScope,canOpen:history.canOpen,entries:history.items});authority.current={token,actorScope,enabled,openDisabled,scopeKey:history.scopeKey,navigationScope,canOpen:history.canOpen,entries:history.items};
  const resolvingFlight=useRef<{navigationScope:string;entry:UnifiedHistoryEntry;abort:AbortController}|null>(null);
  const [resolving,setResolving]=useState<string|null>(null),[navigationError,setNavigationError]=useState<{scope:string;message:string;entry:UnifiedHistoryEntry}|null>(null);
  useEffect(()=>{
    const flight=resolvingFlight.current;
    if(flight&&(flight.navigationScope!==navigationScope||!enabled||openDisabled||!history.canOpen)){flight.abort.abort();resolvingFlight.current=null;setResolving(null);}
    if(navigationError && (navigationError.scope!==navigationScope || !history.items.includes(navigationError.entry)))setNavigationError(null);
  },[navigationScope,enabled,openDisabled,history.canOpen,history.items]);
  useEffect(()=>()=>{resolvingFlight.current?.abort.abort();resolvingFlight.current=null;},[]);
  const admission=(entry:UnifiedHistoryEntry)=>{const latest=authority.current;return latest.token===token&&latest.actorScope===actorScope&&latest.scopeKey===history.scopeKey&&latest.navigationScope===navigationScope
    && latest.enabled&&!latest.openDisabled&&latest.canOpen&&document.visibilityState==="visible"&&latest.entries.includes(entry);};
  const open=async(entry:UnifiedHistoryEntry)=>{
    if(!admission(entry)||resolvingFlight.current)return;
    if(entry.kind==="CONVERSATION"&&entry.conversation_id){onConversationOpen(entry.conversation_id);return;}
    if(entry.kind!=="TASK"||!entry.task_id)return;
    const flight={navigationScope,entry,abort:new AbortController()};resolvingFlight.current=flight;setResolving(entry.entry_id);setNavigationError(null);
    try {
      const raw=await request<unknown>(`/api/v3/customer/history/tasks/${encodeURIComponent(entry.task_id)}/resolve`,token,"GET",undefined,undefined,{signal:flight.abort.signal});
      if(resolvingFlight.current!==flight||flight.abort.signal.aborted||!admission(entry))return;
      const resolved=parseHistoryTaskResolution(raw,actorScope,actorRole,entry.task_id);
      if(!resolved)throw new ApiError("History destination could not be verified",0,"RESPONSE_SCHEMA_INVALID");
      if(resolved.kind==="CONVERSATION")onConversationOpen(resolved.conversation_id!);else onTaskOpen(resolved.task_id);
    }catch(error){if(resolvingFlight.current===flight&&!flight.abort.signal.aborted&&admission(entry))setNavigationError({scope:navigationScope,entry,message:en?"This record could not be opened or its access could not be verified. Your history is unchanged.":"这条记录暂未打开或访问权限尚未核验，历史记录仍保留。"});}
    finally {if(resolvingFlight.current===flight){resolvingFlight.current=null;setResolving(null);}}
  };
  const date=(value:string)=>new Intl.DateTimeFormat(en?"en-US":"zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"short",day:"numeric"}).format(Date.parse(value));
  const status=(entry:UnifiedHistoryEntry)=>{
    if(entry.task_count===0)return en?"Conversation":"对话";
    const counts=entry.task_state_counts;
    if(counts.UNKNOWN)return en?"Checking result":"结果核查中";
    if(counts.PARTIAL)return en?"Partially completed":"部分完成";
    if(counts.AWAITING_CONFIRMATION||counts.WAITING_CONFIRMATION)return en?"Awaiting review":"待确认";
    if(counts.NEED_CLARIFICATION)return en?"More information needed":"待补充";
    if(Object.entries(counts).some(([key,value])=>value>0&&!["SUCCEEDED","FAILED","CLOSED","CANCELLED","CANCELED"].includes(key)))return en?"In progress":"办理中";
    return Object.entries(counts).filter(([,count])=>count>0).every(([key])=>["SUCCEEDED","CLOSED","CANCELLED","CANCELED"].includes(key))?en?"Completed":"已办结":en?"Needs attention":"需查看";
  };
  return <section className="unified-history" aria-label={en?"History":"历史记录"}>
    <header><h2>{en?"History":"历史记录"}</h2><AccessibleReloadButton scopeKey={history.scopeKey} blocked={history.loading||history.paused} onReload={history.refresh} label={en?"Refresh history":"刷新历史记录"} className="icon-button"><Icon name="refresh" size={17}/></AccessibleReloadButton></header>
    <form className="unified-history-search" onSubmit={event=>{event.preventDefault();history.search(queryInput);}}><input type="search" aria-label={en?"Search history":"搜索历史记录"} placeholder={en?"Search conversations or requests":"搜索对话或办理内容"} value={queryInput} onChange={event=>setInput({scope:inputScope,value:event.target.value})} maxLength={HISTORY_QUERY_LIMIT}/><button type="submit" className="button secondary compact" disabled={history.paused}>{en?"Search":"搜索"}</button></form>
    {openDisabled&&<p className="unified-history-note">{en?"Your current conversation has unfinished input or an action in progress. You can search history; finish it before opening another record.":"当前对话还有未处理的输入或办理过程。可以先搜索，处理好后再打开其他记录。"}</p>}
    {history.paused&&<p role="status" className="unified-history-note">{en?"History is paused until this page is active and your identity is verified.":"页面恢复可见并核验身份后，会继续读取历史记录。"}</p>}
    {history.loading&&!history.items.length&&<><p role="status" className="unified-history-note">{en?"Loading your history…":"正在读取历史记录…"}</p><Skeleton lines={3}/></>}
    {Boolean(history.error)&&<div className="unified-history-error" role="alert"><p>{history.appendFailed ? en?"More records could not be loaded. The records already shown are retained; this is not the complete history.":"更多记录暂未读到，已展示的记录仍保留，目前不是完整历史。":en?"History could not be verified or loaded. This does not mean you have no records.":"历史记录暂未读到或未通过核验，不代表没有记录。"}</p><button type="button" className="text-button" onClick={history.appendFailed?history.more:history.refresh}>{en?"Retry":"重试"}</button></div>}
    {navigationError?.scope===navigationScope&&<div className="unified-history-error" role="alert"><p>{navigationError.message}</p><button type="button" className="text-button" disabled={openDisabled||!history.canOpen||!!resolving} onClick={()=>open(navigationError.entry)}>{en?"Retry opening":"重新打开这条记录"}</button></div>}
    {resolving&&<p className="unified-history-note" role="status">{en?"Opening the record…":"正在打开记录…"}</p>}
    {!history.paused&&history.verified&&!history.loading&&!history.error&&!history.items.length&&<p className="unified-history-empty">{history.query?en?"No matching history was found.":"没有找到匹配的历史记录。":en?"No history yet. Your conversations and request results will appear here.":"还没有历史记录。开始对话后，相关内容会保留在这里。"}</p>}
    <div className="unified-history-list">{history.items.map(entry=><button type="button" className="unified-history-row" key={entry.entry_id} disabled={openDisabled||!history.canOpen||!!resolving} onClick={()=>open(entry)}><Icon name={entry.kind==="CONVERSATION"?"spark":"bill"} size={17}/><span className="unified-history-row-text"><strong>{entry.title.trim() || (en?"Untitled record":"未命名记录")}</strong>{entry.preview&&<span>{entry.preview}</span>}<small>{date(entry.updated_at)}{entry.task_count>0?en?` · ${entry.task_count} request${entry.task_count===1?"":"s"}`:` · ${entry.task_count} 项办理`:""}</small></span><span className={`unified-history-state ${entry.task_count===0 ? "is-neutral" : entry.task_state_counts.UNKNOWN||entry.task_state_counts.FAILED||entry.task_state_counts.PARTIAL ? "is-attention" : ""}`}>{status(entry)}</span><Icon name="chevron" size={14}/></button>)}</div>
    {history.hasMore&&<button type="button" className="unified-history-more text-button" disabled={history.loading||history.paused} onClick={history.more}>{history.loading?en?"Loading more…":"正在读取更多…":en?"Load more":"加载更多"}</button>}
  </section>;
}
