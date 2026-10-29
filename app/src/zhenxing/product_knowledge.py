"""Versioned product facts and current actor-scoped evidence, not investment advice."""
import json
import re
from decimal import Decimal

from .models import utcnow
from .contracts import DomainError
from .planning import canonical_hash


KNOWLEDGE_VERSION = "zhenxing-product-knowledge-20261013-subscription-rights-v8"
PAYMENT_KNOWLEDGE_VERSION = "zhenxing-payment-services-knowledge-20261018-v1"
DOMAIN_POLICY_IDS = {
    'CARDS': ('credit.shared-limit-v1','credit.installments-v1','credit.repayment-v1','credit.application-v1'),
    'FAMILY': ('life-plan.bounded-authorization-v1','life-plan.execution-refund-v1'),
    'GENERAL': ('merchant.catalog-scope-v1','merchant.payment-refund-v1'),
}
KNOWLEDGE_ENTRY_VERSIONS = {identifier: PAYMENT_KNOWLEDGE_VERSION for group in DOMAIN_POLICY_IDS.values() for identifier in group}
ENTRIES = [
    ("transfer.confirm-v1", "转账前要核对什么", "转账|付款|支付|汇款|收款|确认", "先核对完整收款人、金额、币种、付款卡和收款方式。提出需求只是准备办理方案；需要验证时，完成验证后还要在确认卡上提交。聊天里回复“好”或“确认”不会付款。"),
    ("transfer.unknown-v1", "结果没确定时怎么办", "未知|处理中|重复|没收到|查单|超时|失败|撤销|取消|撤回", "超时或结果未确定，不等于付款失败。先查看原请求和回执，别重复付款。关闭请求只能停止还没执行的步骤；已经完成的记录会保留，不能据此认定钱已退回。"),
    ("cards.identity-v1", "锁卡、解锁和挂失有什么区别", "卡|锁|解锁|挂失|尾号", "临时锁卡将选定的本人卡设为“已锁定”，解锁恢复“正常”。挂失是另一状态，不能直接解锁。锁卡不改变现金余额。平台内指定这张付款卡的转账、使用双币卡的换汇或转账，会拒绝不可用的卡；这不表示所有付款都停止。家人只能在有效委托内建议；解锁仍需本人核对最新状态并确认。"),
    ("subscription.scope-v1", "会员开通、续订与自动续费", "会员|订阅|代扣|续费|开通|恢复", "已登记且接入的会员按当前目录核对价格和周期，不猜第三方价格。权益仍有效时，普通“开通”只查状态，不收费、不延长，也不恢复自动续费。明确恢复自动续费须独立确认未来扣款方案；本次不收费，原到期不变。购买新周期用付费续订；权益过期后的开通也须新确认付款。续订不默认改变自动续费，新开通默认仅买本次周期。已接入银行代扣或商户订阅须核对后确认取消，以对应回执为准；仅查看或咨询不会自动取消或付款。关闭续费不是退款，已付权益保留到期。未接入服务需到服务商核实并关闭，这里只能提供指引，不能保证外部商户已关闭。"),
    ("wealth.risk-v1", "先了解产品，再决定是否买入", "理财|基金|风险|收益|投资|申购|赎回|比较", "比较产品不会买入。办理前会核对风险是否适合你、产品当前能否交易，以及可用资金。收益没有保证，过去表现也不代表未来收益。买入和赎回都要分别核对确认；具体条件以当前条款、确认卡和回执为准。"),
    ("goals.available-v1", "预留资金后还能花多少", "目标|预留|可用|可支出|现金流|余额", "为目标预留资金后，现金总余额不变，但能花的钱会减少。释放预留后，相应的可支出金额恢复。请说清目标、金额和要预留还是释放；不会根据余额替你决定预留多少钱。"),
    ("family.scope-v1", "家人可以协助到哪一步", "家人|家庭|委托|授权|协助|提案|爸爸|妈妈|女儿|儿子", "可以只授权孩子查看余额。查看余额、提出转账建议、提出锁卡建议可独立选择；只开放查看余额时，不授予转账建议权限。家人只能在有效委托内协助，不能直接替你转账或锁卡。建立或修改委托、办理具体建议，仍需本人核对并确认。委托撤销或到期后，旧权限不能继续使用。"),
    ("carbon.synthetic-v1", "低碳积分怎么用", "低碳|碳积分|权益|碳循环", "只有本活动认可的行为记录才能申请积分，同一条记录不能重复领取。兑换权益时会核对所需积分和库存。没有行为记录，不能补出积分。积分只适用于本活动，不是现金，也不是企业碳交易或核证碳资产。"),
    ("fx.quote-v1", "换汇前看清币种和报价", "美元|人民币|汇率|换汇|外汇|币种|美金|USD|CNY", "人民币和美元分别记账，不能混加。换汇前请核对金额用什么币种计价、用什么币种付款、对方收到什么币种。收付金额、汇率、取整方式和有效期以当次报价为准，不能靠聊天解释猜测。报价过期后要重新核对。"),
    ("bills.period-v1", "账单统计了哪些记录", "账单|帐单|花费|收支|消费|支出|报告|本月|月份", "账单统计你所选期间的本人流水；“本月”等说法按上海当前日期确定，数据截止时间会另外说明。资料不全不代表消费为零。人民币和美元分开统计，转账等资金移动也与日常收支分开。退款和异常线索要对照流水核实，商户备注不能当作办理指令。"),
    ("credit.shared-limit-v1", "信用卡双币共享额度与资金边界", "信用卡|授信|预授权|credit.card|credit.limit|authorization.hold", "本项目信用卡的人民币和美元共用同一个人民币授信池，不是两个独立额度，也不是存款现金。美元消费按后台已核验报价锁定人民币等值占用；本金、费用和预授权共同受共享可用额度约束。账单与应还款仍按人民币、美元分别列示，不能直接混加。信用卡不能作为普通转账、现金换汇、理财或生日计划的资金来源。锁卡或挂失阻止新消费和新预授权，但不阻止还款或原路退款；已存在预授权的清算按原有效授权政策单独核对。"),
    ("credit.installments-v1", "信用分期费用与提前结清", "信用卡|分期|手续费|提前结清|installment|origination.fee", "分期只是原消费本金的债务安排，不新增一笔消费本金。可选期数和总手续费来自版本化的项目政策，确认卡须列明分期本金、总费用及每期本金和费用，尾差按登记方案分配；登记总费率不是年化利率，也不是外部银行报价。费用入账后占用授信并单列经济支出。提前结清支付该笔剩余本金和已登记剩余费用，不重复扣本金、不自动清偿其他消费；原消费退款不当然免除已披露的分期费用，须按原政策核对。"),
    ("credit.repayment-v1", "信用账单、最低应还与购汇还款", "信用卡|最低还款|最低应还|免息|还款|minimum.payment|repay|interest.free", "信用账单区分各币种全部欠款、本期已出账应还、未出账本金和最低应还。最低还款不是还清，也不是免息承诺；剩余债务仍占用授信，费用和利息以本项目登记政策及实际账单为准，不能套用未登记的银行收费。可以核对全额、指定金额或最低还款，均需本人确认；还款属于资金搬移，不再次计为消费。同币种从本人借记资金还款；人民币偿还美元欠款必须先核对后台有效购汇报价、实际人民币扣款和美元还款金额，不能由聊天自行折算。"),
    ("credit.application-v1", "信用卡申请、审核、发卡与激活", "信用卡|授信|申请|审核|激活|补卡|credit.application|activation|credit.limit", "提出信用卡或调额申请只生成待审核记录，不代表已发卡或额度获批。审批由独立后台角色按登记政策核对；首次发卡须明确核验成年申请条件，不能从亲属身份、生日、客户等级、卡等级或风险等级推断授信资格。审核通过后发卡，仍需本人另行激活。初始成人样例的一张双币信用卡不是终身张数上限；后续获批卡可共用原授信池，不能再复制一笔额度。挂失旧卡不能靠解锁撤销挂失，补发卡与原卡历史分别保留。"),
    ("life-plan.bounded-authorization-v1", "生日计划的一次有限授权", "生日|生日计划|长期计划|生日授权|birthday|life.plan", "生日日期记录与支付授权是两件事。本人核对并确认一次有限授权后，只能在已确认的一个生日年度发生项内自动执行：绑定预算、本人人民币借记账户、商品及单价上限、收件信息、执行时窗和截止时间，最多执行一次，不自动循环。月内预留只减少可用现金，不是扣款、转账或锁卡；预留也不允许其他用途消费。信用卡不用于本项目生日计划。农历生日没有对应映射时须本人确认当年公历日期，不能猜测。"),
    ("life-plan.execution-refund-v1", "生日计划暂停、执行和取消退款", "生日|生日计划|暂停|取消计划|birthday|life.plan", "符合原授权时窗的生日计划可在生日提前两天下单，等待触发或授权成功不等于已经购买或送达。库存不足、超出价格上限、收件信息变化、资金不足、卡片不可用或授权过期等阻断时应暂停核对，不自动换卡、换商品或解锁。取消只能撤销未执行部分；已付款订单按原订单条件申请原路退款。退款处理中或部分完成不能说全部已退，只有实际退款回执才能证明退回金额。已付款也不代表商品已经履约或送达。"),
    ("merchant.catalog-scope-v1", "商户商品、价格与库存的资料口径", "商户|商品|鲜花|蛋糕|礼物|merchant|product.catalog|flowers|cake|gift", "本项目商品来自系统内的虚拟商户目录，不表示已连接外部花店或配送网络。商品名称、价格、币种、库存和可售状态须查询当前目录；公开规则说明不代替当前商品报价，不保证历史价格或库存仍有效。当前目录按人民币定价；不能把人民币商品默认为美元报价或自行换汇。商品资料、库存核对和规则咨询本身不会下单或付款。"),
    ("merchant.payment-refund-v1", "商品付款、履约与原路退款", "商户|商品|订单|退款|merchant|order|refund", "单笔商品购买须核对原商品、数量、价格上限、收件信息和付款工具，并经本人确认。人民币商品可在支持范围内选择本人可用借记卡或信用卡，信用消费占用授信而不是扣存款；生日计划只使用已授权人民币借记资金。实际付款与履约是独立状态，付款成功不等于配送完成。符合原订单退款条件时只能退回原付款工具；退款待处理不等于已到账，信用退款也不是把授信额度变成现金。"),
    ("scope.boundary-v1", "解释和实际办理有什么区别", "", "你可以先问规则，或说明想办理的内容。聊天中的解释、建议和方案，不代表已经办理成功。办理时要核对本人权限、最新资料和确认范围。资料没有注明的费用、收益、风险条款或外部处理结果，不能补成确定结论。"),
]

