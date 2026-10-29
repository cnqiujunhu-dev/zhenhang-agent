import { ApiError } from "./api.ts";
import type { ReviewedEvidenceRef } from "./conversationTypes";
import type { AttachmentCatalog, AttachmentPurpose, AttachmentView } from "./attachmentTypes";
import { documentFields } from "./attachmentTypes.ts";
import { validateAttachmentFile } from "./attachmentState.ts";
import {
  AttachmentUploadStorageError, attachmentUploadRecord, attachmentUploadRecordKey,
  originalFileSha256, persistAttachmentUploadRecord, prepareAttachmentUploadRecord,
  type AttachmentUploadRecord,
} from "./attachmentUploadRetry.ts";

export const MAX_ATTACHMENT_BATCH = 5;
export type AttachmentInitialSelection = { id: string; token: string; actorScope: string; files: readonly File[] };
export type AttachmentBatchItem = {
  index: number; file: File; status: "PENDING" | "HASHING" | "UPLOADING" | "ACKNOWLEDGED" | "RECOVERED" | "UNKNOWN" | "INVALID" | "DUPLICATE";
  record?: AttachmentUploadRecord; attachment?: AttachmentView; attachmentId?: string; error?: string; recoverySaved?: boolean;
};
export function validateAttachmentSelection(files: readonly File[]) {
  if (!Array.isArray(files) || !files.length || files.length > MAX_ATTACHMENT_BATCH)
    throw new Error("请选择1至5张图片。超出上限时不会发送任何图片。");
}

/** Read only previously acknowledged metadata. Selecting it never resends the
 * image or resets its idempotency state; the panel must GET its current access. */
function acknowledgedRecord(scope: string, hash: string, purpose: AttachmentPurpose) {
  let saved: string | null;
  try { saved = globalThis.sessionStorage.getItem(attachmentUploadRecordKey(scope, hash, purpose)); }
  catch { throw new AttachmentUploadStorageError(); }
  let value: unknown; try { value = JSON.parse(saved || "null"); } catch { return null; }
  const record = attachmentUploadRecord(value, scope, hash, purpose);
  return record?.status === "ACKNOWLEDGED" && typeof record.attachment_id === "string" && record.attachment_id.trim() ? record : null;
}
export async function postAttachmentImage(file: File, purpose: AttachmentPurpose, token: string, record: AttachmentUploadRecord) {
  const form = new FormData(); form.append("file", file); form.append("purpose", purpose);
  form.append("cloud_processing_consent", "true");
  const response = await fetch("/api/v3/attachments", {method:"POST",headers:{Authorization:`Bearer ${token}`,"Idempotency-Key":record.idempotency_key},body:form,signal:AbortSignal.timeout(30000)});
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new CustomEvent("zx-auth-invalid",{detail:{token}}));
    throw new ApiError(data?.error?.message || "材料受理未完成，请先读取材料列表核对，勿盲目重复上传。",response.status,data?.error?.code || "UPLOAD_ERROR");
  }
  if (typeof data?.attachment_id !== "string" || !data.attachment_id.trim())
    throw new Error("材料响应缺少标识，请在材料列表核对受理状态。");
  return data as AttachmentView;
}

/** Each file keeps its original hash + purpose + request identity. A possibly
 * received POST stops the remaining batch; explicit retry uses the same key.
 * Neither file selection nor this helper may manufacture cloud consent. */
