"""Bounded bilingual request-context signals, never a risk clearance service.

Only the bank integration supplies the authoritative parent user utterance.
No match means *no matching contextual signal*, not a safe recipient. Existing
bank labels, ownership, UNKNOWN handling and confirmation gates still apply.
"""
from __future__ import annotations

import re


RULES_VERSION = "fraud-rules-v2"
MAX_CONTEXT_CHARS = 4000  # The verified conversation-source budget; individual chat turns remain <=2000.
RULE_REFERENCES = (
    {
        "title": "公安机关发布国庆假期防范电信网络诈骗安全提示",
        "url": "https://legalinfo.moj.gov.cn/zhfxfzzx/fzzxyw/202610/t20261004_540393.html",
        "published": "2026-10-04",
        "verified": "2026-10-08",
        "supports": ["REMOTE_ACCESS_INDUCEMENT", "REFUND_ADVANCE_FEE_INDUCEMENT"],
    },
    {
        "title": "金融监管总局：关于防范新型电信网络诈骗的风险提示",
        "url": "https://app.www.gov.cn/govdata/gov/202407/25/517630/article.html",
        "published": "2024-07-25",
        "verified": "2026-10-08",
        "supports": ["REMOTE_ACCESS_INDUCEMENT", "ADVANCE_FEE_INDUCEMENT"],
    },
    {
        "title": "招商银行：防范电信网络诈骗",
        "url": "https://market.cmbchina.com/brand/2019614qiandaiziyiwangtong/index.html",
        "verified": "2026-10-08",
        "supports": ["SAFETY_ACCOUNT_INDUCEMENT", "AUTHORITY_IMPERSONATION"],
    },
    {
        "title": "招商银行：银行卡安全支付使用指引（保管密码）",
        "url": "https://market.cmbchina.com/personal/cmbcardsafepay/index.html",
        "verified": "2026-10-08",
        "supports": ["CREDENTIAL_COLLECTION_INDUCEMENT"],
        "scope": "第三方索取银行卡取款/支付密码或短信验证码；本人在专用控件输入不是向他人提供。",
    },
)

_FLAGS = re.IGNORECASE
_DENIED_PREFIX = re.compile(
    r"(?:不是|并非|不属于|不是什么|没有|不会|无需|无须|不需要|不要|拒绝|未开启|未启用|未使用|"
    r"别|禁止|不能|不准|不让|切勿|不允许|not|never|without|no|avoid|refuse|refused|"
    r"do not|don't|did not|didn't|must not|should not)\s*(?:开启|启用|进行|使用|做|打开|"
    r"enable|use|allow|share|open|start)?\s*(?:a|an|any|the)?\s*$", _FLAGS)
_FINANCIAL_CONTEXT = re.compile(
    r"账户|银行卡|银行|资金|钱|转账|汇款|支付|退款|退费|理赔|赔偿|保证金|验证码|密码|"
    r"百万保障|续费|信用卡|\b(?:bank|account|money|funds?|payment|transfer|refund|"
    r"compensation|deposit|password|pin|otp|verification\s+code|card|renewal)\b", _FLAGS)