ENGLISH_ENTRIES = {
    "transfer.confirm-v1": ("What to check before a transfer", r"transfer|payment|pay|send|remit|confirm|verification|authorization|authentication|OTP|code", "Check the full recipient, amount, currency, paying card and receiving method. Asking for a transfer only prepares a plan. If verification is required, complete it and then submit the confirmation card. Saying 'yes' in chat does not make a payment."),
    "transfer.unknown-v1": ("When a payment result is unclear", r"unknown|pending|duplicate|not received|timeout|failed|cancel|undo", "A timeout or unclear result does not mean the payment failed. Check the original request and receipt before trying again. Closing a request stops only steps that have not run; completed records stay, and this does not mean money was returned."),
    "cards.identity-v1": ("What locking, unlocking and reporting loss do", r"card|lock|unlock|freeze|lost|last four", "Temporarily locking your selected card marks it as locked; unlocking restores it to active. A lost-card report is separate and cannot be undone by unlocking. Locking does not change your cash balance. Here, transfers with that paying card and exchanges or transfers using that dual-currency card require the card to be active. This does not mean every payment stops. Family assistance only suggests actions within permission; you must review the latest status and confirm unlocking."),
    "subscription.scope-v1": ("Membership starts, paid renewals and automatic renewal", r"subscription|membership|renewal|renew|mandate|reactivat", "Use current registered prices and periods; do not guess external prices. While paid access is valid, an ordinary start only reads status: no charge, extension or auto-renewal restoration. Explicitly restoring automatic renewal requires separate confirmation of future debit terms: no current charge or expiry change. Buying another period is a paid renewal; starting expired access also requires fresh payment confirmation. Renewal preserves unspecified automatic-renewal settings; a new start does not enable them implicitly. Connected mandates or subscriptions require review and confirm cancellation; use the matching receipt. Viewing or consulting does not cancel anything or pay. Turning off renewal is not a refund; paid access remains until expiry. For unconnected services, verify and close through the provider; guidance cannot prove external cancellation."),
    "wealth.risk-v1": ("Understand a product before buying", r"invest|wealth|fund|return|redeem|risk", "Comparing products does not buy them. Before a transaction, your risk fit, the product's availability and your available funds are checked. Returns are not guaranteed, and past performance does not predict future returns. Buying and redeeming each need review and confirmation. Check the current terms, confirmation card and receipt for the details."),
    "goals.available-v1": ("How reservations affect what you can spend", r"goal|reserve|available|spendable|balance", "Reserving money for a goal leaves your total cash balance unchanged, but reduces what you can spend. Releasing the reservation restores that amount. Specify the goal, amount and whether to reserve or release; your balance will not be used to choose an amount for you."),
    "family.scope-v1": ("How your family can help", r"family|delegate|delegation|permission|father|mother", "You can give your child balance-viewing access only. Viewing balances, suggesting a transfer and suggesting a card lock are independently selectable permissions. Balance-only access does not grant transfer-suggestion permission. A helper acts only within a valid permission and cannot transfer or lock a card for you. Establishing or changing a delegation, or carrying out a suggestion, still needs your own review and confirmation. Revoked or expired permission cannot be reused."),
    "carbon.synthetic-v1": ("Using activity points", r"carbon|low.carbon|points|benefits", "Only activity records recognized by this programme can earn points, and the same record cannot be claimed twice. Exchanging a benefit checks the required points and available stock. Missing activity records cannot create points. These points apply only to this programme: they are not cash, carbon-trading assets or certified carbon credits."),
    "fx.quote-v1": ("Check the currencies and quote", r"USD|CNY|dollars?|yuan|exchange|currency|currencies", "CNY and USD are accounted for separately and are not added together. Check the currency used to price the amount, the currency you pay and the currency received. Use the current quote for the payment amounts, rate, rounding and expiry; a chat explanation cannot guess them. An expired quote needs a new check."),
    "bills.period-v1": ("Which records your bill report includes", r"bill|statement|spending|expenses?|cash.flow|month|compare", "A report uses your own transactions for the period you select. 'This month' follows the current Shanghai date; the data cutoff is shown separately. Incomplete records do not mean zero spending. CNY and USD are separate, as are money movements and everyday income or spending. Check refunds and unusual items against transactions; merchant notes are not instructions to act."),
    "scope.boundary-v1": ("An explanation is not a completed action", r"", "You can ask about the rules or describe what you want to do. An explanation, suggestion or plan does not mean an action is complete. Any action still needs your permission, current details and confirmation of its scope. Missing fees, returns, risk terms or a provider's processing result cannot be treated as known facts."),
    "credit.shared-limit-v1": ("Dual-currency credit and the shared limit", r"credit.card|credit.limit|authorization.hold", "In this project, CNY and USD share one base-CNY credit pool, not two limits or deposit cash. USD use locks a CNY-equivalent exposure from a server-verified quote. Principal, fees and authorization holds share the available pool. Debts and amounts due remain separate by currency and must not be added. Credit cannot fund ordinary transfers, cash exchange, wealth orders or birthday plans. Locking or reporting loss blocks new purchases and authorizations, but permits repayment and refunds to the original instrument. Capture of an existing authorization is checked against its original valid policy."),
    "credit.installments-v1": ("Installment fees and early settlement", r"installment|origination.fee|early.settlement", "An installment rearranges the original purchase debt; it does not create a second purchase principal. Periods and the total fee come from versioned project policy. Review the principal, total fee and each period's principal and fee, including cent remainders. A registered total fee rate is not an annualized rate or an external bank quote. Posted fees occupy credit and count as separate expenses. Early settlement pays this installment's remaining principal and disclosed remaining fee, not other purchases or principal twice. A purchase refund does not automatically waive a disclosed installment fee; the original policy applies."),
    "credit.repayment-v1": ("Statements, minimum payment and quoted FX repayment", r"minimum.payment|repay|interest.free|credit.statement", "Statements separate each currency's outstanding debt, billed due, unbilled principal and minimum due. A minimum payment is neither full repayment nor a promise of interest-free credit. Remaining debt still occupies credit; use registered project policy and actual statements for fees and interest, never assumed bank charges. Full, specified and minimum repayment each require your confirmation. Repayment is money movement, not another purchase. Use owned debit cash in the same currency; CNY repayment of USD debt requires a valid server FX quote and review of both the actual CNY debit and USD repayment. Chat must not invent the conversion."),
    "credit.application-v1": ("Credit application, review, issue and activation", r"credit.application|activation|credit.limit", "A card or limit request creates a pending review, not an issued card or approved limit. A separate administrator checks registered policy; first issue requires explicit adult eligibility, never inferred from family role, a birthday, customer tier, card tier or risk level. Approval and issue are followed by the holder's separate activation. One initial dual-currency card in an adult sample is not a lifetime card-count limit. Later approved cards may share the original pool, without duplicating credit. Unlocking cannot undo a lost-card report; replacement and old-card history stay separate."),
    "life-plan.bounded-authorization-v1": ("One bounded birthday-plan authorization", r"birthday|life.plan", "Recording a birthday does not authorize payment. One reviewed and confirmed mandate can automatically execute only one birthday-year occurrence within its bound budget, owned CNY debit account, product and unit-price cap, recipient details, execution window and expiry. It is not recurring. Reserving money reduces spendable cash, not the cash book balance; it is not payment, transfer or a card lock and is unavailable to other purposes. This project's birthday plans do not use credit cards. A lunar date without a registered mapping needs your explicit solar date for that year; the system must not guess."),
    "life-plan.execution-refund-v1": ("Birthday-plan pauses, execution and refunds", r"birthday|life.plan", "Within its original mandate and window, a birthday plan can order two days before the birthday. Waiting or authorization is not purchase or delivery. Missing stock, prices above the cap, changed recipient details, insufficient funds, unusable cards or expiry pause execution for review; the system does not switch cards or products or unlock a card automatically. Cancellation stops unexecuted work only. Paid orders follow their original refund conditions and original instrument. Pending or partial refunds do not mean everything has been returned; an actual refund receipt proves the amount. Paid also does not mean fulfilled or delivered."),
    "merchant.catalog-scope-v1": ("Merchant products, prices and stock", r"merchant|product.catalog|flowers|cake|gift", "This project's products belong to its internal virtual merchant catalogue, not a connected external flower shop or delivery network. Query the current catalogue for names, prices, currency, stock and sale status. Public rules are not a current product quote, and historical price or stock is not guaranteed. The current catalogue prices in CNY, not implicit USD or an invented conversion. Reading products, stock or rules does not place or pay for an order."),
    "merchant.payment-refund-v1": ("Product payment, fulfilment and original-instrument refunds", r"merchant|order|refund", "A single purchase requires review of the original product, quantity, price cap, recipient details and payment instrument, then your confirmation. Supported CNY purchases may use your available debit or credit card; credit occupies the credit pool rather than deposit cash. Birthday plans use their authorized CNY debit funds only. Payment and fulfilment are separate: paid does not mean delivered. An eligible refund returns only to the original instrument. A pending refund is not received money, and a credit refund does not turn available credit into cash."),
}


