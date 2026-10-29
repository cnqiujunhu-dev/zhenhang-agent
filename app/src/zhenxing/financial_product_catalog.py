"""Public, versioned product literature; never a bank position or execution rule.

The catalogue deliberately does not mutate InvestmentProduct rows. Its labels
match the existing six demo products, but prices, risk suitability, inventory
and eligibility must still be read by the current banking tools.
"""
from copy import deepcopy

CATALOG_VERSION = "public-products-20261007-v1"
_PRODUCTS = (
    ("cash-management", "稳健现金管理", "Cash management", 1,
     "关注流动性、短期备用金安排的用户。", "People comparing liquidity options for short-term reserves.",
     "关注流动性并不等于保证随时到账；应核对当次赎回说明。", "A liquidity objective does not promise instant settlement. Check redemption terms."),
    ("short-bond", "短期纯债", "Short-duration bond portfolio", 2,
     "愿意承担净值波动、比较债券类配置的用户。", "People comparing bond allocations who accept fluctuations in value.",
     "利率变化和信用风险可能影响净值，债券类不等于保本。", "Interest-rate changes and credit risk may affect value. Bonds are not a capital guarantee."),
    ("retirement-balanced", "稳健养老组合", "Retirement-oriented diversified portfolio", 2,
     "希望比较长期资金配置的用户，不代表具备养老账户或税收优惠资格。", "People comparing long-term allocations; this does not establish pension-account or tax-benefit eligibility.",
     "长期配置仍有市场和流动性风险，不保证达到养老金目标。", "Long-term allocations still carry market and liquidity risks and do not guarantee a retirement target."),
    ("balanced-allocation", "均衡配置", "Balanced allocation", 3,
     "可以承担中等波动、希望了解多资产分散配置的用户。", "People accepting moderate fluctuations who want to understand diversified allocations.",
     "分散配置不能消除损失风险，资产相关性可能在市场变化时上升。", "Diversification cannot eliminate losses; correlations can rise when markets change."),
    ("growth-selection", "成长精选", "Growth selection", 4,
     "可以承担较高波动、比较成长型权益配置的用户。", "People comparing growth-oriented equity allocations who accept higher volatility.",
     "权益价格可能显著波动，短期资金需求与高波动投资可能不匹配。", "Equity prices can fluctuate substantially; short-term cash needs may not fit volatile investments."),
    ("technology-equity", "科技权益", "Technology equity", 5,
     "了解行业集中风险、能承担高波动的用户。", "People who understand sector concentration and accept high volatility.",
     "行业集中度较高，政策、竞争和估值变化可能放大波动。", "Sector concentration can magnify policy, competition and valuation risks."),
)


def product_catalog(language="zh-CN"):
    """Return fresh public projections, without current money or bank identifiers."""
    english = language == "en"
    result = []
    for key, name, name_en, risk, audience, audience_en, risk_note, risk_note_en in _PRODUCTS:
        result.append({
            "product_key": key, "catalog_version": CATALOG_VERSION,
            "name": name_en if english else name, "name_zh": name, "name_en": name_en,
            "bank_name_match": name + "（仿真）", "currency": "CNY",
            "illustrative_risk_level": risk, "risk_level_is_current_bank_fact": False,
            "audience": audience_en if english else audience,
            "risk_note": risk_note_en if english else risk_note,
            "principal_guaranteed": False, "return_guaranteed": False,
            "guidance_only": True, "transaction_support": "EXISTING_WEALTH_WORKFLOW_IF_MATCHED",
            "supported_actions": ["COMPARE", "EXPLAIN"],
            "transaction_actions_require_current_bank_product": ["WEALTH_BUY", "WEALTH_REDEEM"],
            "minimum": "Use the current order's whole-unit price and eligibility checks." if english else "以当次订单的整份单位价格和资格核验为准，不在知识文案中固定最低金额。",
            "fees": "No fee schedule is specified in this literature; do not infer zero fees. Check current transaction terms." if english else "本说明未规定费率，不可据此认定零费用；以当次交易条款为准。",
            "exit_conditions": "Redemption requires owned available units and separate confirmation. No settlement-time promise is provided." if english else "赎回要核对本人可用持有份额并独立确认；本说明不承诺到账时间。",
            "availability": "The live banking service must verify the product, current price, risk fit and available funds." if english else "由实时银行服务核验产品、当前价格、风险适配和可用资金。",
            "source": "public-product-catalog:" + key,
            "terms_document_key": "product:" + key + ":" + ("en" if english else "zh"),
        })
    return deepcopy(result)


def product_detail(product_key, language="zh-CN"):
    return next((product for product in product_catalog(language) if product["product_key"] == product_key), None)


def product_terms(product_key, language="zh-CN"):
    item = product_detail(product_key, language)
    if item is None:
        return None
    if language == "en":
        return (f"{item['name']}\nCurrency: CNY. Public catalogue version: {CATALOG_VERSION}.\n"
                f"Who may compare it: {item['audience']}\nRisk: {item['risk_note']}\n"
                f"Capital and returns are not guaranteed. Illustrative risk level: {item['illustrative_risk_level']}; "
                "this is public literature, not a current suitability decision.\n"
                f"Minimum and limits: {item['minimum']}\nFees: {item['fees']}\nExit: {item['exit_conditions']}\n"
                "This literature supports explanations and comparisons only. Where a corresponding product exists, "
                "buying or redemption uses the existing CNY workflow with live verification and separate confirmation. "
                "USD wealth orders, loans, securities trading, guaranteed returns and automatic asset allocation are not offered.")
    return (f"{item['name']}\n币种：人民币。公版目录版本：{CATALOG_VERSION}。\n"
            f"适合了解的人群：{item['audience']}\n风险：{item['risk_note']}\n"
            f"本金和收益均不保证。说明性风险等级：{item['illustrative_risk_level']}；这不是本人的实时风险适配结论。\n"
            f"额度与门槛：{item['minimum']}\n费用：{item['fees']}\n退出限制：{item['exit_conditions']}\n"
            "本资料用于解释和比较，不构成申购承诺。若当前银行服务存在同名产品，申购与赎回使用既有人民币理财工作流，"
            "核对最新价格、风险测评、资金或持有份额后分别确认。不提供美元理财订单、贷款、证券交易、保本收益承诺或自动资产配置。")
