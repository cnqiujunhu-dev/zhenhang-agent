"""Mask only expressly denied unit mentions, never money or write authority."""
import re


_NUMBER = r"[\d零〇一二两三四五六七八九十百千万]+(?:\s*[./]\s*\d+|(?:又|分之)[\d零〇一二两三四五六七八九十半]+)?"
_MENTION = re.compile(r"(?<![\d./])"+_NUMBER+r"\s*(?:份(?:额)?|units?\b|shares?\b)",re.I)
_DENIAL = re.compile(
    r"(?:不按|不以|不是|并非|而不是|不要按|不用按|不用|不要|拒绝)\s*(?:原来|原本|之前|旧的)?\s*$|"
    r"\b(?:not|instead\s+of|rather\s+than|do\s+not\s+use|don['’]?t\s+use)\s*(?:(?:the\s+)?(?:old|original|previous)\s*)?$",re.I)


def mask_denied_wealth_units(text: str) -> str:
    """Equal-length, clause-local masking; the untouched text remains proof.

    '200元，不按1份了' denies only the one-share measure, not the positive
    money request. The caller still grounds money, product, currency and write
    permission against the original source; this mask cannot authorize them.
    """
    ranges=[]
    for mention in _MENTION.finditer(text):
        prefix=re.split(r"[，,。；;\n]",text[max(0,mention.start()-60):mention.start()])[-1]
        if _DENIAL.search(prefix): ranges.append(mention.span())
    value=text
    for start,end in reversed(ranges): value=value[:start]+" "*(end-start)+value[end:]
    return value
