import { containsCredentialValue } from "./conversationPendingPrivacy.ts";
export type DraftActorIdentity = {user_id:string;sandbox_id:string;account_id:string;role:string};
const same = (left: DraftActorIdentity, right: DraftActorIdentity) =>
  left.user_id === right.user_id && left.sandbox_id === right.sandbox_id
  && left.account_id === right.account_id && left.role === right.role;
function valid(value: DraftActorIdentity) {
  return value && [value.user_id,value.sandbox_id,value.account_id,value.role].every(field => typeof field === "string" && field.trim().length > 0);
}
function scope(value: DraftActorIdentity): DraftActorIdentity {
  return {user_id:value.user_id,sandbox_id:value.sandbox_id,account_id:value.account_id,role:value.role};
}
/** Live page draft and a one-use renewal bridge stay in RAM; nothing is persisted. */
export class VolatileDraftBridge {
  private live: {message:string;actor:DraftActorIdentity} | null = null;
  private handoff: {message:string;owner:DraftActorIdentity;actor:DraftActorIdentity;nonce:string;deadline:number} | null = null;
  capture(message: string, actor: DraftActorIdentity) {
    if (typeof message !== "string" || message.length > 2000 || !valid(actor) || containsCredentialValue(message)) {
      this.clear(); return false;
    }
    this.live = message ? {message,actor:scope(actor)} : null;
    return true;
  }
  prepare(owner: DraftActorIdentity, actor: DraftActorIdentity, nowMs = Date.now()): string | null {
    this.handoff = null;
    if (!valid(owner) || !valid(actor) || !Number.isFinite(nowMs) || !this.live || !same(this.live.actor,actor)) return null;
    const nonce = crypto.randomUUID();
    this.handoff = {message:this.live.message,owner:scope(owner),actor:scope(actor),nonce,deadline:nowMs+60_000};
    return nonce;
  }
  take(nonce: string, owner: DraftActorIdentity, actor: DraftActorIdentity, nowMs = Date.now()): string | null {
    const record = this.handoff;
    this.handoff = null;
    if (!record || !valid(owner) || !valid(actor) || !Number.isFinite(nowMs) || nowMs > record.deadline
      || nonce !== record.nonce || !same(record.owner,owner) || !same(record.actor,actor)) {
      this.live = null; return null;
    }
    this.live = null;
    return record.message;
  }
  discard(nonce: string) {if(this.handoff?.nonce===nonce)this.handoff=null;}
  clear() {this.live=null;this.handoff=null;}
}
