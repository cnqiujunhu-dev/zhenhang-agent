import type {AttachmentCatalog} from "./attachmentTypes";
import type {AttachmentBatchItem} from "./attachmentBatchUpload";

/** Metadata only. A list refresh must never replace the selected editor DTO,
 * change manually reviewed fields or create another upload/recognition job. */
export function attachmentCatalogPending(catalog:AttachmentCatalog|null, batch:readonly AttachmentBatchItem[]) {
  const rows=catalog?.attachments || [];
  return rows.some(row=>["QUEUED","PROCESSING"].includes(row.state))
    || batch.some(item=>item.attachmentId && !rows.some(row=>row.attachment_id===item.attachmentId));
}
export function attachmentPanelVisible(element:HTMLElement|null) {
  if(!element?.isConnected || element.ownerDocument.visibilityState!=="visible" || !element.getClientRects().length
    ||element.closest('[hidden],[inert],[aria-hidden="true"]'))return false;
  const style=element.ownerDocument.defaultView?.getComputedStyle(element);
  return Boolean(style&&style.visibility!=="hidden"&&style.display!=="none");
}
