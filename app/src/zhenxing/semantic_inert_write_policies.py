"""Finite reviewed compatibility policies, never executable WRITE authority.

Every current WRITE is allowed or explicitly excluded. Module ownership and a
read_only=False flag cannot opt a new action into metadata projection.
"""
from dataclasses import dataclass
from types import MappingProxyType
import re


VERSION = 'reviewed-inert-action-policy-v1'
ROLES = ('CUSTOMER', 'DEV_TEST')


@dataclass(frozen=True)
class InertAnnotationPolicy:
    tool: str
    read_only: bool
    group: str
    resource_types: tuple[str, ...] = ()
    roles: tuple[str, ...] = ROLES
    polarity: str = 'AFFIRMATIVE_WRITE'
    excluded_reason: str | None = None

    @property
    def allowed(self):
        return self.excluded_reason is None


def own(tool, group, *resources, polarity='AFFIRMATIVE_WRITE'):
    return InertAnnotationPolicy(tool, False, group, tuple(resources), polarity=polarity)


def excluded(tool, reason):
    return InertAnnotationPolicy(tool, False, 'EXCLUDED', excluded_reason=reason)


WRITE_POLICIES = MappingProxyType({
    'OWN_ACCOUNT_TRANSFER': own('own.account.transfer.v1', 'OWN_FUNDS', 'BANK_INSTRUMENT'),
    'WEALTH_BUY': own('wealth.order.v1', 'OWN_FUNDS', 'BANK_INSTRUMENT', 'WEALTH_RECORD'),
    'WEALTH_REDEEM': own('wealth.order.v1', 'OWN_POSITION', 'BANK_INSTRUMENT', 'ASSET_POSITION', 'WEALTH_RECORD'),
    'FX_EXCHANGE': own('fx.exchange.v1', 'OWN_FUNDS', 'BANK_INSTRUMENT'),
    'CARD_LOCK': own('card.manage.v1', 'OWN_CARD', 'BANK_INSTRUMENT'),
    'CARD_UNLOCK': own('card.manage.v1', 'OWN_CARD', 'BANK_INSTRUMENT'),
    'CARD_REPORT_LOSS': own('card.manage.v1', 'OWN_CARD', 'BANK_INSTRUMENT'),
    'CARD_REPLACEMENT': own('card.manage.v1', 'OWN_CARD', 'BANK_INSTRUMENT'),
    'CARD_SET_LIMIT': own('card.manage.v1', 'OWN_CARD', 'BANK_INSTRUMENT'),
    'CREDIT_REPAY': own('credit.repay.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT', 'CREDIT_RECORD'),
    'CREDIT_INSTALLMENT': own('credit.installment.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT', 'BILL_RECORD', 'CREDIT_RECORD'),
    'CREDIT_SETTLE_INSTALLMENT': own('credit.settle_installment.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT', 'CREDIT_RECORD'),
    'CREDIT_APPLY': own('credit.apply.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT', 'CREDIT_RECORD'),
    'CREDIT_LIMIT_REQUEST': own('credit.limit_request.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT', 'CREDIT_RECORD'),
    'CREDIT_ACTIVATE': own('credit.activate.v1', 'OWN_CREDIT', 'BANK_INSTRUMENT'),
    'MERCHANT_PURCHASE': own('merchant.purchase.v1', 'OWN_FUNDS', 'BANK_INSTRUMENT', 'MERCHANT_ORDER'),
    'SUBSCRIPTION_START': own('subscription.start.v1', 'OWN_SUBSCRIPTION', 'BANK_INSTRUMENT', 'SUBSCRIPTION'),
    'SUBSCRIPTION_RENEW': own('subscription.renew.v1', 'OWN_SUBSCRIPTION', 'BANK_INSTRUMENT', 'SUBSCRIPTION'),
    'SUBSCRIPTION_CANCEL': own('subscription.cancel.v1', 'OWN_SUBSCRIPTION', 'SUBSCRIPTION', polarity='CANCEL_RECORD'),
    'LIFE_PLAN_AUTHORIZE': own('life.plan.authorize.v1', 'OWN_LIFE_PLAN', 'BANK_INSTRUMENT', 'LIFE_PLAN', 'FAMILY_OCCASION'),
    'LIFE_PLAN_CANCEL': own('life.plan.cancel.v1', 'OWN_LIFE_PLAN', 'LIFE_PLAN', polarity='CANCEL_RECORD'),
    'OCCASION_MEMBER_SET': own('occasion.member.set.v1', 'OWN_PROFILE', 'FAMILY_PROFILE'),
    'OCCASION_SET': own('occasion.set.v1', 'OWN_PROFILE', 'FAMILY_PROFILE', 'FAMILY_OCCASION'),
    'PREFERENCE_SET': own('preference.set.v1', 'OWN_PROFILE', 'PREFERENCE'),
    'PREFERENCE_REMOVE': own('preference.remove.v1', 'OWN_PROFILE', 'PREFERENCE', polarity='REMOVE_RECORD'),
    'TRANSFER': excluded('transfer.commit.v1', 'CROSS_SUBJECT_TRANSFER'),
    'TRANSFER_SCHEDULE': excluded('transfer.schedule.v1', 'CROSS_SUBJECT_DEFERRED_TRANSFER'),
    'FX_TRANSFER': excluded('fx.transfer.v1', 'CROSS_SUBJECT_TRANSFER'),
    'AA_PAY': excluded('transfer.commit.v1', 'CROSS_SUBJECT_AA_PAYMENT'),
    'AA_REQUEST': excluded('transfer.aa.v1', 'OTHER_ACTOR_PAYMENT_OBLIGATIONS'),
    'CONTACT_BIND': excluded('contact.bind.v1', 'CROSS_SUBJECT_RECIPIENT_BINDING'),
    'FAMILY_GRANT': excluded('family.grant.v1', 'DELEGATED_BANK_AUTHORITY'),
    'FAMILY_REVOKE': excluded('family.revoke.v1', 'DELEGATED_BANK_AUTHORITY'),
    'FAMILY_PROPOSE': excluded('family.propose.v1', 'DELEGATED_ACTOR_PROPOSAL'),
    'GOAL_CREATE': excluded('goal.create.v1', 'DEFERRED_SCOPE_NOT_REVIEWED'),
    'GOAL_RESERVE': excluded('goal.reserve.v1', 'DEFERRED_SCOPE_NOT_REVIEWED'),
    'GOAL_RELEASE': excluded('goal.release.v1', 'DEFERRED_SCOPE_NOT_REVIEWED'),
    'CARBON_CLAIM': excluded('carbon.claim.v1', 'DEFERRED_SCOPE_NOT_REVIEWED'),
    'CARBON_REDEEM': excluded('carbon.redeem.v1', 'DEFERRED_SCOPE_NOT_REVIEWED'),
})
PUBLIC_EXCEPTIONS = MappingProxyType({
    'FX_QUOTE': InertAnnotationPolicy('fx.quote.v1', True, 'PUBLIC_RATE_NOT_WALLET_READ', ('BANK_INSTRUMENT',)),
})
POLICIES = MappingProxyType({**WRITE_POLICIES, **PUBLIC_EXCEPTIONS})

