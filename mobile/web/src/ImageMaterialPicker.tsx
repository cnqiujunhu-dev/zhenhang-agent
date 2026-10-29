import type { ChangeEventHandler, ReactNode, RefObject } from "react";

/** The original input must survive the temporary hidden/identity-read phase of
 * Android's system picker. Availability controls only the visible button.
 */
export function ImageMaterialPicker({ inputRef, inputLabel, onChange, showButton, button }: {
  inputRef: RefObject<HTMLInputElement | null>;
  inputLabel: string;
  onChange: ChangeEventHandler<HTMLInputElement>;
  showButton: boolean;
  button: ReactNode;
}) {
  return <>
    <input ref={inputRef} type="file" multiple hidden accept="image/jpeg,image/png,image/webp"
      aria-label={inputLabel} onChange={onChange}/>
    {showButton && button}
  </>;
}

export function mayAcceptImageSelection(value: {
  intent: { owner: string } | null;
  currentOwner: string;
  authorized: boolean;
  readVisible: boolean;
  documentVisible: boolean;
  working: boolean;
  attachmentBusy: boolean;
}): boolean {
  return Boolean(value.intent && value.intent.owner === value.currentOwner && value.authorized
    && value.readVisible && value.documentVisible && !value.working && !value.attachmentBusy);
}
