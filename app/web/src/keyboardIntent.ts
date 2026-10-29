type ShortcutEvent = {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
};

/** IME candidate confirmation must never submit a financial message. */
export function shouldSendOnShortcut(
  event: ShortcutEvent,
  composing = false,
): boolean {
  return event.key === "Enter"
    && Boolean(event.ctrlKey || event.metaKey)
    && !event.shiftKey && !event.altKey && !event.repeat
    && !composing && !event.isComposing && !event.nativeEvent?.isComposing
    && event.keyCode !== 229 && event.nativeEvent?.keyCode !== 229;
}
