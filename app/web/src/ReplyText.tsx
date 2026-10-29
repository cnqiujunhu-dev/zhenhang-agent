import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";
import type { QueryRevealRequest } from "./queryReveal";
import { ReplyTextRevealLedger, replyTextRequestFresh, replyTextRequestKey } from "./replyTextReveal";
import { useReplyTextReveal } from "./useReplyTextReveal";
import "./replyText.css";

const staticLedger = new ReplyTextRevealLedger();
const ReplyContext = createContext({ identity: "", enabled: false, ledger: staticLedger });

/** One ephemeral send receipt, shared by every natural-language reply path at
 * its own turn. Financial controls and result cards never enter this layer. */
export function ReplyRevealScope({ request, scopeKey, turnId, enabled, ledger, children }: {
  request?: QueryRevealRequest | null; scopeKey: string; turnId: string; enabled: boolean;
  ledger: ReplyTextRevealLedger; children: ReactNode;
}) {
  const identity = replyTextRequestKey(request, scopeKey, turnId);
  const allowed = enabled && replyTextRequestFresh(request, performance.now());
  const value = useMemo(() => ({ identity, enabled: allowed, ledger }), [identity, allowed, ledger]);
  return <ReplyContext.Provider value={value}>{children}</ReplyContext.Provider>;
}

/** Complete source text remains selectable and accessible once. The changing
 * prefix is an aria-hidden visual overlay, not backend token streaming. */
export function ReplyText({ text, slot, immediate = false }: { text: string; slot: string; immediate?: boolean }) {
  const scope = useContext(ReplyContext);
  const identity = scope.identity ? JSON.stringify([scope.identity, slot]) : "";
  const liveSource = useRef("");
  // Reduced motion changes only the visual presentation. A genuine new reply
  // still has one complete announcement; a historical/remounted reply has none.
  if (typeof window !== "undefined" && !immediate && scope.enabled && identity && !scope.ledger.has(identity))
    liveSource.current = identity;
  const reveal = useReplyTextReveal({ text, identity, enabled: scope.enabled && !immediate, ledger: scope.ledger });
  if ((!reveal.animated && liveSource.current !== identity) || !identity) return <>{text}</>;
  return <span className="reply-text" data-reply-revealing={reveal.revealing ? "true" : undefined}>
    <span className="reply-text-source" role="status" aria-live="polite" aria-atomic="true">{text}</span>
    {reveal.revealing && <span className="reply-text-visual" aria-hidden="true">{reveal.visible}</span>}
  </span>;
}