# Short bounded relations reduce cross-topic matches and pathological regex
# cost. Negation is evaluated at each match, not over the entire utterance.
_RULES = (
    ("SAFETY_ACCOUNT_INDUCEMENT", re.compile(
        r"(?:安全账户|资金审查账户|解冻账户|\b(?:safe|secure|protected|security)\s+(?:bank\s+)?account\b)", _FLAGS), False),
    ("ADVANCE_FEE_INDUCEMENT", re.compile(
        r"(?:刷单|返利|中奖).{0,35}(?:转账|先付|保证金|手续费)|"
        r"\b(?:prize|lottery|reward|rebate)\b.{0,80}\b(?:pay|transfer|fee|deposit)\b", _FLAGS), False),
    ("AUTHORITY_IMPERSONATION", re.compile(
        r"(?:公检法|公安|法院|检察院).{0,50}(?:转账|汇款|资金审查|转钱)|"
        r"\b(?:police|prosecutor|court|law\s+enforcement)\b.{0,100}\b(?:transfer|wire|send|move)\b", _FLAGS), False),
    ("SECRECY_INDUCEMENT", re.compile(
        r"(?:不要告诉|别告诉|不能告诉|别和|不要和|保密).{0,20}(?:家人|亲友|银行|转账)|"
        r"\b(?:do\s+not\s+tell|don't\s+tell|keep\s+(?:it\s+)?(?:a\s+)?secret\s+from|"
        r"hide\s+(?:it\s+)?from)\b.{0,45}\b(?:family|parents?|friends?|bank)\b", _FLAGS), False),
    ("REMOTE_ACCESS_INDUCEMENT", re.compile(
        r"屏幕共享|共享屏幕|远程控制|远程操控|远程操作|"
        r"\b(?:screen[- ]sharing|share\s+(?:my|your|the)\s+screen|remote\s+(?:access|control))\b", _FLAGS), True),
    ("REFUND_ADVANCE_FEE_INDUCEMENT", re.compile(
        r"(?:退款|退费|理赔|赔偿).{0,50}(?:先付|先交|先缴|支付|缴纳|转账).{0,16}(?:保证金|手续费|费用|钱|元)|"
        r"(?:先付|先交|先缴|缴纳|支付).{0,16}(?:保证金|手续费|费用).{0,35}(?:退款|退费|理赔|赔偿)|"
        r"\b(?:refund|compensation|reimbursement)\b.{0,100}\b(?:pay|deposit|fee|transfer)\b|"
        r"\b(?:pay|send|transfer|require|requires|required)\b.{0,40}\b(?:fee|deposit|money)\b.{0,70}\b(?:refund|compensation)\b", _FLAGS), False),
)
_DENIED_ADVANCE_PAYMENT = re.compile(
    r"(?:不需要|不用|不必|无需|无须|不要求|不应|不要|拒绝|"
    r"(?:does|do|must|should)\s+not|doesn't|don't|not\s+required\s+to)\s*.{0,12}"
    r"(?:先付|先交|先缴|缴纳|支付|转账|pay|deposit|fee|transfer)", _FLAGS)

# Relation boundaries, not topic whitelists: a legitimate meeting does not
# erase a later request to share financial credentials. It only cannot borrow
# the banking words from an explicitly independent sentence or request.
_REMOTE_BOUNDARY = re.compile(
    r"[；;。！？!?\n]|\.(?=\s|$)|[，,]\s*(?=(?:另外|另行|独立|单独|无关|"
    r"separately\b|independently\b|unrelated\b|on\s+a\s+separate\s+note\b))", _FLAGS)
_REMOTE_DIRECTIVE = re.compile(
    r"(?:让|叫|要求|指示|催促)(?:我|你|您|本人|客户|用户)|(?:客服|对方|那人|他|她).{0,20}(?:要|请|帮|协助)|"
    r"\b(?:asked?|told|instructed?|ordered?|requires?|requiring|install|enable|allow)\b", _FLAGS)
_REMOTE_FINANCIAL_PURPOSE = re.compile(
    r"退款|退费|理赔|赔偿|保证金|资金审查|账户核验|银行核验|银行验证|验证账户|银行账户|"
    r"银行卡|信用卡|支付密码|登录密码|验证码|"
    r"\b(?:refund|compensation|reimbursement|deposit|password|pin|otp|"
    r"verification\s+code|bank\s+account|bank\s+card|credit\s+card)\b", _FLAGS)
_REMOTE_SENSITIVE = re.compile(
    r"(?:输入|填写|提供|透露|发送|告知|读取|操作|登录|登入|打开|查看|核对|展示).{0,25}"
    r"(?:银行卡|信用卡|银行账户|账户余额|钱包余额|银行密码|支付密码|登录密码|验证码|密码|余额)|"
    r"(?:银行卡|信用卡|银行账户|账户余额|钱包余额).{0,20}(?:操作|登录|登入|支付|付款|转账)|"
    r"(?:操作|执行|办理|完成)(?:银行|我的|本人)?(?:转账|汇款|支付|付款)|"
    r"(?:转账?|汇款|付款|支付)\s*(?:人民币|美元|CNY|RMB|USD)?\s*\d|"
    r"(?:给|向)[^\s，,。；;]{1,80}?(?:转账?|汇款|付款|支付)\s*(?:人民币|美元|CNY|RMB|USD)?\s*\d|"
    r"(?:转账给|转给|汇给)[^\s，,。；;]{1,80}?\s*\d|"
    r"(?:验证码|密码|(?i:OTP|PIN))\s*[:：]?\s*\d{4,}|"
    r"\b(?:enter|type|provide|reveal|send|read|share)\b.{0,35}\b(?:password|PIN|OTP|verification\s+code|bank\s+details)\b|"
    r"\b(?:open|opening|log\s+in(?:to)?|sign\s+in(?:to)?|access|view|read|check|control|operate)\b.{0,35}"
    r"\b(?:my|your|their|his|her|the)\s+(?:bank\s+)?(?:account|wallet|card|balance)\b|"
    r"\b(?:make|making|perform|complete|initiate|authorize)\s+(?:a|my|your|the)?\s*(?:bank\s+)?(?:transfer|payment)\b|"
    r"\b(?:transfer|send|wire|pay)\s+(?:CNY|RMB|USD|US\$)?\s*\d", _FLAGS)
