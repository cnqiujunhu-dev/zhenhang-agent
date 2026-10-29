export function minorFromYuan(input: string): number {
  const value = input.trim();
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value))
    throw new Error("金额请使用最多两位小数的元数值。");
  const negative = value.startsWith("-");
  const [whole, decimals = ""] = value.replace(/^-/, "").split(".");
  const minor = BigInt(whole) * 100n + BigInt(decimals.padEnd(2, "0"));
  if (minor > BigInt(Number.MAX_SAFE_INTEGER))
    throw new Error("金额超出可准确表示的范围。");
  return Number(negative ? -minor : minor);
}
