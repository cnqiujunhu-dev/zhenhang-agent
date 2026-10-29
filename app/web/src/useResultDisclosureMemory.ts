import {useLayoutEffect,useRef,type RefObject} from "react";
import {ResultDisclosureMemory,resultDisclosureKey} from "./resultDisclosureMemory";

/** Reauthorization may remove result DOM. Restore only an expanded UI choice
 * once the same task/version/node is independently rendered again. */
export function useResultDisclosureMemory(root: RefObject<HTMLDivElement | null>, scope: string, blocked: boolean) {
  const memory=useRef(new ResultDisclosureMemory());
  const identities=useRef(new WeakMap<HTMLDetailsElement,string>());
  const current=useRef({scope,blocked});current.current={scope,blocked};
  memory.current.update(scope,blocked);
  const key=(details:HTMLDetailsElement)=>{
    const section=details.closest<HTMLElement>("[data-reading-key]");
    if (!section || !root.current?.contains(section)) return null;
    return resultDisclosureKey(section.dataset.readingKey,
      Array.from(section.querySelectorAll("details")).indexOf(details),details.querySelector(":scope > summary")?.textContent);
  };
  useLayoutEffect(()=>{
    const element=root.current;if(!element||blocked||!scope)return;
    const active=()=>current.current.scope===scope&&!current.current.blocked;
    const toggled=(event:Event)=>{
      const target=event.target as HTMLDetailsElement | null;
      if(active()&&target?.tagName==="DETAILS"&&element.contains(target))
        memory.current.remember(scope,key(target),target.open);
    };
    const clicked=(event:Event)=>{
      const summary=(event.target as Element | null)?.closest?.("summary");
      const target=summary?.parentElement as HTMLDetailsElement | null;
      if(active()&&target?.tagName==="DETAILS"&&element.contains(target))
        memory.current.remember(scope,key(target),!target.open);
    };
    element.addEventListener("toggle",toggled,true);element.addEventListener("click",clicked,true);
    return()=>{element.removeEventListener("toggle",toggled,true);element.removeEventListener("click",clicked,true);};
  },[scope,blocked,root]);
  useLayoutEffect(()=>{
    for(const details of root.current?.querySelectorAll<HTMLDetailsElement>("[data-reading-key] details")||[]){
      const entry=key(details),identity=JSON.stringify([scope,entry]);
      const previous=identities.current.get(details);
      // React may reuse the native node when a task version or actor changes.
      // Its old open attribute must not outlive that display identity.
      if(blocked||!scope||previous!==undefined&&previous!==identity)details.open=false;
      identities.current.set(details,identity);
      if(!blocked&&scope&&!details.open&&memory.current.wantsOpen(scope,entry))details.open=true;
    }
  });
}
