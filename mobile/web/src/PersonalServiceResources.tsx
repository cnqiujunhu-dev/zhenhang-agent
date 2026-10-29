import { useEndpoint } from "./useEndpoint";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { PersonalServiceCards } from "./PersonalServiceCards";
import type { JsonValue } from "./types";

const registry: Record<string, { action: string; path: string }> = {
  assets: { action: "ASSET_POSITIONS_READ", path: "/api/v2/assets/positions" },
  membership: { action: "MEMBERSHIP_VIEW", path: "/api/v2/profile/membership" },
  benefits: { action: "BENEFITS_VIEW", path: "/api/v2/benefits" },
  preferences: { action: "PREFERENCE_VIEW", path: "/api/v2/preferences" },
};
export const isPersonalResource = (id: string) => Object.hasOwn(registry, id);
export function PersonalServiceResources({ context, token, revision, onPrompt }: {
  context: string; token: string; revision: number; onPrompt: (message: string) => void;
}) {
  const entry = registry[context];
  const { locale } = useCustomerLanguage();
  const read = useEndpoint<Record<string, JsonValue>>(entry?.path || null, token, revision);
  return <section className="panel">
    {read.error ? <p role="status">{read.error}<button className="text-button" onClick={() => read.refresh()}>{locale === "en" ? "Try again" : "重新读取"}</button></p>
      : read.data && entry ? <PersonalServiceCards node={{ node_id: `${context}:${read.readReceipt}`, action: entry.action, result: read.data }} onPrompt={onPrompt}/>
      : <p role="status">{locale === "en" ? "Loading your information…" : "正在查询你的资料…"}</p>}
  </section>;
}
