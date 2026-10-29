"""Reviewed READ resource/subject policies, not an intent or owner classifier.

These are noun-field grammars and bounded possessive modifiers. They neither
select an action nor resolve a person, account, record or banking permission.
Every registered READ has an explicit policy; private proof stays optional.
"""
from dataclasses import dataclass
import re
from types import MappingProxyType
from .subscription_catalog_data import CATALOG_DEFAULTS, MERCHANT_ALIASES


VERSION = 'typed-read-resource-subject-catalog-v2'
SELF_RECORDS = 'CURRENT_ACTOR_OWNED_RECORDS'


@dataclass(frozen=True)
class ReadResourcePolicy:
    subject_policy: str
    resource_types: tuple[str, ...] = ()
    legacy_complete_proof: bool = False


READ_RESOURCE_POLICIES = MappingProxyType({
    'CARD_STATUS': ReadResourcePolicy(SELF_RECORDS, ('BANK_INSTRUMENT',), True),
    'BILL_ANALYSIS': ReadResourcePolicy(SELF_RECORDS, ('BILL_RECORD', 'BANK_INSTRUMENT'), True),
    'WEALTH_COMPARE': ReadResourcePolicy(SELF_RECORDS, ('WEALTH_RECORD', 'ASSET_POSITION', 'RISK_PROFILE')),
    'ASSET_POSITIONS_READ': ReadResourcePolicy(SELF_RECORDS, ('ASSET_POSITION', 'WEALTH_RECORD'), True),
    'MEMBERSHIP_VIEW': ReadResourcePolicy(SELF_RECORDS, ('MEMBERSHIP_PROFILE', 'RISK_PROFILE'), True),
    'BENEFITS_VIEW': ReadResourcePolicy(SELF_RECORDS, ('BENEFIT',), True),
    'PREFERENCE_VIEW': ReadResourcePolicy(SELF_RECORDS, ('PREFERENCE',), True),
    'SUBSCRIPTION_LIST': ReadResourcePolicy(SELF_RECORDS, ('SUBSCRIPTION',), True),
    'GOAL_VIEW': ReadResourcePolicy(SELF_RECORDS, ('GOAL',), True),
    'CARBON_VIEW': ReadResourcePolicy(SELF_RECORDS, ('CARBON_POINTS',), True),
    'AA_VIEW': ReadResourcePolicy(SELF_RECORDS, ('AA_RECORD',)),
    'TRANSFER_SCHEDULE_VIEW': ReadResourcePolicy(SELF_RECORDS, ('SCHEDULE_RECORD',)),
    'CREDIT_VIEW': ReadResourcePolicy(SELF_RECORDS, ('BANK_INSTRUMENT', 'CREDIT_RECORD'), True),
    'CREDIT_STATEMENT': ReadResourcePolicy(SELF_RECORDS, ('BILL_RECORD', 'BANK_INSTRUMENT', 'CREDIT_RECORD'), True),
    'OCCASION_MEMBER_VIEW': ReadResourcePolicy(SELF_RECORDS, ('FAMILY_PROFILE',), True),
    'OCCASION_VIEW': ReadResourcePolicy(SELF_RECORDS, ('FAMILY_OCCASION',), True),
    'LIFE_PLAN_VIEW': ReadResourcePolicy(SELF_RECORDS, ('LIFE_PLAN',), True),
    'MERCHANT_ORDERS': ReadResourcePolicy(SELF_RECORDS, ('MERCHANT_ORDER',)),
    'FAMILY_VIEW': ReadResourcePolicy('DELEGATED_FAMILY_AUTHORITY'),
    'FRAUD_ANALYZE': ReadResourcePolicy('SOURCE_BOUND_CONTEXT_ANALYSIS'),
    'FX_QUOTE': ReadResourcePolicy('PUBLIC_REFERENCE_RATE'),
    'MERCHANT_CATALOG': ReadResourcePolicy('PUBLIC_REGISTERED_PRODUCT_CATALOG'),
})
PRIVATE_READ_KINDS = frozenset(kind for kind, policy in READ_RESOURCE_POLICIES.items()
    if policy.subject_policy == SELF_RECORDS)
LEGACY_PRIVATE_READ_KINDS = frozenset(kind for kind, policy in READ_RESOURCE_POLICIES.items()
    if policy.legacy_complete_proof)

