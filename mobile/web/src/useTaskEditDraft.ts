import { useRef,useState } from "react";
import { TaskEditDrafts,type TaskEditBinding } from "./taskEditDraft";

export function useTaskEditDraft(scope:string,binding:TaskEditBinding|null):[string,(value:string)=>void] {
  const store=useRef<TaskEditDrafts|null>(null);
  const currentScope=useRef(scope);
  currentScope.current=scope;
  if(!store.current)store.current=new TaskEditDrafts();
  store.current.forScope(scope);
  const [,invalidate]=useState(0);
  const value=store.current.read(binding);
  // Scope is checked synchronously, not one effect after a private render.
  return [value,(next:string)=>{
    // An async callback captured under an old actor must not edit the new view.
    if(scope!==currentScope.current)return;
    if(store.current!.write(binding,next))invalidate(revision=>revision+1);
  }];
}
