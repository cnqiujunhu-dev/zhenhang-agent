/** Presentation-only geometry. No task state or bank fields are changed here. */
export type CustomerTailViewport = { scrollTop: number; scrollHeight: number; height: number };
export type CustomerTailGuards = {
  enabled: boolean; readingEarlier: boolean; loadingEarlier: boolean; composing: boolean; editing: boolean;
};
export const CUSTOMER_TAIL_THRESHOLD = 96;
export const CUSTOMER_TAIL_SCROLL_MS = 180;

export function customerTailTarget(viewport: CustomerTailViewport): number | null {
  if (![viewport.scrollTop, viewport.scrollHeight, viewport.height].every(Number.isFinite)
    || viewport.height <= 0 || viewport.scrollHeight < 0) return null;
  return Math.max(0, viewport.scrollHeight - viewport.height);
}

export function canFollowCustomerTail(followIntent: boolean, guards: CustomerTailGuards): boolean {
  return followIntent && guards.enabled && !guards.readingEarlier && !guards.loadingEarlier && !guards.composing && !guards.editing;
}

/** Content growth is not a user scroll. Only an actual gesture or unambiguous upward scroll changes intent. */
export function customerTailScrollIntent(previous: CustomerTailViewport | null, next: CustomerTailViewport,
  options: { userGesture: boolean; programmatic: boolean; loadingEarlier: boolean; threshold?: number }): boolean | null {
  const target = customerTailTarget(next);
  if (target === null || options.programmatic || options.loadingEarlier) return null;
  const dimensionsChanged = !previous || Math.abs(previous.scrollHeight - next.scrollHeight) > 1 || Math.abs(previous.height - next.height) > 1;
  const deliberatelyUp = Boolean(previous && !dimensionsChanged && next.scrollTop < previous.scrollTop - 1);
  if (!options.userGesture && !deliberatelyUp) return null;
  return target - Math.max(0, next.scrollTop) <= (options.threshold ?? CUSTOMER_TAIL_THRESHOLD);
}

export function customerTailFrame(start: number, target: number, elapsedMs: number, reducedMotion: boolean): number {
  if (![start, target, elapsedMs].every(Number.isFinite)) return Math.max(0, Number.isFinite(target) ? target : 0);
  if (reducedMotion || elapsedMs >= CUSTOMER_TAIL_SCROLL_MS) return Math.max(0, target);
  const progress = Math.max(0, Math.min(1, elapsedMs / CUSTOMER_TAIL_SCROLL_MS));
  const eased = 1 - (1 - progress) ** 3;
  return Math.max(0, start + (target - start) * eased);
}
