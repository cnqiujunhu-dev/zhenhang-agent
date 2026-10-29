/** Display wording only. Never normalizes a card reference, credential or payload. */
export function cardClarificationText(text: string): string {
  const card = /\b(?:bank\s+)?cards?\b|银行卡|卡片/u;
  const verification = /\b(?:otp|pin|code|passcode|password|verification|authentication|cvv|cvc)\b|验证码|校验码|动态码|密码|口令|密钥/iu;
  if (!card.test(text.toLowerCase()) || verification.test(text)) return text;
  // Quoted identifiers/labels remain literal, even when their wording happens
  // to contain the same phrase. No NFKC, digit stripping or redaction occurs.
  return text.replace(/(["“][^"”\n]*["”]|['‘][^'’\n]*['’])|\blast\s+(?:four|4)\s+digits\b|(?:末|最后)四位数字/giu,
    (match, quoted: string | undefined) => {
      if (quoted) return match;
      if (/四位数字/u.test(match)) return "四位尾码";
      if (match === match.toUpperCase()) return "LAST FOUR CHARACTERS";
      return match.startsWith("L") ? "Last four characters" : "last four characters";
    });
}
