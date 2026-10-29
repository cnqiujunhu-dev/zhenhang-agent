/** Bound one CSS-pixel focus correction to the scroll space of its own container. */
export function boundedFocusScrollTop(
  geometry: { scrollTop: number; scrollHeight: number; clientHeight: number },
  delta: number,
): number | null {
  const { scrollTop, scrollHeight, clientHeight } = geometry;
  if (![scrollTop, scrollHeight, clientHeight, delta].every(Number.isFinite)
    || scrollTop < 0 || clientHeight <= 0 || scrollHeight < clientHeight) return null;
  return Math.max(0, Math.min(scrollHeight - clientHeight, scrollTop + delta));
}

/** Controlled readiness feedback contains no code, amount or account details. */
export function confirmationFeedback(options: {
  busy: boolean; updating: boolean; blocked: boolean; resultUnknown: boolean;
  requiresVerification: boolean; verified: boolean; challengeReady: boolean; verificationError: boolean;
}): string {
  if (options.busy) return "本次操作正在处理，请等待回复后核对。";
  if (options.updating) return "办理信息正在更新，暂不能确认。";
  if (options.resultUnknown) return "本次操作结果尚未核验，请先查看最新办理信息，避免重复提交。";
  if (options.blocked) return "暂不能确认，请先核对当前办理信息与权限。";
  if (options.verificationError) return "本次验证尚未完成，请核对验证提示。";
  if (options.requiresVerification && !options.verified)
    return options.challengeReady ? "验证码已准备，请在输入框填写后验证。" : "请先完成本次二次验证，再核对确认。";
  return options.requiresVerification ? "本次验证已完成，请核对本次操作后确认。" : "请核对本次操作，确认后执行。";
}
