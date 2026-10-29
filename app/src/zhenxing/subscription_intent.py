"""Current-user subscription semantics, with no prices, database or execution.

Inputs must already have untrusted quoted/source spans removed by _intent_text.
Missing duration/renewal preference remains missing: server defaults belong in
the fresh confirmation, never in a claim that the user supplied those fields.
"""
import re

COMMERCE_KINDS = frozenset({"SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"})
_START = re.compile(r"开通|重新开通|恢复|重新订阅|订阅|(?:购买|买)(?=.{0,30}(?:会员|订阅|云存储))|\b(?:subscribe(?:\s+to)?|activate|reactivate|restore)\b", re.I)
_RENEW = re.compile(r"续订|续期|续费|\b(?:renew|extend)\b", re.I)
_NOUN = re.compile(r"会员|订阅|云存储|自动续费|\b(?:membership|subscription|cloud\s+storage)\b", re.I)
_QUERY = re.compile(r"查询|查看|看看|查一下|列出|显示|多少钱|价格|费用|到期|状态|扣款|\b(?:check|show|list|price|cost|status|when|how\s+much)\b", re.I)
_CONSULT = re.compile(r"怎么|如何|什么时候|何时|能否|是否|会.{0,5}(?:扣|付|收费)|(?:开通|恢复|订阅|续订|续期).{0,40}(?:要多少钱|扣多少钱|费用是多少)|可以.{0,8}吗|能不能|\b(?:how\s+(?:do|to)|can\s+I|when|would|will.*charg)\b", re.I)
_DENY = re.compile(r"不要|先别|别|不想|不需要|无需|暂不|不订了|不续了|\b(?:do\s+not|don't|never|not\s+now)\b", re.I)
_CONDITION = re.compile(r"如果|假如|假设|一旦|只有|明天|后天|下月|下个月|下周|下星期|月底|过几天|\b(?:if|unless|suppose|tomorrow|next\s+(?:month|week)|later)\b|\d{4}[-/]\d{1,2}[-/]\d{1,2}", re.I)
_ENGLISH_MONTH_COUNTS = {'a':1, 'one':1, 'two':2, 'three':3, 'four':4, 'five':5,
    'six':6, 'seven':7, 'eight':8, 'nine':9, 'ten':10, 'eleven':11, 'twelve':12}
_MONTH = re.compile(r"(?<![A-Za-z_])(?P<count>\d+|每|[一二两三四五六七八九十]+|(?:"
    + '|'.join(_ENGLISH_MONTH_COUNTS) + r")(?![A-Za-z_]))\s*(?:个)?(?:月|months?(?![A-Za-z_]))", re.I)
_YEAR = re.compile(r"(?<![A-Za-z0-9_.零〇一二两三四五六七八九十百千万亿点])(?:1|一|one|a)\s*(?:个)?(?:年|year(?![A-Za-z0-9_]))", re.I)
_UNPARSED_DURATION_UNIT = re.compile(r"(?<![A-Za-z_])(?:months?|years?|decades?|quarters?|weeks?|days?)(?![A-Za-z_])|每\s*(?:个)?(?:月|年|季度|周|天)", re.I)
_DURATION_RANGE_PREFIX = re.compile(r"(?<![A-Za-z_])(?:\d+|[一二两三四五六七八九十]+|(?:"
    + '|'.join(_ENGLISH_MONTH_COUNTS) + r")(?![A-Za-z_]))\s*(?:(?:个)?月|months?)?\s*(?:to|or|and|到|至|或|和|与|[-~～–—−/])\s*$", re.I)
_AUTO = re.compile(r"(?P<negative>不(?:要|用|想|需要)?|不要|关闭|取消|停止|禁用|别|without\s+|no\s+|disable\s+|turn\s+off\s+)?\s*(?:自动续费|自动续订|auto[- ]?renew(?:al)?)", re.I)


