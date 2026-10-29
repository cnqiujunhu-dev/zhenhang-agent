import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ResourceReadArrivalLedger, type ResourceArrivalSnapshot } from "./resourceArrivalLedger";

export function useResourceReadArrival(snapshot: ResourceArrivalSnapshot) {
  const ledger = useRef<ResourceReadArrivalLedger | null>(null);
  if (!ledger.current) ledger.current = new ResourceReadArrivalLedger();
  const [receipt, setReceipt] = useState<{ id: string; scopeKey: string; generation: number } | null>(null);
  const [blocked, setBlocked] = useState(() => typeof document === "undefined" || typeof window === "undefined"
    || document.visibilityState !== "visible" || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    || window.matchMedia("(forced-colors: active)").matches);
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)"), forced = window.matchMedia("(forced-colors: active)");
    const update = () => setBlocked(document.visibilityState !== "visible" || reduced.matches || forced.matches);
    document.addEventListener("visibilitychange", update); reduced.addEventListener("change", update); forced.addEventListener("change", update);
    return () => { document.removeEventListener("visibilitychange", update); reduced.removeEventListener("change", update); forced.removeEventListener("change", update); };
  }, []);
  useLayoutEffect(() => {
    const id = ledger.current!.observe({ ...snapshot, suppressed: Boolean(blocked || snapshot.suppressed) });
    if (id) setReceipt({ id, scopeKey: snapshot.scopeKey, generation: snapshot.generation });
    else setReceipt(null);
  }, [snapshot.scopeKey, snapshot.generation, snapshot.state, snapshot.visible, snapshot.count, snapshot.suppressed, blocked]);
  useEffect(() => {
    if (!receipt) return;
    // Expire the decorative receipt, not the data. Opening an old disclosure
    // later must not replay an arrival on newly mounted old records.
    const timeout = window.setTimeout(() => setReceipt(null), 360);
    return () => window.clearTimeout(timeout);
  }, [receipt?.id]);
  return !blocked && !snapshot.suppressed && snapshot.visible && snapshot.state === "READY"
    && receipt?.scopeKey === snapshot.scopeKey && receipt.generation === snapshot.generation ? receipt.id : null;
}

/** Only the decorative edge animates. Controls, amounts and warnings stay still. */
export function ResourceReadArrival({ receipt, children }: { receipt: string | null; children: ReactNode }) {
  const element = useRef<HTMLDivElement>(null), cancelled = useRef<string | null>(null);
  const [, invalidate] = useState(0);
  useLayoutEffect(() => {
    if (!receipt || !element.current) return;
    const ancestors: HTMLElement[] = [];
    for (let node: HTMLElement | null = element.current; node; node = node.parentElement) ancestors.push(node);
    const cancel = () => {
      if (cancelled.current === receipt || !ancestors.some(node => node.hasAttribute("hidden") || node.hasAttribute("inert"))) return;
      cancelled.current = receipt; invalidate(value => value + 1);
    };
    cancel(); const observer = new MutationObserver(cancel);
    for (const ancestor of ancestors) observer.observe(ancestor, { attributes:true, attributeFilter:["hidden", "inert"] });
    return () => observer.disconnect();
  }, [receipt]);
  return <div ref={element} className="resource-read-arrival" data-resource-arrival={receipt && cancelled.current !== receipt ? receipt : undefined}>{children}</div>;
}
