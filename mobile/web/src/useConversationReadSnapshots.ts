import {useEffect,useMemo,useRef,useState} from "react";
import {ApiError,request} from "./api";
import type {ConversationTurn} from "./conversationTypes";
import type {ConversationTaskResult} from "./conversationTaskResults";
import {ConversationReadSnapshotReader,readSnapshotCandidates,type ReadSnapshots} from "./conversationReadSnapshots";
const EMPTY:ReadSnapshots={items:{},permissionBlocked:false,loading:false};

export function useConversationReadSnapshots(token:string,scope:string,conversationId:string,turns:ConversationTurn[],
  regular:Record<string,ConversationTaskResult>,blocked:boolean,visible:boolean,visibilityEpoch:number) {
  const [record,setRecord]=useState<{key:string;snapshot:ReadSnapshots}|null>(null);
  const key=JSON.stringify([token,scope,conversationId]),current=useRef(key),reader=useRef<ConversationReadSnapshotReader|null>(null);
  current.current=key;
  const calculated=readSnapshotCandidates(turns,regular),candidateKey=JSON.stringify(calculated);
  const candidates=useMemo(()=>calculated,[candidateKey]),latest=useRef(candidates);latest.current=candidates;
  const visibleRef=useRef(visible);visibleRef.current=visible;
  useEffect(()=>{
    if(!token||!scope||!conversationId)return;
    const bound=new ConversationReadSnapshotReader((candidate,signal)=>request(
      `/api/v3/customer/conversations/${encodeURIComponent(candidate.conversationId)}/turns/${encodeURIComponent(candidate.turnId)}/read-results`,token,"GET",undefined,undefined,{signal}),
      snapshot=>{if(current.current===key)setRecord({key,snapshot});},error=>error instanceof ApiError?error.status:undefined);
    reader.current=bound;if(!visibleRef.current)bound.pause();bound.update(latest.current);
    const focus=()=>{if(visibleRef.current)bound.revalidate();};
    window.addEventListener("focus",focus);const interval=window.setInterval(focus,60_000);
    return()=>{bound.dispose();if(reader.current===bound)reader.current=null;window.removeEventListener("focus",focus);clearInterval(interval);};
  },[key]);
  useEffect(()=>{reader.current?.update(candidates);},[candidates]);
  useEffect(()=>{if(visible)reader.current?.resume();else reader.current?.pause();},[visible,visibilityEpoch]);
  useEffect(()=>{if(blocked)reader.current?.blockPermissions();},[blocked]);
  return {snapshot:visible&&!blocked&&record?.key===key?record.snapshot:EMPTY,
    restoreAccess:()=>reader.current?.restoreAccess(),refresh:()=>reader.current?.revalidate()};
}
