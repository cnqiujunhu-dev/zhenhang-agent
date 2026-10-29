"""Versioned explanatory policies for the feedback release, not execution rules."""
from copy import deepcopy
import hashlib
from types import MappingProxyType

V1_VERSION = "zhenxing-feedback-public-policy-20261019-v1"
VERSION = "zhenxing-feedback-public-policy-20261019-v2"
# Immutable published v1 bodies. Never relabel edited text as this revision.
V1_POLICIES = {
    "wealth.risk-v1": (
        "比较产品不会买入。新增投资前须由本人独立完成当前八题风险问卷，涵盖经验、承受损失、期限、收入、资金用途、资产缓冲、负债和偿债压力；资产多不等于愿意承担高风险，冲突答案按保守限制处理。旧版、到期或只有初始等级的档案不能替代当前测评，助手不代填答案。买入仍核对产品风险、交易状态及可用资金。赎回已有持仓不因测评待更新而被阻止，但须核对本人可赎份额、产品规则并单独确认。收益没有保证。",
        "Comparing does not buy. Before new investment, independently complete the current eight-question risk assessment covering experience, loss tolerance, horizon, income, purpose, asset buffer, liabilities and repayment pressure. More assets do not imply greater risk appetite; conflicting answers are conservatively constrained. An old, expired or initial seeded profile is not a current assessment, and the assistant cannot answer for you. Buying still checks product suitability, availability and spendable funds. An assessment needing renewal does not block redemption of owned holdings; ownership, available units, product rules and separate confirmation remain required. Returns are not guaranteed.",
    ),
    "family.scope-v1": (
        "家庭资料中的称呼、关系和生日不等于银行账户或协助权限。可以仅保存家人资料，不为其开户或授予查账权限。银行协助另行授权：查看余额、提出转账建议、提出锁卡建议可独立选择；家人不能直接替你转账或锁卡。建立或修改委托、办理具体建议，仍须本人核对确认。撤销或到期的委托不能继续使用。",
        "A family profile's name, relationship or birthday is not a bank account or permission. Saving a relative's profile does not open an account or grant access. Banking assistance needs a separate delegation: balance viewing, transfer suggestions and card-lock suggestions are independently selectable. Helpers cannot execute transfers or locks for you. Creating or changing a delegation and carrying out suggestions still need your own review and confirmation. Revoked or expired delegation cannot be reused.",
    ),
    "credit.repayment-v1": (
        "信用账单按人民币、美元分别列示全部欠款、本期已出账应还、未出账本金和最低应还，不直接相加。按上海自然月自动衔接账期；查询不会临时出账或自动还款。已还或已退的本金不能重复计入应还。最低还款不是还清，也不是免息承诺；剩余债务仍占用共享额度。全额、指定金额或最低还款均须本人确认，不能把还款再次计为消费。同币种使用本人借记资金；人民币偿还美元欠款须核对当前有效购汇报价、人民币扣款和美元还款金额，聊天不能自行折算。卡锁定或挂失不阻止原欠款还款和原路退款。",
        "Statements separately show CNY and USD outstanding debt, billed due, unbilled principal and minimum due; do not add currencies. Billing follows consecutive Shanghai calendar months. Reading does not close a statement or repay automatically. Repaid or refunded principal is not billed twice. Minimum payment is not full repayment or an interest-free promise; remaining debt occupies the shared pool. Full, specified and minimum repayment need your confirmation and are money movement, not another purchase. Use owned same-currency debit cash. CNY repayment of USD debt needs a current server FX quote and review of both CNY debit and USD repayment. Locking or reporting loss does not block repayment of existing debt or original-instrument refunds.",
    ),
    "life-plan.bounded-authorization-v1": (
        "保存生日不授权付款。一次本人确认的生日计划只适用于一个生日年度，不自动循环；绑定预算、本人人民币借记账户、所选商品及数量、单价上限、收件信息、预留与下单时点、配送窗和截止时间。可以只选餐厅，也可组合已登记鲜花、蛋糕等商品；未选商品就先询问，不默认替你加购。预算默认1000元，增额必须本人明确提出且符合当次上限。月初预留或提前两天下单与生日冲突时，须核对具体可行替代时点，不能改生日或全局日期。预留只减少可用现金，不是付款或锁卡。信用卡不用于生日计划。农历日期缺少映射时须本人确认当年公历日期。",
        "Saving a birthday does not authorize payment. One confirmed plan covers one birthday-year occurrence, not an automatic recurrence. It binds budget, owned CNY debit account, selected products and quantities, unit-price caps, recipient, reservation and order times, delivery window and expiry. Choose a registered restaurant alone or combine flowers, cake and other listed products; missing selections require a question, not default purchases. The default budget is CNY 1,000; an increase must be explicit and within the current cap. If month-start reservation or ordering two days before conflicts with the birthday, review concrete feasible alternative times without changing the birthday or clock. Reservation reduces spendable cash; it is not payment or a card lock. Credit cards cannot fund birthday plans. A lunar date without a mapping needs your confirmed solar date for that year.",
    ),
    "life-plan.execution-refund-v1": (
        "等待触发或授权成功不是已购买或送达。按已确认时点核验库存、价格上限、收件信息、资金、卡状态和授权期限；条件不符暂停核对，不自动换卡、换商品或解锁。尚未付款的安排可提出商品、预算或时间变更，生成新核对页；确认成功后才原子替换旧授权和未用预留，准备或失败不影响旧安排。已付款订单不能借改期再付款，应按原订单取消和退款条件处理。取消只撤销未执行部分；退款处理中或部分退款不能说全部到账，须以原路退款回执为准。付款与履约、送达分别记录。",
        "Waiting or an authorized mandate is not a purchase or delivery. At confirmed times, check stock, price caps, recipient, funds, card state and expiry; a mismatch pauses for review, without switching instruments or products or unlocking. Before payment, changing items, budget or times produces a new review. Successful confirmation atomically replaces the old mandate and unused reserve; preparation or failure leaves the old plan intact. A paid order cannot be rescheduled into a second payment: use the original cancellation and refund conditions. Cancellation stops unexecuted work only. Pending or partial refunds are not a complete return; rely on original-instrument refund receipts. Payment, fulfilment and delivery are separate.",
    ),
}


