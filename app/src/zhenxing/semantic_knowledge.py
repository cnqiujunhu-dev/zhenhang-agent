"""Public knowledge tools selected by cloud topics, not wording classifiers."""
import json
from .product_knowledge import ENTRIES,ENGLISH_ENTRIES,KNOWLEDGE_VERSION,KNOWLEDGE_ENTRY_VERSIONS,DOMAIN_POLICY_IDS
from .planning import canonical_hash
from .models import utcnow

TOPICS={
    'TRANSFER':['transfer.confirm-v1','transfer.unknown-v1'],
    'TRANSFERS':['transfer.confirm-v1','transfer.unknown-v1'],
    'BILL':['bills.period-v1'],'BILLS':['bills.period-v1'],'BILL_ANALYSIS':['bills.period-v1'],
    'CARD':['cards.identity-v1'],'CARDS':['cards.identity-v1'],
    'WEALTH':['wealth.risk-v1'],'INVESTMENT':['wealth.risk-v1'],
    'SUBSCRIPTION':['subscription.scope-v1'],'SUBSCRIPTIONS':['subscription.scope-v1'],
    'SUBSCRIPTION_PRICE':['subscription.scope-v1'],'MEMBERSHIP':['subscription.scope-v1'],
    'FAMILY':['family.scope-v1'],'FAMILY_PERMISSION':['family.scope-v1'],
    'GOALS':['goals.available-v1'],'CARBON':['carbon.synthetic-v1'],'LOW_CARBON':['carbon.synthetic-v1'],
    'FX':['fx.quote-v1'],'FOREX':['fx.quote-v1'],'CURRENCY':['fx.quote-v1'],
    'SECURITY':[],'FRAUD':[],
    'GENERAL':['scope.boundary-v1'],'CAPABILITIES':['scope.boundary-v1'],
}
for _topic, _aliases in {'CARDS':('CARDS','CARD'),'FAMILY':('FAMILY','FAMILY_PERMISSION'),
        'GENERAL':('GENERAL','CAPABILITIES')}.items():
    for _alias in _aliases:
        TOPICS[_alias] = [*TOPICS[_alias], *DOMAIN_POLICY_IDS[_topic]]


def registered_family_policy():
    row=next((entry for entry in ENTRIES if entry[0]=='family.scope-v1'),None)
    english=ENGLISH_ENTRIES.get('family.scope-v1')
    if not row or not english:return None
    return {'source_id':'kb:family.scope-v1','title':row[1],'title_en':english[0],
        'content':row[3],'content_en':english[2],'kind':'PRODUCT_KNOWLEDGE',
        'reference':'product_knowledge:family.scope-v1',
        'version':KNOWLEDGE_ENTRY_VERSIONS.get('family.scope-v1',KNOWLEDGE_VERSION)}


def exact_public_family_policy(records,flag):
    """A strict current single public policy, not a client/model permission."""
    current=registered_family_policy()
    return flag is True and current is not None and records==[current]


def select_public_family_policy(session,decision,records,role,reviewed_evidence_refs,locale='zh-CN'):
    """Shrink one actual model-selected public topic, never private sources."""
    fields=decision.model_dump() if hasattr(decision,'model_dump') else decision
    if not isinstance(fields,dict) or fields.get('mode')!='CONSULT' \
            or fields.get('knowledge_topics')!=['FAMILY'] or fields.get('action_kinds') \
            or fields.get('entity_refs') or fields.get('focus_ref') is not None or fields.get('changes') \
            or reviewed_evidence_refs or role not in {'CUSTOMER','DEV_TEST','DEMO_OWNER','FAMILY_HELPER'}:
        return None
    query=fields.get('retrieval_query')
    # FAMILY now also indexes bounded birthday plans. Only an explicitly
    # selected public permission subject may discard those other policies.
    # A generic FAMILY topic or a birthday subject is not a permission FAQ.
    if not isinstance(query,dict) or query.get('subject_ref')!='policy:family-permission':
        return None
    current=registered_family_policy()
    from .knowledge_runtime import directory_source
    row=next((entry for entry in ENTRIES if entry[0]=='scope.boundary-v1'),None)
    english=ENGLISH_ENTRIES.get('scope.boundary-v1')
    if current is None or not row or not english:return None
    boundary={'source_id':'kb:scope.boundary-v1','title':row[1],'title_en':english[0],
        'content':row[3],'content_en':english[2],'kind':'PRODUCT_KNOWLEDGE',
        'reference':'product_knowledge:scope.boundary-v1','version':KNOWLEDGE_VERSION}
    expected=public_semantic_sources(session,['FAMILY'],role,locale)
    if records not in ([current],expected):return None
    return [current]


