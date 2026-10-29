"""Remove known server presentation copy, never genuine business risk warnings."""
FOOTERS = (
    "本轮仅解释，未执行银行操作。",
    "本轮仅解释或读取已授权资料，未执行银行操作。",
    "本轮仅提供解释，办理时仍需明确请求并按原流程核对。",
    "This reply explains the available information; it did not execute a bank operation.",
    "This reply only explains authorized information; no banking action was executed.",
    "This reply only explains information. A banking action needs an explicit request and the original review and confirmation process.",
)

# Only server-owned result narration is passed to this filter. Do not use it
# for user messages, business records, quoted consultations or audit evidence.
INTERNAL_RESULT_COPY = (
    "余额与单笔限额分开展示；共用同一账户的卡片不重复合计，不同币种不直接相加。",
    "Balances and per-payment limits are separate.",
    "Cards sharing one account are not added twice; different currencies are not added together.",
    "收入、消费、退款与资金搬移分别展示，人民币和美元不混加。",
    "Spending, income, refunds and money movements are separate; CNY and USD are not added together.",
)


def strip_internal_result_copy(text):
    """Hide exact historical generated copy without rewriting stored facts."""
    if not isinstance(text, str):
        return text
    value = text
    for sentence in INTERNAL_RESULT_COPY:
        value = value.replace(sentence, "")
    return value.strip()


def strip_customer_boilerplate(text):
    if not isinstance(text, str): return text
    value = text.rstrip()
    for _ in range(3):
        suffix = next((footer for footer in FOOTERS if value == footer or value.endswith('\n\n'+footer)), None)
        if suffix is None: break
        value = value[:-len(suffix)].rstrip()
    return value
