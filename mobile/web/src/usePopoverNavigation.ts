import { useEffect, useLayoutEffect, useRef } from "react";

type Input = {
  scope: string;
  visible: boolean;
  open: boolean;
  setOpen: (open: boolean) => void;
};

/** Local surface navigation only: no delayed focus, requests or stored return target. */
export function usePopoverNavigation(input: Input) {
  const current = useRef(input);
  current.current = input;
  const openingScope = useRef<string | null>(null);
  const previousScope = useRef(input.scope);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const open = input.open && input.visible && openingScope.current === input.scope;

  const dismiss = (restoreFocus = false) => {
    const value = current.current;
    const owned = value.open && openingScope.current === value.scope;
    if (restoreFocus && owned && value.visible && typeof document !== "undefined"
      && document.visibilityState === "visible") {
      const trigger = triggerRef.current;
      const active = document.activeElement;
      // Escape/modal handoff may return our own focus; outside navigation owns its focus.
      if ((panelRef.current?.contains(active) || active === trigger) && trigger?.isConnected
        && !trigger.matches(':disabled, [aria-disabled="true"]')
        && !trigger.closest("[hidden], [inert]") && trigger.getClientRects().length > 0
        && getComputedStyle(trigger).visibility === "visible") {
        trigger.focus({ preventScroll: true });
      }
    }
    openingScope.current = null;
    value.setOpen(false);
  };
  const toggle = () => {
    const value = current.current;
    if (value.open && openingScope.current === value.scope) { dismiss(); return; }
    if (!value.visible || typeof document === "undefined" || document.visibilityState !== "visible") return;
    openingScope.current = value.scope;
    value.setOpen(true);
  };

  useLayoutEffect(() => {
    if (previousScope.current !== input.scope || !input.visible) dismiss();
    previousScope.current = input.scope;
  }, [input.scope, input.visible]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const outside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !panelRef.current?.contains(target) && !triggerRef.current?.contains(target)) dismiss();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      dismiss(true);
    };
    const visibility = () => { if (document.visibilityState !== "visible") dismiss(); };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [open]);

  return { open, triggerRef, panelRef, toggle, dismiss };
}