from .feedback_public_policy import upgrade_entries as _upgrade_feedback_entries

ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_ENTRY_VERSIONS, FEEDBACK_POLICY_PREDECESSORS = _upgrade_feedback_entries(
    ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_ENTRY_VERSIONS, KNOWLEDGE_VERSION)


def knowledge_sources(message):
    matched = [(identifier, title, text) for identifier, title, pattern, text in ENTRIES
               if pattern and (re.search(pattern, message, re.I) or re.search(ENGLISH_ENTRIES[identifier][1], message, re.I))]
    if not matched:
        matched = [(ENTRIES[-1][0], ENTRIES[-1][1], ENTRIES[-1][3])]
    return [{"source_id": "kb:" + identifier, "title": title, "title_en": ENGLISH_ENTRIES[identifier][0], "content_en": ENGLISH_ENTRIES[identifier][2], "kind": "PRODUCT_KNOWLEDGE",
             "reference": "product_knowledge:" + identifier, "content": text,
             "version": KNOWLEDGE_ENTRY_VERSIONS.get(identifier, KNOWLEDGE_VERSION)} for identifier, title, text in matched[:6]]


def subscription_catalog_source(session,message):
    """Public current terms only; no bank-account lookup or payment authority."""
    if not re.search(r"订阅|会员|续费|云存储|\b(?:subscription|membership|renewal|reactivation|cloud\s+storage)\b",message,re.I):
        return None
    from .subscription_commerce import subscription_catalog_rows,MERCHANT_ALIASES
    products=subscription_catalog_rows(session)
    named={product["merchant"] for product in products if product["merchant"] in message}
    named.update(name for alias,name in MERCHANT_ALIASES.items() if alias in message.casefold())
    if len(named)==1:products=[product for product in products if product["merchant"] in named]
    return {"source_id":"facts:subscription-products","title":"当前已登记会员方案",
        "kind":"SCOPED_BUSINESS_FACTS","reference":"subscription-catalog:registered-products",
        "content":json.dumps(products,ensure_ascii=False,sort_keys=True),"observed_at":utcnow().isoformat(),
        "catalog_filter":next(iter(named)) if len(named)==1 else None,
        "authority":"REGISTERED_PRODUCT_TERMS_NOT_PAYMENT_OR_RECURRING_AUTHORIZATION"}