def subscription_duration_tokens(text):
    """Exact registered duration tokens; never choose an intent or default."""
    return sorted([*re.finditer(r'半年',text), *_YEAR.finditer(text), *_MONTH.finditer(text)],
        key=lambda match:match.start())


def duration_range_prefix(prefix):
    """A quantity followed by a connector, not an assignment ending in to."""
    return _DURATION_RANGE_PREFIX.search(prefix) is not None


def subscription_field_spans(text):
    """Literal field receipt spans; conflicting or out-of-range fields rejected."""
    rows=[]
    years = list(_YEAR.finditer(text))
    for match in years:
        prefix = text[max(0,match.start()-16):match.start()]
        suffix = text[match.end():match.end()+16]
        if re.search(r"大约|差不多|至少|最多|不超过|约|负|或|到|至|[-~～]|about|up\s+to|at\s+(?:least|most)",prefix,re.I) \
                or re.match(r"\s*(?:左右|上下|以上|以下|以内|多|少|半|或|or\b|and\s+(?:a\s+)?half\b)",suffix,re.I):return None
        rows.append(('subscription_months',12,match.start(),match.end()))
    half_year=re.search(r"半年",text)
    if half_year:rows.append(('subscription_months',6,half_year.start(),half_year.end()))
    remaining=text[:half_year.start()]+" "*(half_year.end()-half_year.start())+text[half_year.end():] if half_year else text
    for match in reversed(years):
        remaining=remaining[:match.start()]+" "*(match.end()-match.start())+remaining[match.end():]
    if re.search(r"\d+[.点]\d+\s*(?:个)?(?:月|months?)|(?:半|几|多少|若干)(?:个)?月|(?:\d+|[一二两三四五六七八九十]+)\s*(?:年|季度|周|天|years?|weeks?|days?)",remaining,re.I):return None
    for match in _MONTH.finditer(text):
        raw=match['count']
        if raw=='每':value=1  # An explicit monthly interval, not an omitted purchase default.
        elif raw.isdigit():value=int(raw)
        elif raw.casefold() in _ENGLISH_MONTH_COUNTS:value=_ENGLISH_MONTH_COUNTS[raw.casefold()]
        else:
            from .intelligence import _chinese_integer
            value=_chinese_integer(raw)
        # A range, approximation, or upper bound is not an exact purchase.
        prefix=text[max(0,match.start()-32):match.start()]
        suffix=text[match.end():match.end()+32]
        if not 1<=value<=12 or duration_range_prefix(prefix) \
                or re.search(r"大约|差不多|至少|最多|不超过|约|负|或|[-~～]|(?<![A-Za-z_])(?:about|around|approximately|roughly|between)(?![A-Za-z_])|up\s+to|at\s+(?:least|most)|(?<![A-Za-z_])(?:or|and|point|minus|negative|not|no|never)\s*$",prefix,re.I) \
                or re.match(r"\s*(?:左右|上下|以上|以下|以内|或|or\b|to\b|and\s+(?:a\s+)?half\b)",suffix,re.I):return None
        rows.append(('subscription_months',value,match.start(),match.end()))
    # A recognizable but unparsed unit is explicit unsupported input, not an
    # omitted duration eligible for a server default. Remove only the exact
    # accepted literals first; never guess an unregistered count or duration.
    duration_residual=text
    for _field,_value,start,end in sorted(rows,key=lambda row:row[2],reverse=True):
        duration_residual=duration_residual[:start]+" "*(end-start)+duration_residual[end:]
    if _UNPARSED_DURATION_UNIT.search(duration_residual):return None
    for match in _AUTO.finditer(text):
        prefix=text[max(0,match.start()-8):match.start()]
        if re.search(r"是否|能否|如果|假如|假设|\bif\b",prefix,re.I):return None
        negative=bool(match['negative'])
        # A bare automatic-renewal noun is not a switch-on instruction.
        enable_prefix=re.search(r"(?:开启|启用|打开|允许|同意|要|需要|enable|turn\s+on)\s*$",prefix,re.I)
        if negative or enable_prefix:
            left=match.start() if negative else max(0,match.start()-8)+enable_prefix.start()
            rows.append(('subscription_auto_renew',not negative,left,match.end()))
    if any(len({row[1] for row in rows if row[0]==field})>1 for field in ('subscription_months','subscription_auto_renew')):return None
    return rows


