"""Recognize a personal financial question, never an instruction to move funds."""
import re
from .card_identifiers import CARD_NUMBER_PATTERN


CN_FINANCIAL_INQUIRY = (
    r"(?:钱|余额).{0,16}(?:变少|减少|下降|去哪|去哪里|哪里去了)|"
    r"(?:钱|余额).{0,12}(?:花在|花到|花去|怎么没|怎么少).{0,8}(?:哪里|哪儿|哪了|了)|"
    r"(?:本月|这个月|当月).{0,8}(?:花在|花到|花去|花).{0,6}(?:哪里|哪儿|哪了)|"
    r"(?:消费|支出|花费|开销).{0,16}(?:还是|或是|是不是|是否).{0,12}(?:转[账帐]|汇款)|"
    r"(?:哪些|哪里|怎么|如何|什么).{0,14}(?:能省|可省|省钱|节省|少花)|"
    r"(?:消费|支出|花费|开销).{0,12}(?:为什么|为啥|为何).{0,12}(?:多|高|增加|上涨)"
)
_QUESTION = re.compile(CN_FINANCIAL_INQUIRY)
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|`[^`]*`')
_FOREIGN = re.compile(r"(?:别人|他人|朋友|同事|房东|他的|她的|他们的|她们的).{0,10}(?:钱|余额|消费|支出)")
_SELF_TIME_OWNER = r"(?<![\u4e00-\u9fffA-Za-z0-9_])我(?:自己)?(?:本月|这个月|上月|上个月|今年|去年|本周|这周|上周|最近|近期|近来|这段时间)"
_OWN_SUFFIX = re.compile(r"(?:我的|本人的|我自己的|本账户的|当前账户的|" + _SELF_TIME_OWNER + r"的|(?:我(?:自己)?的|本人(?:的)?)(?:人民币|美元|CNY|USD)?(?:银行卡|卡片|账户|钱包)的)\s*$", re.I)
_DECLARED_WRITE_PREFIX = re.compile(
    r"^\s*(?:(?:请|现在|马上|帮我|直接|先|立即)\s*){0,3}"
    r"(?:转[账帐]给|转给|汇给|打给|(?:给|向).{1,84}(?:转|汇|打)|取消|关闭|停用|退订|申购|赎回|锁卡|解锁|挂失|补卡|预留|释放)|"
    r"^\s*(?:please\s+)?(?:transfer|send|cancel|buy|redeem|lock|unlock|freeze|exchange|convert)\b", re.I)

_PRIVATE_FINANCE = r"(?:余额|账单|帐单|消费|支出|钱(?!包))"
_SELF_OWNER = re.compile(r"(?:我|本人|我自己|本账户|当前账户|" + _SELF_TIME_OWNER + r"|(?:我(?:自己)?的|本人(?:的)?)(?:人民币|美元|CNY|USD)?(?:银行卡|卡片|账户|钱包)|(?:我(?:自己)?|本人)(?:的)?(?:最近|近期|近来|当前|现在|这段时间))\s*$", re.I)
_SELF_RESOURCE_LOCATION = re.compile(r"(?:我(?:自己)?|本人)(?:的)?(?:人民币|美元|CNY|USD)?(?:银行卡|卡片|账户|钱包)(?:里|内|中)?\s*$", re.I)
_GENERIC_RESOURCE_OWNER = re.compile(r"(?:(?:请|帮我|麻烦|查询|查看|看看|检查|解释|分析|显示|我想知道)\s*)*(?:银行卡|卡片|账户|钱包)\s*$")
_GENERIC_REFERENCED_CARD_OWNER = re.compile(r"(?:(?:请|帮我|麻烦|查询|查看|查一下|看看|检查|解释|分析|显示|我想知道)\s*)*"
    r"(?:(?:我|本人)(?:的)?\s*)?(?:尾号|末四位)\s*[0-9a-fA-F]{4}\s*(?:的)?(?:银行卡|卡片|卡)\s*$")
_QUERY_PREFIX = re.compile(r"^\s*(?:(?:请|帮我|麻烦|查询|查看|查一下|看看|看一下|检查|解释|分析|显示|我想知道)\s*)+")
_OWNER_QUERY_LEAD = re.compile(r"^\s*(?:(?:那(?:么)?|先|再|就|只|请|麻烦|帮我|分别|各自|现在|目前|以及)\s*)*")
_OWNER_QUERY_VERB = re.compile(r"^(?:(?:查询|查看|检查|解释|分析|显示|我想知道)|(?P<verb>[看查盘算理])(?:一?(?P=verb))?)(?:一下)?\s*")
_OWNER_PERIOD = r"(?:本月|这个月|上月|上个月|今年|去年|本年|本周|这周|上周|今天|昨天|最近|近期|近来|这段时间|(?:最近|近|过去)[一二两三四五六七八九十\d]+(?:个)?(?:天|周|月|季度|年)|(?:\d{4}年)?(?:\d{1,2}|[一二三四五六七八九十]|十一|十二)月(?:份)?)"
_PERIOD_OWNER = re.compile(_OWNER_PERIOD + r"\s*$")
_SELF_PERIOD_OWNER = re.compile(r"(?:我(?:自己)?|本人|本账户|当前账户)(?:的)?\s*" + _OWNER_PERIOD + r"\s*$")
# Whole card-reference + period descriptions are resource nominals, not names.
# This only avoids a lexical foreign-owner veto; actual ownership is still
# checked by the selected READ domain. Named/mixed prefixes never fullmatch.
_REFERENCED_CARD_PERIOD_OWNER = re.compile(
    r"(?:(?:我(?:自己)?|本人)(?:的)?\s*)?(?:尾号|末四位)\s*[0-9a-fA-F]{4}\s*"
    r"(?:\s*(?:和|与|及|\band\b)\s*(?:尾号|末四位)?\s*[0-9a-fA-F]{4}\s*){0,7}"
    r"(?:的)?(?:银行卡|借记卡|信用卡|卡片|卡)?\s*(?:在)?\s*" + _OWNER_PERIOD + r"\s*$")
# A complete report-grouping nominal is a resource description, not an owner.
# Keep the dimensions and report operators closed; never strip a name prefix
# or accept a matching suffix of a mixed/foreign owner phrase.
_FINANCIAL_GROUP_DIMENSION = r"(?:卡种|卡片类型|账户类型|币种|消费类别|交易类型)"
_GROUPED_RESOURCE_OWNER = re.compile(
    r"(?:(?:请|保留|展示|显示|列出|统计|汇总|比较|对比|分别)\s*)*"
    r"(?:按|按照|依照)\s*" + _FINANCIAL_GROUP_DIMENSION
    + r"(?:\s*(?:和|与|及)\s*" + _FINANCIAL_GROUP_DIMENSION + r"){0,5}\s*$")
_EN_PERIOD_OWNER = re.compile(r"\b(?:this|last|previous|current)\s+(?:month|year|week)$", re.I)
_SELF_QUANTIFIED_CARD = re.compile(r"(?:我(?:自己)?|本人)(?:的)?\s*[一二两三四五六七八九十\d]+\s*张\s*(?:银行卡|卡片|卡)(?:里|内|中)?\s*$")
_CARD_RESOURCE = r"(?:银行卡|借记卡|信用卡|卡片|卡)"
_RESOURCE_JOIN = r"\s*(?:和|与|及|\band\b)\s*"
_CARD_NUMBER = r'(?-i:' + CARD_NUMBER_PATTERN + r')'
_NEUTRAL_CARD_TIME = r'(?:当前|现在|目前|此时)'
_CARD_DESCRIPTION = (
    r"(?:(?:我(?:自己)?|本人)(?:的)?\s*)?(?:(?:这|那)\s*)?"
    r"(?:[一二两三四五六七八九十百\d]+\s*张\s*|些\s*|(?:所有|全部)(?:的)?\s*)?"
    r"(?:人民币|美元|CNY|USD)?(?:" + _NEUTRAL_CARD_TIME + r')?'
    r'(?:' + _CARD_RESOURCE + r'(?:\s*' + _CARD_NUMBER + r')?|'
    + _CARD_NUMBER + r'\s*(?:的)?' + _CARD_RESOURCE + r')'
    r"(?:里|内|中)?(?:" + _NEUTRAL_CARD_TIME + r')?')
_RESOURCE_DESCRIPTION = re.compile(_CARD_DESCRIPTION + r"(?:" + _RESOURCE_JOIN + _CARD_DESCRIPTION + r"){0,7}\s*$", re.I)
_EN_CARD_NOUN = r'(?:(?:debit|credit|bank)\s+)?cards?'
_EN_CARD_DESCRIPTION = (r'(?:(?:my|our|own)\s+)?(?:(?:current|present|existing)\s+)?'
    r'(?:(?:CNY|USD)\s+)?(?:' + _EN_CARD_NOUN + r'(?:\s+' + _CARD_NUMBER + r')?|'
    + _CARD_NUMBER + r'\s+' + _EN_CARD_NOUN + r')(?:\s+(?:now|currently|at\s+present))?')
_EN_RESOURCE_DESCRIPTION = re.compile(r'(?:(?:(?:only|just|please)\s+){0,2}(?:show|check|view|read|query)\s+)?'
    + _EN_CARD_DESCRIPTION + r'(?:\s+and\s+' + _EN_CARD_DESCRIPTION + r'){0,7}\s*$',re.I)
_CARD_REFERENCE = (r"(?:(?:我(?:自己)?|本人)(?:的)?\s*)?(?:尾号|末四位)\s*[0-9a-fA-F]{4}\s*"
    r"(?:" + _RESOURCE_JOIN + r"(?:尾号|末四位)?\s*[0-9a-fA-F]{4}\s*){0,7}"
    r"(?:的)?(?:[一二两三四五六七八九十\d]+\s*张\s*)?" + _CARD_RESOURCE)
_REFERENCED_RESOURCE = re.compile(_CARD_REFERENCE + r"(?:" + _RESOURCE_JOIN + _CARD_REFERENCE + r"){0,7}\s*$", re.I)
# A role alone does not establish an owner. Only a complete finite card
# identifier is a resource nominal; foreign/mixed owner prefixes cannot match.
# The selected bank READ still resolves this identifier under its actor.
_ROLE_IDENTIFIED_CARD_OWNER = re.compile(r"(?:(?:我(?:自己)?|本人)(?:的)?\s*)?"
    r"(?:收款卡|付款卡)\s*(?:尾号|末四位)\s*[0-9a-fA-F]{4}\s*$")


def _requested_period_owner(owner: str) -> str:
    """Separate request grammar from the complete owner/period phrase.

    Query complements and verb reduplication belong only to the query verb
    position. Never strip an arbitrary repeated name such as 张张. The caller
    still requires a whole period or an explicit self reference, so a named
    owner's period is not converted into the customer's own period.
    """
    lead = _OWNER_QUERY_LEAD.match(owner)
    candidate = owner[lead.end():]
    if _ROLE_IDENTIFIED_CARD_OWNER.fullmatch(candidate):
        return candidate
    verb = _OWNER_QUERY_VERB.match(candidate)
    if verb:
        subject = candidate[verb.end():]
    else:
        prefix = _QUERY_PREFIX.match(owner)
        subject = owner[prefix.end():] if prefix else owner
    # A disposal prefix is request grammar only before a whole already
    # supported self subject. Never erase it from names or mixed owners.
    if subject.startswith("把"):
        own_subject = subject[1:].lstrip()
        if _SELF_OWNER.fullmatch(own_subject) or _SELF_PERIOD_OWNER.fullmatch(own_subject):
            return own_subject
    return subject


def foreign_financial_request(trusted_text: str) -> bool:
    """Reject an explicit other-owner topic, never infer an owner's identity.

    Callers remove untrusted source spans first. A named recipient in another
    clause is not an owner of the user's separately requested financial read.
    This guard does not authorize family reads; those retain delegation checks.
    """
    for clause in re.split(r"[，,。；;！？?\n]", trusted_text):
        # These are descriptions of the requested resource, not an owner's
        # possessive ('能花的钱' does not mean a customer named '能花'). Explicit
        # foreign owners elsewhere in the same clause still reach the guard.
        clause = re.sub(r"(?:能花|可花|可以花|可支出|可以支出)的(?=钱)", "可支出", clause)
        if re.search(r"(?:别人|他人|朋友|同事|房东|他们|她们|他|她)(?:的)?[^，,。；;！？?\n]{0,10}" + _PRIVATE_FINANCE, clause):
            return True
        for match in re.finditer(r"(?P<owner>[\u4e00-\u9fffA-Za-z0-9_ .-]{1,80}?)的\s*(?:(?:账户|钱包)(?:的)?\s*)?(?:(?:当前|人民币|美元|CNY|USD|" + _OWNER_PERIOD + r")\s*){0,3}" + _PRIVATE_FINANCE, clause):
            owner = match["owner"].strip()
            period_owner = _requested_period_owner(owner)
            if not _SELF_OWNER.search(period_owner) and not _SELF_RESOURCE_LOCATION.search(period_owner) and not _SELF_QUANTIFIED_CARD.fullmatch(period_owner) and not _RESOURCE_DESCRIPTION.fullmatch(period_owner) and not _REFERENCED_RESOURCE.fullmatch(period_owner) and not _GENERIC_RESOURCE_OWNER.fullmatch(owner) and not _GENERIC_REFERENCED_CARD_OWNER.fullmatch(owner) and not _PERIOD_OWNER.fullmatch(period_owner) and not _SELF_PERIOD_OWNER.fullmatch(period_owner) and not _GROUPED_RESOURCE_OWNER.fullmatch(period_owner) and not _REFERENCED_CARD_PERIOD_OWNER.fullmatch(period_owner) and not _ROLE_IDENTIFIED_CARD_OWNER.fullmatch(period_owner):
                return True
        if re.search(r"\b(?:his|her|their|someone\s+else['’]?s|another\s+user['’]?s)\s+(?:(?:account|wallet)['’]?s?\s+)?(?:(?:current|present|existing|CNY|USD)\s+){0,2}(?:balance|bills?|statements?|spending|expenses?|money)\b", clause, re.I):
            return True
        for match in re.finditer(r"\b(?P<owner>[A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Za-z0-9][A-Za-z0-9_.-]*){0,3})['’]s\s+(?:(?:account|wallet)\s+)?(?:(?:this|last|previous|current)\s+(?:month|year|week)['’]s\s+)?(?:(?:current|present|existing|CNY|USD)\s+){0,2}(?:balance|bills?|statements?|spending|expenses?|money)\b", clause, re.I):
            # The whole prefix, not a suffix after John's/another owner's
            # possessive, must be a closed resource nominal. This is lexical
            # typing only: current bank ownership still belongs to the domain.
            resource_owner = clause[:match.end('owner')].strip()
            if not _EN_RESOURCE_DESCRIPTION.fullmatch(resource_owner) and not re.search(r"\b(?:my|our|own)\s+(?:bank\s+)?(?:account|wallet)$", match["owner"], re.I) and not _EN_PERIOD_OWNER.search(match["owner"]):
                return True
    return False


def personal_financial_inquiry(trusted_text: str) -> bool:
    """An omitted 'my' means the customer; examples and other owners do not."""
    text = _QUOTED.sub(" ", trusted_text)
    if _DECLARED_WRITE_PREFIX.search(text):
        return False  # A stated action with a financial reason is not a diagnosis request.
    found = _QUESTION.search(text)
    if not found or _FOREIGN.search(text):
        return False
    prefix = _QUERY_PREFIX.sub("", text[max(0, found.start() - 40):found.start()])
    if re.search(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_ .-]{1,31}的\s*$", prefix) and not _OWN_SUFFIX.search(prefix):
        return False
    return True
