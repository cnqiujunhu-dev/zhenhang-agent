const MAX_WAIT_MS = 60_000;
const HTTP_DATE = /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/;

/** A bounded delay hint only; this function never retries a request. */
export function retryAfterDelay(header: string | null, nowMs = Date.now()): number | undefined {
  if (!header || header.length > 128 || !Number.isFinite(nowMs)) return undefined;
  const value = header.trim();
  if (/^\d+$/.test(value)) return Math.min(MAX_WAIT_MS, Number(value) * 1000);
  if (!HTTP_DATE.test(value)) return undefined;
  const target = Date.parse(value);
  // Round-trip rejects impossible calendar dates that Date.parse may normalize.
  if (!Number.isFinite(target) || new Date(target).toUTCString() !== value) return undefined;
  return Math.max(0, Math.min(MAX_WAIT_MS, target - nowMs));
}