_WEALTH_V2 = (
    "比较产品不会买入。新增投资前由本人独立完成当前八题风险问卷，涵盖经验、承受损失、期限、收入、资金用途、资产缓冲、负债和偿债压力；资产多不代表愿意承担高风险，冲突答案按保守限制。旧版、到期或只有初始等级不能替代当前测评，助手不代填答案。买入还核对风险是否适合你、产品可交易和可用资金，具体条件以当前条款为准。赎回已有持仓不因测评待更新被阻止，仍核对本人可赎份额和产品规则。买入和赎回都要分别核对确认。收益没有保证，过去表现也不代表未来收益。",
    "Comparing products does not buy them. Before buying, independently complete the current eight-question risk assessment covering experience, loss tolerance, horizon, income, purpose, asset buffer, liabilities and repayment pressure. More assets do not imply greater risk appetite; conflicting answers are conservatively constrained. An old, expired or seeded profile is not a current assessment, and the assistant cannot answer for you. Buying checks your risk fit, product availability and available funds under the current terms. An assessment needing renewal does not block redemption of owned holdings; ownership, available units and product rules still apply. Buying and redeeming each need review and confirmation. Returns are not guaranteed, and past performance does not predict future returns.",
)
_FAMILY_V2 = (
    "家庭资料中的称呼、关系和生日不等于银行账户或协助权限。仅保存家人资料不会开户，也不授予查账权限。可以只授权孩子查看余额，同时不授予转账建议权限。查看余额、提出转账建议、提出锁卡建议可独立选择。家人只能在有效委托范围内查看资料、提出建议，不能直接替你转账或锁卡。建立或修改委托、办理具体建议仍须本人核对并确认。撤销或到期后，旧权限不能继续使用。",
    "A family profile's name, relationship or birthday is not a bank account or permission. Saving a relative's profile does not open an account or grant access. You can give a child balance-viewing access only. Balance viewing, transfer suggestions and card-lock suggestions are independently selectable permissions. Balance-only access does not grant transfer-suggestion permission. Within valid permission, helpers can view information or suggest actions; they cannot transfer or lock a card for you. Establishing or changing a delegation and carrying out a suggestion still require your own review and confirmation. Revoked or expired permission cannot be reused.",
)
_CREDIT_V2 = (
    "信用账单按人民币、美元分别列示全部欠款、本期已出账应还、未出账本金和最低应还，不直接相加。按上海自然月衔接账期，查询不会临时出账或自动还款。已还或已退的本金不能重复计入应还。最低还款不是还清，也不是免息承诺，剩余债务仍占用共享额度。全额、指定金额和最低还款分别需要本人确认。还款是资金归还，不是再次消费。同币种使用本人借记资金；人民币偿还美元欠款须核对后台有效购汇报价、人民币扣款和美元还款金额，聊天不能自行折算。卡锁定或挂失不阻止原欠款还款和原路退款。",
    "Statements separately show CNY and USD outstanding debt, billed due, unbilled principal and minimum due; do not add currencies. Billing follows consecutive Shanghai calendar months; reading does not close a statement or repay automatically. Repaid or refunded principal is not billed twice. Minimum payment is not full repayment or an interest-free promise; remaining debt occupies the shared pool. Full, specified and minimum repayment each require your confirmation. Repayment is money movement, not another purchase. Use owned debit cash in the same currency; CNY repayment of USD debt needs a valid server FX quote and review of the actual CNY debit and USD repayment. Chat must not invent the conversion. Locking or reporting loss does not block repayment of existing debt or original-instrument refunds.",
)
POLICIES = {**V1_POLICIES, "wealth.risk-v1": _WEALTH_V2,
    "family.scope-v1": _FAMILY_V2, "credit.repayment-v1": _CREDIT_V2}