def public_semantic_sources(session,topics,role,locale='zh-CN'):
    from .knowledge_runtime import directory_source
    from .subscription_commerce import subscription_catalog_rows
    ids=['scope.boundary-v1'];catalog_requested=False
    for topic in topics:
        # These are registry keys produced by the semantic decision. They are
        # not keyword tests against a user sentence and carry no write rights.
        key=topic.upper().replace('-','_').replace('.','_')
        selected=TOPICS.get(key,[])
        ids.extend(selected)
        if 'subscription.scope-v1' in selected:catalog_requested=True
    entries={row[0]:row for row in ENTRIES};records=[directory_source(role,locale)]
    for identifier in dict.fromkeys(ids):
        row=entries[identifier];english=ENGLISH_ENTRIES[identifier]
        records.append({'source_id':'kb:'+identifier,'title':row[1],'title_en':english[0],
            'content':row[3],'content_en':english[2],'kind':'PRODUCT_KNOWLEDGE',
            'reference':'product_knowledge:'+identifier,'version':KNOWLEDGE_ENTRY_VERSIONS.get(identifier,KNOWLEDGE_VERSION)})
    if any(topic.upper().replace('-','_').replace('.','_') in {'FRAUD','SECURITY'} for topic in topics):
        from copy import deepcopy
        from .contextual_safety_dialogue import FRAUD_POLICY
        records.append(deepcopy(FRAUD_POLICY))
    if any(topic.upper().replace('-','_').replace('.','_') in {'WEALTH','INVESTMENT'} for topic in topics):
        from .risk_assessment_explanation import registered_questionnaire_source
        # Keep the existing wealth policy's stable position as the final
        # wealth policy. The new guide is a separate versioned public source.
        index = next(index for index, row in enumerate(records) if row['source_id'] == 'kb:wealth.risk-v1')
        records.insert(index, registered_questionnaire_source())
    from .profile_asset_consultation import public_policies
    # Keep the pre-existing wealth policy's final position for its strict
    # versioned selector; relation sources are independently versioned rows.
    relation_index = next((index for index, row in enumerate(records) if row['source_id'] == 'kb:wealth.risk-v1'), len(records))
    records[relation_index:relation_index] = public_policies(topics)
    if catalog_requested:
        records.append({'source_id':'facts:subscription-products','title':'当前已登记会员方案',
            'kind':'SCOPED_BUSINESS_FACTS','reference':'subscription-catalog:registered-products',
            'content':json.dumps(subscription_catalog_rows(session),ensure_ascii=False,sort_keys=True),
            'observed_at':utcnow().isoformat(),'catalog_filter':None,
            'authority':'REGISTERED_PRODUCT_TERMS_NOT_PAYMENT_OR_RECURRING_AUTHORIZATION'})
    return records


def current_semantic_consultation_sources(session,principal,thread,turn,task=None):
    from .permissions import require_banking_actor
    from .language import detect_reply_language
    selector=turn.diagnostics_json.get('semantic_knowledge_selection') or {}
    from .semantic_source_bundle import SELECTOR_VERSION,load_bundle,public_records_from_bundle
    compact=selector.get('version')==SELECTOR_VERSION
    if 'version' in selector and selector['version']!=SELECTOR_VERSION:
        from .contracts import DomainError
        raise DomainError('SEMANTIC_SOURCE_CHANGED','原咨询的来源版本需要重新核对。',409)
    if compact:
        from .planning import canonical_hash
        try:offered,receipt=load_bundle(session,principal,thread,turn,selector.get('bundle_ref'))
        except ValueError:
            from .contracts import DomainError
            raise DomainError('SEMANTIC_SOURCE_CHANGED','原咨询的来源需要重新核对。',409) from None
        public=public_records_from_bundle(turn,offered)
        if [{'source_id':row['source_id'],'source_hash':canonical_hash(row)} for row in public]!=selector.get('public_wealth_record_refs',[]):
            from .contracts import DomainError
            raise DomainError('SEMANTIC_SOURCE_CHANGED','原公开资料来源需要重新核对。',409)
        selector={**selector,'public_wealth_records':public}
    records=public_semantic_sources(session,selector.get('topics',[]),require_banking_actor(session,principal),
        detect_reply_language(turn.message,turn.diagnostics_json.get('reply_language')))
    if selector.get('public_wealth_scope'):
        from .public_wealth_knowledge import current_public_wealth_scope
        records=current_public_wealth_scope(session,principal,thread,turn,selector)
    if selector.get('family_policy_scope') is True:
        from .contracts import DomainError
        decision=(turn.diagnostics_json.get('semantic_receipt') or {}).get('decision')
        selected=select_public_family_policy(session,decision,records,require_banking_actor(session,principal),
            turn.reviewed_evidence_refs_json,detect_reply_language(turn.message,turn.diagnostics_json.get('reply_language')))
        if selected is None:raise DomainError('SEMANTIC_SOURCE_CHANGED','当前家庭规则来源已变化，请重新核对。',409)
        records=selected
    if selector.get('selected_objects'):
        from .semantic_proposal import semantic_selected_object_sources_current
        from .contracts import DomainError
        if compact:
            from .semantic_proposal import selected_records_from_bundle
            try:selected=selected_records_from_bundle(session,principal,thread,turn,selector)
            except DomainError as error:
                if error.status_code!=409:raise
                raise DomainError('SEMANTIC_SOURCE_CHANGED','原查询资料已变化，请重新核对。',409) from None
            except ValueError:
                raise DomainError('SEMANTIC_SOURCE_CHANGED','原查询资料已变化，请重新核对。',409) from None
        else:selected=selector.get('selected_records',[])
        if not isinstance(selected,list) or not selected or not semantic_selected_object_sources_current(session,principal,selected):
            raise DomainError('SEMANTIC_SOURCE_CHANGED','原查询资料已变化，请重新核对。',409)
        records+=selected
    if selector.get('goal_calculation_scope'):
        from .semantic_goal_source import current_goal_calculation_source
        records.append(current_goal_calculation_source(session,principal,selector['goal_calculation_scope']))
    return records
