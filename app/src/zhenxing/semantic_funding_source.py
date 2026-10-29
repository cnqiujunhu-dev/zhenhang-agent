"""Finite selected-funding polarity, not intent, ownership or bank authority.

Callers must first validate each current SET selector's source geometry and
field role. This module neither discovers selectors nor chooses another card.
Unknown roles are deliberately outside this additional refusal-only guard.
"""
import re
from types import MappingProxyType

VERSION = 'selected-funding-source-polarity-v1'
_CARD = frozenset({'card_number', 'card_last4'})
FUNDING_FIELDS = MappingProxyType({
    'TRANSFER': _CARD, 'TRANSFER_SCHEDULE': _CARD,
    'FX_TRANSFER': _CARD, 'FX_EXCHANGE': _CARD,
    'OWN_ACCOUNT_TRANSFER': _CARD, 'AA_PAY': _CARD,
    'WEALTH_BUY': _CARD,
    'SUBSCRIPTION_START': _CARD, 'SUBSCRIPTION_RENEW': _CARD,
    'MERCHANT_PURCHASE': _CARD | frozenset({'card_reference'}),
    'LIFE_PLAN_AUTHORIZE': _CARD | frozenset({'card_reference'}),
    # card_number/card_reference here identify the debt, NOT its funding.
    'CREDIT_REPAY': frozenset({'source_card'}),
    'CREDIT_SETTLE_INSTALLMENT': frozenset({'source_card'}),
})

# Closed resource modifiers, not arbitrary text between negation and selector.
# Another literal is allowed only in a coordinated list of resource selectors;
# this is lexical refusal evidence, never proof that any card exists/is owned.
_IDENTITY = r'(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}'
_ZH_RESOURCE = r'(?:本人名下|本人|我的|我|这张|该|当前|现有|已登记|人民币|美元|借记卡|储蓄卡|银行卡|信用卡|账户|账号|卡号|尾号|余额|资金|完整|的|卡)'
_EN_RESOURCE = r'(?:my|own|the|this|current|registered|debit|credit|bank|card|account|number|ending|in|balance|funds|CNY|USD)\b'
_RESOURCE = re.compile(rf'(?:\s+|{_ZH_RESOURCE}|{_EN_RESOURCE})', re.I)
_OTHER_IDENTITY = re.compile(_IDENTITY, re.I)
_COORDINATION = re.compile(r'(?:和|与|及|或|、|and\b|or\b)', re.I)
_ZH_NEG = r'(?:不要|不用|先别|暂不|请勿|切勿|别|不能|不可|不得|不准|禁止|不允许|不需要|无需)'
_ZH_USE = r'(?:使用|动用|用|从|通过|拿|以)'
_EN_NEG = r"(?:do\s+not|don't|never|must\s+not|shall\s+not|cannot|can't)"
_EN_USE = r'(?:use|pay\s+(?:from|with)|fund\s+(?:from|with)|debit|draw\s+from)'
_BEFORE = re.compile(rf'(?:不用|{_ZH_NEG}(?:再|继续)?{_ZH_USE}|\b{_EN_NEG}\s+{_EN_USE}\b)', re.I)
_AFTER = re.compile(
    rf'^(?:不用|{_ZH_NEG}(?:再|继续)?(?:使用|动用|用)|'
    rf'\s*{_EN_NEG}\s+{_EN_USE}\b|'
    rf'\s*(?:must\s+not|shall\s+not|cannot|can\s+not|is\s+not\s+to)\s+be\s+(?:used|debited)\b)', re.I)
_BREAK = re.compile(r'[，,。；;！!？?\n]')


def is_funding_field(kind, field):
    return field in FUNDING_FIELDS.get(kind, ())


def _resource_prefix(text):
    position=0
    while match:=_RESOURCE.match(text,position):position=match.end()
    return position


def _resource_gap(text):
    # Linear token consumption instead of nested repeated regex alternatives.
    position=0
    while True:
        position+=_resource_prefix(text[position:])
        if position==len(text):return True
        identity=_OTHER_IDENTITY.match(text,position)
        if identity is None:return False
        position=identity.end()
        position+=_resource_prefix(text[position:])
        connector=_COORDINATION.match(text,position)
        if connector is None:return False
        position=connector.end()


def selected_funding_is_negated(message, span):
    """Check only a caller-verified selected span, in its own local clause.

    Invalid geometry raises instead of silently treating an unverified source
    as positive. Bounded resource gaps cannot jump a recipient/free-text owner,
    a quote, a new use-operator, or the clause containing an alternative card.
    """
    if not isinstance(message, str) or not isinstance(span, dict) or set(span) != {'start', 'end'}:
        raise ValueError('INVALID_FUNDING_SOURCE_SPAN')
    start, end = span['start'], span['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(message):
        raise ValueError('INVALID_FUNDING_SOURCE_SPAN')
    # Refusal-only lexical scan: the source compiler remains responsible for
    # quote/condition/foreign-owner checks on the complete original message.
    # The upstream message itself is bounded. Do not let padding move a real
    # negation outside an arbitrary look-behind window and become affirmative.
    before = message[:start]
    boundaries = list(_BREAK.finditer(before))
    if boundaries:
        before = before[boundaries[-1].end():]
    after = _BREAK.split(message[end:], maxsplit=1)[0]
    return any(_resource_gap(before[match.end():]) for match in _BEFORE.finditer(before)) \
        or bool(_AFTER.match(after[_resource_prefix(after):]))