def scoped_sources(session, principal, message, task=None):
    records = knowledge_sources(message)
    from .intelligence import _intent_text, _private_read_veto, financial_inquiry
    from .financial_intent import foreign_financial_request
    # Public topic knowledge may use source material; it must never grant a
    # private bank read, choose a reporting period or request another owner.
    message = _intent_text(message)
    if _private_read_veto(message):
        return records  # The caller authenticated the turn; public KB needs no bank read.
    observed = utcnow().isoformat()
    catalog_record=subscription_catalog_source(session,message)
    if catalog_record:records.append(catalog_record)
    personal = financial_inquiry(message) or bool(re.search(r"我的|本人|我(?:自己|这|当前|现在|的)?|本账户|当前账套|当前(?:余额|钱包|卡片|订阅|目标|授权)|有效委托|\b(?:my|mine|I|me|our)\b", message, re.I))
    foreign_scope = foreign_financial_request(message) or bool(re.search(r"\b(?:his|her|their|(?:friend|father|mother|partner|wife|husband|colleague|landlord)'?s|someone\s+else'?s|another\s+user'?s)\b", message, re.I))
    if not personal and task is None:
        return records  # Generic FAQ needs neither a full banking read nor private facts.
    from .permissions import actor_role
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if foreign_scope and role != "FAMILY_HELPER":
        return records  # Never substitute the customer's money for another owner.
    from .goal_explanation_facts import goal_explanation_question,goal_explanation_requested,goal_calculation_source
    if task is not None and role!='FAMILY_HELPER' and goal_explanation_question(message):
        from .task_dialogue_context import task_context
        context=task_context(session,principal,task)
        source=goal_calculation_source(context) if goal_explanation_requested(message,context) else None
        if source: return [source]+records
    if role == "FAMILY_HELPER" and personal:
        if re.search(r"余额|可用|权限|委托|授权|家人|家庭|协助|\bbalance|available|permission|delegation|family\b", message, re.I):
            from .extensions import family_view
            projection = family_view(session, principal.sandbox_id, principal.user_id)
            visible = {"permissions": projection["permissions"], "delegations": projection["delegations"]}
            owner_ids = [row["owner_user_id"] for row in projection["delegations"]
                         if row["valid"] and row["grantee_user_id"] == principal.user_id and "READ_BALANCE" in row["scopes"]]
            if re.search(r"余额|\bbalance\b", message, re.I) and owner_ids:
                visible["authorized_balances"] = projection.get("authorized_balances", [])
            records.append({"source_id": "facts:family-projection", "title": "当前有效家庭授权投影", "title_en": "Currently authorized family projection",
                "kind": "SCOPED_BUSINESS_FACTS", "reference": "/api/v2/extensions/family",
                "content": json.dumps(visible, ensure_ascii=False, default=str), "observed_at": observed,
                "required_read_owner_ids": owner_ids if "authorized_balances" in visible else []})
    elif role != "FAMILY_HELPER" and personal:
        from .agent_context import planning_facts
        from .financial_diagnostics import financial_question
        facts = planning_facts(session, principal)
        groups = [(r"余额|可用|可支出|预留|现金流|\bbalance|available|spendable|cash.flow|reserved\b", "cash", "本人现金与目标预留", "/api/v1/accounts"),
                  (r"卡|锁|挂失|尾号|\bcard|lock|freeze|lost\b", "cards", "本人当前卡片状态", "/api/v1/cards"),
                  (r"订阅|会员|续费|代扣|\bsubscription|membership|renewal|mandate\b", "subscriptions", "本人当前订阅状态", "/api/v1/subscriptions"),
                  (r"目标|预留|现金流|\bgoal|reserved|cash.flow\b", "goals", "本人当前目标", "/api/v2/extensions/goals"),
                  (r"币种|美元|美金|钱包|\bcurrency|currencies|USD|dollars?|wallet\b", "currencies", "本人双币能力", "/api/v2/fx/wallets")]
        for pattern, key, title, reference in groups:
            if re.search(pattern, message, re.I) and facts.get(key) is not None:
                value = facts[key]
                if key == "currencies" and not re.search(r"余额|钱包|有多少|\bbalance|wallet|how\s+much\b", message, re.I):
                    value = {field: value[field] for field in ("supported", "dual_card", "goals_currency", "foreign_schedules_aa_family_supported") if field in value}
                records.append({"source_id": "facts:" + key, "title": title,
                    "title_en": {"cash": "My cash and reservations", "cards": "My current cards", "subscriptions": "My current subscriptions", "goals": "My current savings goals", "currencies": "My currency capabilities"}[key], "kind": "SCOPED_BUSINESS_FACTS",
                    "reference": reference, "content": json.dumps(value, ensure_ascii=False, default=str),
                    "observed_at": observed})
        if financial_inquiry(message) or re.search(r"账单|帐单|花费|花得|消费|支出|钱.*(?:少|去哪)|\bbills?|statements?|spending|expenses?|cash.flow\b", message, re.I) or financial_question(message) == "CASH_FLOW":
            from .financial_diagnostics import analyze_financial_request
            from .reports import owned_account
            account = owned_account(session, principal.sandbox_id, principal.user_id)
            analysis = analyze_financial_request(session, principal.sandbox_id, account.id, message)
            summary = analysis.model_dump(exclude={"financial_insights", "anomalies", "monthly", "daily"})
            diagnostics = analysis.financial_insights
            if analysis.billing_schema_version == "owned-bill-analysis-v2":
                from .billing_diagnostics import scoped_bill_diagnostics
                diagnostics = scoped_bill_diagnostics(analysis)
            records.append({"source_id": "facts:bill-report", "title": "本人账单期间与已入账统计", "title_en": "My posted bill records and coverage",
                "kind": "SCOPED_BUSINESS_FACTS", "reference": "/api/v2/bills/report",
                "content": json.dumps(summary, ensure_ascii=False), "observed_at": observed})
            records.append({"source_id": "facts:bill-diagnostics", "title": "本人财务问题的核验事实", "title_en": "Verified facts for my financial question",
                "kind": "SCOPED_BUSINESS_FACTS", "reference": "/api/v2/bills/report",
                "content": json.dumps(diagnostics, ensure_ascii=False), "observed_at": observed})
    if task:
        facts = {"state": task.state, "version": task.version, "intent_type": task.intent_type}
        if role != "FAMILY_HELPER":
            facts["error_code"] = task.result_json.get("error") if task.result_json else None
        records.append({"source_id": "facts:current-task", "title": "本会话关联任务的当前状态",
            "kind": "SCOPED_TASK_STATE", "reference": f"/api/v2/tasks/{task.id}/trace",
            "content": json.dumps(facts, ensure_ascii=False), "observed_at": observed})
    return records


