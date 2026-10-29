import type {TaskTrace} from "./types";

/** Opaque UI read provenance. This is not a bank confirmation or nonce. */
export type TaskTraceReadReceipt={actor:string;visibilityEpoch:number;taskId:string;version:number;sequence:number};
export function currentTaskTraceReadReceipt(record:TaskTraceReadReceipt|null,actor:string,visibilityEpoch:number,
  visible:boolean,trace:TaskTrace|null):number{
  return visible&&record?.actor===actor&&record.visibilityEpoch===visibilityEpoch
    &&record.taskId===trace?.task_id&&record.version===trace.version
    &&Number.isSafeInteger(record.sequence)&&record.sequence>0?record.sequence:0;
}
