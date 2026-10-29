import {useEffect,useRef,useState} from "react";
import {ApiError,isAbortError,request} from "./api";
import {useReadVisibility} from "./useReadVisibility";
import {HISTORY_PAGE_LIMIT,HISTORY_QUERY_LIMIT,supportedHistoryRole,parseHistoryPage,mergeHistoryEntries,historyActorEqual,type UnifiedHistoryPage} from "./unifiedHistoryTypes";
export function useUnifiedHistory(token:string,actorScope:string,enabled:boolean,locale:string,actorRole:string) {
  enabled=enabled&&supportedHistoryRole(actorRole);
  const visibility=useReadVisibility();
  const enableAuthority=useRef({enabled,epoch:0});
  if(enableAuthority.current.enabled!==enabled)enableAuthority.current={enabled,epoch:enableAuthority.current.epoch+1};
  const actorKey=JSON.stringify([token,actorScope,actorRole]);
  const [queryState,setQuery]=useState({actorKey,value:""}),[reload,setReload]=useState(0),[pageRequest,setPageRequest]=useState<{context:string;cursor:string;nonce:number}|null>(null);
  const query=queryState.actorKey===actorKey ? queryState.value : "";
  const context=JSON.stringify([token,actorScope,actorRole,query,locale,visibility.epoch,enableAuthority.current.epoch]);
  const current=useRef({context,enabled,visible:visibility.visible});current.current={context,enabled,visible:visibility.visible};
  const generation=useRef(0),flight=useRef<AbortController|null>(null),mounted=useRef(true);
  const appendIntent=useRef<{context:string;cursor:string}|null>(null),pageNonce=useRef(0);
  const [state,setState]=useState<{context:string;page:UnifiedHistoryPage|null;loading:boolean;error:unknown|null;appendFailed:boolean}>({context:"",page:null,loading:false,error:null,appendFailed:false});
  const latest=useRef(state);latest.current=state;
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;generation.current++;flight.current?.abort();appendIntent.current=null;};},[]);
  useEffect(()=>{
    const append=pageRequest?.context===context ? pageRequest : null;
    if(!enabled||!visibility.visible||!token||!actorScope){flight.current?.abort();generation.current++;appendIntent.current=null;return;}
    const controller=new AbortController(),sequence=++generation.current;flight.current?.abort();flight.current=controller;
    const previous=append && latest.current.context===context ? latest.current.page : null;
    if(append&&(!previous||previous.next_cursor!==append.cursor||!previous.has_more))return;
    setState({context,page:previous,loading:true,error:null,appendFailed:false});
    const params=new URLSearchParams({q:query,limit:String(HISTORY_PAGE_LIMIT)});if(append)params.set("cursor",append.cursor);
    const valid=()=>mounted.current&&!controller.signal.aborted&&generation.current===sequence&&current.current.context===context&&current.current.enabled&&current.current.visible;
    request<unknown>(`/api/v3/customer/history?${params}`,token,"GET",undefined,undefined,{signal:controller.signal}).then(raw=>{
      if(!valid())return;
      const page=parseHistoryPage(raw,actorScope,query,HISTORY_PAGE_LIMIT,actorRole);
      if(!page||previous&&!historyActorEqual(previous.actor,page.actor)||append&&page.next_cursor===append.cursor)
        throw new ApiError("History response could not be verified",0,"RESPONSE_SCHEMA_INVALID");
      setState({context,page:previous ? {...page,items:mergeHistoryEntries(previous.items,page.items)} : page,loading:false,error:null,appendFailed:false});
    }).catch(error=>{
      if(!valid()||isAbortError(error))return;
      const denied=error instanceof ApiError&&[401,403,404,410].includes(error.status);
      setState({context,page:denied ? null : previous,loading:false,error,appendFailed:!!previous&&!denied});
    }).finally(()=>{if(generation.current===sequence)appendIntent.current=null;});
    return ()=>controller.abort();
  },[context,enabled,visibility.visible,token,actorScope,query,reload,pageRequest]);
  const available=enabled&&visibility.visible&&state.context===context;
  const refresh=()=>{if(!current.current.enabled||!current.current.visible)return;appendIntent.current=null;setPageRequest(null);setReload(value=>value+1);};
  const search=(value:string)=>{if(!current.current.enabled||!current.current.visible)return;flight.current?.abort();generation.current++;appendIntent.current=null;setPageRequest(null);setQuery({actorKey,value:value.trim().slice(0,HISTORY_QUERY_LIMIT)});setReload(value=>value+1);};
  const more=()=>{const latestState=latest.current;if(!current.current.enabled||!current.current.visible||current.current.context!==context||latestState.context!==context||latestState.loading||appendIntent.current?.context===context||!latestState.page?.has_more||!latestState.page.next_cursor)return;
    appendIntent.current={context,cursor:latestState.page.next_cursor};setPageRequest({context,cursor:latestState.page.next_cursor,nonce:++pageNonce.current});};
  return {scopeKey:context,query,items:available ? state.page?.items || [] : [],loading:available ? state.loading : enabled&&visibility.visible,
    verified:available&&!!state.page,error:available ? state.error : null,appendFailed:available&&state.appendFailed,hasMore:available&&!!state.page?.has_more,
    paused:!enabled||!visibility.visible,refresh,search,more,canOpen:available&&!!state.page&&!state.loading&&!state.error};
}