def subscription_literal_merchant(text,kind):
    if kind=='SUBSCRIPTION_LIST':
        # Legacy adapter compatibility: an already-proven generic owner
        # question is not a merchant name. Native cloud semantics selects
        # registered objects itself and never uses this question classifier.
        from .direct_read_questions import direct_owned_read_kind
        if direct_owned_read_kind(text)=='SUBSCRIPTION_LIST':return None
    """Only a literal name, not pronouns, billing parameters or quoted commands."""
    verb=_RENEW if kind=='SUBSCRIPTION_RENEW' else _START
    candidates=[]
    for match in verb.finditer(text):
        tail=text[match.end():].lstrip()
        tail=re.sub(r"^(?:一个|一项|一下|我的|这个|那个|这项|那项|the\s+|my\s+)","",tail,flags=re.I)
        quoted=re.match(r'^[“「"‘](?P<name>[^”」"’\n]{1,100})[”」"’]',tail)
        if quoted:
            name=quoted['name']
            if not re.search(r"开通|恢复|续订|续期|取消|转账|系统指令|忽略规则|[，,。；;]",name):candidates.append(name)
            continue
        # Use the same exact, registered duration spans as the purchase field
        # parser. A supported one-year literal is 12 months, not part of the
        # merchant name; unsupported/approximate durations gain no new value.
        duration_fields=subscription_field_spans(tail) or []
        duration_start=min((start for field,_value,start,_end in duration_fields
            if field=='subscription_months'),default=None)
        merchant_tail=tail[:duration_start] if duration_start is not None else tail
        name=re.split(r"[，,。；;！？!?\n]|因为|然后|并且|顺便|半年|\d+\s*(?:个)?月|[一二两三四五六七八九十]+\s*(?:个)?月|(?:不|不要|开启|启用|关闭)?自动续费|\s+(?:for|with|without)\b",merchant_tail,maxsplit=1,flags=re.I)[0].strip()
        name=re.sub(r"(?:吧|啊|呀|一下)$","",name).strip()
        name=re.sub(r"(?:的)?(?:订阅|自动续费)$","",name).strip()
        if name and not re.fullmatch(r"会员|订阅|它|这个|那个|这项|那项|this|that|it",name,re.I) and not re.search(r"[“”「」\"‘’]|系统指令|忽略规则|account_id|user_id",name,re.I):candidates.append(name)
    if not candidates:
        before=re.search(r"(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 _-]{0,60}?)(?:的)?(?:续订|续期|续费)(?=\d|[一二两三四五六七八九十]|[，,。；;\s]|$)",text)
        if before:candidates.append(before['name'].strip())
    if kind=='SUBSCRIPTION_LIST':
        candidates=[]
        if re.match(r"\s*(?:please\s+)?(?:check|show|list|view)\b",text,re.I):
            body=re.sub(r"^\s*(?:please\s+)?(?:check|show|list|view)\s+(?:(?:my|the)\s+)?","",text,flags=re.I)
            body=re.sub(r"\s+(?:subscription|status|price|cost)[.?!\s]*$","",body,flags=re.I).strip()
        elif re.search(r"[\u4e00-\u9fff]",text):
            body=re.sub(r"^(?:(?:请|帮我|我的|我|查询|查看|看看|查一下|查|列出|显示)\s*)+","",text)
            for _ in range(3):
                body=re.sub(r"(?:的)?(?:订阅|自动续费|续费|状态|价格|费用|多少钱|到期时间|扣款)(?:是多少|怎么样|如何)?[？?。.!！\s]*$","",body).strip()
        else:body=""
        generic=re.fullmatch(r"(?:(?:订阅|自动续费|会员|代扣|续费|服务|清单|列表)(?:和|与|及|、)?)+|subscriptions?|automatic\s+renewals?",body,re.I)
        if body and not generic and not re.search(r"查询|查看|怎么|如何|是否|能否|[，,。；;“”「」\"‘’]",body):candidates=[body]
    return candidates[0] if len(set(candidates))==1 else None


