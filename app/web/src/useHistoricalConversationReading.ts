import { useLayoutEffect, useRef, useState, type MutableRefObject, type RefObject } from "react";
import type { ConversationTurn } from "./conversationTypes";
import { captureReadingAnchor, resolveReadingAnchor, type ReadingAnchor } from "./conversationReadingAnchor";
import { collectReadingTurns } from "./conversationReadingGeometry";

type Saved = { anchor: ReadingAnchor; scrollTop: number };
type Entry = { actor: string; conversationId: string; trigger: Element | null; started: number; sequence: number };
type Input = {
  root: RefObject<HTMLDivElement | null>; actor: string; conversationId: string;
  turns: ConversationTurn[]; loading: boolean; enabled: boolean; editing: () => boolean;
  followIntent: MutableRefObject<boolean>; restoring: MutableRefObject<boolean>;
  stopFollowing: () => void; saveReadingAnchor: () => void;
  onReadingEarlier: (value: boolean) => void;
};
const viewport = (root: HTMLElement) => ({top:root.getBoundingClientRect().top, height:root.clientHeight,
  scrollTop:root.scrollTop, scrollHeight:root.scrollHeight});
const unavailable = (root: HTMLElement) => document.visibilityState === "hidden" || Boolean(root.closest("[hidden],[inert]"));

