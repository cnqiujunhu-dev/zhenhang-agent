import { useLayoutEffect, useRef, type RefObject } from "react";

type Input = { scopeKey: string; refreshing: boolean; blocked: boolean };
type Space = { element: HTMLElement; minHeight: string; height: number };

/** Keep only pixel geometry while permissions are rechecked, never old answers or controls. */
export function useConversationRefreshLayout(rootRef: RefObject<HTMLDivElement | null>, input: Input) {
  const current = useRef(input); current.current = input;
  const binding = useRef<{ scope: string; settle: () => void; release: () => void } | null>(null);
  useLayoutEffect(() => {
    const timeline = rootRef.current;
    if (!timeline || !input.scopeKey) return;
    const viewport = timeline.closest<HTMLElement>(".conversation-content");
    if (!viewport) return;
    let spaces: Space[] = [], frame = 0, disposed = false, held = false;
    let top = 0, height = 0, wasAtBottom = false;
    const cancel = () => { cancelAnimationFrame(frame); frame = 0; };
    const release = () => {
      cancel();
      for (const row of spaces) row.element.style.minHeight = row.minHeight;
      spaces = []; held = false; delete timeline.dataset.conversationRefreshing;
    };
    const begin = () => {
      if (disposed || current.current.blocked || current.current.scopeKey !== input.scopeKey) return;
      cancel();
      if (held) return;
      top = viewport.scrollTop; height = viewport.scrollHeight;
      wasAtBottom = height - viewport.clientHeight - top <= 1;
      spaces = Array.from(timeline.querySelectorAll<HTMLElement>(".conversation-turn[data-turn-id]"))
        .map(element => ({ element, minHeight: element.style.minHeight, height: element.getBoundingClientRect().height }));
      for (const row of spaces) {
        if (row.height > 0) row.element.style.minHeight = `${row.height}px`;
      }
      held = true; timeline.dataset.conversationRefreshing = "true";
    };
    const settle = () => {
      cancel();
      if (!held || disposed || current.current.refreshing || document.visibilityState === "hidden") return;
      // A window focus listener starts the GET in this same event. Give its
      // React loading commit time to arrive before releasing the geometry.
      frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => {
        frame = 0;
        if (disposed || current.current.refreshing || document.visibilityState === "hidden") return;
        const permitted = !current.current.blocked && current.current.scopeKey === input.scopeKey;
        release();
        if (!permitted) return;
        // Restore before painting, not by starting a new tail animation.
        // A user gesture cancels this restoration entirely.
        const target = wasAtBottom && Math.abs(viewport.scrollHeight - height) <= 1
          ? viewport.scrollHeight - viewport.clientHeight : top;
        viewport.scrollTop = Math.max(0, Math.min(viewport.scrollHeight - viewport.clientHeight, target));
      }); });
    };
    const focus = (event: Event) => { if (event.target === window) { begin(); settle(); } };
    const blur = (event: Event) => { if (event.target === window) begin(); };
    const visibility = () => { if (document.visibilityState === "hidden") begin(); else { begin(); settle(); } };
    const gesture = () => release();
    window.addEventListener("focus", focus, true);
    window.addEventListener("blur", blur, true);
    document.addEventListener("visibilitychange", visibility, true);
    for (const event of ["wheel", "touchstart", "pointerdown", "keydown"]) viewport.addEventListener(event, gesture, { passive: true });
    const cleanup = () => {
      disposed = true; release();
      window.removeEventListener("focus", focus, true); window.removeEventListener("blur", blur, true);
      document.removeEventListener("visibilitychange", visibility, true);
      for (const event of ["wheel", "touchstart", "pointerdown", "keydown"]) viewport.removeEventListener(event, gesture);
      if (binding.current?.scope === input.scopeKey) binding.current = null;
    };
    binding.current = { scope: input.scopeKey, settle, release };
    return cleanup;
  }, [rootRef, input.scopeKey]);
  useLayoutEffect(() => {
    if (input.blocked) binding.current?.release();
    else binding.current?.settle();
  });
}
