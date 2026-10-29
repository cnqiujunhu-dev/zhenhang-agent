/** Bounded, page-memory-only edits. Never authorization, storage or telemetry. */
// planVersion is the legacy store-key name; customer callers supply their
// authoritative public task-view revision, not a hidden plan field.
export type TaskEditBinding = {taskId:string;planVersion:number};
function valid(binding:TaskEditBinding|null):binding is TaskEditBinding {
  return Boolean(binding && binding.taskId && Number.isSafeInteger(binding.planVersion) && binding.planVersion >= 1);
}
export class TaskEditDrafts {
  private scope="";
  private entries=new Map<string,{planVersion:number;text:string}>();
  forScope(scope:string) {
    if(scope===this.scope)return;
    this.scope=scope;this.entries.clear();
  }
  read(binding:TaskEditBinding|null):string {
    if(!this.scope||!valid(binding))return "";
    const saved=this.entries.get(binding.taskId);
    if(saved && binding.planVersion < saved.planVersion)return "";
    if(!saved || binding.planVersion > saved.planVersion) {
      this.entries.set(binding.taskId,{planVersion:binding.planVersion,text:""});
      while(this.entries.size>20)this.entries.delete(this.entries.keys().next().value!);
      return "";
    }
    return saved.text;
  }
  write(binding:TaskEditBinding|null,text:string):boolean {
    if(!this.scope||!valid(binding)||typeof text!=="string"||text.length>2000)return false;
    this.read(binding);
    const saved=this.entries.get(binding.taskId);
    if(!saved||saved.planVersion!==binding.planVersion)return false;
    saved.text=text;return true;
  }
  get size(){return this.entries.size;}
}

export type InitialComposerFocusInput = {
  desktop:boolean;visible:boolean;documentFocused:boolean;focusVacant:boolean;
  hasDraft:boolean;hasTask:boolean;pending:boolean;dialogOpen:boolean;userInteracted:boolean;
};
export function mayInitiallyFocusComposer(input:InitialComposerFocusInput):boolean {
  return input.desktop&&input.visible&&input.documentFocused&&input.focusVacant
    &&!input.hasDraft&&!input.hasTask&&!input.pending&&!input.dialogOpen&&!input.userInteracted;
}