def source_fingerprint(records):
    def stable_clock_metadata(value):
        if isinstance(value, dict):
            if value.get('scoped_projection_version')=='bill-diagnostics-model-facts-v2':
                from .billing_diagnostics import normalize_scoped_bill_diagnostics
                normalized = normalize_scoped_bill_diagnostics(value)
                if normalized is None:
                    return value
                return {key: stable_clock_metadata(item) for key,item in normalized.items() if key!='calendar_time'}
            if value.get('version')=='full-recorded-bill-largest-expense-v1':
                # The original hash covers the observation timestamp. Only an
                # exact, complete registered snapshot may be normalized for a
                # same-Shanghai-date consultation recheck. Invalid hashes keep
                # their full raw identity; never hide source tampering.
                body={key:item for key,item in value.items() if key!='snapshot_hash'}
                from datetime import datetime
                from zoneinfo import ZoneInfo
                try:
                    clock=datetime.fromisoformat(value['calendar_time'])
                    valid=(clock.tzinfo is not None and value.get('snapshot_hash')==canonical_hash(body)
                        and value.get('authority')=='BANK_FULL_RECORDED_WINDOW_NOT_UI_SLICE_OR_MODEL_CALCULATION'
                        and value.get('currency')=='CNY' and value.get('unit')=='MINOR'
                        and value.get('source_complete') is True and value.get('read_only') is True
                        and value.get('bank_state_changed') is False and value.get('model_calculated_money') is False
                        and isinstance(value.get('coverage'),dict)
                        and value['coverage'].get('calendar_date')==clock.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat())
                except (ValueError,TypeError,KeyError):valid=False
                if not valid:return value
                stable_body={key:stable_clock_metadata(item) for key,item in body.items() if key!='calendar_time'}
                stable_body['calendar_date']=clock.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
                return {**stable_body,'snapshot_hash':canonical_hash(stable_body)}
            return {key: stable_clock_metadata(item) for key, item in value.items() if key != "calendar_time"}
        if isinstance(value, list):
            return [stable_clock_metadata(item) for item in value]
        return value
    stable = []
    for row in records:
        record = {key: value for key, value in row.items() if key != "observed_at"}
        if row.get("source_id") in {"facts:bill-report", "facts:bill-diagnostics"}:
            # The snapshot-read timestamp is not a business revision. Calendar
            # date, selected boundaries, data watermark and all money remain in
            # the fingerprint; midnight/coverage/ledger changes still invalidate.
            try:
                record["content"] = json.dumps(stable_clock_metadata(json.loads(record["content"])), ensure_ascii=False, sort_keys=True)
            except (ValueError, TypeError, KeyError):
                pass
        stable.append(record)
    return canonical_hash(stable)


