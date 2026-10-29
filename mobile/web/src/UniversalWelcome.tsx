// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { Icon } from "./components";
import { readonlySuggestions } from "./universalAgent";

export function UniversalWelcome({
  canSend,
  onSend,
}: {
  canSend: boolean;
  onSend: (message: string, trigger: HTMLButtonElement, gesture?: MouseEvent) => void;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  return (
    <div className="universal-welcome">
      <h3><CustomerText text="你好，想办理什么？" /></h3>
      <p><CustomerText text="直接说需求即可。涉及资金或授权，我会请你先核对确认。" /></p>
      <div className="suggestion-grid universal-suggestions">
        {readonlySuggestions.map((example) => (
          <button
            key={example.id}
            disabled={!canSend}
            onClick={event => onSend(locale === "en" ? ({ bills: "Analyze my spending this month", subscriptions: "Show my subscriptions and recurring renewals", combined: "Show my card status and limits, then list my subscriptions" } as Record<string, string>)[example.id] : example.message, event.currentTarget, event.nativeEvent)}
            aria-label={locale === "en" ? `Send read-only question: ${customerUiText(example.label)}` : `发送只读示例：${example.label}`}
          >
            <Icon name={example.icon} size={17} />
            <span>
              <strong>{customerUiText(example.label)}</strong>
            </span>
            <Icon name="send" size={14} />
          </button>
        ))}
      </div>
      <p className="universal-safety-note"><CustomerText text="选一个问题开始，或直接告诉我你想办理什么。" /></p>
    </div>
  );
}
