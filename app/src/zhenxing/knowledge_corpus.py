"""Bootstrap only public knowledge: no account data, secrets or live balances."""
from .financial_product_catalog import CATALOG_VERSION, product_catalog, product_terms
from .product_knowledge import ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_VERSION, KNOWLEDGE_ENTRY_VERSIONS

CORPUS_VERSION = "public-knowledge-20261007-v1"


def corpus_seed():
    """Seed shape consumed by knowledge_store.bootstrap_public_corpus.

    Stable document keys make bootstrap idempotent; existing edited documents
    must never be overwritten or re-published by bootstrapping.
    """
    seeds = []
    roles = ["CUSTOMER", "DEV_TEST", "FAMILY_HELPER", "ADMIN"]
    for key, title, _pattern, body in ENTRIES:
        title_en, _pattern_en, body_en = ENGLISH_ENTRIES[key]
        for language, heading, content in (("zh-CN", title, body), ("en", title_en, body_en)):
            seeds.append({"document_id": f"policy:{key}:{language}", "version": KNOWLEDGE_ENTRY_VERSIONS.get(key, KNOWLEDGE_VERSION),
                          "title": heading, "body": content, "lang": language,
                          "source": "knowledge:policy/" + key, "category": "PUBLIC_POLICY", "kind": "PUBLIC_POLICY",
                          "status": "PUBLISHED", "allowed_roles": roles,
                          "metadata": {"corpus_version": CORPUS_VERSION,
                                       "description": "Public explanatory rules; no execution authority."}})
    for item in product_catalog():
        for language in ("zh-CN", "en"):
            english = language == "en"
            seeds.append({"document_id": "product:" + item["product_key"] + (":en" if english else ":zh"),
                          "version": CATALOG_VERSION, "title": item["name_en"] if english else item["name_zh"],
                          "body": product_terms(item["product_key"], language), "lang": language,
                          "source": "product:" + item["product_key"], "category": "PRODUCT_TERMS", "kind": "PRODUCT_TERMS",
                          "product_id": item["product_key"], "status": "PUBLISHED", "allowed_roles": roles,
                          "metadata": {"corpus_version": CORPUS_VERSION,
                                       "product_id": item["product_key"],
                                       "description": "Public CNY product literature; not current price or execution authority."}})
    for language, title, content in (
        ("zh-CN", "这里可以提供哪些帮助", "可以查询银行卡余额、分析账单与支出、核对转账、了解人民币理财、管理银行卡和订阅续费，"
         "也可以查询换汇报价、家庭协助、资金目标及低碳权益。你可以直接说需求，不需要先选择场景。"
         "询问功能或规则不等于提出办理任务；功能介绍应给出可选的下一步，不要要求补充付款信息。"
         "能否办理仍取决于本人权限、当前数据和受控工作流。"),
        ("en", "What this assistant can help with", "You can ask about card balances, bills and spending, transfers, CNY wealth products, "
         "card controls and subscription renewals, as well as exchange quotes, family assistance, savings goals and activity benefits. "
         "Describe your needs directly; no scene selection is required. Asking what the assistant can do or asking about rules is not an order. "
         "Offer useful next steps rather than requesting payment details for a capability question. Actual availability depends on your permissions, "
         "current data and the controlled workflow."),
        ("zh-CN", "外国访客与双币服务边界", "支持中英文表达和人民币、美元双币查询。转账时先核对金额币种、付款币种和到账币种，"
         "若收款方支持美元，可在受控工作流中核对直接美元到账方案；否则核对人民币换算报价。"
         "汇率必须来自当次有效报价。本平台不提供开户资格、签证、税务或跨境监管合规承诺，不办理美元理财或贷款。"),
        ("en", "Visitor and dual-currency service boundaries", "You can use Chinese or English and ask about CNY and USD capabilities. "
         "For a transfer, verify the currency of the amount, payment and receipt. A recipient with USD capability may receive USD through the controlled "
         "workflow; otherwise review a CNY conversion quote. Rates must come from a current valid quote. This platform makes no promise about account-opening, "
         "visa, tax or cross-border regulatory eligibility and does not offer USD wealth orders or loans."),
    ):
        seeds.append({"document_id": "guide:" + ("capabilities" if "help" in title or "帮助" in title else "visitor") + ":" + language,
                      "version": CORPUS_VERSION, "title": title, "body": content, "lang": language,
                      "source": "knowledge:public-service-guide", "category": "CAPABILITY_GUIDE", "kind": "CAPABILITY_GUIDE",
                      "status": "PUBLISHED", "allowed_roles": roles,
                      "metadata": {"corpus_version": CORPUS_VERSION,
                                   "description": "Public service explanation; no execution authority."}})
    return seeds