def monetary_fact_catalog(records):
    """Typed server facts: money/minor units, points, states and risk categories."""
    facts = {}
    for row in records:
        from .public_wealth_knowledge import fact_catalog as public_product_fact_catalog
        facts.update(public_product_fact_catalog([row]))
        from .dialogue_reference_facts import selected_object_money
        facts.update(selected_object_money(row))
        from .dialogue_reference_facts import selected_object_points
        facts.update(selected_object_points(row))
        from .dialogue_reference_facts import selected_bill_selection_catalog
        facts.update(selected_bill_selection_catalog(row))
        from .selected_result_status_facts import selected_result_status_catalog
        facts.update(selected_result_status_catalog(row))
        if row.get("kind") != "SCOPED_BUSINESS_FACTS":
            continue
        try:
            content = json.loads(row["content"])
        except (ValueError, TypeError):
            continue
        sid = row["source_id"]
        if sid=="facts:subscription-products" and isinstance(content,list):
            for product in content:
                if not isinstance(product,dict) or product.get("currency")!="CNY" \
                        or type(product.get("monthly_price_minor")) is not int or product["monthly_price_minor"]<=0 \
                        or product.get("can_start_here") is not True or not isinstance(product.get("merchant"),str):
                    continue
                identifier=sid+"."+canonical_hash({"merchant":product["merchant"],"version":product.get("version")})[:24]
                facts[identifier]={"fact_id":identifier,"source_id":sid,"field":"monthly_price_minor",
                    "currency":"CNY","unit":"MINOR","value":product["monthly_price_minor"],
                    "label":product["merchant"]+"月价",
                    "label_en":product["merchant"]+" monthly price"}
                months=product.get('supported_months')or{}
                if type(months.get('min')) is int and type(months.get('max')) is int and 1<=months['min']<=months['max']<=12 \
                        and type(product.get('default_months')) is int and months['min']<=product['default_months']<=months['max'] \
                        and type(product.get('default_auto_renew')) is bool:
                    key=identifier+'.terms'
                    facts[key]={'fact_id':key,'source_id':sid,'field':'registered_subscription_terms','unit':'SUBSCRIPTION_TERMS','currency':'NONE',
                        'label':product['merchant'],'value':{'min_months':months['min'],'max_months':months['max'],
                            'default_months':product['default_months'],'default_auto_renew':product['default_auto_renew']}}
        if sid=='facts:goal-calculation':
            from .goal_explanation_facts import goal_source_content,FACT_ID
            proof=goal_source_content(row)
            if proof:
                facts[FACT_ID]={'fact_id':FACT_ID,'source_id':sid,'field':'verified_goal_explanation','currency':'CNY','unit':'GOAL_CALCULATION',
                    'value':proof,'label':'本次目标储蓄测算依据','label_en':'Verified savings calculation'}
        if sid == "facts:cash" and isinstance(content, dict):
            currency = content.get("currency")
            for field, label in (("balance_minor", "本人现金余额"), ("goal_reserved_minor", "目标已预留金额"),
                    ("reserved_cash_minor", "现金预留总额（含目标及已授权计划）"),
                    ("spendable_minor", "扣除现金预留后金额（非最终可执行额度）" if 'reserved_cash_minor' in content else "扣除目标预留后金额（非最终可执行额度）")):
                if currency in {"CNY", "USD"} and type(content.get(field)) is int:
                    identifier = sid + "." + field
                    facts[identifier] = {"fact_id": identifier, "source_id": sid, "field": field,
                        "currency": currency, "unit": "MINOR", "value": content[field], "label": label}
        if sid == "facts:currencies" and isinstance(content, dict):
            wallet = content.get("usd_wallet")
            if isinstance(wallet, dict) and wallet.get("currency") == "USD" and type(wallet.get("balance_minor")) is int:
                identifier = sid + ".usd_wallet.balance_minor"
                facts[identifier] = {"fact_id": identifier, "source_id": sid, "field": "usd_wallet.balance_minor",
                    "currency": "USD", "unit": "MINOR", "value": wallet["balance_minor"], "label": "本人美元钱包余额"}
        if sid == "facts:bill-diagnostics" and isinstance(content, dict):
            coverage = content.get("coverage", {})
            if not isinstance(coverage, dict) or coverage.get("status") in {"INSUFFICIENT", "UNKNOWN", None}:
                continue
            rows = content.get("facts", [])
            if not isinstance(rows, list):
                continue
            for fact in rows:
                if not isinstance(fact, dict) or fact.get("source_id") != sid or fact.get("unit") != "MINOR" or fact.get("currency") not in {"CNY", "USD"} or type(fact.get("value")) is not int:
                    continue
                identifier = sid+"."+str(fact.get("field", ""))
                if fact.get("fact_id") == identifier and isinstance(fact.get("field"), str) and re.fullmatch(r"[a-z_0-9]+", fact["field"]) and isinstance(fact.get("label"), str):
                    facts[identifier] = {**fact, "label_en": fact.get("label_en") if isinstance(fact.get("label_en"), str) else None}
    return facts