# Frozen v1 field grammar for already complete proofs. Keeping this exact lane
# preserves historical decisions; new resource types never acquire its broad
# bank-resource compatibility, and missing-anchor completion is always typed.
LEGACY_CN_RESOURCE = r'(?:借记卡|储蓄卡|银行卡|信用卡|卡片|卡|账户|钱包|账单|持仓|理财持仓|资产|订阅|积分|权益)'
LEGACY_EN_RESOURCE = r'(?:(?:debit|credit|bank)\s+)?(?:cards?|accounts?|wallets?|statements?|holdings|assets|subscriptions|points|benefits)'
LEGACY_SELF_ANCHOR = re.compile(r'(?:'
    r'(?:我(?:自己|本人)?|本人)(?:现在|当前|目前)?'
    r'(?:的|名下(?:的)?|持有的)?(?:这|那)?(?:[一二两三四五六七八九十\d]+(?:张|个|项|笔)?|些)?'
    r'(?:人民币|美元|CNY|USD)?' + LEGACY_CN_RESOURCE + '|'
    r'(?:my(?:\s+own)?|I\s+(?:own|have|hold))\s+'
    r'(?:(?:current|existing)\s+)?(?:(?:two|both|all|three|[1-9]\d*)\s+)?'
    r'(?:(?:CNY|USD)\s+)?' + LEGACY_EN_RESOURCE + ')', re.I)


@dataclass(frozen=True)
class ResourceRule:
    cn: str
    en: str


_SUBSCRIPTION_CN = '(?:' + '|'.join([*(re.escape(row[0]) for row in CATALOG_DEFAULTS), '会员订阅', '订阅']) + ')'
_SUBSCRIPTION_EN = '(?:' + '|'.join([*(re.escape(alias).replace(r'\ ',r'\s+')
    for alias in sorted(MERCHANT_ALIASES,key=len,reverse=True)), 'subscriptions?', 'memberships?']) + ')'
RESOURCE_RULES = MappingProxyType({
    'SCHEDULE_RECORD': ResourceRule(r'(?:定时转账|预约转账|周期转账)(?:安排|计划|记录)?', r'(?:scheduled|recurring)\s+transfers?(?:\s+(?:plans?|records?))?'),
    'BANK_INSTRUMENT': ResourceRule(r'(?:借记卡|储蓄卡|银行卡|信用卡|卡片|卡|账户|钱包)',
        r'(?:(?:debit|credit|bank)\s+)?(?:cards?|accounts?|wallets?)'),
    'BILL_RECORD': ResourceRule(r'(?:信用卡账单|账单|交易记录|消费记录|消费明细|流水)',
        r'(?:(?:credit\s+card|bank)\s+)?(?:statements?|bills?|transactions?|spending\s+records?)'),
    'ASSET_POSITION': ResourceRule(r'(?:理财持仓|资产持仓|持仓|资产)',
        r'(?:(?:wealth|investment)\s+)?(?:holdings|assets|positions)'),
    'WEALTH_RECORD': ResourceRule(r'(?:理财产品|理财)', r'(?:wealth\s+products?|investments?)'),
    'RISK_PROFILE': ResourceRule(r'(?:风险测评|风险档案)', r'(?:risk\s+(?:assessments?|profiles?))'),
    'MEMBERSHIP_PROFILE': ResourceRule(r'(?:客户等级|会员等级|客户资料|会员资料)',
        r'(?:customer\s+(?:tiers?|profiles?)|membership\s+(?:tiers?|profiles?))'),
    'BENEFIT': ResourceRule(r'(?:客户权益|权益)', r'(?:(?:customer|membership)\s+)?benefits'),
    'PREFERENCE': ResourceRule(r'(?:出行偏好|消费偏好|偏好)', r'(?:(?:travel|spending|consumption)\s+)?preferences'),
    'SUBSCRIPTION': ResourceRule(_SUBSCRIPTION_CN, _SUBSCRIPTION_EN),
    'GOAL': ResourceRule(r'(?:资金目标|储蓄目标|旅行目标|目标)', r'(?:(?:savings?|financial|travel)\s+)?goals?'),
    'CARBON_POINTS': ResourceRule(r'(?:低碳积分|碳积分|积分)', r'(?:(?:carbon|activity)\s+)?points'),
    'AA_RECORD': ResourceRule(r'(?:AA(?:应付份额|待付份额|收付款|收款|义务|记录|份额)?)',
        r'AA\s+(?:obligations?|payments?|collections?|records?|shares?)'),
    'CREDIT_RECORD': ResourceRule(r'(?:信用卡额度|信用额度|授信额度|信用卡欠款|信用卡分期)',
        r'(?:credit\s+(?:limits?|debt|installments?))'),
    'FAMILY_PROFILE': ResourceRule(
        r'(?:家庭成员(?:显示名(?:和|与|及)关系资料|(?:显示名|关系)(?:资料|标签)?|资料|档案|记录)?|家人资料|家庭资料)',
        r'(?:family\s+(?:member\s+)?(?:profiles?|records?|directory)|family\s+members)'),
    'FAMILY_OCCASION': ResourceRule(r'(?:(?:家庭成员|家庭|家人)?生日(?:资料|记录)?)',
        r'(?:(?:family|family\s+member)\s+)?birthdays?(?:\s+records?)?'),
    'LIFE_PLAN': ResourceRule(r'(?:家庭生日计划|生日安排|生日计划|生活计划|礼物安排)',
        r'(?:(?:birthday|life|gift)\s+(?:plans?|arrangements?))'),
    'MERCHANT_ORDER': ResourceRule(r'(?:商户订单|商品订单|订单)', r'(?:(?:merchant|product)\s+)?orders?'),
})