def stock_predecessors(identifier, original):
    """Only reviewed exact stock sources; administrator edits are not stock."""
    old_cn, old_en = V1_POLICIES[identifier]
    return (original, {"version": V1_VERSION, "zh-CN": old_cn, "en": old_en})


# Exact public stock observed READ ONLY and byte-compared with the raw AST of
# Git 0.20 ac7ac25771fc0556bb5e803e28f23184dea70112. These older active labels
# survived idempotent bootstrap. They are NOT version aliases for other IDs,
# languages, bodies or any administrator-edited/withdrawn public document.
HISTORICAL_STOCK_SOURCE_REF = 'ac7ac25771fc0556bb5e803e28f23184dea70112'
HISTORICAL_STOCK_PREDECESSORS = MappingProxyType({
    'policy:wealth.risk-v1:zh-CN': ('zh-CN', 'zhenxing-product-knowledge-20261005-v4',
        '2758a89f402f2398441f3080266ddf24073e7ec41b2b6fa968ba173835e2eb2b'),
    'policy:wealth.risk-v1:en': ('en', 'zhenxing-product-knowledge-20261005-v4',
        '7c55c74cc842a58b01d2e8d5023d0379a97bd03bb55b4f5f342f78632ffe1098'),
    'policy:family.scope-v1:zh-CN': ('zh-CN', 'zhenxing-product-knowledge-20261009-v5',
        'd49dea5d334efbad8d295866e3618ea65d74b6926fa6f62684e5b82d7e280af9'),
    'policy:family.scope-v1:en': ('en', 'zhenxing-product-knowledge-20261009-v5',
        'eca84794b9020fb7ff1dc3d7da6bce4f85fd83bc7bb79c16c30f91aaffc008db'),
})


def is_exact_historical_stock(*, document_key, language, version, body, stored_hash, expected_body):
    registered = HISTORICAL_STOCK_PREDECESSORS.get(document_key)
    return bool(registered and isinstance(body, str) and body == expected_body
        and (language, version, stored_hash) == registered
        and hashlib.sha256(body.encode('utf-8')).hexdigest() == registered[2])


def source_sentence_indices(identifier, language, rules):
    """Explicit recipe for this exact v2 source, not a free-text classifier.

    The caller has already matched the entire current bilingual policy record.
    Keep independent safety sentences rather than depending on v1 positions.
    """
    if identifier == "family.scope-v1" and set(rules) <= {"GENERAL", "ELIGIBILITY", "LIMITS"}:
        return list(range(8 if language == "en" else 7))
    if identifier == "credit.repayment-v1" and set(rules) <= {"SETTLEMENT"}:
        return [3, 4, 5, 6, 7] if language == "en" else [3, 4, 5, 6]
    if identifier == "life-plan.execution-refund-v1" and set(rules) <= {"REFUND", "CANCELLATION"}:
        if language == "en":
            return [4, 5, 6, 7] if "REFUND" in rules else [2, 3, 4, 5]
        return [3, 4, 5] if "REFUND" in rules else [2, 3, 4]
    return None


def upgrade_entries(entries, english, versions, default_version):
    """Keep IDs and old policy bodies for exact, non-overwriting KB migration."""
    predecessors = {}
    updated, translated, labels = [], deepcopy(english), dict(versions)
    for identifier, title, pattern, body in entries:
        if identifier in POLICIES:
            predecessors[identifier] = {
                "version": labels.get(identifier, default_version),
                "zh-CN": body, "en": english[identifier][2],
            }
            body, body_en = POLICIES[identifier]
            heading, pattern_en, _ = translated[identifier]
            translated[identifier] = (heading, pattern_en, body_en)
            labels[identifier] = VERSION
        updated.append((identifier, title, pattern, body))
    return updated, translated, labels, predecessors
