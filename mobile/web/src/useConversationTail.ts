import { useEffect, useLayoutEffect, useRef, type MutableRefObject, type RefObject, type UIEvent } from "react";
import { canFollowCustomerTail, customerTailFrame, customerTailScrollIntent, customerTailTarget,
  type CustomerTailGuards, type CustomerTailViewport } from "./customerResultScroll";

type TailInput = {
  root: RefObject<HTMLDivElement | null>;
  scopeKey: string;
  followIntent: MutableRefObject<boolean>;
  restoring: MutableRefObject<boolean>;
  guards: Omit<CustomerTailGuards, "composing" | "editing">;
  composing: () => boolean;
  editing: () => boolean;
  onReadingEarlier: (value: boolean) => void;
  onSaveAnchor: () => void;
  onPreserveAnchor?: () => void;
  onUserGesture?: () => void;
};
function measure(root: HTMLElement): CustomerTailViewport {
  return { scrollTop: root.scrollTop, scrollHeight: root.scrollHeight, height: root.clientHeight };
}
function unavailable(root: HTMLElement) {
  return document.visibilityState === "hidden" || Boolean(root.closest("[hidden],[inert]"));
}

/** Follows only an existing bottom-reading intent. Observers enqueue work; they never write in their callbacks. */
export function useConversationTail(input: TailInput) {
  const current = useRef(input); current.current = input;
  const binding = useRef<{ root: HTMLElement; scopeKey: string; generation: number; release: () => void } | null>(null);
  const generation = useRef(0);
  const operation = useRef(0);
  const paused = useRef<{ scopeKey: string; scrollTop: number } | null>(null);
  const previous = useRef<CustomerTailViewport | null>(null);
  const gestureUntil = useRef(0);
  const animation = useRef(0);
  const pendingRead = useRef(0);
  const programmatic = useRef(false);
  const localComposing = useRef(false);
  const stop = () => {
    operation.current += 1;
    cancelAnimationFrame(animation.current); animation.current = 0;
    cancelAnimationFrame(pendingRead.current); pendingRead.current = 0;
    programmatic.current = false;
  };
  const owns = (root: HTMLElement, expected: number) => binding.current?.root === root
    && binding.current.generation === expected && binding.current.scopeKey === current.current.scopeKey
    && current.current.root.current === root && root.isConnected !== false;
  const allowed = (root: HTMLElement) => canFollowCustomerTail(current.current.followIntent.current, {
    ...current.current.guards, enabled: current.current.guards.enabled && !unavailable(root)
      && !root.querySelector?.('[data-conversation-refreshing="true"]'),
    composing: current.current.composing() || localComposing.current, editing: current.current.editing(),
  });
  const follow = (root: HTMLElement, expected: number) => {
    if (!owns(root, expected)) return;
    // Native scrolling is asynchronous. A render/observer RAF may run before
    // React's scroll callback, so the stale reading anchor must not win that
    // race. Capture the current position without moving it during a gesture.
    if (performance.now() < gestureUntil.current) {
      const next = measure(root), prior = previous.current;
      if (!prior || Math.abs(prior.scrollTop - next.scrollTop) > 1
        || Math.abs(prior.scrollHeight - next.scrollHeight) > 1 || Math.abs(prior.height - next.height) > 1)
        current.current.onSaveAnchor();
      previous.current = next; stop(); return;
    }
    if (!allowed(root)) {
      if (current.current.guards.enabled && !unavailable(root) && !current.current.followIntent.current)
        current.current.onPreserveAnchor?.();
      stop(); return;
    }
    const viewport = measure(root), target = customerTailTarget(viewport);
    previous.current = viewport;
    if (target === null || Math.abs(target - root.scrollTop) <= 1 || animation.current) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const start = root.scrollTop, startedAt = performance.now();
    const ticket = operation.current;
    programmatic.current = true;
    const frame = (now: number) => {
      if (!owns(root, expected) || ticket !== operation.current) return;
      if (!allowed(root)) { stop(); return; }
      const liveTarget = customerTailTarget(measure(root));
      if (liveTarget === null) { stop(); return; }
      const elapsed = now - startedAt;
      root.scrollTop = customerTailFrame(start, liveTarget, elapsed, reduced);
      previous.current = measure(root);
      if (!reduced && elapsed < 180) animation.current = requestAnimationFrame(frame);
      else {
        animation.current = 0;
        // Keep the final native scroll event separate from the user's next gesture.
        pendingRead.current = requestAnimationFrame(() => {
          if (!owns(root, expected) || ticket !== operation.current) return;
          pendingRead.current = 0; programmatic.current = false; previous.current = measure(root); queue(root, expected);
        });
      }
    };
    animation.current = requestAnimationFrame(frame);
  };
  const queue = (root: HTMLElement, expected: number) => {
    if (!owns(root, expected)) return;
    if (!current.current.guards.enabled || unavailable(root)) { stop(); return; }
    if (pendingRead.current) return;
    const ticket = operation.current;
    pendingRead.current = requestAnimationFrame(() => {
      if (!owns(root, expected) || ticket !== operation.current) return;
      pendingRead.current = 0; follow(root, expected);
    });
  };
  // Refs update during commit. Checking the actual root in each layout pass
  // also handles a replacement DOM node under an unchanged conversation ID.
  useLayoutEffect(() => {
    const root = input.root.current;
    const old = binding.current;
    if (old && (old.root !== root || old.scopeKey !== input.scopeKey || !input.guards.enabled)) {
      paused.current = old.scopeKey === input.scopeKey ? { scopeKey: old.scopeKey, scrollTop: old.root.scrollTop } : null;
      old.release();
    }
    if (paused.current?.scopeKey !== input.scopeKey) paused.current = null;
    if (!root || !input.guards.enabled) { stop(); return; }
    if (binding.current) { queue(root, binding.current.generation); return; }
    stop(); previous.current = measure(root); gestureUntil.current = 0; localComposing.current = false;
    const expected = ++generation.current;
    const scopedQueue = () => queue(root, expected);
    const observed = new Set<Element>();
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(scopedQueue) : null;
    const observeChildren = () => {
      if (!owns(root, expected)) return;
      for (const target of observed) if (target !== root && target.parentElement !== root) { resize?.unobserve(target); observed.delete(target); }
      for (const target of [root, ...Array.from(root.children)]) if (!observed.has(target)) { resize?.observe(target); observed.add(target); }
    };
    const mutations = new MutationObserver(() => { if (!owns(root, expected)) return; observeChildren(); scopedQueue(); });
    mutations.observe(root, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["hidden", "inert", "data-query-reveal-phase", "data-task-state"] });
    const gesture = (event: Event) => {
      if (!owns(root, expected)) return;
      if (event instanceof KeyboardEvent && (!['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)
        || event.target instanceof HTMLElement && event.target.matches('input,textarea,select,[contenteditable="true"]'))) return;
      gestureUntil.current = performance.now() + 800;
      stop(); // An intentional wheel, touch or scrollbar gesture always wins over our follow animation.
      current.current.restoring.current = false;
      current.current.onUserGesture?.();
      scopedQueue(); // Capture the native position even if scroll delivery comes after this RAF.
    };
    const suspend = () => { if (owns(root, expected) && (unavailable(root) || window.matchMedia("(prefers-reduced-motion: reduce)").matches)) stop(); };
    const compositionStarted = () => { if (owns(root, expected)) { localComposing.current = true; stop(); } };
    const compositionEnded = () => { if (owns(root, expected)) { localComposing.current = false; scopedQueue(); } };
    const inputChanged = () => { if (owns(root, expected)) stop(); };
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) root.addEventListener(type, gesture, { passive: true });
    root.addEventListener("compositionstart", compositionStarted, true);
    root.addEventListener("compositionend", compositionEnded, true);
    root.addEventListener("input", inputChanged, true);
    document.addEventListener("visibilitychange", suspend); media.addEventListener("change", suspend);
    const release = () => {
      if (binding.current?.generation === expected) {
        stop(); generation.current += 1; binding.current = null; localComposing.current = false;
      }
      resize?.disconnect(); mutations.disconnect();
      for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) root.removeEventListener(type, gesture);
      root.removeEventListener("compositionstart", compositionStarted, true); root.removeEventListener("compositionend", compositionEnded, true);
      root.removeEventListener("input", inputChanged, true);
      document.removeEventListener("visibilitychange", suspend); media.removeEventListener("change", suspend);
    };
    binding.current = { root, scopeKey: input.scopeKey, generation: expected, release };
    observeChildren();
    const saved = paused.current; paused.current = null;
    if (saved && !input.followIntent.current && !input.composing() && !input.editing() && !unavailable(root)) {
      root.scrollTop = Math.min(Math.max(0, root.scrollHeight - root.clientHeight), Math.max(0, saved.scrollTop));
      input.onPreserveAnchor?.(); // The parent's opaque record anchor accounts for content growth while paused.
      previous.current = measure(root);
    }
    scopedQueue();
  });
  useEffect(() => () => { binding.current?.release(); paused.current = null; }, []);
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const root = event.currentTarget, next = measure(root);
    if (!binding.current || !owns(root, binding.current.generation) || !current.current.guards.enabled) return;
    if (performance.now() < gestureUntil.current && !programmatic.current && !input.restoring.current) {
      // Continued wheel/touch inertia is still the same reading gesture.
      gestureUntil.current = performance.now() + 800; current.current.onUserGesture?.();
    }
    const intent = customerTailScrollIntent(previous.current, next, {
      userGesture: performance.now() < gestureUntil.current, programmatic: programmatic.current || input.restoring.current,
      loadingEarlier: current.current.guards.loadingEarlier,
    });
    previous.current = next;
    if (intent === null || unavailable(root)) return;
    current.current.followIntent.current = intent;
    current.current.onReadingEarlier(!intent);
    if (!intent) current.current.onSaveAnchor();
  };
  return { onScroll, stopFollowingAnimation: stop };
}
