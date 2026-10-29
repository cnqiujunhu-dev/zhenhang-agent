/** Runtime checks for business verification: parseable JSON alone proves nothing. */
export function validChallengeResponse(value: unknown, now = Date.now()): boolean {
  if (!value || typeof value !== "object") return false;
  const challenge = value as { challenge_id?: unknown; expires_at?: unknown; demo_code?: unknown; provider?: unknown };
  return typeof challenge.challenge_id === "string" && challenge.challenge_id.length > 0 && challenge.challenge_id.length <= 200
    && typeof challenge.expires_at === "string" && Number.isFinite(Date.parse(challenge.expires_at)) && Date.parse(challenge.expires_at) > now
    && typeof challenge.provider === "string" && challenge.provider.length > 0
    && (challenge.demo_code === undefined || typeof challenge.demo_code === "string" && challenge.demo_code.length > 0 && challenge.demo_code.length <= 32);
}

/** Observed expiry is sticky; a clock rollback cannot revive this challenge. */
export class ChallengeLifetime {
  private expired=new Set<string>();
  current(value:unknown,now=Date.now()):boolean {
    if(!value||typeof value!=="object"||!Number.isFinite(now))return false;
    const challenge=value as {challenge_id?:unknown;expires_at?:unknown};
    const key=JSON.stringify([challenge.challenge_id,challenge.expires_at]);
    if(this.expired.has(key))return false;
    const deadline=typeof challenge.expires_at==="string"?Date.parse(challenge.expires_at):NaN;
    if(Number.isFinite(deadline)&&deadline<=now) {
      this.expired.add(key);
      while(this.expired.size>8)this.expired.delete(this.expired.values().next().value!);
      return false;
    }
    return validChallengeResponse(value,now);
  }
}