# These finite operators validate polarity only after the model and reviewed
# registry have selected the action. They never select an action, supply a slot
# or authorize a write. Unknown wording keeps the original strict rejection.
AFFIRMATIVE_OPERATORS = MappingProxyType({
    'OWN_ACCOUNT_TRANSFER': r'划转|转存|转入|转出|转账|\btransfer\b',
    'WEALTH_BUY': r'买入|申购|购买|\b(?:buy|purchase|invest)\b',
    'WEALTH_REDEEM': r'赎回|\bredeem\b',
    'FX_EXCHANGE': r'兑换|换汇|换成|\b(?:exchange|convert)\b',
    'CARD_LOCK': r'锁定|锁卡|冻结|\b(?:lock|freeze)\b',
    'CARD_UNLOCK': r'解锁|解除|解冻|\b(?:unlock|unfreeze)\b',
    'CARD_REPORT_LOSS': r'挂失|\breport\s+(?:the\s+)?(?:loss|lost)\b',
    'CARD_REPLACEMENT': r'补卡|补发|\b(?:replace|replacement)\b',
    'CARD_SET_LIMIT': r'调整|设置|设为|改为|\b(?:set|adjust|change)\b',
    'CREDIT_REPAY': r'偿还|还款|归还|\b(?:repay|pay)\b',
    'CREDIT_INSTALLMENT': r'办理|分成|转换|\b(?:install|convert)\b',
    'CREDIT_SETTLE_INSTALLMENT': r'结清|\b(?:settle|prepay)\b',
    'CREDIT_APPLY': r'申请|\bapply\b',
    'CREDIT_LIMIT_REQUEST': r'申请|调整|调额|提高|降低|\b(?:request|adjust|increase|decrease)\b',
    'CREDIT_ACTIVATE': r'激活|\bactivate\b',
    'MERCHANT_PURCHASE': r'购买|买入|订购|下单|\b(?:buy|purchase|order)\b',
    'SUBSCRIPTION_START': r'开通|恢复|\b(?:subscribe|start|activate|resume)\b',
    'SUBSCRIPTION_RENEW': r'续费|续订|续期|\brenew\b',
    'LIFE_PLAN_AUTHORIZE': r'授权|安排|新建|修改|\b(?:authorize|arrange|create|amend)\b',
    'OCCASION_MEMBER_SET': r'新增|保存|更新|修改|设置|\b(?:add|save|update|set|rename)\b',
    'OCCASION_SET': r'保存|登记|记录|更新|修改|设置|\b(?:save|record|update|set)\b',
    'PREFERENCE_SET': r'保存|记住|更新|修改|设置|\b(?:save|remember|update|set)\b',
})


