import { useLayoutEffect,useRef,type RefObject,type MutableRefObject } from "react";
import type { QueryRevealRequest } from "./queryReveal";
import type { ConversationTurn } from "./conversationTypes";
import { freshReplyReading } from "./freshReplyReading";

type Input={root:RefObject<HTMLDivElement|null>;scopeKey:string;request:QueryRevealRequest|null;
  turn:ConversationTurn|undefined;enabled:boolean;editing:()=>boolean;
  followIntent:MutableRefObject<boolean>;restoring:MutableRefObject<boolean>;
  stopFollowing:()=>void;saveReadingAnchor:()=>void;};
const MAX_AGE_MS=120000;

/** Read a new long answer from its beginning, without focusing or replaying history. */
export function useFreshReplyReading(input:Input) {
  const current=useRef(input);current.current=input;
  const consumed=useRef(new Set<string>());
  useLayoutEffect(()=>{
    const root=input.root.current,request=input.request;
    if (!root||!request) return;
    const key=JSON.stringify([request.scopeKey,request.requestId,request.turnId]);
    if (consumed.current.has(key)) return;
    let frame=0,restoreFrame=0,disposed=false,interrupted=false;
    const consume=()=>{
      consumed.current.add(key);
      while(consumed.current.size>64)consumed.current.delete(consumed.current.values().next().value!);
      observer.disconnect();clearTimeout(deadline);
    };
    const inspect=()=>{
      if(disposed||consumed.current.has(key))return;
      const value=current.current,turn=value.turn;
      const turnRoot=root.querySelector<HTMLElement>(`[data-turn-id="${CSS.escape(request.turnId)}"]`);
      const query=turn?.task_link
        ?turnRoot?.querySelector<HTMLElement>(`[data-task-id="${CSS.escape(turn.task_link.task_id)}"] [data-outcome-kind="QUERY_RESULT"]`):null;
      const answer=turn?.mode==="CONSULT"?turnRoot?.querySelector<HTMLElement>(".turn-answer:not(.turn-failed)"):null;
      const reply=query||answer;
      const box=reply?.getBoundingClientRect();
      const now=performance.now();
      const active=document.activeElement;
      const activeInReply=active instanceof HTMLElement&&root.contains(active)
        &&active.matches('button,summary,input,textarea,select,[contenteditable="true"],[tabindex="-1"]');
      const decision=freshReplyReading({sameScope:value.scopeKey===request.scopeKey&&value.request?.requestId===request.requestId,
        sameTurn:!turn||turn.turn_id===request.turnId,fresh:now>=request.startedAtMs&&now-request.startedAtMs<=MAX_AGE_MS,
        answered:turn?.status==="ANSWERED",failed:turn?.status==="FAILED"||Boolean(turn?.error_code),
        allowed:value.enabled&&value.root.current===root&&root.isConnected
          &&document.visibilityState!=="hidden"&&!root.closest("[hidden],[inert]"),
        following:value.followIntent.current,interrupted,editing:value.editing()||activeInReply,
        readyKind:query?"QUERY_RESULT":answer?"CONSULT":null,
        geometry:box?{scrollTop:root.scrollTop,scrollHeight:root.scrollHeight,height:root.clientHeight,
          rootTop:root.getBoundingClientRect().top,replyTop:box.top,replyHeight:box.height}:null});
      if(decision.kind==="WAIT")return;
      consume();
      if(decision.kind!=="READ_START")return;
      value.stopFollowing();value.followIntent.current=false;value.restoring.current=true;
      root.scrollTop=decision.top;value.saveReadingAnchor();
      restoreFrame=requestAnimationFrame(()=>{restoreFrame=0;value.restoring.current=false;});
    };
    const schedule=()=>{if(!frame&&!disposed&&!consumed.current.has(key))frame=requestAnimationFrame(()=>{frame=0;inspect();});};
    const observer=new MutationObserver(schedule);
    observer.observe(root,{childList:true,subtree:true,attributes:true,
      attributeFilter:["hidden","inert","data-query-reveal-phase","data-outcome-kind","data-turn-status"]});
    const deadline=setTimeout(()=>{if(!disposed)consume();},Math.max(0,MAX_AGE_MS-(performance.now()-request.startedAtMs)));
    const gesture=(event:Event)=>{
      if(event instanceof KeyboardEvent&&!['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key))return;
      interrupted=true;consume();
    };
    const visibility=()=>{if(document.visibilityState==="hidden")consume();};
    for(const event of ["wheel","touchstart","pointerdown","keydown"])root.addEventListener(event,gesture,{passive:true});
    document.addEventListener("visibilitychange",visibility);
    schedule();
    return ()=>{disposed=true;cancelAnimationFrame(frame);cancelAnimationFrame(restoreFrame);
      if(restoreFrame)current.current.restoring.current=false;
      observer.disconnect();clearTimeout(deadline);
      for(const event of ["wheel","touchstart","pointerdown","keydown"])root.removeEventListener(event,gesture);
      document.removeEventListener("visibilitychange",visibility);};
  },[input.request?.requestId,input.scopeKey,input.turn?.status,input.turn?.mode,input.enabled]);
}