/** Explicit history navigation. Opaque anchors live only in this mounted actor's memory. */
export function useHistoricalConversationReading(input: Input) {
  const current = useRef(input); current.current = input;
  const actor = useRef(input.actor);
  const saved = useRef(new Map<string, Saved>());
  const pending = useRef<Entry | null>(null);
  const historical = useRef<{actor:string;conversationId:string} | null>(null);
  const sequence = useRef(0);
  const [epoch, setEpoch] = useState(0);
  const remember = () => {
    const value=current.current, root=value.root.current;
    if (!root || !value.conversationId || !value.enabled || value.loading || unavailable(root)) return;
    const anchor=captureReadingAnchor({actor:value.actor,conversationId:value.conversationId},viewport(root),collectReadingTurns(root));
    if (!anchor) return;
    saved.current.delete(value.conversationId);
    saved.current.set(value.conversationId,{anchor,scrollTop:root.scrollTop});
    while(saved.current.size>24)saved.current.delete(saved.current.keys().next().value!);
  };
  const cancel = () => { pending.current=null;historical.current=null; };
  const open = (conversationId: string, trigger: Element | null) => {
    const value=current.current;
    remember();
    if (!conversationId || !value.actor) return;
    pending.current={actor:value.actor,conversationId,trigger,started:performance.now(),sequence:++sequence.current};
    historical.current={actor:value.actor,conversationId};
    value.stopFollowing(); value.followIntent.current=false;
    value.onReadingEarlier(true); setEpoch(value=>value+1);
  };
  const isHistorical = (id: string) => historical.current?.actor===current.current.actor && historical.current.conversationId===id;
  useLayoutEffect(()=>{
    if(actor.current===input.actor)return;
    actor.current=input.actor;saved.current.clear();cancel();
  },[input.actor]);
  useLayoutEffect(()=>{
    const entry=pending.current,root=input.root.current;
    if(!entry||!root)return;
    let frame=0,restoreFrame=0,disposed=false;
    const consume=()=>{if(pending.current===entry)pending.current=null;observer.disconnect();clearTimeout(deadline);};
    const inspect=()=>{
      const value=current.current;
      if(disposed||pending.current!==entry)return;
      const active=document.activeElement;
      if(value.actor!==entry.actor||!value.enabled||value.root.current!==root||!root.isConnected||unavailable(root)
        ||value.editing()||performance.now()-entry.started>120000
        ||active!==entry.trigger&&active!==document.body){consume();return;}
      if(value.conversationId!==entry.conversationId||value.loading)return;
      const turn=value.turns.at(-1);
      if(!turn)return;
      if(turn.conversation_id!==entry.conversationId || typeof turn.turn_id!=="string" || !turn.turn_id
        ||turn.task_link&&(!turn.task_link.task_id||!Number.isSafeInteger(turn.task_link.version)||turn.task_link.version<0)){
        consume();return;
      }
      if(turn.error_code==="HISTORY_ACCESS_RECHECK"||!['ANSWERED','FAILED'].includes(turn.status))return;
      const turnRoot=root.querySelector<HTMLElement>(`[data-turn-id="${CSS.escape(turn.turn_id)}"]`);
      if(!turnRoot)return;
      const result=turn.task_link?turnRoot.querySelector<HTMLElement>(`[data-task-id="${CSS.escape(turn.task_link.task_id)}"] [data-outcome-kind]`):null;
      const details=turn.task_link?root.querySelector<HTMLElement>(`.task-conversation[data-task-id="${CSS.escape(turn.task_link.task_id)}"]`):null;
      const answer=turnRoot.querySelector<HTMLElement>('.turn-answer,.turn-result-recovery');
      // A query placeholder is not its content. Wait for the actual scoped result or read error.
      const waiting=turnRoot.querySelector('.turn-processing,[data-query-reveal-phase],.turn-result-recovery');
      if(turn.task_link && ['NEW_TASK','REVISE','CLARIFY'].includes(turn.mode||'') && !result && !details
        && !turnRoot.querySelector('.turn-result-recovery'))return;
      const target=result||details||(!waiting?answer:null)||turnRoot.querySelector<HTMLElement>('.turn-result-recovery');
      if(!target||target.closest('[hidden],[inert]')||target.getBoundingClientRect().height<=0)return;
      const turns=collectReadingTurns(root),old=saved.current.get(entry.conversationId),box=viewport(root);
      const matchingAnchor=old&&turns.some(turn=>turn.turnId===old.anchor.turnId)?old:null;
      const restored=matchingAnchor?resolveReadingAnchor(matchingAnchor.anchor,
        {actor:value.actor,conversationId:value.conversationId},box,turns,matchingAnchor.scrollTop):null;
      const top=restored??Math.max(0,Math.min(root.scrollHeight-root.clientHeight,
        root.scrollTop+target.getBoundingClientRect().top-box.top-12));
      consume();value.stopFollowing();value.followIntent.current=false;value.restoring.current=true;
      root.scrollTop=top;value.saveReadingAnchor();value.onReadingEarlier(true);
      restoreFrame=requestAnimationFrame(()=>{restoreFrame=0;value.restoring.current=false;});
    };
    const schedule=()=>{if(!frame&&!disposed&&pending.current===entry)frame=requestAnimationFrame(()=>{frame=0;inspect();});};
    const observer=new MutationObserver(schedule);
    observer.observe(root,{childList:true,subtree:true,attributes:true,
      attributeFilter:['hidden','inert','data-task-state','data-turn-status','data-outcome-kind','data-query-reveal-phase']});
    const deadline=setTimeout(consume,Math.max(0,120000-(performance.now()-entry.started)));
    const gesture=()=>consume();
    const visibility=()=>{if(document.visibilityState==='hidden')consume();};
    for(const event of ['wheel','touchstart','pointerdown','keydown'])root.addEventListener(event,gesture,{passive:true});
    document.addEventListener('visibilitychange',visibility);schedule();
    return()=>{disposed=true;cancelAnimationFrame(frame);cancelAnimationFrame(restoreFrame);
      if(restoreFrame)current.current.restoring.current=false;
      observer.disconnect();clearTimeout(deadline);
      for(const event of ['wheel','touchstart','pointerdown','keydown'])root.removeEventListener(event,gesture);
      document.removeEventListener('visibilitychange',visibility);};
  },[epoch,input.actor,input.conversationId,input.loading,input.enabled,input.turns]);
  return {open,cancel,isHistorical,remember};
}
