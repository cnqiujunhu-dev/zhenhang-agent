import { useSyncExternalStore } from "react";
type ReadVisibility = { visible: boolean; epoch: number };
const listeners = new Set<() => void>();
let snapshot: ReadVisibility = { visible: typeof document === "undefined" || document.visibilityState === "visible", epoch: 0 };
const serverSnapshot: ReadVisibility = { visible: true, epoch: 0 };
function changed() {
  const visible = document.visibilityState === "visible";
  if (visible === snapshot.visible) return;
  snapshot = { visible, epoch: snapshot.epoch + 1 };
  for (const notify of listeners) notify();
}
function subscribe(notify: () => void) {
  listeners.add(notify);
  if (listeners.size === 1) { changed(); document.addEventListener("visibilitychange", changed); }
  return () => {
    listeners.delete(notify);
    if (!listeners.size) document.removeEventListener("visibilitychange", changed);
  };
}
/** One document listener; hidden transitions invalidate read authorization epochs. */
export function useReadVisibility() {
  return useSyncExternalStore(subscribe, () => snapshot, () => serverSnapshot);
}
