/** Presentation only: never use these strings as matching keys or API values. */
const catalogue: Record<string,string>={"演示步行5公里":"步行5公里","演示骑行8公里":"骑行8公里","演示地铁20公里":"地铁20公里",
    "演示节能主题权益":"节能主题权益","演示绿色出行权益券":"绿色出行权益券","演示出行权益":"出行权益","演示出行券":"出行券","演示旅行基金":"旅行基金",
    "演示理财产品":"理财产品","演示权益":"权益","模拟权益":"权益"};
export function customerName(value: string): string {
  const name=value.replace(/[（(](?:仿真|模拟|演示)[）)]$/, "");
  return Object.hasOwn(catalogue,name) ? catalogue[name] : name;
}

/** Approved legacy labels only; user messages and technical evidence stay raw. */
export function customerNarrative(value: string): string {
  const shown = value
    .replace(/^(?:好的[，,。]\s*)?(?:按本次已核验的查询结果说明[：:]|Here is what the verified query establishes\.)\s*/u, "")
    // Retained replies can contain the old UI-owned progress guidance. Only
    // these complete, known sentences are adapted; user messages, quoted
    // instructions and unknown text remain unchanged in their own views.
    .replaceAll("请查看办理进度中的取消入口；文字撤销不会取消业务，已完成的操作不会自动撤回。", "请使用这项办理的取消按钮；文字撤销不会取消业务，已完成的操作不会自动撤回。")
    .replaceAll("结果尚未确认，请稍后查看办理进度。", "结果尚未核实，请不要重复提交；核实后才能继续办理。")
    .replaceAll("尚未取得已确认的办理结果，请稍后查看进度。", "尚未取得已确认的办理结果，请不要重复提交。")
    .replaceAll("尚未取得完整办理结果，请稍后查看进度。", "尚未取得完整办理结果，请不要重复提交。")
    .replaceAll("请稍后查看办理进度。", "请稍候；无需重复提交。")
    .replaceAll("这条消息暂未处理完成，请查看关联办理进度。", "这条消息暂未处理完成，请阅读回复中的说明。")
    .replaceAll("Check progress shortly.", "Please wait; do not submit again.")
    .replaceAll("The result is unverified. Check progress later.", "The result is unverified. Do not submit again.")
    .replaceAll("No verified result yet. Check progress later.", "No verified result has been received. Do not submit again.")
    .replaceAll("The complete result is not available. Check progress later.", "The complete result has not been received. Do not submit again.")
    .replace(/[（(](?:仿真|模拟|演示)[）)]/g, "")
    .replaceAll("模拟外部转出", "外部清算转出")
    .replaceAll("外部模拟转账", "外部清算转账")
    .replaceAll("受控外汇仿真付款/换出", "外汇付款/换出")
    .replaceAll("受控外汇仿真收款/换入", "外汇收款/换入")
    .replaceAll("仿真外部清算账户", "外部清算账户")
    .replaceAll("模拟银行代扣", "银行代扣")
    .replaceAll("仿真银行代扣", "银行代扣")
    .replaceAll("仿真商户订阅", "商户订阅")
    .replaceAll("模拟卡片", "卡片")
    .replaceAll("模拟卡 ", "卡片 ")
    .replaceAll("演示理财产品", "理财产品")
    .replaceAll("演示积分", "积分")
    .replaceAll("演示权益", "权益")
    .replaceAll("模拟权益", "权益")
    .replaceAll("当前合成账套", "当前账户")
    .replaceAll("双方合成账务", "双方账户账务");
  // Typed explanations label each verified object with a colon. Reuse the
  // exact UI catalogue there; do not strip prefixes from arbitrary names,
  // user messages, record identities or technical evidence.
  return Object.entries(catalogue).reduce((text, [recorded, display]) =>
    text.replaceAll(recorded + "：", display + "：").replaceAll(recorded + ":", display + ":"), shown);
}
