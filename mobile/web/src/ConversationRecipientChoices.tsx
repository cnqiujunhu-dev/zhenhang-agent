import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { Icon } from "./components";
import "./conversationRecipientChoices.css";

export type RecipientChoiceSubmission = {
  origin_turn_id: string; choice_set_id: string; choice_token: string;
  task_id: string; task_version: number; label: string;
};
type ChoiceSet = {
  version: "recipient-choice-v1"; choice_set_id?: string; origin_turn_id?: string;
  task_id?: string; task_version?: number; expires_at?: string;
  choices: { choice_token: string; name: string; card_last4: string; currency: string; label: string }[];
};

export function ConversationRecipientChoices({ token, conversationId, turnId, taskId, taskVersion, blocked, onSelect }: {
  token: string; conversationId: string; turnId: string; taskId: string; taskVersion: number;
  blocked: boolean; onSelect: (selection: RecipientChoiceSubmission) => Promise<void>;
}) {
  const { locale } = useCustomerLanguage();
  const scope = JSON.stringify([token, conversationId, turnId, taskId, taskVersion]);
  const current = useRef({ scope, blocked }); current.current = { scope, blocked };
  const [result, setResult] = useState<{ scope: string; value: ChoiceSet } | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    if (blocked || !token) return;
    const controller = new AbortController();
    setError("");
    request<ChoiceSet>(`/api/v3/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/recipient-choices`,
      token, "POST", undefined, undefined, { signal: controller.signal, operation: "read" })
      .then(value => {
        if (controller.signal.aborted || current.current.scope !== scope) return;
        if (value.version !== "recipient-choice-v1" || !Array.isArray(value.choices) || value.choices.length > 12
          || value.choices.length && (value.origin_turn_id !== turnId || value.task_id !== taskId || value.task_version !== taskVersion))
          throw new Error("Invalid recipient choices");
        setResult({ scope, value });
      }).catch(() => {
        if (!controller.signal.aborted && current.current.scope === scope) setError(locale === "en" ? "Recipient choices could not be loaded." : "收款选项暂时未加载。");
      });
    return () => controller.abort();
  }, [scope, blocked, revision]);
  const data = result?.scope === scope ? result.value : null;
  if (!data?.choices.length && !error) return null;
  async function selectChoice(choice: ChoiceSet["choices"][number]) {
    if (lock.current || current.current.scope !== scope || current.current.blocked || document.visibilityState !== "visible"
      || !data?.choice_set_id || !data.expires_at || !data.origin_turn_id || !data.task_id || !data.task_version) return;
    if (Date.now() >= Date.parse(data.expires_at)) { setResult(null); setRevision(v => v + 1); return; }
    lock.current = true; setBusy(true);
    try {
      await onSelect({ origin_turn_id: data.origin_turn_id, choice_set_id: data.choice_set_id, choice_token: choice.choice_token,
        task_id: data.task_id, task_version: data.task_version, label: choice.label });
    } finally { lock.current = false; setBusy(false); }
  }
  return <div className="conversation-recipient-choices" aria-label={locale === "en" ? "Receiving account" : "收款账户"}>
    {data?.choices.length ? <><p>{locale === "en" ? "Choose the receiving account:" : "可以直接选择收款账户："}</p>
      <div className="recipient-choice-list">{data.choices.map(choice => <button key={choice.choice_token} type="button"
        disabled={blocked || busy} onClick={() => void selectChoice(choice)}>
        <Icon name="card" size={19}/><span><strong>{choice.name}</strong><small>{locale === "en" ? "Ending " : "尾号 "}{choice.card_last4} · {choice.currency}</small></span>
        <Icon name="arrow" size={15}/>
      </button>)}</div></> : null}
    {error && <p role="status">{error} <button type="button" className="text-button" onClick={() => setRevision(v => v + 1)} disabled={blocked}>{locale === "en" ? "Retry" : "重试"}</button></p>}
  </div>;
}