export async function uploadAttachmentBatch(options: {
  files: readonly File[]; token: string; actorScope: string; purpose: AttachmentPurpose; consent: boolean;
  limits: AttachmentCatalog["limits"]; isCurrent: () => boolean; onItem: (item: AttachmentBatchItem) => void;
  previousItems?: readonly AttachmentBatchItem[];
  post?: typeof postAttachmentImage;
}) {
  validateAttachmentSelection(options.files);
  if (options.consent !== true || !options.token || !options.actorScope || !["BILLING","FRAUD"].includes(options.purpose) || !options.isCurrent()) return [];
  const results: AttachmentBatchItem[] = [], seen = new Map<string, AttachmentBatchItem>();
  const emit = (item: AttachmentBatchItem) => { if (options.isCurrent()) options.onItem(item); return item; };
  for (const [index,file] of options.files.entries()) {
    if (!options.isCurrent()) break;
    let record: AttachmentUploadRecord | undefined;
    let sent = false;
    try {
      validateAttachmentFile(file,options.limits);
      emit({index,file,status:"HASHING"});
      const hash = await originalFileSha256(file);
      if (!options.isCurrent()) break;
      const existing = options.previousItems?.find(item=>item.file === file && ["ACKNOWLEDGED","RECOVERED","DUPLICATE"].includes(item.status)
        && item.record?.scope === options.actorScope && item.record.file_sha256 === hash && item.record.purpose === options.purpose
        && item.record.status==="ACKNOWLEDGED" && item.attachmentId && item.record.attachment_id===item.attachmentId);
      const duplicate = seen.get(hash);
      if (duplicate) {
        const item=emit({index,file,status:"DUPLICATE",record:duplicate.record,attachmentId:duplicate.attachmentId,recoverySaved:duplicate.recoverySaved});results.push(item);continue;
      }
      const acknowledged = existing?.record || acknowledgedRecord(options.actorScope,hash,options.purpose);
      if (acknowledged?.attachment_id || existing?.attachmentId) {
        const recoverySaved=existing?.recoverySaved===false ? Boolean(acknowledged && persistAttachmentUploadRecord(acknowledged)) : true;
        const item=emit({index,file,status:"RECOVERED",record:acknowledged || undefined,attachment:existing?.attachment,attachmentId:existing?.attachmentId || acknowledged!.attachment_id,recoverySaved});
        results.push(item);seen.set(hash,item);
        if(!recoverySaved) break;
        continue;
      }
      record=prepareAttachmentUploadRecord(options.actorScope,hash,options.purpose,crypto.randomUUID());
      emit({index,file,status:"UPLOADING",record});
      if (!options.isCurrent()) break;
      sent=true;
      const attachment=await (options.post || postAttachmentImage)(file,options.purpose,options.token,record);
      if (!options.isCurrent()) break;
      record={...record,status:"ACKNOWLEDGED",attachment_id:attachment.attachment_id};
      const recoverySaved=persistAttachmentUploadRecord(record);
      const item=emit({index,file,status:"ACKNOWLEDGED",record,attachment,attachmentId:attachment.attachment_id,recoverySaved});
      results.push(item);seen.set(hash,item);
      // Never continue creating new identities when recovery persistence fails.
      if (!recoverySaved) break;
    } catch(failure) {
      if (!options.isCurrent()) break;
      const item=emit({index,file,status:sent ? "UNKNOWN" : "INVALID",record,error:failure instanceof Error ? failure.message : "材料上传结果未确定，请先核对列表。"});
      results.push(item);
      if (sent || failure instanceof AttachmentUploadStorageError) break;
    }
  }
  return results;
}

export function reviewedBatchReference(view: AttachmentView, now = Date.now()): ReviewedEvidenceRef | null {
  const reviewed=view.latest_review;
  if(view.state!=="REVIEWED" || !Number.isSafeInteger(view.version) || view.version<1 || !/^[a-f0-9]{64}$/i.test(view.sha256)
    || !Number.isFinite(Date.parse(view.expires_at)) || Date.parse(view.expires_at)<=now || !reviewed || reviewed.user_confirmed!==true
    || reviewed.source_kind!=="USER_VALIDATED_DOCUMENT" || reviewed.attachment_version!==view.version || reviewed.sha256!==view.sha256
    || typeof reviewed.review_id!=="string" || !reviewed.review_id.trim() || !Array.isArray(reviewed.selected_fields) || !reviewed.selected_fields.length
    || new Set(reviewed.selected_fields).size!==reviewed.selected_fields.length || reviewed.selected_fields.some(field=>!documentFields.includes(field)))
    return null;
  return {review_id:reviewed.review_id,attachment_version:reviewed.attachment_version,sha256:reviewed.sha256};
}
