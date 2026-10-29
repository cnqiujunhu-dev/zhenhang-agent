import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { CreditCards } from "./CreditCards";
import { useEndpoint } from "./useEndpoint";
import type { JsonValue } from "./types";

export function CreditResources({ token, revision, onPrompt }: { token: string; revision: number; onPrompt: (message: string) => void }) {
  const { locale } = useCustomerLanguage();
  const en = locale === "en";
  const read = useEndpoint<Record<string, JsonValue>>("/api/v2/credit/cards", token, revision);
  return <section className="panel"><div className="small-panel-heading"><h2>{en ? "My credit cards" : "我的信用卡"}</h2>
    <button type="button" className="button secondary compact" disabled={read.loading} onClick={() => read.refresh()}>{en ? "Refresh" : "刷新"}</button></div>
    {read.loading ? <p role="status">{en ? "Checking credit cards…" : "正在查询信用卡…"}</p> : read.error ? <p role="alert">{read.error}</p>
      : read.data && <CreditCards node={{node_id: "credit-resource", action: "CREDIT_VIEW", result: read.data}} onPrompt={onPrompt}/>}
    <button type="button" className="button secondary compact" onClick={() => onPrompt(en ? "I would like to apply for a credit card." : "我想申请一张信用卡。")}>{en ? "Prepare application" : "准备信用卡申请"}</button>
  </section>;
}
