import { cardClarificationText } from "./cardClarificationText";
import type { CustomerLocale } from "./customerLanguage";
import { ReplyText } from "./ReplyText";
/** Display only: retain source questions/payloads; clarify card-tail wording without inferring values. */
export function clarificationQuestionsForDisplay(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string")
    .map(item => cardClarificationText(item.trim())).filter(Boolean))];
}

const genericQuestions = new Set([
  "请说明希望查询或办理的业务。",
  "Please clarify the specific information or supported bank operation you want. Missing or ambiguous details will not be guessed.",
]);

/** A narrow display hint for an explicit balance query; no value, action or authorization is inferred. */
export function balanceClarificationForDisplay(questions: unknown, message: string | undefined,
  locale: CustomerLocale, bankScopeAvailable: boolean): string[] | null {
  const values = clarificationQuestionsForDisplay(questions);
  const original = message?.trim().toLowerCase();
  if (!bankScopeAvailable || !original || !new Set([
    "查询余额", "查看余额", "查一下余额", "查询我的余额", "check my balance", "show my balance",
  ]).has(original) || values.length > 1 || values.length === 1 && !genericQuestions.has(values[0])) return null;
  const chineseRequest = /[\u4e00-\u9fff]/.test(original);
  return [locale === "en"
    ? `Which balance would you like to check? Reply “${chineseRequest ? "银行卡" : "bank card"}” below to see the linked account balance.`
    : "你想查哪类余额？可以在下方回复“银行卡”，查看关联账户的余额。"];
}

export function ClarificationDetails({questions, fallback, message, locale = "zh-CN", bankScopeAvailable = false}: {
  questions: unknown; fallback: string; message?: string; locale?: CustomerLocale; bankScopeAvailable?: boolean;
}) {
  const values = balanceClarificationForDisplay(questions, message, locale, bankScopeAvailable)
    ?? clarificationQuestionsForDisplay(questions);
  if (!values.length) return <p><ReplyText slot="clarification-fallback" text={cardClarificationText(fallback)}/></p>;
  if (values.length === 1) return <p className="clarification-question"><ReplyText slot="clarification-question:0" text={values[0]}/></p>;
  return <ul className="clarification-questions">{values.map((question, index) => <li key={question}><ReplyText slot={`clarification-question:${index}`} text={question}/></li>)}</ul>;
}