_REMOTE_REFER_BACK = re.compile(
    r"^\s*(?:(?:然后|接着|随后|再)(?:他|她|客服|对方)?|(?:他|她|客服|对方|那人)(?:接着|随后|然后)?).{0,25}"
    r"(?:让|要求|指示|叫|输入|填写|登录|登入|打开|核验|操作)|"
    r"^\s*(?:then\s+)?(?:he|she|they|the\s+(?:caller|agent|support)).{0,35}"
    r"\b(?:asks?|asked|told|instructs?|wants?|requires?|open|enter|log|provide|type)\b", _FLAGS)
_REMOTE_INDEPENDENT_FOLLOWUP = re.compile(
    r"^\s*(?:(?:然后|随后|接着)\s*)?(?:另外|另行|独立|单独|无关|(?:我|本人)\s*(?:再|要)?\s*(?:另行|独立|单独))|"
    r"^\s*(?:then\s+)?(?:separately|independently|unrelated|on\s+a\s+separate\s+note|"
    r"I\s+(?:will\s+)?(?:separately|independently))\b", _FLAGS)
_REMOTE_ACTORS = re.compile(r"客服|对方|那人|同事|他|她|\b(?:support|agent|caller|colleague|he|she|they)\b", _FLAGS)
_REMOTE_ADDITIONAL_ACTOR_LINK = re.compile(
    r"^\s*(?:另外|另行|同时|接着|再|also\b|additionally\b|separately\b)\s*"
    r"(?:the\s+)?(?P<actor>客服|对方|那人|同事|他|她|support|agent|caller|colleague|he|she|they).{0,25}"
    r"(?:让我|要求|指示|叫我|告诉我|\b(?:asks?|asked|told|instructs?|wants?|requires?)\b)", _FLAGS)


def _actor_kind(actor: str) -> str:
    value=actor.lower()
    if value in {"客服","support","agent","caller"}:
        return "SUPPORT"
    if value in {"同事","colleague"}:
        return "COLLEAGUE"
    return "REFERENCE"


def _remote_related(message: str, start: int, end: int) -> bool:
    separators = list(_REMOTE_BOUNDARY.finditer(message))
    left = max((match.end() for match in separators if match.end() <= start), default=0)
    right = min((match.start() for match in separators if match.start() >= end), default=len(message))
    current = message[max(left,start-100):min(right,end+100)]
    if _REMOTE_SENSITIVE.search(current):
        return True
    directive = bool(_REMOTE_DIRECTIVE.search(current))
    if directive and _REMOTE_FINANCIAL_PURPOSE.search(current):
        return True
    # Only one immediately adjacent sentence can carry an explicit refer-back
    # to the remote instruction. Ordinary banking words in a different topic
    # do not establish a connection, even if they are within 100 characters.
    if directive:
        following_boundary = next((match for match in separators if match.start() == right), None)
        if following_boundary:
            following_end = next((match.start() for match in separators if match.start() >= following_boundary.end()), len(message))
            following = message[following_boundary.end():min(following_end,following_boundary.end()+100)]
            additional_link=_REMOTE_ADDITIONAL_ACTOR_LINK.search(following)
            prior_actors=list(_REMOTE_ACTORS.finditer(message[max(left,start-100):start]))
            same_actor=bool(additional_link and prior_actors and (_actor_kind(additional_link["actor"])=="REFERENCE"
                or _actor_kind(additional_link["actor"])==_actor_kind(prior_actors[-1].group())))
            ordinary_boundary=following_boundary.group() == "\n" or following_boundary.group().strip() in {"；", ";", "。", ".", "!", "！", "?", "？"}
            related=bool(same_actor or (ordinary_boundary and not _REMOTE_INDEPENDENT_FOLLOWUP.search(following)
                                       and _REMOTE_REFER_BACK.search(following)))
            if related and _REMOTE_SENSITIVE.search(following):
                return True
    return False


def _denied(message: str, start: int) -> bool:
    prefix = message[max(0, start - 40):start].rstrip()
    # Reporting what a third party requires is not the user's own denial.
    # "他说不是诈骗，让我转安全账户" still matches the later account.
    return bool(_DENIED_PREFIX.search(prefix))


