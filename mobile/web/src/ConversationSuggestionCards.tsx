import { useEffect, useRef, type MouseEvent } from "react";
import { Icon } from "./components";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import type { ConversationSuggestion, ConversationTurn } from "./conversationTypes";
import { currentConversationSuggestions, sameConversationSuggestion, type ConversationSuggestionContext } from "./conversationSuggestions";
import { CustomerMotionBoundary } from "./CustomerMotionBoundary";
import type { CustomerMotionEvent } from "./customerMotion";
import "./conversationSuggestions.css";

export type ConversationSuggestionHandler = (suggestion: ConversationSuggestion, turn: ConversationTurn, event: MouseEvent<HTMLButtonElement>) => void;

/** Optional hints within the conversation, never a required scene selection. */
export function ConversationSuggestionCards({ turn, context, onSuggestion, motionEvent }: {
  turn: ConversationTurn;
  context: ConversationSuggestionContext;
  onSuggestion?: ConversationSuggestionHandler;
  motionEvent?: CustomerMotionEvent | null;
}) {
  const { locale } = useCustomerLanguage();
  const root = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  const current = useRef({ turn, context, onSuggestion });
  current.current = { turn, context, onSuggestion };
  useEffect(() => {
    const document = typeof window === "undefined" ? null : window.document;
    if (!document) return;
    const start = () => { composing.current = true; };
    const end = () => { composing.current = false; };
    document.addEventListener("compositionstart", start, true);
    document.addEventListener("compositionend", end, true);
    return () => {
      document.removeEventListener("compositionstart", start, true);
      document.removeEventListener("compositionend", end, true);
      composing.current = false;
    };
  }, [context.scopeKey, context.conversationId]);
  const suggestions = onSuggestion ? currentConversationSuggestions(turn, context) : [];
  if (!suggestions.length) return null;
  const activate = (selected: ConversationSuggestion, event: MouseEvent<HTMLButtonElement>) => {
    const latest = current.current, element = root.current;
    if (!element || !event.nativeEvent.isTrusted || event.eventPhase === 0 || composing.current
      || latest.context.scopeKey !== context.scopeKey || latest.turn.turn_id !== turn.turn_id
      || element.ownerDocument.visibilityState !== "visible" || !element.getClientRects().length
      || element.closest('[hidden], [inert], [aria-hidden="true"]')) return;
    const active = element.ownerDocument.activeElement;
    if (active instanceof HTMLElement && active.matches('input[autocomplete="one-time-code"], .confirmation-card input, .password-modal input')) return;
    const reviewed = currentConversationSuggestions(latest.turn, latest.context).find(row => sameConversationSuggestion(row, selected));
    if (!reviewed) return;
    latest.onSuggestion?.(reviewed, latest.turn, event);
  };
  const button = (suggestion: ConversationSuggestion) => <button key={suggestion.id} type="button"
    className="conversation-suggestion" data-suggestion-id={suggestion.id} data-suggestion-behavior={suggestion.behavior}
    title={suggestion.behavior === "SEND_READ_ONLY" ? locale === "en" ? "Send this query" : "发送这条查询" : locale === "en" ? "Put this request in the message box" : "将这项需求填入输入框"}
    onPointerDown={event => {
      const active = event.currentTarget.ownerDocument.activeElement;
      if (composing.current || active instanceof HTMLElement && active.matches('input[autocomplete="one-time-code"], .confirmation-card input, .password-modal input')) event.preventDefault();
    }}
    onKeyDown={event => { if (event.nativeEvent.isComposing || event.keyCode === 229 || composing.current) event.preventDefault(); }}
    onClick={event => activate(suggestion, event)}>
    <span>{suggestion.label}</span><Icon name={suggestion.behavior === "SEND_READ_ONLY" ? "arrow" : "chevron"} size={15}/>
  </button>;
  return <div ref={root} className="conversation-suggestions" data-suggestion-turn-id={turn.turn_id}>
    <CustomerMotionBoundary event={motionEvent} interactive>
      <p className="conversation-suggestion-heading">{locale === "en" ? "You can try" : "你可以试试"}</p>
      <div className="conversation-suggestion-list">{suggestions.slice(0, 5).map(button)}</div>
      {suggestions.length > 5 && <details className="conversation-suggestion-more">
        <summary>{locale === "en" ? "More ideas" : "更多方向"}</summary>
        <div className="conversation-suggestion-list">{suggestions.slice(5).map(button)}</div>
      </details>}
      <small>{locale === "en" ? "Or describe what you need in your own words." : "也可以直接说你的需求。"}</small>
    </CustomerMotionBoundary>
  </div>;
}
