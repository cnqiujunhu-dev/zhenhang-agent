import type { JsonValue, LayaReadonlyCandidateEnvelope } from "../types";

type Data = Record<string, unknown>;
export type LayaReadonlyObservationView =
  | {kind: "valid"; envelope: LayaReadonlyCandidateEnvelope}
  | {kind: "missing" | "invalid"; reason: string};
const object = (value: unknown): value is Data => Boolean(value && typeof value === "object" && !Array.isArray(value));
const exactKeys = (value: Data, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const kinds = new Set(["BILL_ANALYSIS", "CARD_STATUS", "SUBSCRIPTION_LIST", "WEALTH_COMPARE"]);
export const layaReadonlyReasons: Record<string, string> = {
  DEADLINE_EXCEEDED: "观察超出时间预算",
  REAL_PINNED_SDK_REQUIRED: "缺少可核验的固定 SDK",
  FINGERPRINT_MISMATCH: "固定指纹不匹配",
  SERVER_READ_GROUNDING_ABSTAINED: "服务端只读范围核对弃权",
  INVALID_OR_TRUNCATED_RESPONSE: "响应不完整或已截断",
  CLASSIFIER_OTHER: "分类结果为其他请求",
  LOW_CONFIDENCE_OR_MARGIN: "分数或候选间隔未达到实验阈值",
  SERVER_GROUNDING_DISAGREEMENT: "候选与服务端只读范围不一致",
  PINNED_CLASSIFIER_AGREES_WITH_READ_GROUNDING: "固定分类候选与服务端只读范围一致",
  OBSERVER_BUSY: "观察后端繁忙",
  SDK_INFERENCE_ERROR: "SDK 推理返回错误",
};

/** Closed v1 display guard, not an independent SDK or fingerprint verifier. */
export function inspectLayaReadonlyObservation(advice: unknown): LayaReadonlyObservationView {
  if (!object(advice)) return {kind: "missing", reason: "该计划版本未记录只读候选观察。"};
  if (advice.readonly_candidate_error !== undefined && advice.readonly_candidate_error !== null)
    return {kind: "invalid", reason: advice.readonly_candidate_error === "REAL_SIDECAR_REQUIRED"
      ? "服务端未确认真实 sidecar，SDK 运行状态未知。" : "服务端候选格式或固定指纹核验未通过，观察状态未知。"};
  const value = advice.readonly_candidate;
  if (value === undefined || value === null) return {kind: "missing", reason: "该计划版本未记录只读候选观察。"};
  const invalid = (): LayaReadonlyObservationView => ({kind: "invalid", reason: "候选字段缺失、格式错误或相互矛盾，观察状态未知。"});
  if (!object(value) || !exactKeys(value, ["schema_version", "pin", "source_sha256", "inference_input_sha256", "quality_gate_passed", "adoption_enabled", "observation"])
    || value.schema_version !== "laya-readonly-candidate-v1" || value.quality_gate_passed !== false || value.adoption_enabled !== false
    || !hash(value.source_sha256) || !hash(value.inference_input_sha256) || !object(value.pin)
    || !exactKeys(value.pin, ["identity_sha256", "questions_sha256", "policy_sha256", "manifest_sha256"])
    || !Object.values(value.pin).every(hash) || !object(value.observation)) return invalid();
  const observation = value.observation;
  if (!exactKeys(observation, ["status", "reason", "candidate_kind", "calibrated_score", "score_is_correctness_probability", "sdk_inference_used", "latency_ms", "mode", "authority", "adopted"])
    || typeof observation.status !== "string" || !["ABSTAIN", "EXPERIMENTAL_CANDIDATE"].includes(observation.status)
    || typeof observation.reason !== "string" || !Object.hasOwn(layaReadonlyReasons, observation.reason)
    || observation.score_is_correctness_probability !== false || typeof observation.sdk_inference_used !== "boolean"
    || observation.mode !== "SHADOW" || observation.authority !== "OBSERVATION_ONLY" || observation.adopted !== false
    || observation.latency_ms !== null && (typeof observation.latency_ms !== "number" || !Number.isFinite(observation.latency_ms) || observation.latency_ms < 0 || observation.latency_ms > 60000)) return invalid();
  if (observation.status === "ABSTAIN") {
    if (observation.candidate_kind !== null || observation.calibrated_score !== null) return invalid();
  } else if (typeof observation.candidate_kind !== "string" || !kinds.has(observation.candidate_kind)
    || typeof observation.calibrated_score !== "number" || !Number.isFinite(observation.calibrated_score)
    || observation.calibrated_score < 0 || observation.calibrated_score > 1 || observation.sdk_inference_used !== true
    || observation.reason !== "PINNED_CLASSIFIER_AGREES_WITH_READ_GROUNDING") return invalid();
  return {kind: "valid", envelope: value as unknown as LayaReadonlyCandidateEnvelope};
}

/** Candidate bodies use the guarded card, never an untyped technical dump. */
export function withoutLayaReadonlyPayload(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(withoutLayaReadonlyPayload);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== "readonly_candidate" && key !== "readonly_candidate_error")
    .map(([key, item]) => [key, withoutLayaReadonlyPayload(item)]));
  return value;
}
