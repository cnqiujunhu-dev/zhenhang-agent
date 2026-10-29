import type { JsonValue } from "./types";
export type AttachmentPurpose = "BILLING" | "FRAUD";
export const documentFields = [
  "amount_minor",
  "currency",
  "date",
  "merchant",
  "counterparty",
  "recipient_card",
  "status",
  "chat_text",
  "role_context",
] as const;
export type DocumentField = (typeof documentFields)[number];
export type DocumentFields = Record<DocumentField, JsonValue>;
export interface ReviewedDocument {
  review_id: string;
  source_kind: "USER_VALIDATED_DOCUMENT";
  attachment_version: number;
  sha256: string;
  selected_fields: DocumentField[];
  fields: Partial<DocumentFields>;
  user_confirmed: boolean;
  created_at: string;
}
export interface AttachmentView {
  attachment_id: string;
  display_name?: string;
  purpose: AttachmentPurpose;
  state:
    | "QUEUED"
    | "PROCESSING"
    | "EXTRACTED"
    | "REVIEWED"
    | "FAILED"
    | "EXPIRED"
    | "DELETED";
  version: number;
  sha256: string;
  mime_type: string;
  byte_size: number;
  width: number;
  height: number;
  created_at: string;
  expires_at: string;
  preview_url: string | null;
  preview_expires_at: string | null;
  extraction: null | {
    schema_version: "vision-evidence-v1";
    fields: DocumentFields;
    evidence: {
      field: string;
      text: string;
      box: null | number[];
      confidence: null | number;
    }[];
    uncertainties: string[];
    tainted: boolean;
    bank_authoritative: boolean;
    provider: string;
    model: string;
    model_http_attempts: number;
    usage: Record<string, JsonValue> | null;
  };
  comparison: null | {
    status: string;
    transactions: Record<string, JsonValue>[];
    subscriptions: Record<string, JsonValue>[];
    conflicts: JsonValue[];
    bank_data_changed: boolean;
    screenshot_proves_posting: boolean;
  };
  fraud: null | {
    status: string;
    signals: { code: string; label: string; snippet: string }[];
    safety_guaranteed: boolean;
  };
  latest_review: ReviewedDocument | null;
  error: null | { code: string; message: string };
  warnings: string[];
}
export interface AttachmentCatalog {
  attachments: AttachmentView[];
  limits: {
    max_bytes: number;
    max_pixels: number;
    max_edge: number;
    allowed_mime_types: string[];
  };
  cloud_processing: {
    provider: string;
    model: string;
    local_fallback: boolean;
  };
}