CN_RESOURCE_MODIFIER = r'(?:当前|现在|目前|现有|已有|实际|已(?:经)?(?:购买|购|登记|保存|记录|开通)|有效)'
EN_RESOURCE_MODIFIER = r'(?:current|currently|existing|already|purchased|registered|saved|recorded|opened|active|valid)'
_CN_MODIFIERS = r'(?:(?:' + CN_RESOURCE_MODIFIER + r')(?:的)?\s*){0,4}'
_EN_MODIFIERS = r'(?:(?:' + EN_RESOURCE_MODIFIER + r')\s+){0,4}'
# Neutral nominal status does not assert the record is actually active/paid.
# It never skips an owner/person or arbitrary prose and never selects a tool.
_CN_PREFIX = (r'(?:我(?:自己|本人)?|本人)' + _CN_MODIFIERS
    + r'(?:的|名下(?:的)?|持有的|登记的|保存的|记录的)?' + _CN_MODIFIERS + r'(?:这|那)?'
    r'(?:[一二两三四五六七八九十\d]+(?:张|个|项|笔)?|些)?(?:人民币|美元|CNY|USD)?')
_EN_PREFIX = (r'(?:my(?:\s+own)?|I\s+(?:own|have|hold))\s+' + _EN_MODIFIERS
    + r'(?:(?:two|both|all|three|[1-9]\d*)\s+)?(?:(?:CNY|USD)\s+)?')
_PATTERNS = {key: re.compile(r'(?:' + _CN_PREFIX + rule.cn + '|' + _EN_PREFIX + rule.en + ')', re.I)
    for key, rule in RESOURCE_RULES.items()}
# Private record nouns also participate in the complete-owner audit. They may
# be owned records, but never become possessors of another person's account.
RECORD_CN_RESOURCE = '(?:' + '|'.join(rule.cn for rule in RESOURCE_RULES.values()) + ')'
RECORD_EN_RESOURCE = '(?:' + '|'.join(rule.en for rule in RESOURCE_RULES.values()) + ')'


@dataclass(frozen=True)
class ResourceMatch:
    start: int
    end: int
    text: str
    resource_types: tuple[str, ...]


def compatible_resource_types(kind, text):
    policy = READ_RESOURCE_POLICIES.get(kind)
    return tuple(resource for resource in policy.resource_types if _PATTERNS[resource].fullmatch(text)) if policy else ()


def self_resource_matches(message, kind=None):
    """Lexical candidates only, before caller source/polarity/owner auditing."""
    policy = READ_RESOURCE_POLICIES.get(kind) if kind else None
    allowed = set(policy.resource_types) if policy else (set(RESOURCE_RULES) if kind is None else set())
    found = {}
    for resource, grammar in _PATTERNS.items():
        if resource not in allowed: continue
        for match in grammar.finditer(message):
            found.setdefault((match.start(), match.end(), match[0]), set()).add(resource)
    # Preserve old exact candidates as well; no new missing-anchor completion
    # can use this untyped compatibility lane.
    if kind is None:
        for match in LEGACY_SELF_ANCHOR.finditer(message):
            found.setdefault((match.start(), match.end(), match[0]), set())
    keys = sorted(found, key=lambda item: (item[0], -item[1]))
    maximal = [item for item in keys if not any(left <= item[0] < item[1] <= right
        and (left, right) != item[:2] for left, right, _text in keys)]
    return [ResourceMatch(left, right, text, tuple(sorted(found[(left, right, text)])))
        for left, right, text in maximal]