def render_monetary_facts(facts, identifiers, reply_language=None, *, language=None):
    from .language import normalize_language
    english = normalize_language(language or reply_language) == "en"
    english_labels = {"balance_minor": "My cash balance", "goal_reserved_minor": "Funds reserved for goals",
                      "reserved_cash_minor": "Total cash reserved for goals and authorized plans",
                      "spendable_minor": "Available after reservations (not a final executable limit)",
                      "usd_wallet.balance_minor": "My USD wallet balance"}
    from .goal_explanation_facts import render_goal_explanation
    rendered=[]
    for key in identifiers:
        fact=facts[key]
        if fact.get("unit")=="GOAL_CALCULATION":
            rendered.append(render_goal_explanation(fact["value"],english))
        elif fact.get("unit")=="BILL_SELECTION":
            from .dialogue_reference_facts import render_selected_bill_selection
            rendered.append(render_selected_bill_selection(fact,"en" if english else "zh-CN"))
        elif fact.get("unit")=="RESULT_STATE":
            from .selected_result_status_facts import render_selected_result_status
            text=render_selected_result_status(fact,"en" if english else "zh-CN")
            if text:rendered.append(text)
        elif fact.get("unit")=="PUBLIC_RISK_CATEGORY":
            from .public_wealth_knowledge import render_risk_fact
            rendered.append(render_risk_fact(fact,"en" if english else "zh-CN"))
        elif fact.get('unit')=='SUBSCRIPTION_TERMS':
            terms=fact['value']
            rendered.append((f"{fact['label']}: choose {terms['min_months']}–{terms['max_months']} months, default {terms['default_months']}; automatic renewal defaults to {'on' if terms['default_auto_renew'] else 'off'}. A separate confirmation is required before charging." if english else
                f"{fact['label']}可选{terms['min_months']}—{terms['max_months']}个月，默认{terms['default_months']}个月；自动续费默认{'开启' if terms['default_auto_renew'] else '关闭'}。核对并确认后才会付款。"))
        elif fact.get("unit")=="POINTS":
            # Personal benefit points are an integer unit, never money cents.
            if type(fact.get('value')) is not int or fact['value']<0:
                raise ValueError('INVALID_POINT_FACT')
            label=(fact.get('label_en') or 'Verified points') if english else fact['label']
            rendered.append(f"{label}: {fact['value']} points." if english else f"{label}：{fact['value']}积分。")
        elif english:
            rendered.append((fact.get("label_en") or english_labels.get(fact["field"],"Verified amount"))+f": {Decimal(fact['value'])/100:.2f} {fact['currency']}.")
        else:
            rendered.append(f"{fact['label']}：{Decimal(fact['value'])/100:.2f}"+("人民币元" if fact["currency"]=="CNY" else "美元")+"。")
    return "\n".join(rendered)


def _english_business_content(row, facts, identifiers):
    """Conservative registered fallback, not raw JSON or translated identifiers."""
    references = {"facts:cards": "/api/v1/cards", "facts:subscriptions": "/api/v1/subscriptions",
                  "facts:goals": "/api/v2/extensions/goals", "facts:currencies": "/api/v2/fx/wallets",
                  "facts:family-projection": "/api/v2/extensions/family"}
    identifier = row.get("source_id")
    if identifier not in references or row.get("kind") != "SCOPED_BUSINESS_FACTS" or row.get("reference") != references[identifier]:
        return None
    try:
        data = json.loads(row["content"])
    except (ValueError, TypeError):
        return "The authorized details are unavailable. Please check the corresponding service view."
    def name(value):
        return re.sub(r"[\r\n\t]", " ", value)[:100] if isinstance(value, str) else "This service"
    states = {"ACTIVE": "active", "LOCKED": "temporarily locked", "LOST": "reported lost",
              "CANCELLED": "cancelled in the connected record", "PAUSED": "paused", "CLOSED": "closed"}
    lines = []
    if identifier == "facts:cards" and isinstance(data, list):
        for item in data[:6]:
            if not isinstance(item, dict):
                continue
            suffix = item.get("display_last4", item.get("last4"))
            label = "Card ending "+suffix if isinstance(suffix, str) and re.fullmatch(r"[0-9a-fA-F]{4}", suffix) else "Your card"
            lines.append(label+": "+states.get(item.get("status"), "status needs verification")+".")
    elif identifier == "facts:subscriptions" and isinstance(data, list):
        for item in data[:10]:
            if isinstance(item, dict):
                lines.append(name(item.get("merchant"))+": "+states.get(item.get("status"), "status needs verification")
                             + (", bank direct debit." if item.get("source") == "BANK_MANDATE" else ". Check the service connection before cancelling."))
    elif identifier == "facts:goals" and isinstance(data, list):
        for item in data[:10]:
            if isinstance(item, dict):
                lines.append(name(item.get("goal_name"))+": savings goal record available; check its amounts and reservation details in the goal card.")
    elif identifier == "facts:currencies" and isinstance(data, dict):
        supported = data.get("supported")
        if isinstance(supported, list):
            listed = [value for value in supported if value in {"CNY", "USD"}]
            if listed:
                lines.append("Supported account currencies: "+", ".join(listed)+". They remain separate denominations.")
        if type(data.get("dual_card")) is bool:
            lines.append("A dual-currency card is available." if data["dual_card"] else "No dual-currency card is recorded for this account.")
        if identifiers:
            lines.append(render_monetary_facts(facts, identifiers, reply_language="en"))
    elif identifier == "facts:family-projection" and isinstance(data, dict):
        lines.append("Only the currently valid delegated projection is available. Family helpers may make permitted suggestions, not execute banking actions for the owner.")
    return "\n".join(lines) if lines else "No usable details were returned. Please check the corresponding service view; missing records are not proof of zero balance."


