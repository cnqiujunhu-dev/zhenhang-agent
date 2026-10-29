/** Presentation-only: an exact server footer is already the consultation notice.
 * Never infer execution, permissions or safety from arbitrary model prose.
 */
const serverFooters = [
  "本轮仅解释，未执行银行操作。",
  "本轮仅解释或读取已授权资料，未执行银行操作。",
  "This reply only explains authorized information; no banking action was executed.",
  "This reply explains the available information; it did not execute a bank operation.",
  "本轮仅提供解释，办理时仍需明确请求并按原流程核对。",
  "This reply only explains information. A banking action needs an explicit request and the original review and confirmation process.",
];

export function visibleConsultationAnswer(answer: string | null | undefined): string {
  let text = typeof answer === "string" ? answer.trimEnd() : "";
  for (let i = 0; i < 3; i++) {
    const footer = serverFooters.find(value => text === value || text.endsWith("\n\n" + value));
    if (!footer) break;
    text = text.slice(0, -footer.length).trimEnd();
  }
  return text;
}

export function hasServerConsultationBoundary(answer: string | null | undefined): boolean {
  if (typeof answer !== "string") return false;
  const paragraphs = answer.trimEnd().split(/\r?\n\s*\r?\n/);
  return paragraphs.length > 1 && serverFooters.includes(paragraphs.at(-1) || "");
}
