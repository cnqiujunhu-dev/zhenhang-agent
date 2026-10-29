import { useRef, type RefObject } from "react";

type Input = {
  scopeKey: string;
  enabled: boolean;
  visible: boolean;
  composing: () => boolean;
  readingRoot: RefObject<HTMLDivElement | null>;
};

/** A focused welcome gesture hands focus to the stable reading region;
 * an unfocused native pointer gesture sends without moving another focus.
 * The existing sender still owns request authority, pending records and replies. */
export function useWelcomeSendFocus(input: Input) {
  const current = useRef(input); current.current = input;
  const claimed = useRef<string | null>(null);
  if (claimed.current !== input.scopeKey || !input.enabled) claimed.current = null;
  return (trigger: HTMLButtonElement, send: () => void, gesture?: MouseEvent) => {
    const value = current.current, root = value.readingRoot.current;
    if (input.scopeKey !== value.scopeKey || claimed.current === value.scopeKey || !value.enabled
      || !value.visible || value.composing() || !root || typeof document === "undefined"
      || typeof HTMLElement === "undefined" || !(trigger instanceof HTMLElement)
      || document.visibilityState !== "visible" || root.ownerDocument !== trigger.ownerDocument
      || !root.isConnected || !trigger.isConnected || trigger.disabled
      || !root.contains(trigger) || !root.getClientRects().length || !trigger.getClientRects().length
      || root.closest('[hidden], [inert], [aria-hidden="true"]')
      || trigger.closest('[hidden], [inert], [aria-hidden="true"]')
      || getComputedStyle(root).visibility !== "visible" || getComputedStyle(trigger).visibility !== "visible") return false;
    const ownsFocus = document.activeElement === trigger;
    // Some pointer/touch platforms activate buttons without focusing them.
    // An actual in-dispatch native click may send; a retained event may not.
    const pointerType = typeof PointerEvent !== "undefined" && gesture instanceof PointerEvent ? gesture.pointerType : "";
    const currentPointer = Boolean(gesture instanceof MouseEvent && gesture.isTrusted && gesture.type === "click"
      && (gesture.detail > 0 || ["mouse", "touch", "pen"].includes(pointerType))
      && gesture.eventPhase !== Event.NONE && gesture.target instanceof Node && trigger.contains(gesture.target));
    if (!ownsFocus && !currentPointer) return false;
    claimed.current = value.scopeKey;
    if (ownsFocus) {
      root.focus({ preventScroll: true });
      if (document.activeElement !== root) { claimed.current = null; return false; }
    }
    send();
    return true;
  };
}