_CREDENTIAL_ACTOR_CN = r"(?:对方|那人|陌生人|客服|有人|别人|他|她|电话那头的人)"
_CREDENTIAL_CN = r"(?:(?:银行卡|银行|短信|手机|支付|登录|取款|动态|一次性)\s*)?(?:验证码|校验码|动态码|密码|口令)"
_CREDENTIAL_EN = r"(?:(?:bank|payment|withdrawal|SMS|text|login|one[- ]time)\s+)?(?:OTP|PIN|verification\s+code|password|passcode)"
_CREDENTIAL_ACTOR_EN = r"\b(?:someone|somebody|they|he|she|(?:the|a)\s+(?:caller|agent|stranger)|customer\s+support|support)\b"
_COLLECTION_PATTERNS = (
    re.compile(_CREDENTIAL_ACTOR_CN+r"(?P<bridge>[^，,。；;！？!?\n]{0,22}?)(?P<verb>索要|索取|收集|收取)(?P<request>[^，,。；;！？!?\n]{0,22}?)"+_CREDENTIAL_CN,_FLAGS),
    re.compile(_CREDENTIAL_ACTOR_CN+r"(?P<bridge>[^，,。；;！？!?\n]{0,22}?)(?P<verb>(?:要|让|叫|要求|催促)(?:我|你|您))(?P<request>[^，,。；;！？!?\n]{0,22}?(?:给|提供|告诉|透露|发送|交出|念|传)[^，,。；;！？!?\n]{0,12}?)"+_CREDENTIAL_CN,_FLAGS),
    re.compile(_CREDENTIAL_ACTOR_CN+r"(?P<bridge>[^，,。；;！？!?\n]{0,22}?)(?P<verb>(?:要|让|叫|要求|催促)(?:我|你|您))(?P<request>[^，,。；;！？!?\n]{0,12}?"+_CREDENTIAL_CN+r"[^，,。；;！？!?\n]{0,12}?(?:给|告诉|透露|发送|念|交出)(?:他|她|他们|客服|对方))",_FLAGS),
    re.compile(_CREDENTIAL_ACTOR_EN+r"(?P<bridge>[^,.;!?\n]{0,30}?)(?P<verb>asked?|requests?|wants?|demands?|requires?)\s+(?P<request>(?:me\s+|you\s+)?for\s+[^,.;!?\n]{0,18}?)"+_CREDENTIAL_EN,_FLAGS),
    re.compile(_CREDENTIAL_ACTOR_EN+r"(?P<bridge>[^,.;!?\n]{0,30}?)(?P<verb>asked?|told|requests?|wants?|requires?|instructs?)\s+(?P<request>(?:me|you)\s+to\s+[^,.;!?\n]{0,12}?(?:give|provide|tell|send|reveal|read\s+out|share)[^,.;!?\n]{0,25}?)"+_CREDENTIAL_EN,_FLAGS),
)
_COLLECTION_DENIAL = re.compile(r"没有|未(?:曾)?|不(?:需要|要求|会|想|用)?|拒绝|不要|别|无需|禁止|不能|"
    r"\b(?:not|never|no|without|don't|doesn't|didn't|do\s+not|does\s+not|did\s+not)\b",_FLAGS)


def _credential_collection_requested(message: str) -> bool:
    for pattern in _COLLECTION_PATTERNS:
        for match in pattern.finditer(message):
            if _denied(message,match.start()):
                continue
            if _COLLECTION_DENIAL.search(match.group('bridge')) or _COLLECTION_DENIAL.search(match.group('request')):
                continue
            # The capture requires third-party acquisition, not general OTP
            # help, a user's request for their own bank to send a code, or
            # entering a code in their dedicated authentication control.
            return True
    return False


def risk_reason_codes(message: str) -> list[str]:
    """Return stable reason codes; never expose raw text or credentials.

    Individual chat turns remain <=2000. A server-verified continuation may
    combine owned original wording within the 4000-character source budget.
    Oversize input is rejected, never truncated into a misleading clearance.
    """
    if not isinstance(message, str) or len(message) > MAX_CONTEXT_CHARS:
        raise ValueError("RISK_CONTEXT_BOUND_EXCEEDED")
    reasons = []
    for code, pattern, needs_financial_context in _RULES:
        for match in pattern.finditer(message):
            if _denied(message, match.start()):
                continue
            if code in {"REFUND_ADVANCE_FEE_INDUCEMENT", "ADVANCE_FEE_INDUCEMENT"} and _DENIED_ADVANCE_PAYMENT.search(match.group()):
                continue
            if needs_financial_context:
                if not _remote_related(message,match.start(),match.end()):
                    continue
            reasons.append(code)
            break
    if _credential_collection_requested(message):
        reasons.append("CREDENTIAL_COLLECTION_INDUCEMENT")
    return reasons
