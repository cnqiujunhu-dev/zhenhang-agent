export type FreshReplyGeometry = {scrollTop:number;scrollHeight:number;height:number;rootTop:number;replyTop:number;replyHeight:number};
export type FreshReplyDecision = {kind:"WAIT"|"DISCARD"|"KEEP_TAIL"}|{kind:"READ_START";top:number};
export type FreshReplyReadingInput = {
  sameScope:boolean;sameTurn:boolean;fresh:boolean;answered:boolean;failed:boolean;
  allowed:boolean;following:boolean;interrupted:boolean;editing:boolean;
  readyKind:"QUERY_RESULT"|"CONSULT"|null;geometry:FreshReplyGeometry|null;
};
/** A one-time presentation choice, never a task or model decision. */
export function freshReplyReading(input:FreshReplyReadingInput):FreshReplyDecision {
  if (!input.sameScope || !input.sameTurn || !input.fresh || input.failed || !input.allowed
    || !input.following || input.interrupted || input.editing) return {kind:"DISCARD"};
  if (!input.answered || !input.readyKind || !input.geometry) return {kind:"WAIT"};
  const box=input.geometry;
  if (![box.scrollTop,box.scrollHeight,box.height,box.rootTop,box.replyTop,box.replyHeight].every(Number.isFinite)
    || box.height<=0 || box.replyHeight<=0 || box.scrollHeight<0) return {kind:"DISCARD"};
  if (box.replyHeight<=Math.max(0,box.height-24)) return {kind:"KEEP_TAIL"};
  return {kind:"READ_START",top:Math.min(Math.max(0,box.scrollHeight-box.height),
    Math.max(0,box.scrollTop+box.replyTop-box.rootTop-12))};
}