def matching_policy(kind, capability, role):
    policy = POLICIES.get(kind)
    return policy if policy and policy.allowed and role in policy.roles \
        and capability.get('kind') == kind and capability.get('tool') == policy.tool \
        and capability.get('read_only') is policy.read_only else None


# Source-polarity operators, not request templates or action selection. An
# unresolved negative remains on the original strict path. Positive cancellation
# is allowed only directly bound to the selected own record/selector; a cancel
# of another operation or an unproved clause never becomes authorization.
_NEGATIVE = re.compile(r'不|未(?!来)|没|别|勿|禁止|暂缓|\b(?:not|no|never|cannot|without|don[’\x27]?t)\b', re.I)
_CONDITIONAL = re.compile(r'如果|假如|假设|只有|一旦|若|倘|万一|\b(?:if|unless|suppose|hypothetical)\b', re.I)
_CANCEL = re.compile(r'取消|撤销|停止|关闭|禁用|终止|\b(?:cancel|stop|disable|revoke|end|turn\s+off)\b', re.I)
_REMOVE = re.compile(r'撤回|删除|移除|清除|取消|\b(?:remove|delete|clear|withdraw|forget)\b', re.I)
_CLAUSE_BOUNDARY = re.compile(r'[，,。；;、\n]|但是|但|不过|而是|并且|\bbut\b', re.I)
_OTHER_OBJECT = re.compile(r'其他|其它|其余|别的|另外的|\b(?:other|another|remaining)\b', re.I)
_CURRENT_OBJECT = re.compile(r'这(?:笔|张|个|项|份)|该(?:笔|张|项|卡|分期|计划|订阅)|本次|刚才|上述|'
    r'\b(?:this|that|it|the\s+same|above)\b', re.I)
_GENERIC_CONTROL = re.compile(r'动|办理|执行|操作|继续|这样做|\b(?:do\s+(?:it|that|this)|execute|proceed|touch)\b', re.I)
_OTHER_OPERATIONS = re.compile('|'.join(['(?:' + value + ')' for value in AFFIRMATIVE_OPERATORS.values()]
    + [_CANCEL.pattern, _REMOVE.pattern, r'退款|续买|退费|\brefund\b']), re.I)
# Negative-looking nominal fields are not denied commands. Keep the exact
# offsets and only mask finite debt/order state nouns, never an action clause.
_NOMINAL_STATE = re.compile(r'(?:尚)?未还(?:本金|费用|利息|余额)|'
    r'(?:尚)?未(?:付款|支付|履约|执行)(?:的)?(?:订单|计划|安排|事项|部分|余额|任务)')
_RETAIN_PAID_BENEFIT = re.compile(r'^\s*保留(?:刚|已)付费(?P<descriptive_de>的)(?:有效)?权益(?=和|与|及|、|\s|$)')
_RECORD_CONNECTORS = re.compile(r'(?:\s|\x00|的|把|将|对|该|这份|这项|当前|后续|未来|自动|续费|续订|扣款|安排|计划|'
    r'\b(?:the|for|of|on|my|automatic|auto|renewal|renewals|future|arrangement|arrangements)\b)*', re.I)


def _clauses(text):
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text):
        if start < boundary.start(): yield start, boundary.start(), text[start:boundary.start()]
        start = boundary.end()
    if start < len(text): yield start, len(text), text[start:]


