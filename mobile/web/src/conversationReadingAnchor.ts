/** Pure reading geometry; caller keeps the anchor in memory, never storage. */
export type ReadingContext = { actor: string; conversationId: string };
export type ReadingViewport = { top: number; height: number; scrollTop: number; scrollHeight: number };
export type ReadingRegion = { key: string; top: number; height: number };
export type ReadingTurn = { turnId: string; top: number; height: number; regions?: ReadingRegion[] };
export type ReadingAnchor = { context: ReadingContext; turnId: string; offset: number; region?: { key: string; offset: number } };

export function shouldFollowNewContent(viewport: ReadingViewport, options: {
  readingEarlier?: boolean; composing?: boolean; interactionPending?: boolean; threshold?: number;
} = {}): boolean {
  if (options.readingEarlier || options.composing || options.interactionPending) return false;
  return viewport.scrollHeight - viewport.scrollTop - viewport.height <= (options.threshold ?? 160);
}

export function captureReadingAnchor(
  context: ReadingContext, viewport: ReadingViewport, turns: ReadingTurn[],
): ReadingAnchor | null {
  if (!context.actor || !context.conversationId || !Number.isFinite(viewport.top) || viewport.height <= 0) return null;
  const turn = turns.find(item => item.height > 0 && Number.isFinite(item.top) &&
    item.top + item.height > viewport.top && item.top < viewport.top + viewport.height);
  if (!turn) return null;
  const visible = (item: ReadingRegion) => item.key && item.height > 0 && Number.isFinite(item.top)
    && item.top + item.height > viewport.top && item.top < viewport.top + viewport.height;
  const region = turn.regions?.find(visible);
  return { context: { ...context }, turnId: turn.turnId, offset: turn.top - viewport.top,
    ...(region ? { region: { key: region.key, offset: region.top - viewport.top } } : {}) };
}

export function resolveReadingAnchor(
  anchor: ReadingAnchor | null, context: ReadingContext, viewport: ReadingViewport,
  turns: ReadingTurn[], fallbackScrollTop: number,
): number | null {
  if (!anchor || anchor.context.actor !== context.actor || anchor.context.conversationId !== context.conversationId) return null;
  const target = turns.find(item => item.turnId === anchor.turnId && Number.isFinite(item.top));
  const region = anchor.region && target?.regions?.find(item => item.key === anchor.region!.key && Number.isFinite(item.top));
  const proposed = region ? viewport.scrollTop + region.top - viewport.top - anchor.region!.offset
    : target ? viewport.scrollTop + target.top - viewport.top - anchor.offset : fallbackScrollTop;
  if (!Number.isFinite(proposed)) return null;
  return Math.min(Math.max(0, viewport.scrollHeight - viewport.height), Math.max(0, proposed));
}
