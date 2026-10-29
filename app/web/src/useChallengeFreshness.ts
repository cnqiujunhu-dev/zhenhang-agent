import {useEffect,useRef,useState} from "react";
import {ChallengeLifetime} from "./verificationResponse";
import type {Challenge} from "./types";

/** UI-only expiry. No auth request, renewal, nonce rotation or bank callback. */
export function useChallengeFreshness(challenge:Challenge|null):boolean {
  const lifetime=useRef<ChallengeLifetime|null>(null);
  if(!lifetime.current)lifetime.current=new ChallengeLifetime();
  const [,refresh]=useState(0);
  const current=lifetime.current.current(challenge);
  useEffect(()=>{
    if(!challenge)return;
    let timer:ReturnType<typeof setTimeout>|undefined;
    const observe=()=>{
      clearTimeout(timer);
      const now=Date.now();
      const valid=lifetime.current!.current(challenge,now);
      refresh(version=>version+1);
      if(valid)timer=setTimeout(observe,Math.max(1,Math.min(2_147_483_647,Date.parse(challenge.expires_at)-now)));
    };
    observe();
    window.addEventListener("focus",observe);
    window.addEventListener("pageshow",observe);
    document.addEventListener("visibilitychange",observe);
    return ()=>{
      clearTimeout(timer);window.removeEventListener("focus",observe);window.removeEventListener("pageshow",observe);
      document.removeEventListener("visibilitychange",observe);
    };
  },[challenge?.challenge_id,challenge?.expires_at]);
  return current;
}
