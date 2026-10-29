import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { customerMotionDelay, type CustomerMotionEvent } from "./customerMotion";
import "./customerMotion.css";

export type CustomerMotionBoundaryProps = {
  event?: CustomerMotionEvent | null;
  layer?: number;
  interactive?: boolean;
  urgent?: boolean;
  className?: string;
  children: ReactNode;
};
/** A display-only layer: no delayed rendering, aria changes or disabled controls. */
export function CustomerMotionBoundary({ event, layer = 0, interactive = true, urgent = false, className = "", children }: CustomerMotionBoundaryProps) {
  const element = useRef<HTMLDivElement>(null);
  const cancelled = useRef(new Set<string>());
  const [, invalidate] = useState(0);
  const active = event && !urgent && !cancelled.current.has(event.id);
  useLayoutEffect(() => {
    if (!event || !element.current) return;
    const ancestors: HTMLElement[] = [];
    for (let node: HTMLElement | null = element.current; node; node = node.parentElement) ancestors.push(node);
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const colors = window.matchMedia("(forced-colors: active)");
    const cancelIfHidden = () => {
      if (cancelled.current.has(event.id) || !(media.matches || colors.matches || document.visibilityState === "hidden"
        || ancestors.some(node => node.hasAttribute("hidden") || node.hasAttribute("inert")))) return;
      cancelled.current.add(event.id);
      while (cancelled.current.size > 8) cancelled.current.delete(cancelled.current.values().next().value!);
      invalidate(value => value + 1);
    };
    cancelIfHidden();
    const observer = new MutationObserver(cancelIfHidden);
    for (const ancestor of ancestors) observer.observe(ancestor, {attributes:true,attributeFilter:["hidden","inert"]});
    document.addEventListener("visibilitychange", cancelIfHidden);
    media.addEventListener("change", cancelIfHidden);
    colors.addEventListener("change", cancelIfHidden);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", cancelIfHidden); media.removeEventListener("change", cancelIfHidden); colors.removeEventListener("change", cancelIfHidden); };
  }, [event?.id]);
  const style = active ? {
    "--customer-motion-delay": `${customerMotionDelay(layer)}ms`,
    "--customer-motion-duration": `${event.durationMs}ms`,
  } as CSSProperties : undefined;
  return <div ref={element} className={`customer-motion-boundary ${className}`.trim()}
    data-customer-motion={active ? event.id : undefined}
    data-customer-motion-kind={active ? event.kind : undefined}
    data-customer-motion-interactive={interactive ? "true" : "false"}
    data-customer-motion-urgent={urgent ? "true" : undefined}
    style={style}>{children}</div>;
}