def annotation_owner_audit_view(message, policy):
    """Classify one finite descriptive particle, without asserting an owner.

    Only an independent 'retain + just/already paid + benefit' clause on the
    reviewed cancellation lane may mask its descriptive 的. Names, people,
    banking resources and every other character stay in the complete audit.
    The actual semantic source, selectors and domain compiler never use this
    equal-length metadata-only view.
    """
    if policy is not WRITE_POLICIES['SUBSCRIPTION_CANCEL']: return message
    from .semantic_read_subject import _trusted_owner_message
    chars = list(message)
    for start, _end, body in _clauses(_trusted_owner_message(message)):
        found = _RETAIN_PAID_BENEFIT.match(body)
        if found:
            chars[start+found.start('descriptive_de')] = ' '
    return ''.join(chars)


def _nominal_mask(text):
    return _NOMINAL_STATE.sub(lambda found: ' ' * len(found[0]), text)


def _same_sentence(text, left, right):
    return re.search(r'[。；;\n]', text[min(left,right):max(left,right)]) is None


def _selected_clause(left, right, anchor, changes):
    return left < anchor.source_span.end and right > anchor.source_span.start or any(
        left < row.source_span.end and right > row.source_span.start for row in changes)


def _related_negative_clause(body, selected, operator):
    if selected or _CURRENT_OBJECT.search(body): return True
    if _OTHER_OBJECT.search(body): return False
    if operator.search(body) or _GENERIC_CONTROL.search(body): return True
    # Explicitly declining a different operation is not a denial of this one.
    # An unresolved/elliptical prohibition remains fail-closed.
    return _OTHER_OPERATIONS.search(body) is None


def _operation_polarity(trusted, operation, operator, anchor, changes, policy):
    for left, right, original in _clauses(trusted):
        body = _nominal_mask(original)
        selected = _selected_clause(left,right,anchor,changes)
        operation_clause = left <= operation.start() < operation.end() <= right
        if _CONDITIONAL.search(body):
            # A preceding condition in this sentence governs the requested
            # operation. A later condition naming another operation/object does
            # not cancel a separately affirmed action; unresolved terms do.
            clearly_other_operation = not selected and not operator.search(body) and _OTHER_OBJECT.search(body) and _OTHER_OPERATIONS.search(body)
            if operation_clause or right <= operation.start() and _same_sentence(trusted,right,operation.start()) and not clearly_other_operation \
                    or _related_negative_clause(body,selected,operator):
                return False
            if not _OTHER_OPERATIONS.search(body): return False
        if _NEGATIVE.search(body) and (operation_clause or _related_negative_clause(body,selected,operator)):
            return False
        if policy.polarity == 'AFFIRMATIVE_WRITE' and (_CANCEL.search(body) or _REMOVE.search(body)) \
                and (operation_clause or _related_negative_clause(body,selected,operator)):
            return False
    return True


def _record_operation_bound(trusted, operation, anchor, changes):
    left, right = anchor.source_span.start, anchor.source_span.end
    if operation.end() <= left: start, end = operation.end(), left
    elif operation.start() >= right: start, end = right, operation.start()
    else: return False
    gap = list(trusted[start:end])
    for change in changes:
        span = change.source_span
        if start <= span.start < span.end <= end:
            gap[span.start-start:span.end-start] = ['\x00'] * (span.end-span.start)
    return _RECORD_CONNECTORS.fullmatch(''.join(gap)) is not None


def affirmative_projection_source(message, policy, anchor, changes, action_kind):
    from .semantic_read_subject import _trusted_owner_message
    trusted = _trusted_owner_message(message)
    if policy.read_only:
        return action_kind in PUBLIC_EXCEPTIONS and not (_NEGATIVE.search(trusted) or _CONDITIONAL.search(trusted))
    if policy.polarity == 'AFFIRMATIVE_WRITE':
        pattern = AFFIRMATIVE_OPERATORS.get(action_kind)
        if not pattern: return False
        operator = re.compile(pattern,re.I)
    else:
        operator = {'CANCEL_RECORD': _CANCEL, 'REMOVE_RECORD': _REMOVE}.get(policy.polarity)
        if operator is None: return False
    for operation in operator.finditer(trusted):
        if anchor.source_span.start <= operation.start() < operation.end() <= anchor.source_span.end: continue
        if policy.polarity != 'AFFIRMATIVE_WRITE' and not _record_operation_bound(trusted,operation,anchor,changes): continue
        if not _same_sentence(trusted,operation.start(),anchor.source_span.start): continue
        if _operation_polarity(trusted,operation,operator,anchor,changes,policy): return True
    return False
