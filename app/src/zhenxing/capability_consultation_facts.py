"""Current role-filtered service facts, never account facts or execution rights.

The semantic model selects a registered capability subject. No user-word
classifier lives here. A directory group's aggregate write flag is never an
action's classification; each action is checked against the current registry.
"""
import json

VERSION = 'public-capability-facts-v1'
SUBJECT = 'capabilities:cards'
GROUPS = ('cards', 'credit')
APPLICATIONS = {'CREDIT_APPLY', 'CREDIT_LIMIT_REQUEST'}
CARD_CONTROLS = {'CARD_LOCK', 'CARD_UNLOCK', 'CARD_REPORT_LOSS', 'CARD_REPLACEMENT',
                 'CARD_SET_LIMIT', 'CREDIT_ACTIVATE'}
FINANCIAL = {'CREDIT_REPAY', 'CREDIT_INSTALLMENT', 'CREDIT_SETTLE_INSTALLMENT'}
ENGLISH_LABELS = {
    'CARD_STATUS': 'card balances, status and transaction limits',
    'CREDIT_VIEW': 'shared credit and available credit',
    'CREDIT_STATEMENT': 'credit-card statements and existing installments',
    'CARD_LOCK': 'temporary locking', 'CARD_UNLOCK': 'unlocking',
    'CARD_REPORT_LOSS': 'loss reporting', 'CARD_REPLACEMENT': 'replacement cards',
    'CARD_SET_LIMIT': 'transaction-limit changes', 'CREDIT_ACTIVATE': 'credit-card activation',
    'CREDIT_REPAY': 'repayment', 'CREDIT_INSTALLMENT': 'purchase installments',
    'CREDIT_SETTLE_INSTALLMENT': 'early installment settlement',
    'CREDIT_APPLY': 'credit-card applications', 'CREDIT_LIMIT_REQUEST': 'credit-limit applications',
}


def is_capability_subject(subject):
    return subject == SUBJECT


def _effect(action):
    if action['read_only'] is True and action['requires_confirmation'] is False:
        return 'QUERY'
    if action['read_only'] is not False or action['requires_confirmation'] is not True:
        raise ValueError('CAPABILITY_ACTION_CONTRACT_CHANGED')
    kind = action['kind']
    if kind in APPLICATIONS:
        return 'APPLICATION_REVIEW'
    if kind in CARD_CONTROLS:
        return 'CARD_CONTROL'
    if kind in FINANCIAL:
        return 'FINANCIAL_OPERATION'
    raise ValueError('CAPABILITY_ACTION_CONTRACT_CHANGED')


def registration_source():
    """Public subject fingerprint; preserves old policy subject projections."""
    from .capability_catalog import catalog, customer_directory
    from .customer_capability_data import CUSTOMER_DIRECTORY_VERSION
    allowed = {row['kind']: row for row in catalog('CUSTOMER')['capabilities']}
    groups = [row for row in customer_directory('CUSTOMER') if row['id'] in GROUPS]
    kinds = list(dict.fromkeys(kind for row in groups for kind in row['actionKinds']))
    return {'version': VERSION, 'directory_version': CUSTOMER_DIRECTORY_VERSION,
            'groups': groups, 'actions': [{**allowed[kind], 'effect': _effect(allowed[kind]),
                'label_en': ENGLISH_LABELS.get(kind)} for kind in kinds]}


def current_capability_facts(records, language='zh-CN'):
    """Require the complete exact current directory, not source-ID presence.

    The caller separately revalidates actor epoch and source fingerprint before
    publication. Public descriptions do not prove this person's eligibility.
    """
    from .knowledge_runtime import directory_source
    from .capability_catalog import catalog
    rows = [row for row in records if row.get('kind') == 'CUSTOMER_CAPABILITY_DIRECTORY']
    if len(rows) != 1:
        raise ValueError('CAPABILITY_DIRECTORY_SOURCE_CHANGED')
    source = rows[0]
    role = source.get('role')
    if role not in {'CUSTOMER', 'DEMO_OWNER', 'DEV_TEST', 'FAMILY_HELPER'} \
            or source != directory_source(role, language):
        raise ValueError('CAPABILITY_DIRECTORY_SOURCE_CHANGED')
    groups = [row for row in json.loads(source['content']) if row['id'] in GROUPS]
    allowed = {row['kind']: row for row in catalog(role)['capabilities']}
    kinds = list(dict.fromkeys(kind for row in groups for kind in row['actionKinds']))
    facts = []
    for kind in kinds:
        action = allowed.get(kind)
        if action is None:
            raise ValueError('CAPABILITY_ACTION_CONTRACT_CHANGED')
        facts.append({'ref': 'capability:' + kind, 'kind': kind, 'label': action['label'],
            'label_en': ENGLISH_LABELS.get(kind), 'effect': _effect(action),
            'read_only': action['read_only'], 'requires_confirmation': action['requires_confirmation'],
            'source_id': source['source_id'], 'authority': 'REGISTERED_SERVICE_NOT_PERSONAL_ELIGIBILITY_OR_EXECUTION'})
    return facts, source['source_id']


