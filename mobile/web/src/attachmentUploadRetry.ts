import type { AttachmentPurpose } from "./attachmentTypes";
import { safeSessionGet, safeSessionKeys, safeSessionSet } from "./safeStorage.ts";
export interface AttachmentUploadRecord {
  scope: string;
  file_sha256: string;
  purpose: AttachmentPurpose;
  idempotency_key: string;
  status: "UNKNOWN" | "ACKNOWLEDGED";
  attachment_id?: string;
}
export const attachmentUploadRecordKey = (
  scope: string,
  hash: string,
  purpose: AttachmentPurpose,
) => `zx_v3_upload:${scope}:${purpose}:${hash}`;
export function attachmentUploadRecord(
  value: unknown,
  scope: string,
  hash: string,
  purpose: AttachmentPurpose,
): AttachmentUploadRecord | null {
  if (!value || typeof value !== "object") return null;
  const row = value as AttachmentUploadRecord;
  return row.scope === scope &&
    row.file_sha256 === hash &&
    /^[0-9a-f]{64}$/i.test(row.file_sha256) &&
    row.purpose === purpose &&
    ["BILLING", "FRAUD"].includes(row.purpose) &&
    typeof row.idempotency_key === "string" &&
    row.idempotency_key.length >= 1 &&
    row.idempotency_key.length <= 120 &&
    ["UNKNOWN", "ACKNOWLEDGED"].includes(row.status)
    ? {
        scope: row.scope,
        file_sha256: row.file_sha256,
        purpose: row.purpose,
        idempotency_key: row.idempotency_key,
        status: row.status,
        ...(typeof row.attachment_id === "string"
          ? { attachment_id: row.attachment_id }
          : {}),
      }
    : null;
}
export function readUnknownAttachmentUpload(scope: string) {
  for (const key of safeSessionKeys().reverse()) {
    if (!key.startsWith(`zx_v3_upload:${scope}:`)) continue;
    try {
      const value = JSON.parse(safeSessionGet(key) || "null");
      const record = attachmentUploadRecord(
        value,
        scope,
        value?.file_sha256,
        value?.purpose,
      );
      if (record?.status === "UNKNOWN") return record;
    } catch {}
  }
  return null;
}
export class AttachmentUploadStorageError extends Error {
  constructor() {
    super("材料上传恢复标识无法可靠保存，未发送图片。请恢复浏览器存储后再上传；仍可读取和核对已有材料。");
    this.name = "AttachmentUploadStorageError";
  }
}
/** A write is usable for upload recovery only after its exact value can be read back. */
export function persistAttachmentUploadRecord(record: AttachmentUploadRecord) {
  const key = attachmentUploadRecordKey(record.scope, record.file_sha256, record.purpose);
  const value = JSON.stringify(record);
  return safeSessionSet(key, value) && safeSessionGet(key) === value;
}
/** Never replace an unreadable original request with a new upload identity. */
export function prepareAttachmentUploadRecord(
  scope: string,
  hash: string,
  purpose: AttachmentPurpose,
  key: string,
): AttachmentUploadRecord {
  const recordKey = attachmentUploadRecordKey(scope, hash, purpose);
  let saved: string | null;
  try { saved = globalThis.sessionStorage.getItem(recordKey); }
  catch { throw new AttachmentUploadStorageError(); }
  let value: unknown = null;
  try { value = JSON.parse(saved || "null"); } catch {}
  const record = attachmentUploadRecord(value, scope, hash, purpose)
    || newAttachmentUploadRecord(scope, hash, purpose, key);
  const pending: AttachmentUploadRecord = { ...record, status: "UNKNOWN" };
  if (!persistAttachmentUploadRecord(pending)) throw new AttachmentUploadStorageError();
  return pending;
}
export function newAttachmentUploadRecord(
  scope: string,
  hash: string,
  purpose: AttachmentPurpose,
  key: string,
): AttachmentUploadRecord {
  if (!scope || !/^[0-9a-f]{64}$/i.test(hash) || !key)
    throw new Error("材料重试记录缺少当前身份、文件摘要或请求标识。");
  return {
    scope,
    file_sha256: hash,
    purpose,
    idempotency_key: key,
    status: "UNKNOWN",
  };
}
export async function originalFileSha256(file: Pick<File, "arrayBuffer">) {
  if (!crypto.subtle)
    throw new Error(
      "当前页面不支持安全文件摘要，请使用HTTPS或本机入口；未发送图片。",
    );
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
