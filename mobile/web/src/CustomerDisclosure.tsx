import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import "./customerDisclosure.css";

export const CUSTOMER_DISCLOSURE_MS = 180;
type Props = { summary: ReactNode; children: ReactNode; className?: string; summaryClassName?: string;
  defaultOpen?: boolean; disabledMotion?: boolean };

/** Native details/summary semantics with an ephemeral, cancelable height transition. No preference is saved. */
export function CustomerDisclosure({ summary, children, className = "", summaryClassName, defaultOpen = false, disabledMotion = false }: Props) {
  const details = useRef<HTMLDetailsElement>(null), summaryRef = useRef<HTMLElement>(null), body = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(defaultOpen), [closing, setClosing] = useState(false);
  const transition = useRef<{ expanded: boolean; from: number } | null>(null);
  const animation = useRef<Animation | null>(null);
  const finish = (expanded: boolean) => {
    const previous = animation.current; animation.current = null;
    if (previous) { previous.onfinish = null; previous.oncancel = null; previous.cancel(); }
    transition.current = null; setOpen(expanded); setClosing(false);
  };
  const canAnimate = () => !disabledMotion && typeof document !== "undefined" && document.visibilityState !== "hidden"
    && !window.matchMedia("(prefers-reduced-motion: reduce)").matches
    && !window.matchMedia("(forced-colors: active)").matches
    && !details.current?.closest("[hidden],[inert]") && typeof body.current?.animate === "function";
  const toggle = () => {
    const expanded = closing || !open;
    const from = body.current?.getBoundingClientRect().height || 0;
    if (!expanded && body.current?.contains(document.activeElement)) summaryRef.current?.focus({ preventScroll: true });
    if (!canAnimate()) { finish(expanded); return; }
    animation.current?.cancel(); animation.current = null;
    transition.current = { expanded, from: open ? from : 0 };
    setOpen(true); setClosing(!expanded);
  };
  useLayoutEffect(() => {
    const intent = transition.current, element = body.current;
    if (!intent || !element) return;
    if (!canAnimate()) { finish(intent.expanded); return; }
    const to = intent.expanded ? element.scrollHeight : 0;
    const run = element.animate([{ height: `${intent.from}px`, opacity: intent.expanded ? .98 : 1 },
      { height: `${to}px`, opacity: intent.expanded ? 1 : .98 }], { duration: CUSTOMER_DISCLOSURE_MS, easing: "cubic-bezier(.2,.65,.3,1)" });
    animation.current = run;
    run.onfinish = () => { if (animation.current === run) finish(intent.expanded); };
  }, [open, closing, disabledMotion]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const colors = window.matchMedia("(forced-colors: active)");
    const suspend = () => {
      if (transition.current && !canAnimate()) finish(transition.current.expanded);
    };
    const observer = new MutationObserver(suspend);
    for (let ancestor: Element | null = details.current; ancestor; ancestor = ancestor.parentElement)
      observer.observe(ancestor, { attributes: true, attributeFilter: ["hidden", "inert"] });
    media.addEventListener("change", suspend); colors.addEventListener("change", suspend); document.addEventListener("visibilitychange", suspend);
    return () => {
      observer.disconnect(); media.removeEventListener("change", suspend); colors.removeEventListener("change", suspend); document.removeEventListener("visibilitychange", suspend);
      if (animation.current) { animation.current.onfinish = null; animation.current.cancel(); animation.current = null; }
    };
  }, [disabledMotion]);
  return <details ref={details} className={`customer-disclosure ${className}`.trim()} open={open}
    data-disclosure-closing={closing ? "true" : undefined}
    onToggle={event => {
      // A deliberate focus-to-step may open native details directly. Keep the next summary toggle in sync.
      if (!transition.current && !animation.current) setOpen(event.currentTarget.open);
    }}>
    <summary ref={summaryRef} className={summaryClassName} onClick={event => { event.preventDefault(); toggle(); }}>{summary}</summary>
    <div ref={body} className="customer-disclosure-body" inert={closing ? true : undefined}>{children}</div>
  </details>;
}
