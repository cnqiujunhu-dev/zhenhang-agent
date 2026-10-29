import { useLayoutEffect, useRef, type RefObject, type MutableRefObject } from "react";
import { captureReadingAnchor, resolveReadingAnchor, type ReadingAnchor, type ReadingContext } from "./conversationReadingAnchor";
import { collectReadingTurns } from "./conversationReadingGeometry";

type Input = {
  open: boolean; setOpen: (open: boolean) => void; context: ReadingContext;
  root: RefObject<HTMLDivElement | null>; visible: boolean; composing: () => boolean;
  followIntent: MutableRefObject<boolean>; stopFollowing: () => void;
  setReadingEarlier: (reading: boolean) => void; clearReadingAnchor: () => void;
  readingEarlier: boolean;
};
type Intent = { context: ReadingContext; trigger: Element | null; anchor: ReadingAnchor | null;
  scrollTop: number; follow: boolean; reading: boolean; phase: "OPEN" | "SHOWN" | "CLOSE" };

export function mayNavigateMaterialSurface(input: {
  sameContext: boolean; visible: boolean; composing: boolean; focusUnchanged: boolean;
}) {
  return input.sameContext && input.visible && !input.composing && input.focusUnchanged;
}
const viewport = (root: HTMLElement) => ({top:root.getBoundingClientRect().top,
  height:root.clientHeight,scrollTop:root.scrollTop,scrollHeight:root.scrollHeight});

/** Explicit surface navigation only. No storage, request, business or automatic replay. */
export function useMaterialNavigation(input: Input) {
  const current = useRef(input); current.current = input;
  const intent = useRef<Intent | null>(null);
  const close = (restoreReading = true) => {
    if (intent.current) {
      if (restoreReading) intent.current.phase = "CLOSE";
      else intent.current = null; // A reviewed-document draft intentionally goes to the composer.
    }
    current.current.setOpen(false);
  };
  const toggle = (trigger: Element | null) => {
    const value = current.current;
    if (value.open) { close(); return; }
    const root = value.root.current;
    intent.current = {context:{...value.context},trigger,
      anchor:root ? captureReadingAnchor(value.context,viewport(root),collectReadingTurns(root)) : null,
      scrollTop:root?.scrollTop || 0,follow:value.followIntent.current,reading:value.readingEarlier,phase:"OPEN"};
    value.stopFollowing(); value.followIntent.current = false;
    value.clearReadingAnchor(); value.setReadingEarlier(true); value.setOpen(true);
  };
  useLayoutEffect(() => {
    const saved = intent.current, value = current.current, root = value.root.current;
    if (!saved) return;
    const sameContext = saved.context.actor === value.context.actor
      && saved.context.conversationId === value.context.conversationId;
    if (!sameContext) { intent.current = null; return; }
    if (!value.open && saved.phase === "CLOSE") {
      // Restore the prior reading intent even when hidden/IME forbids visual
      // navigation. Never defer focus or scrolling until the user returns.
      value.followIntent.current = saved.follow; value.setReadingEarlier(saved.reading);
    }
    if (!root || !value.visible || root.closest("[hidden],[inert]") || value.composing()) {
      // Consume this navigation edge; becoming visible later never steals focus.
      if (saved.phase === "OPEN") saved.phase = "SHOWN";
      else if (!value.open) intent.current = null;
      return;
    }
    if (value.open && saved.phase === "OPEN") {
      saved.phase = "SHOWN";
      if (!mayNavigateMaterialSurface({sameContext,visible:value.visible,composing:value.composing(),
        focusUnchanged:document.activeElement === saved.trigger || document.activeElement === document.body})) return;
      const heading = root.querySelector<HTMLElement>('[data-customer-opened-surface="materials"] h3');
      if (!heading) return;
      heading.tabIndex = -1; heading.focus({preventScroll:true});
      heading.scrollIntoView({block:"start",behavior:"auto"});
    } else if (!value.open) {
      intent.current = null;
      if (saved.phase !== "CLOSE") return; // Identity changes/other navigation never restore a stale surface.
      const top = resolveReadingAnchor(saved.anchor,value.context,viewport(root),collectReadingTurns(root),saved.scrollTop)
        ?? Math.min(Math.max(0,root.scrollHeight-root.clientHeight),Math.max(0,saved.scrollTop));
      root.scrollTop = top;
      if (document.activeElement === document.body && saved.trigger instanceof HTMLElement && saved.trigger.isConnected)
        saved.trigger.focus({preventScroll:true});
    }
  },[input.open,input.context.actor,input.context.conversationId,input.visible]);
  return {toggle,close};
}
