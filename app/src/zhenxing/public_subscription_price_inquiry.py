"""Exact public subscription price FAQ, not account data or purchase authority.

This recognizer performs no database lookup. A caller must obtain current
registered public terms and use its normal model/typed-fact explanation path.
It deliberately excludes personal next-debit questions and mixed write goals.
"""
import re


from .subscription_catalog_data import CATALOG_DEFAULTS, MERCHANT_ALIASES
_MERCHANT = '|'.join(re.escape(name).replace(r'\ ',r'\s+') for name in
    sorted({row[0] for row in CATALOG_DEFAULTS} | set(MERCHANT_ALIASES), key=lambda name:(-len(name),name)))
_CN = re.compile(
    r"^(?:(?:请问|问一下|想知道|我想知道|我想问问|帮我查一下|帮我查|查询|查一下)\s*)?"
    r"(?P<merchant>" + _MERCHANT + r")\s*"
    r"(?:多少钱|(?:的)?(?:价格|月费|费用|月费用)(?:是多少|多少))\s*[？?。.!！]?$", re.I)
_EN = re.compile(
    r"^(?:please\s+(?:tell\s+me\s+)?|tell\s+me\s+)?"
    r"(?:how\s+much\s+is\s+(?:the\s+)?|what\s+is\s+the\s+(?:price|monthly\s+price|monthly\s+fee|cost)\s+of\s+(?:the\s+)?)"
    r"(?P<merchant>" + _MERCHANT + r")\s*[?.!]?$", re.I)


def public_subscription_price_request(message):
    """Return a literal public product selector only for one whole pure FAQ."""
    if not isinstance(message,str) or not message.strip() or len(message)>160:return None
    text=message.strip()
    matched=_CN.fullmatch(text) or _EN.fullmatch(text)
    if not matched:return None
    return {"merchant":matched['merchant'],"scope":"PUBLIC_REGISTERED_TERMS_ONLY",
        "private_account_read_requested":False,"purchase_requested":False,"write_authorized":False}
