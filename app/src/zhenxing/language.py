"""Presentation locale and bounded English evidence, never a translator/authority.

Account/card/merchant identifiers and source spans remain the original text.
Unsupported or ambiguous wording is deliberately left unproved.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def normalize_language(value):
    return "en" if isinstance(value, str) and value.strip().lower().split(",")[0].split(";")[0].startswith("en") else "zh-CN"


def detect_reply_language(message, preferred=None):
    if preferred is not None:
        return normalize_language(preferred)
    text = re.sub(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|\b[A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_]*\b", "", message or "")
    # A username/API abbreviation in Chinese is not a request to switch locale.
    if re.search(r"[\u4e00-\u9fff]", text):
        return "en" if re.match(r"\s*(?:show|check|list|view|transfer|send|cancel|freeze|lock|how|why|please|compare|if|suppose|only)\b", text, re.I) and len(re.findall(r"\b[A-Za-z]{2,}\b", text)) >= 2 else "zh-CN"
    return "en" if re.search(r"\b(?:show|check|transfer|send|exchange|freeze|lock|cancel|spending|bills?|subscriptions?|cards?|renewals?|investments?|how|why|what|which|where|explain|help|products?|advice|hello|thanks?|yes|continue|change|amount|recipient|please|USD|CNY|RMB)\b", text, re.I) else "zh-CN"


EN_ACTION_PATTERNS = {
    "BILL_ANALYSIS": r"\b(?:bills?|statements?|spending|spent|expenses?|transactions?|cash\s*flow|income|expenditure)\b|\b(?:why\s+(?:is|was)\s+my\s+balance\s+(?:lower|less)|where\s+did\s+my\s+money\s+go)\b",
    "CARD_STATUS": r"\bcards?\b.{0,20}\b(?:status|limits?|balance|usable|working|active)\b|\b(?:show|check|list|view)\b.{0,20}\bcards?\b|\b(?:status|limits?)\s+of\s+(?:my\s+)?(?:bank\s+)?cards?\b|\b(?:show|check|list|view|pull\s+up|bring\s+up)\s+(?:me\s+)?(?:the\s+)?(?:current\s+)?(?:status|limits?)\s+(?:of|for)\s+(?:my\s+(?:bank\s+)?cards?|the\s+(?:bank\s+)?cards?\s+(?:that\s+)?belongs?\s+to\s+me)\b",
    "CARD_LOCK": r"\b(?:freeze|lock|block|temporarily\s+disable)\b.{0,35}\bcards?\b",
    "CARD_UNLOCK": r"\b(?:unfreeze|unlock|unblock)\b.{0,35}\bcards?\b",
    "CARD_REPORT_LOSS": r"\breport\b.{0,25}\bcards?\b.{0,35}\b(?:lost|stolen|loss)\b|\breport\b.{0,15}\b(?:lost|stolen)\b.{0,15}\bcards?\b",
    "CARD_REPLACEMENT": r"\b(?:replace|replacement)\b.{0,20}\bcards?\b",
    "CARD_SET_LIMIT": r"\b(?:set|change|adjust|raise|lower|reduce)\b.{0,25}\b(?:card\s+)?limit\b",
    "TRANSFER": r"\b(?:transfer|send|remit|pay)\b",
    "TRANSFER_SCHEDULE": r"\b(?:transfer|send|remit|pay)\b",
    "WEALTH_COMPARE": r"\b(?:wealth|investments?|funds?|financial\s+products?)\b",
    "WEALTH_BUY": r"\b(?:buy|purchase|subscribe\s+to|invest\s+in)\b(?:.{0,60}\b(?:fund|wealth|investment|product)\b|\s+[\"“「][^\"”」]{1,100}[\"”」])",
    "WEALTH_REDEEM": r"\b(?:redeem|sell)\b(?:.{0,60}\b(?:fund|wealth|investment|product)\b|\s+[\"“「][^\"”」]{1,100}[\"”」])",
    "SUBSCRIPTION_LIST": r"\b(?:subscriptions?|auto(?:matic)?[- ]?renewals?|recurring\s+(?:payments?|charges?))\b",
    "SUBSCRIPTION_CANCEL": r"\b(?:cancel|stop|unsubscribe\s+from|turn\s+off)\b.{0,70}\b(?:subscription|auto[- ]?renewal|recurring\s+(?:payment|charge))\b|\b(?:cancel|unsubscribe\s+from)\s+[\"“「][^\"”」]{1,100}[\"”」]",
    "FX_QUOTE": r"\b(?:exchange\s+rate|FX\s+(?:rate|quote)|currency\s+rate|quote|USD\s+equivalent|how\s+much)\b|\b(?:spending|expenses?|bills?|statements?|transactions?)\b[^.;\n]{0,35}\bin\s+USD\b",
    "FX_TRANSFER": r"\b(?:transfer|send|remit|pay)\b",
    "FX_EXCHANGE": r"\b(?:exchange|convert)\b",
    "FRAUD_ANALYZE": r"\b(?:fraud|scam|phishing|suspicious|transfer\s+risk)\b",
    "FAMILY_VIEW": r"\b(?:family\s+(?:assistance|permissions?|delegation)|delegations?)\b",
    "GOAL_VIEW": r"\b(?:savings?\s+goals?|goal\s+progress|goal\s+budget|how\s+(?:much\s+)?(?:sooner|earlier))\b",
    "CARBON_VIEW": r"\b(?:carbon\s+(?:credits?|rewards?|points?)|low[- ]carbon\s+(?:benefits?|rewards?))\b",
}
EN_NEGATION = re.compile(r"\b(?:do\s+not|don['’]?t|never|no|not|avoid|must\s+not|should\s+not|cannot|can['’]?t|without)\b", re.I)
EN_CONDITION = re.compile(r"\b(?:if|unless|when|once|after|before|until|provided|assuming|suppose|hypothetically|tomorrow|tomorow|tmrw?|tonight|today|this\s+(?:evening|afternoon|morning)|next\s*(?:week|month|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)|later|schedule|in\s+(?:an?|one|two|three|\d+)\s+(?:hours?|minutes?|days?))\b", re.I)
EN_UNMET_CONDITION = re.compile(r"\b(?:if|unless|when|once|after|before|until|provided|assuming|suppose|hypothetically)\b", re.I)
EN_SCHEDULE_DATE = re.compile(r"\btomorrow(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)?|\bnext\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)?|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?:\s*(?:AM|PM))?)?", re.I)
EN_DATE_HINT = re.compile(r"\b(?:tomorrow|tomorow|tmrw?|tonight|today|this\s+(?:evening|afternoon|morning)|next\s*(?:week|month|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)|later|schedule|in\s+(?:an?|one|two|three|\d+)\s+(?:hours?|minutes?|days?))\b|\bon\s+(?:\d|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December)\b|\b\d{1,2}/\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b", re.I)
_ADDITIONAL_TIMING = r"\b(?:next\s+(?:year|weekend)|in\s+(?:an?|one|two|three|\d+)\s+(?:weeks?|months?|years?)|(?:on|in|by)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|at\s+\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b"
EN_CONDITION = re.compile("(?:"+EN_CONDITION.pattern+")|(?:"+_ADDITIONAL_TIMING+")", re.I)
EN_DATE_HINT = re.compile("(?:"+EN_DATE_HINT.pattern+")|(?:"+_ADDITIONAL_TIMING+")", re.I)
EN_QUESTION = re.compile(r"\b(?:how|why|what|would|could|whether)\b|^\s*(?:can|may|is|are|does|do)\b", re.I)
EN_EXPLANATION = re.compile(r"\b(?:explain|describe|instructions?|tutorial|procedure|check|show|view|analyse|analyze|compare|estimate|equivalent|quote)\b", re.I)
EN_NO_WRITE = re.compile(r"\b(?:don['’]?t|do\s+not|never)\s+(?:actually\s+)?(?:execute|change|transfer|send|pay|exchange|convert|freeze|lock|cancel|do\s+anything)|\b(?:read[- ]?only|just\s+(?:explain|show|check|query|analyse|analyze)|no\s+changes?)\b", re.I)
EN_PRIVATE_READ_VETO = re.compile(r"\b(?:don['’]?t|do\s+not|never|without)\s+(?:want\s+(?:you\s+)?to\s+)?(?:read(?:ing)?|access(?:ing)?|query(?:ing)?|show(?:ing)?|check(?:ing)?|us(?:e|ing))\b.{0,30}\b(?:my|personal|private|account)\b.{0,20}\b(?:data|details|information|bills?|statements?|balance|transactions?)\b|\b(?:no|refuse)\s+access\s+(?:to\s+)?my\b.{0,20}\b(?:data|information|bills?|statements?|balance|transactions?)\b", re.I)


def polite_operational_body(trusted_intent):
    """Classify one bounded modal request, never rewrite its business evidence.

    Callers provide _intent_text, not quoted/source material. Original message,
    object names, currency/money and source offsets remain authoritative.
    The old EN_QUESTION boundary rejected even 'Can you freeze my card ...'.
    A genuine modal now permits preparing the ordinary reviewed proposal; it
    never supplies confirmation, verification or permission to execute it.
    """
    if not isinstance(trusted_intent, str) or len(trusted_intent) > 2000:
        return None
    if re.search(r'["“”‘’「」]|(?<![A-Za-z])\'|\'(?![A-Za-z])', trusted_intent):
        return None
    found = re.fullmatch(
        r"\s*(?:could|would|can)\s+you\s+(?:please\s+)?"
        r"(?P<body>(?:show|check|list|view|analyse|analyze|compare|transfer|send|remit|pay|freeze|lock|unfreeze|unlock|cancel|stop|buy|purchase|redeem|exchange|convert|set|change|adjust)\b[^\r\n]*)",
        trusted_intent, re.I)
    if not found:
        return None
    body = found["body"].strip().rstrip("。？！?!. ")
    # Could/Can is politeness only here. A subordinate question, negation or
    # conditional does not become an action merely because its prefix matches.
    if EN_NEGATION.search(body) or EN_UNMET_CONDITION.search(body) or EN_NO_WRITE.search(body) \
            or re.search(r"\b(?:how|why|what|whether|tell|explain|describe|instructions?|tutorial|procedure|example|hypothetical|only\s+consult|just\s+ask)\b", body, re.I):
        return None
    return body
EN_IMPRECISE = re.compile(r"\b(?:about|around|approximately|roughly|at\s+(?:most|least)|up\s+to|more\s+than|less\s+than|under|over|all|entire|remaining|between|max(?:imum)?|min(?:imum)?)\b|[~±]", re.I)
EN_UNSUPPORTED_TIMEZONE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}[ T])?\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z\b|[+-]\d{2}:?\d{2}\b)|"
    r"\b(?:PST|PDT|EST|EDT|CST|CDT|MST|MDT|CET|CEST|BST|London|New\s+York|my\s+(?:local\s+)?time|local\s+time)\b|"
    r"\b(?:UTC|GMT)(?!\s*\+\s*(?:0?8|08:00)(?![:\d]))(?:\s*[+-]\s*\d{1,2}(?::\d{2})?)?\b", re.I)


def english_allowed(kind, intent, read_kinds):
    pattern = EN_ACTION_PATTERNS.get(kind)
    found = re.search(pattern, intent, re.I) if pattern else None
    if not found:
        return None
    if kind in read_kinds:
        prefix = re.split(r"[;,。；]", intent[:found.start()])[-1]
        if re.search(r"\b(?:don['’]?t|do\s+not|never|not)\s+(?:want\s+(?:you\s+)?to\s+)?(?:pull|bring)\s+up\b", intent, re.I):
            return False
        if EN_PRIVATE_READ_VETO.search(intent):
            return False
        if kind != "FRAUD_ANALYZE" and re.search(r"\b(?:his|her|their|friend['’]?s|someone\s+else['’]?s|another\s+user['’]?s)\b", intent, re.I):
            return False
        possessives = list(re.finditer(r"\b([A-Za-z][A-Za-z0-9_]{2,31})['’]s\b", intent))
        if kind != "FRAUD_ANALYZE" and any(match[1].lower() not in {"card", "account", "bank", "subscription", "product", "investment"} and not (
                match[1].lower() in {"month", "year", "week"} and re.search(r"\b(?:this|last|previous|current)\s+$", intent[:match.start()], re.I)) for match in possessives):
            return False
        if EN_NEGATION.search(prefix) and re.search(r"\b(?:read|access|query|show|check|view|analyse|analyze|compare)\b", prefix, re.I):
            return False
        # "Don't show ... cards" matches from "show", so the negation-only
        # prefix does not contain the read verb. Check that immediate boundary
        # too, without treating a negated transfer as a ban on a separate read.
        if (re.search(r"\b(?:don['’]?t|do\s+not|never|not)\s+(?:want\s+(?:you\s+)?to\s+)?$", prefix, re.I)
                and re.match(r"(?:read|access|query|show|check|list|view|analyse|analyze|compare)\b", found.group(0), re.I)):
            return False
        return True
    # No executable English future-condition contract yet. Future wording and
    # questions may inform a read/consultation but can never authorize a write.
    classification = polite_operational_body(intent) or intent
    if EN_NEGATION.search(intent) or EN_UNMET_CONDITION.search(intent) or EN_QUESTION.search(classification) or EN_NO_WRITE.search(intent) or EN_UNSUPPORTED_TIMEZONE.search(intent):
        return False
    if EN_EXPLANATION.search(intent):
        return False
    if kind == "TRANSFER_SCHEDULE":
        return bool(EN_SCHEDULE_DATE.search(intent))
    if EN_CONDITION.search(intent) or (kind in {"TRANSFER", "FX_TRANSFER"} and EN_DATE_HINT.search(intent)):
        return False
    if kind == "CARD_LOCK" and re.search(EN_ACTION_PATTERNS["CARD_UNLOCK"], intent, re.I):
        return False
    # An action on somebody else's card is not an instruction on the user's.
    if kind.startswith("CARD_") and re.search(r"\b(?:his|her|their|friend['’]?s|someone\s+else['’]?s)\b", intent, re.I):
        return False
    if kind in {"TRANSFER", "FX_TRANSFER"} and re.search(r"\b(?:risk|safe|fraud|scam|hypothetical|example|draft)\b", intent, re.I):
        return False
    if kind in {"TRANSFER", "FX_TRANSFER", "TRANSFER_SCHEDULE"} and re.search(r"\bfrom\s+(?:account\s+)?(?!my\b|CNY\b|USD\b|RMB\b|yuan\b|renminbi\b|US\$)[A-Za-z][A-Za-z0-9_.-]{2,31}\b", intent, re.I):
        return False  # An unverified third-party funding account cannot default to mine.
    return True


_SMALL = {word: number for number, word in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
_NUM_WORD = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|and|point)"
_NUMBER = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|" + _NUM_WORD + r"(?:[ -]+" + _NUM_WORD + r"){0,18})"
_CURRENCY = r"(?:CNY|RMB|yuan|renminbi|USD|US\$|US\s+dollars?|U\.S\.\s+dollars?)"
_EN_MONEY = re.compile(r"(?<![A-Za-z0-9_.])(?:(?P<prefix>" + _CURRENCY + r")\s*(?P<pnumber>" + _NUMBER + r")(?:\s*(?P<punit>cents?))?|(?P<number>" + _NUMBER + r")\s*(?P<unit>" + _CURRENCY + r"))(?![A-Za-z0-9_.])", re.I)


def _word_integer(text):
    words = text.replace("-", " ").split()
    total = group = 0
    previous = None
    scale = 10**9
    for index, word in enumerate(words):
        if word == "and":
            if index == 0 or index == len(words)-1 or previous == "and":
                raise ValueError("Unbounded conjunction")
            continue
        if word in _SMALL:
            if previous in _SMALL or (previous in _TENS and _SMALL[word] >= 10):
                raise ValueError("Adjacent cardinal numbers")
            group += _SMALL[word]
        elif word in _TENS:
            if previous in _SMALL or previous in _TENS:
                raise ValueError("Adjacent tens")
            group += _TENS[word]
        elif word == "hundred":
            if previous not in _SMALL or not 1 <= group <= 9:
                raise ValueError("Invalid hundred")
            group *= 100
        elif word in {"thousand", "million"}:
            magnitude = 1000 if word == "thousand" else 1_000_000
            if not group or magnitude >= scale:
                raise ValueError("Invalid scale")
            total += group*magnitude
            group, scale = 0, magnitude
        else:
            raise ValueError("Not a cardinal")
        previous = word
    return total+group


def _english_decimal(number):
    if number[:1].isdigit():
        if not re.fullmatch(r"\d+(?:,\d{3})*(?:\.\d{1,2})?", number):
            raise ValueError("Imprecise decimal")
        return Decimal(number.replace(",", ""))
    integral, _, fraction = number.lower().partition(" point ")
    value = Decimal(_word_integer(integral.strip()))
    if fraction:
        digits = fraction.split()
        if not 1 <= len(digits) <= 2 or any(d not in _SMALL or _SMALL[d] > 9 for d in digits):
            raise ValueError("Invalid decimal cardinal")
        value += Decimal("0."+"".join(str(_SMALL[d]) for d in digits))
    return value


def english_money_evidence(text, currency, *, allow_limits=False):
    for match in _EN_MONEY.finditer(text):
        prefix, suffix = text[max(0, match.start()-35):match.start()], text[match.end():]
        token = match.group("prefix") or match.group("unit")
        code = "USD" if re.match(r"USD|US\$|U\.?S\.?\s", token, re.I) else "CNY"
        if code != currency:
            continue
        if re.search(r"(?:大概|大约|左右|上下|约|不到|剩余|余额|全部|超过|最多|至少|负)\s*$|[\d零〇一二两三四五六七八九十百千万亿]+\s*(?:美元|美金|USD|US\$|人民币|元|块)?\s*(?:到|至|-|~|～|—|–)\s*$", prefix, re.I) or re.match(r"\s*(?:人民币|美元|美金|元|块|角|分|港币|港元|欧元|日元|英镑|左右|上下|以内|以上|以下|(?:到|至|~|～|—|–)\s*[\d零一二两三四五六七八九十百千万])", suffix):
            continue
        if EN_IMPRECISE.search(prefix) or EN_IMPRECISE.match(suffix.strip()) or re.search(r"[-−+±]\s*$", prefix) or re.match(r"\s*(?:\d|[,.，]\s*\d|[-–~]\s*\d|(?:to|through|or)\s+(?=\d|"+_NUM_WORD+r"\b)|about\b|approximately\b|(?:CNY|RMB|USD|yuan|renminbi|dollars?|cents?|percent)\b)", suffix, re.I):
            continue
        # Avoid accepting the last suffix of a malformed number or a range.
        if re.search(r"\d\s*[,\-–]\s*$|\b(?:between|negative|minus|plus)\s*$", prefix, re.I):
            continue
        try:
            value = _english_decimal((match.group("pnumber") or match.group("number")).strip())
            if match.group("punit") and code != "USD":
                continue
            minor = value*(1 if match.group("punit") else 100)
            if minor == int(minor) and 0 < minor <= 10**13:
                yield match.start(), match.end(), int(minor)
        except (ValueError, InvalidOperation):
            continue


def english_payee(text):
    # Identifiers use exact original spelling, never transliterated identity.
    reference = r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[A-Za-z][A-Za-z0-9_.-]{2,31}|[\u4e00-\u9fff]{2,16}"
    match = re.search(r"\b(?:transfer|send|remit|pay)\b[^;。；\n]{0,85}?\bto\s+(?:account\s+)?(?:[\"“「](?P<quoted>[^\"”」]{1,80})[\"”」]|(?P<ref>"+reference+r"))(?![A-Za-z0-9_.-])", text, re.I)
    if not match:
        match = re.search(r"\b(?:transfer|send|remit|pay)\s+(?P<ref>"+reference+r")\s+(?=(?:CNY|RMB|USD|US\$|\d)\b)", text, re.I)
    value = (match.groupdict().get("quoted") or match.groupdict().get("ref")) if match else None
    if match and re.match(r"\s+(?:and|or|plus)\b", text[match.end():], re.I):
        return None
    if value and (value.lower() in {"me", "him", "her", "them", "someone", "anyone", "this", "that", "account", "the", "my"} or value.endswith(("_", ".", "-"))):
        return None
    return value


def english_receiving_currencies(text):
    currency = r"(?:USD|US\$|US\s+dollars?|CNY|RMB|yuan|renminbi)"
    pattern = r"\b(?:recipient|they|he|she)\s+(?:must\s+)?(?:receive|receives|gets?)\s+("+currency+r")|\breceiving\s+currency\s*(?:is|:)\s*("+currency+r")"
    if EN_UNMET_CONDITION.search(text):
        return set()
    values = set()
    for match in re.finditer(pattern, text, re.I):
        if EN_NEGATION.search(text[max(0, match.start()-20):match.end()]):
            continue
        token = match.group(1) or match.group(2)
        values.add("USD" if re.match(r"USD|US\$|US\s", token, re.I) else "CNY")
    return values


def english_spending_reference(text):
    return bool(re.search(r"\b(?:spending|expenses?|bills?|statements?|transactions?)\b[^.;\n]{0,35}\b(?:in\s+USD|USD\s+equivalent)\b|\bUSD\s+equivalent\s+of\s+(?:my\s+)?(?:spending|expenses?|bills?|statements?|transactions?)\b", text, re.I))


def english_slots(kind, text):
    slots = {}
    if kind in {"TRANSFER", "FX_TRANSFER", "TRANSFER_SCHEDULE"}:
        slots["payee_name"] = english_payee(text)
    if kind == "TRANSFER_SCHEDULE":
        date = EN_SCHEDULE_DATE.search(text)
        if date:
            slots["scheduled_at"] = date.group(0)
    if kind.startswith("CARD_") or kind.startswith("FX_"):
        number = re.search(r"\b(?:card(?:\s+number)?|using(?:\s+my\s+card)?|from\s+my\s+card)\s*[:#]?\s*((?:zhenhang|xiaozhao)[0-9a-fA-F]{8})(?![A-Za-z0-9_])", text, re.I)
        ending = re.search(r"\b(?:card\s+)?(?:ending(?:\s+in)?|last\s+four(?:\s+(?:digits|characters))?|last\s+4(?:\s+(?:digits|characters))?)\s*[:#]?\s*([0-9a-fA-F]{4})(?![A-Za-z0-9])", text, re.I)
        if number and not re.search(r"\b(?:recipient|their|his|her|friend['’]?s)\b", text[:number.start()], re.I):
            slots["card_number"] = number.group(1)
        if ending and not re.search(r"\b(?:recipient|their|his|her|friend['’]?s)\b", text[:ending.start()], re.I):
            slots["card_last4"] = ending.group(1)
    if kind == "SUBSCRIPTION_CANCEL":
        target = re.search(r"\b(?:cancel|stop|unsubscribe\s+from|turn\s+off)\s+(?:the\s+)?(?:[\"“「](?P<quoted>[^\"”」]{1,100})[\"”」]|(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_ -]{0,75}?)\s+(?:subscription|auto[- ]?renewal|recurring\s+(?:payment|charge))\b)", text, re.I)
        if target:
            name = target.group("quoted") or target.group("name")
            if not re.search(r"\b(?:all|only|any|that|after|before|when|if|which|this|my)\b", name, re.I):
                slots["merchant"] = name.strip()
    if kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        target = re.search(r"\b(?:buy|purchase|redeem|sell|invest\s+in)\s+[\"“「]([^\"”」]{1,100})[\"”」]", text, re.I)
        if target:
            slots["product_name"] = target.group(1)
    if kind.startswith("FX_"):
        currency = r"(?:USD|US\$|US\s+dollars?|CNY|RMB|yuan|renminbi)"
        code = lambda value: "USD" if re.match(r"USD|US\$|US\s", value, re.I) else "CNY"
        direction = re.search(r"(?P<src>"+currency+r")[^;。；\n]{0,40}?\b(?:to|for|into)\s*(?:"+_NUMBER+r"\s*)?(?P<dst>"+currency+r")\b", text, re.I)
        if direction:
            slots.update(source_currency=code(direction.group("src")), target_currency=code(direction.group("dst")))
        present = {c: list(english_money_evidence(text, c)) for c in ("CNY", "USD")}
        nonempty = [c for c, evidence in present.items() if evidence]
        if len(nonempty) == 1 and len({v[2] for v in present[nonempty[0]]}) == 1:
            slots.update(currency=nonempty[0], amount_minor=present[nonempty[0]][0][2])
        if kind == "FX_TRANSFER":
            slots["currency"] = "USD" if re.search(r"\bUSD\b|US\$|US\s+dollars?", text, re.I) else None
            payment = re.search(r"\b(?:paying|funded|pay|debit)\s+(?:with|in|from)\s*("+currency+r")", text, re.I)
            receiving = english_receiving_currencies(text)
            if payment:
                slots["source_currency"] = code(payment.group(1))
            if len(receiving) == 1:
                slots["target_currency"] = next(iter(receiving))
    return {key: value for key, value in slots.items() if value is not None}


EN_RECIPIENT_CONTINUATION = re.compile(r"^(?:recipient(?:\s+account)?\s*(?:is|to|:)?|change\s+(?:the\s+)?recipient\s+to|send\s+to|to)\s*(?P<recipient>(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[A-Za-z][A-Za-z0-9_.-]{2,31}|[\u4e00-\u9fff]{2,16})(?:\s*[,;]\s*(?:keep\s+)?(?:the\s+)?amount\s+unchanged)?\s*[.!]?\s*$", re.I)


def english_continuation_money(message, inherited_currency):
    match = re.fullmatch(r"(?:change\s+(?:the\s+)?amount\s+to|change\s+to|amount\s*(?:is|:|to)?|make\s+it|instead)\s+(?P<body>.+?)\s*[.!]?", message.strip(), re.I)
    if not match:
        # A standalone cardinal/explicit currency is a closed amount fragment.
        match = re.fullmatch(r"(?P<body>(?:"+_CURRENCY+r"\s*)?"+_NUMBER+r"(?:\s*"+_CURRENCY+r")?)", message.strip(), re.I)
    if not match:
        return None
    body = match.group("body")
    if EN_IMPRECISE.search(body) or EN_CONDITION.search(body) or EN_NEGATION.search(body):
        return None
    explicit = bool(re.search(_CURRENCY, body, re.I))
    normalized = body if explicit else f"{inherited_currency} {body}"
    values = {currency: list(english_money_evidence(normalized, currency)) for currency in ("CNY", "USD")}
    proven = [(currency, evidence) for currency, evidence in values.items() if evidence]
    if len(proven) != 1 or len(proven[0][1]) != 1 or proven[0][1][0][:2] != (0, len(normalized)):
        return None
    return proven[0][1][0][2], proven[0][0], proven[0][0] if explicit else None


def english_amount_fragment(message):
    return bool(re.match(r"^\s*(?:change\s+(?:the\s+)?amount\s+to|change\s+to|amount\b|make\s+it\b|instead\b|(?:about|around|at\s+most|at\s+least|all|between|under|over)\b)|^\s*(?:CNY|RMB|USD|US\$|yuan|renminbi)\b", message, re.I))


def localize_clarification(value, preferred="zh-CN"):
    """Finite guidance, not a translation that could alter a business object."""
    if normalize_language(preferred) != "en" or not isinstance(value, str) or not re.search(r"[\u4e00-\u9fff]", value):
        return value
    if "只支持人民币" in value or "只支持" in value and "裸$" in value:
        return "Supported currencies are CNY and explicit USD. A bare '$' or 'dollars' is ambiguous; specify the exact denomination."
    if "尚未开放" in value or "旧业务为人民币" in value or "旧人民币写流程" in value:
        return "This operation does not support the requested foreign-currency route. Use a supported currency and operation; an unsupported USD request will not be converted into a CNY write."
    if "收款币种" in value or "币种与用户原文不一致" in value:
        return "Specify one amount denomination and one intended receiving currency. Conflicting currencies cannot be guessed, and funding currency is a separate instruction."
    if "收款人" in value or "用户名" in value or "payee_name" in value:
        return "Provide the complete recipient account or card identifier as written. Separate the recipient from the exact amount and currency."
    if "准确金额" in value or "金额" in value:
        return "Provide one exact positive amount with an explicit supported currency. Approximate amounts, ranges and the entire balance cannot be used as an exact amount."
    if "尾号" in value or "卡片" in value or "card_last4" in value or "card_number" in value:
        return "Specify your exact card number or a unique last-four identifier. No other person's card or inferred card identifier will be used."
    if "日期" in value or "时间" in value:
        return "Specify an unambiguous supported date and time. Use YYYY-MM-DD for a numeric date; ambiguous dates will not become an immediate transfer."
    if "前置" in value or "条件" in value or "否定" in value:
        return "Clarify whether you want a supported operation now and its exact scope. A condition, denial, quotation or cancellation is not authorization to execute it."
    if "merchant" in value or "订阅" in value:
        return "Specify the exact subscription or merchant using its original recorded name. A translated name or description will not select a different service."
    if "product_name" in value or "产品" in value:
        return "Specify the exact investment product using its original recorded name. Comparison does not authorize a purchase."
    return "Please clarify the specific information or supported bank operation you want. Missing or ambiguous details will not be guessed."
