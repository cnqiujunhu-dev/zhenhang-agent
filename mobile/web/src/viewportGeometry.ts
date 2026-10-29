export type VisibleViewportGeometry = {
  top: number;
  bottom: number;
  height: number;
  scale: number;
  layoutHeight: number;
};

export function viewportGeometry(
  layoutHeight: number,
  visual?: { height?: number; offsetTop?: number; scale?: number } | null,
): VisibleViewportGeometry {
  const layout = Number.isFinite(layoutHeight) && layoutHeight > 0 ? layoutHeight : 800;
  const height = Number.isFinite(visual?.height) && visual!.height! > 0
    ? Math.min(layout, visual!.height!) : layout;
  const offset = Number.isFinite(visual?.offsetTop) ? visual!.offsetTop! : 0;
  const top = Math.max(0, Math.min(layout - height, offset));
  const scale = Number.isFinite(visual?.scale) && visual!.scale! > 0 ? visual!.scale! : 1;
  return { top, bottom: top + height, height, scale, layoutHeight: layout };
}

/** CSS-pixel delta for the current focused control only, not a keyboard detector. */
export function focusedElementScrollDelta(
  rect: { top: number; bottom: number },
  viewport: VisibleViewportGeometry,
  options: { focused: boolean; inset?: number },
): number {
  if (!options.focused || !Number.isFinite(rect.top) || !Number.isFinite(rect.bottom)
    || rect.bottom < rect.top || !Number.isFinite(viewport.top)
    || !Number.isFinite(viewport.bottom) || viewport.bottom <= viewport.top) return 0;
  const requestedInset = options.inset ?? 12;
  const inset = Math.max(0, Math.min((viewport.bottom - viewport.top) / 4,
    Number.isFinite(requestedInset) ? requestedInset : 12));
  const top = viewport.top + inset, bottom = viewport.bottom - inset;
  if (rect.top < top) return rect.top - top;
  if (rect.bottom > bottom) {
    // Oversized controls cannot fit; keep their start visible without scroll oscillation.
    return rect.bottom - rect.top > bottom - top
      ? rect.top > bottom ? rect.top - top : 0
      : rect.bottom - bottom;
  }
  return 0;
}
