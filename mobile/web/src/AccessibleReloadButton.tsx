import { useEffect, useRef, useState, type ReactNode } from "react";

type Props = {
  scopeKey: string;
  blocked: boolean;
  onReload: () => void | Promise<unknown>;
  label: string;
  className?: string;
  children: ReactNode;
};

/** Keep the focused control available to Tab while denying duplicate reads.
 * Local flight is only an input lock, never a successful read receipt. */
export function AccessibleReloadButton({ scopeKey, blocked, onReload, label, className, children }: Props) {
  const current = useRef({ scopeKey, blocked, onReload });
  current.current = { scopeKey, blocked, onReload };
  const sequence = useRef(0);
  const flight = useRef<{ scopeKey: string; sequence: number } | null>(null);
  const mounted = useRef(true);
  const [pending, setPending] = useState<{ scopeKey: string; sequence: number } | null>(null);
  if (flight.current?.scopeKey !== scopeKey) flight.current = null;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; flight.current = null; sequence.current += 1; };
  }, []);
  const unavailable = blocked || Boolean(pending?.scopeKey === scopeKey && flight.current === pending);
  return <button type="button" className={className} aria-label={label} aria-disabled={unavailable}
    onClick={() => {
      const value = current.current;
      if (!mounted.current || scopeKey !== value.scopeKey || value.blocked || flight.current
        || typeof document === "undefined" || document.visibilityState !== "visible") return;
      const attempt = { scopeKey, sequence: ++sequence.current };
      flight.current = attempt; setPending(attempt);
      const release = () => {
        if (!mounted.current || current.current.scopeKey !== attempt.scopeKey || flight.current !== attempt) return;
        flight.current = null;
        setPending(previous => previous === attempt ? null : previous);
      };
      try { Promise.resolve(value.onReload()).then(release, release); }
      catch (error) { release(); throw error; }
    }}>{children}</button>;
}