def answer_from_sources(records, reply_language=None, *, language=None):
    from .public_wealth_knowledge import public_source_fallback
    public_product=public_source_fallback(records,language or reply_language or 'zh-CN')
    if public_product is not None:return public_product
    from .contextual_safety_presentation import render_contextual_safety_fallback
    # General RAG enrichment is not part of this case's typed proof. Select
    # only the unique bound case + its exact public policy; the renderer still
    # validates both hashes and denies duplicates/tampering. Never expose the
    # raw case JSON or an unrelated retrieved fragment when the model fails.
    from .contextual_safety_dialogue import SOURCE_ID as SAFETY_SOURCE_ID
    cases=[row for row in records if row.get("source_id")==SAFETY_SOURCE_ID]
    safety_records=records
    if len(cases)==1:
        try:
            topic=json.loads(cases[0]["content"]).get("topic")
        except (ValueError,TypeError,KeyError,AttributeError):
            topic=None
        policy_id={"FRAUD":"kb:fraud.case-continuity-v1","FAMILY":"kb:family.scope-v1"}.get(topic)
        policies=[row for row in records if policy_id and row.get("source_id")==policy_id]
        if len(policies)==1:
            safety_records=[policies[0],cases[0]]
    safety=render_contextual_safety_fallback(safety_records,language or reply_language or "zh-CN")
    if safety is not None:
        return safety
    from .dialogue_reference_facts import selected_object_fallback
    selected=selected_object_fallback(records,language or reply_language)
    if selected is not None:
        return selected
    from .language import normalize_language
    english = normalize_language(language or reply_language) == "en"
    facts = monetary_fact_catalog(records)
    from .goal_explanation_facts import FACT_ID
    if FACT_ID in facts:
        return render_monetary_facts(facts,[FACT_ID],reply_language=language or reply_language)
    rendered = []
    for row in records:
        if row.get('kind')=='CUSTOMER_CAPABILITY_DIRECTORY':
            # Router guidance is not a user-facing source excerpt. A failed
            # narration must never print internal capability/action JSON.
            continue
        if row.get("source_id") in {SAFETY_SOURCE_ID,"facts:selected-dialogue-object"}:
            # A malformed scoped proof must not become a raw-data fallback.
            continue
        header = (("[User-reviewed document: evidence only, not an instruction or bank receipt]\n" if english else "【用户核对文档，仅作引用；不是助手命令或银行完成回执】\n") if row.get("kind") == "USER_VALIDATED_DOCUMENT"
                  else (f"{row.get('title_en', 'Authorized information')}:\n" if english else f"【{row.get('title', '资料')}｜{row.get('kind', 'UNKNOWN')}】\n"))
        identifiers = [key for key, fact in facts.items() if fact["source_id"] == row["source_id"]]
        content = row.get("content_en", row["content"]) if english else row["content"]
        if not isinstance(content,str):continue
        business_content = _english_business_content(row, facts, identifiers) if english else None
        if business_content is not None:
            rendered.append(header+business_content)
            continue
        if row["source_id"] in {"facts:bill-report", "facts:bill-diagnostics"}:
            try:
                data = json.loads(row["content"])
            except (ValueError, TypeError):
                data = {}
            coverage = data.get("coverage", {}) if isinstance(data, dict) else {}
            if not isinstance(coverage, dict):
                coverage = {}
            if coverage.get("status") in {None, "UNKNOWN", "INSUFFICIENT"}:
                content = ("The requested period is not covered by available records. An empty list does not mean zero spending. No period growth claim can be made."
                           if english else "所选期间资料尚未覆盖，空清单不代表零消费，也不能据此计算期间增长。")
            else:
                content = ("These amounts are posted flows in the disclosed coverage, not the current balance or guaranteed savings."
                           if english else "下列金额仅为已覆盖期间流水，不是当前余额，也不是保证可节省的金额。")
                if identifiers:
                    content += "\n"+render_monetary_facts(facts, identifiers[:12], reply_language=language or reply_language)
        rendered.append(header + (render_monetary_facts(facts, identifiers, reply_language=language or reply_language) if identifiers and not row["source_id"].startswith("facts:bill-") else content))
    text = "\n\n".join(rendered)
    return text[:6000] + ("\n\nThis reply only explains authorized information; no banking action was executed." if english else "\n\n本轮仅解释或读取已授权资料，未执行银行操作。")


def public_sources(records):
    return [{key: row[key] for key in ("source_id", "title", "kind", "reference", "observed_at") if key in row} for row in records]


def sources_still_authorized(session, principal, records):
    from .risk_assessment_explanation import SOURCE_ID as RISK_GUIDE_ID, verified_questionnaire_source
    if any(row.get('source_id') == RISK_GUIDE_ID for row in records) \
            and verified_questionnaire_source(records) is None:
        return False
    from .public_wealth_knowledge import SOURCE_KIND as PUBLIC_PRODUCT_KIND,source_current as public_product_current
    for row in records:
        if row.get('kind')==PUBLIC_PRODUCT_KIND or str(row.get('source_id','')).startswith('public-product:'):
            if not public_product_current(session,principal,row):return False
    from .semantic_materials import analysis_material_sources_current
    if not analysis_material_sources_current(session,principal,records):
        return False
    from .attachments import attachment_evidence_for_conversation
    from .contextual_safety_dialogue import contextual_safety_sources_current
    if not contextual_safety_sources_current(session, principal, records):
        return False
    from .dialogue_reference_facts import selected_object_sources_current
    if not selected_object_sources_current(session,principal,records):
        return False
    for row in records:
        if str(row.get('source_id','')).startswith('merchant-catalog:'):
            from .merchant_catalog_consultation import catalog_source_authorized
            if not catalog_source_authorized(session,principal,row):return False
        if row.get("source_id")=="facts:subscription-products":
            from .subscription_commerce import subscription_catalog_rows
            if row.get("kind")!="SCOPED_BUSINESS_FACTS" or row.get("authority")!="REGISTERED_PRODUCT_TERMS_NOT_PAYMENT_OR_RECURRING_AUTHORIZATION":
                return False
            try:
                current=subscription_catalog_rows(session)
                selected=row.get("catalog_filter")
                if selected is not None:
                    if selected not in {product["merchant"] for product in current}:return False
                    current=[product for product in current if product["merchant"]==selected]
                if json.loads(row["content"])!=current:return False
            except (ValueError,TypeError,KeyError):return False
        if row.get('source_id')=='facts:goal-calculation':
            from .goal_explanation_facts import goal_source_content,goal_calculation_source
            from .task_dialogue_context import task_context
            from .models import Task
            proof=goal_source_content(row)
            if not proof: return False
            try:
                task=session.get(Task,proof['task_id'])
                fresh=goal_calculation_source(task_context(session,principal,task))
                if fresh is None or source_fingerprint([fresh])!=source_fingerprint([row]): return False
            except (DomainError,ValueError,TypeError,KeyError,AttributeError): return False
        if row.get("kind") == "USER_VALIDATED_DOCUMENT":
            try:
                identifier = row["source_id"].removeprefix("review:")
                if not row["source_id"].startswith("review:"):
                    return False
                attachment_evidence_for_conversation(session, principal, identifier, row["attachment_version"], row["sha256"])
            except (DomainError, ValueError, KeyError, TypeError, AttributeError):
                return False
    required = {identifier for row in records for identifier in row.get("required_read_owner_ids", [])}
    if not required:
        return True
    from .extensions import family_view
    projection = family_view(session, principal.sandbox_id, principal.user_id)
    readable = {row["owner_user_id"] for row in projection["delegations"]
                if row["valid"] and row["grantee_user_id"] == principal.user_id and "READ_BALANCE" in row["scopes"]}
    return required <= readable