def offered_capability_options(prepared):
    """Closed public selectors, intersected with this actor's actual offer."""
    from .knowledge_runtime import directory_source
    role=prepared.get('role')
    if role not in {'CUSTOMER','DEMO_OWNER','DEV_TEST','FAMILY_HELPER'}:
        return []
    language=prepared.get('reply_language','zh-CN')
    facts,_=current_capability_facts([directory_source(role,language)],language)
    offered={row['kind'] for row in prepared.get('capabilities',[])}
    return [row for row in facts if row['kind'] in offered]


def render_capability_overview(records, language='zh-CN', *, subject_ref=None,
                               question_aspects=None, rule_aspects=None, selected_public_refs=None):
    if not is_capability_subject(subject_ref):
        return None
    if not set(question_aspects or []) <= {'RULES', 'SUMMARY'} \
            or not set(rule_aspects or []) <= {'GENERAL', 'PROCESS', 'LIMITS', 'ELIGIBILITY'}:
        raise ValueError('CAPABILITY_QUESTION_SCOPE_UNSUPPORTED')
    facts, source_id = current_capability_facts(records, language)
    selected=list(selected_public_refs or [])
    if len(selected)!=len(set(selected)) or not set(selected)<={fact['ref'] for fact in facts}:
        raise ValueError('CAPABILITY_SELECTION_NOT_OFFERED')
    if selected:
        current={fact['ref']:fact for fact in facts}
        facts=[current[ref] for ref in selected]
    english = language == 'en'
    if not facts:
        text = ('Your current role has no card-management services available.' if english
                else '当前身份没有开放这些银行卡或信用卡办理服务。')
    else:
        groups = {effect: [fact for fact in facts if fact['effect'] == effect]
                  for effect in ('QUERY', 'CARD_CONTROL', 'FINANCIAL_OPERATION', 'APPLICATION_REVIEW')}
        lines = []
        for effect, rows in groups.items():
            if not rows:
                continue
            labels = [row['label_en'] if english else row['label'] for row in rows]
            if any(not label for label in labels):
                raise ValueError('CAPABILITY_ACTION_CONTRACT_CHANGED')
            names = (', ' if english else '、').join(labels)
            if english:
                suffix = {'QUERY': 'Read-only: no money movement or payment confirmation.',
                    'CARD_CONTROL': 'Each change needs your review and confirmation.',
                    'FINANCIAL_OPERATION': 'Review the amount or debt arrangement and confirm separately.',
                    'APPLICATION_REVIEW': 'Submission creates a pending review, not an approved card or limit.'}[effect]
                label = {'QUERY': 'Queries', 'CARD_CONTROL': 'Card management',
                         'FINANCIAL_OPERATION': 'Payments and debt arrangements', 'APPLICATION_REVIEW': 'Applications'}[effect]
                lines.append(label + ': ' + names + '. ' + suffix)
            else:
                suffix = {'QUERY': '只读取资料，不动资金，也不需要付款确认。',
                    'CARD_CONTROL': '变更需本人核对并确认。',
                    'FINANCIAL_OPERATION': '需分别核对金额或债务方案后确认。',
                    'APPLICATION_REVIEW': '提交后仅进入审核，不代表已发卡或额度获批。'}[effect]
                label = {'QUERY': '查询', 'CARD_CONTROL': '卡片管理',
                         'FINANCIAL_OPERATION': '资金与债务办理', 'APPLICATION_REVIEW': '申请审核'}[effect]
                lines.append(label + '：' + names + '。' + suffix)
        text = ('Supported services:\n' if english else '可以，支持这些服务：\n')+'\n'.join(lines) if selected else '\n'.join(lines)
    return text, {'capability_fact_version': VERSION, 'capability_subject': SUBJECT,
        'cited_source_ids': [source_id], 'rendered_capability_refs': [fact['ref'] for fact in facts],
        'capability_selection': 'SELECTED' if selected else 'ALL',
        'question_coverage': {'complete': True, 'covered_service_kinds': [fact['kind'] for fact in facts]},
        'authority': 'EXPLANATION_NOT_EXECUTION'}
