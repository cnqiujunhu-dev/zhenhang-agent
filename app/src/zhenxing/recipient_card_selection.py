"""Closed receiving-resource grammar, not a transfer intent classifier.

The selector identifies the recipient's registered default, never a guessed
card ID. The bank service must resolve that default and seal its real version.
"""
import re
import unicodedata


REGISTERED_DEFAULT = "REGISTERED_DEFAULT"
KINDS = frozenset({"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"})
# The semantic model may select a complete receiving-resource phrase rather
# than only its noun. Permit one closed directional wrapper; it contributes
# no payee, card identity, money, intent, or authorization of its own.
_ZH = re.compile(r"^(?:(?:收款(?:用|使用|选用|采用)|转入|汇入|打入|存入)\s*)?"
    r"(?:(?:该收款人|收款人|收款方|对方|他|她|其)(?:的)?\s*)?"
    r"(?:(?:已)?登记(?:的)?\s*)?默认(?:的)?(?:收款)?(?:借记卡|储蓄卡|银行卡|卡|账户)$")
_EN = re.compile(r"^(?:(?:receive|receiving)\s+(?:with|using)\s+)?"
    r"(?:(?:the\s+recipient's|recipient's|his|her|their)\s+)?"
    r"(?:registered\s+)?default\s+(?:(?:receiving|recipient)\s+)?(?:debit\s+)?(?:card|account)$", re.I)


def is_registered_default_literal(value):
    if not isinstance(value, str) or not 3 <= len(value) <= 120:
        return False
    literal = unicodedata.normalize("NFKC", value).strip()
    return bool(_ZH.fullmatch(literal) or _EN.fullmatch(literal))