def subscription_request(text):
    """Mode/kind from current trusted intent; never supplies model money."""
    # Removing a local renewal prohibition must not veto an unrelated START.
    if re.fullmatch(r'\s*(?:“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’)\s*',text):return {'mode':'CONSULT','kind':None,'slots':{}}
    decision=_AUTO.sub(' ',text)
    if not (_NOUN.search(text) or _START.search(text) or _RENEW.search(text)):return None
    if re.fullmatch(r"\s*(?:开启|启用|打开|允许|同意|不要|不|不用|关闭|取消|停止|禁用|enable|disable|turn\s+(?:on|off))\s*(?:自动续费|自动续订|auto[- ]?renew(?:al)?)[。.!！\s]*",text,re.I):
        return {'mode':'FIELDS','kind':None,'slots':{}}
    if _CONSULT.search(decision) or _CONDITION.search(decision):return {'mode':'CONSULT','kind':None,'slots':{}}
    if _DENY.search(decision):return {'mode':'DECLINED','kind':None,'slots':{}}
    if re.search(r"取消|关闭|停用|停止|退订|关掉|不用了|不再续费了|不续费了|\b(?:cancel|unsubscribe)\b",decision,re.I):
        return {'mode':'CANCEL','kind':'SUBSCRIPTION_CANCEL','slots':{}}
    if _QUERY.search(decision):kind='SUBSCRIPTION_LIST'
    elif _RENEW.search(decision) and not re.fullmatch(r"\s*自动续费\s*",decision):kind='SUBSCRIPTION_RENEW'
    elif any(match.group().lower()!='订阅' or re.fullmatch(r"\s*(?:(?:请|帮我|给我|我想要|我想|我要|我需要|现在|再)\s*)*",decision[:match.start()]) for match in _START.finditer(decision)):kind='SUBSCRIPTION_START'
    else:kind='SUBSCRIPTION_LIST'
    if kind in COMMERCE_KINDS and re.search(r"免费|零元|美元|美金|港币|欧元|英镑|日元|[\d零〇一二两三四五六七八九十百千万]+(?:[.点][\d零〇一二两三四五六七八九]+)?\s*(?:元|块|分|人民币|CNY|RMB|yuan)|\$|\b(?:USD|EUR|HKD|JPY|GBP|free|dollars?)\b",text,re.I):
        return {'mode':'UNSUPPORTED_PRICE','kind':None,'slots':{}}
    slots={}
    merchant=subscription_literal_merchant(text,kind)
    if merchant:slots['merchant']=merchant
    if kind in COMMERCE_KINDS:
        fields=subscription_field_spans(text)
        if fields is None:return {'mode':'CLARIFY','kind':kind,'slots':slots}
        slots.update({field:value for field,value,_,_ in fields})
    return {'mode':'QUERY' if kind=='SUBSCRIPTION_LIST' else 'COMMERCE','kind':kind,'slots':slots}


def subscription_field_reply_safe(text):
    """Even caller-supplied field spans cannot waive whole-message source veto."""
    from .intelligence import _intent_text
    if _intent_text(text)!=text or re.fullmatch(r'\s*(?:“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’)\s*',text):return False
    request=subscription_request(text)
    return not (_CONDITION.search(text) or _CONSULT.search(text) or re.search(r"[？?]|系统指令|忽略规则",text) \
        or request and request['mode'] in {'CONSULT','DECLINED','CLARIFY','UNSUPPORTED_PRICE'})
