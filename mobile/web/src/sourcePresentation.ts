export function sourceKindLabel(kind: string) {
  return (
    (
      {
        PRODUCT_KNOWLEDGE: "产品与服务说明",
        BANK_QUERY: "本人银行查询",
        BANK_DATA: "本人银行数据",
        USER_VALIDATED_DOCUMENT: "本人已核对材料（不是银行到账凭证）",
        IMAGE_EXTRACTION: "图片提取候选（尚非银行事实）",
        CONVERSATION_CONTEXT: "本对话已核验上下文",
        USER_CURRENT: "本轮原文",
        USER_CONTEXT: "此前本对话原文",
        POLICY: "受控策略",
        CAPABILITY: "已登记能力",
      } as Record<string, string>
    )[kind] || kind
  );
}
