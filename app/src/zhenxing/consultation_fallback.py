"""Bounded public-policy fallback, selected by verified semantic scope only.

No user-word matching, arbitrary RAG excerpts, model retry or bank authority.
Caller must still revalidate the actor and all original sources before publish.
"""
from copy import deepcopy
import json
import re

_TOPIC_POLICY = {'TRANSFER':'transfer.confirm-v1','BILLS':'bills.period-v1','CARDS':'cards.identity-v1',
    'WEALTH':'wealth.risk-v1','SUBSCRIPTIONS':'subscription.scope-v1',
    'CARBON':'carbon.synthetic-v1','FX':'fx.quote-v1','GENERAL':'scope.boundary-v1'}
_CREDIT = {'LIMITS':'credit.shared-limit-v1','GENERAL':'credit.shared-limit-v1','RISK':'credit.shared-limit-v1',
    'SETTLEMENT':'credit.repayment-v1','PROCESS':'credit.application-v1','ELIGIBILITY':'credit.application-v1'}


def _policy(identifier):
    if identifier=='wealth.assessment-guide-v1':
        from .risk_assessment_explanation import registered_questionnaire_source
        return registered_questionnaire_source()
    if identifier=='security.credentials-channel-v1':
        from .legacy_consultation_facts import credential_channel_source
        return credential_channel_source()
    if identifier=='fraud.case-continuity-v1':
        from .contextual_safety_dialogue import FRAUD_POLICY
        return deepcopy(FRAUD_POLICY)
    from .product_knowledge import ENTRIES,ENGLISH_ENTRIES,KNOWLEDGE_VERSION,KNOWLEDGE_ENTRY_VERSIONS
    row=next((entry for entry in ENTRIES if entry[0]==identifier),None)
    english=ENGLISH_ENTRIES.get(identifier)
    if row is None or english is None:return None
    return {'source_id':'kb:'+identifier,'title':row[1],'title_en':english[0],'content':row[3],
        'content_en':english[2],'kind':'PRODUCT_KNOWLEDGE','reference':'product_knowledge:'+identifier,
        'version':KNOWLEDGE_ENTRY_VERSIONS.get(identifier,KNOWLEDGE_VERSION)}


def verified_legacy_family_kwargs(records,family_policy_only):
    """Forward an existing server-verified scope; never derive one from IDs."""
    if family_policy_only is True and records==[_policy('family.scope-v1')]:
        return {'public_subject_ref':'policy:family-permission','question_aspects':['RULES'],'rule_aspects':['LIMITS']}
    return {}


def _typed(records,language):
    from .public_wealth_knowledge import public_source_fallback
    text=public_source_fallback(records,language)
    if text is not None:return text,[]
    from .contextual_safety_dialogue import SOURCE_ID
    from .contextual_safety_presentation import render_contextual_safety_fallback
    cases=[row for row in records if row.get('source_id')==SOURCE_ID]
    if len(cases)==1:
        try:topic=json.loads(cases[0]['content']).get('topic')
        except (ValueError,TypeError,KeyError):topic=None
        identifier={'FRAUD':'kb:fraud.case-continuity-v1','FAMILY':'kb:family.scope-v1'}.get(topic)
        policies=[row for row in records if identifier and row.get('source_id')==identifier]
        if len(policies)==1:
            text=render_contextual_safety_fallback([policies[0],cases[0]],language)
            if text is not None:return text,[]
    from .dialogue_reference_facts import selected_object_fallback
    text=selected_object_fallback(records,language)
    if text is not None:return text,[]
    from .product_knowledge import monetary_fact_catalog,render_monetary_facts
    from .goal_explanation_facts import FACT_ID
    facts=monetary_fact_catalog(records)
    if FACT_ID in facts:return render_monetary_facts(facts,[FACT_ID],reply_language=language),[FACT_ID]
    # Legacy authorized account/bill sources also have a closed typed MINOR
    # contract. Rejecting free model prose must not erase those exact values.
    # This selects no topic, amount or source from the model's rejected text.
    refs=[key for key,fact in facts.items() if fact.get('unit')=='MINOR'
        and fact.get('source_id') in {'facts:cash','facts:currencies','facts:bill-diagnostics'}]
    from .legacy_consultation_facts import registered_public_price_refs,registered_state_fallback
    refs.extend(registered_public_price_refs(records,facts))
    states=registered_state_fallback(records,language)
    text=render_monetary_facts(facts,refs,reply_language=language) if refs else None
    if text or states:return '\n\n'.join(part for part in (text,states) if part),refs
    return None


def bounded_fallback(records,language,*,public_subject_ref=None,question_aspects=None,rule_aspects=None,include_typed=True,selected_public_refs=None):
    """Use an explicit registered subject, not the presence of source IDs.

    Current record equality includes language, version, complete body and ref.
    RAG chunks may support a successful model answer, but never become a raw
    fallback. Sentence recipes select complete registered policy sentences;
    an oversize/missing-language paragraph is declined, never hard-truncated.
    """
    from .risk_assessment_explanation import questionnaire_fallback
    questionnaire = questionnaire_fallback(records, language, subject_ref=public_subject_ref,
        question_aspects=question_aspects, rule_aspects=rule_aspects)
    if questionnaire is not None:
        return questionnaire
    missing=('This explanation has not been verified yet. Please try again later.'
        if language=='en' else '这条解释暂时没有核实好，请稍后再试。')
    from .capability_consultation_facts import is_capability_subject, render_capability_overview
    if is_capability_subject(public_subject_ref):
        try:
            text, facts=render_capability_overview(records,language,subject_ref=public_subject_ref,
                question_aspects=question_aspects,rule_aspects=rule_aspects,selected_public_refs=selected_public_refs)
            return text,{**facts,'fallback_scope':'REGISTERED_CAPABILITY_DIRECTORY'}
        except ValueError:
            return missing,{'fallback_scope':'NO_VERIFIED_CAPABILITY_DIRECTORY','question_coverage':{'complete':False}}
    from .merchant_consultation_facts import partial_terms_fallback
    partial = partial_terms_fallback(records,language,subject_ref=public_subject_ref,
        question_aspects=question_aspects,rule_aspects=rule_aspects,
        public_rule_renderer=lambda supported_rules:bounded_fallback(records,language,public_subject_ref=public_subject_ref,
            question_aspects=['RULES'],rule_aspects=supported_rules,include_typed=False,
            selected_public_refs=selected_public_refs))
    if partial is not None:
        return partial
    if include_typed:
        typed=_typed(records,language)
        if typed is not None:
            text,refs=typed
            if 'RULES' in (question_aspects or []):
                # Recorded objects prove their own facts, not the answer to an
                # independently selected policy question. Keep those facts,
                # then consume the exact same verified public subject. A
                # birthday/member record never becomes a delegation grant.
                policy_text,scope=bounded_fallback(records,language,
                    public_subject_ref=public_subject_ref,question_aspects=['RULES'],
                    rule_aspects=rule_aspects,include_typed=False)
                complete=scope.get('fallback_scope')=='REGISTERED_SUBJECT_POLICY'
                return text+'\n\n'+policy_text,{**scope,
                    'fallback_scope':('VERIFIED_TYPED_FACTS_AND_REGISTERED_SUBJECT_POLICY' if complete
                        else 'VERIFIED_TYPED_FACTS_RULES_UNVERIFIED'),
                    'rendered_fact_refs':refs,
                    'question_coverage':{'complete':complete,
                        'covered_rule_aspects':list(dict.fromkeys(rule_aspects or ['GENERAL'])) if complete else [],
                        'missing_rule_aspects':[] if complete else list(dict.fromkeys(rule_aspects or ['GENERAL']))}}
            return text,{'fallback_scope':'VERIFIED_TYPED_FACTS','fallback_policy_source_ids':[],
                'rendered_fact_refs':refs}
    ids=[];rules=list(dict.fromkeys(rule_aspects or ['GENERAL']))
    if question_aspects and not set(question_aspects)<={'RULES','SUMMARY'}:
        return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
    if public_subject_ref=='topic:FRAUD':ids=['fraud.case-continuity-v1']
    elif public_subject_ref=='policy:credentials-channel':ids=['security.credentials-channel-v1']
    elif public_subject_ref=='policy:goal-reservations':ids=['goals.available-v1']
    elif public_subject_ref=='policy:credit':
        if any(rule not in _CREDIT for rule in rules):return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        ids=list(dict.fromkeys(_CREDIT[rule] for rule in rules))
    elif public_subject_ref=='policy:family-permission':ids=['family.scope-v1']
    elif public_subject_ref=='policy:birthday-plan':
        ids=['life-plan.execution-refund-v1' if any(rule in {'REFUND','CANCELLATION'} for rule in rules) else 'life-plan.bounded-authorization-v1']
    elif public_subject_ref=='policy:merchant':
        ids=['merchant.payment-refund-v1' if any(rule in {'SETTLEMENT','REFUND','PROCESS'} for rule in rules) else 'merchant.catalog-scope-v1']
    elif isinstance(public_subject_ref,str) and public_subject_ref.startswith('subscription:'):
        from .semantic_retrieval_query import public_retrieval_subjects
        if any(row['ref']==public_subject_ref and row['topic']=='SUBSCRIPTIONS' for row in public_retrieval_subjects()):
            ids=['subscription.scope-v1']
    elif isinstance(public_subject_ref,str) and public_subject_ref.startswith('topic:'):
        identifier=_TOPIC_POLICY.get(public_subject_ref[6:])
        if identifier:ids=[identifier]
    if not ids or len(ids)>2:return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
    rendered=[];sources=[]
    for identifier in ids:
        expected=_policy(identifier)
        matching=[row for row in records if expected and row.get('source_id')==expected['source_id']]
        if len(matching)!=1 or matching[0]!=expected:
            return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        body=expected.get('content_en') if language=='en' else expected.get('content')
        if identifier=='security.credentials-channel-v1' and language=='en':
            # Finite translation of this exact v1 policy, not model prose.
            body=('Passwords belong in the login control and verification codes in the matching verification control. '
                'Chat does not use or fill in passwords, codes, keys or tokens. '
                'Use the current task verification control when required, then review the owner, version, amount, currency and recipient; verification cannot replace final confirmation. '
                'Do not send credentials in chat. Use available login or verification support; an explanation cannot reset a password or resend a code.')
        if not isinstance(body,str) or not body.strip() or language=='en' and re.search(r'[\u4e00-\u9fff]',body):
            return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        sentences=[part.strip() for part in re.split(r'(?<=[。！？])|(?<=[.!?])\s+',body) if part.strip()]
        indices=([0,1,2,3,4] if identifier=='security.credentials-channel-v1' else
            [0,1,2,3] if identifier=='fx.quote-v1' else
            [0,1,3] if identifier=='credit.shared-limit-v1' and language=='en' else
            [0,1,2] if identifier=='credit.shared-limit-v1' else
            ([0,1,2,3,4,5] if language=='en' else [0,1,2,3,4]) if identifier=='family.scope-v1' else
            [0,1,2] if identifier=='fraud.case-continuity-v1' else [0,1])
        policy_rules=([rule for rule in rules if _CREDIT.get(rule)==identifier] if public_subject_ref=='policy:credit' else rules)
        if policy_rules!=['GENERAL'] and identifier not in {'credit.shared-limit-v1','credit.application-v1','fraud.case-continuity-v1'}:
            if identifier=='credit.repayment-v1' and set(policy_rules)<={'SETTLEMENT'}:indices=[3,4,5]
            elif identifier=='family.scope-v1' and set(policy_rules)<={'GENERAL','ELIGIBILITY','LIMITS'}:
                indices=[0,1,2,3,4,5] if language=='en' else [0,1,2,3,4]
            elif identifier=='life-plan.execution-refund-v1' and set(policy_rules)<={'GENERAL','REFUND','CANCELLATION'}:
                specific=[rule for rule in policy_rules if rule!='GENERAL']
                indices=([3,4,5] if len(specific)>1 else [4,5] if 'REFUND' in specific else [3,4]) if language=='en' else [2,3]
                from .feedback_public_policy import source_sentence_indices
                current_specific=source_sentence_indices(identifier,language,specific)
                if current_specific is not None:indices=current_specific
                if 'GENERAL' in policy_rules:indices=sorted(set([0,1,*indices]))
            elif identifier=='life-plan.bounded-authorization-v1' and set(policy_rules)<={'GENERAL','PROCESS','LIMITS','ELIGIBILITY'}:
                indices=list(range(len(sentences)))
            elif identifier=='wealth.risk-v1' and set(policy_rules)<={'GENERAL','RISK','PROCESS','ELIGIBILITY','LIMITS'}:
                indices=list(range(len(sentences)))
            elif identifier=='merchant.payment-refund-v1' and set(policy_rules)<={'REFUND'}:indices=[4,5] if language=='en' else [3,4]
            elif identifier=='merchant.payment-refund-v1' and set(policy_rules)<={'SETTLEMENT'}:indices=[1,3] if language=='en' else [1,2]
            elif identifier=='merchant.payment-refund-v1' and set(policy_rules)<={'PROCESS'}:indices=[0]
            elif identifier=='subscription.scope-v1' and set(policy_rules)<={'GENERAL','PROCESS','ELIGIBILITY','CANCELLATION','REFUND','SETTLEMENT','BILLING_PERIOD','AUTO_RENEWAL','BENEFITS'}:
                indices=list(range(len(sentences)))
            else:return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        from .feedback_public_policy import source_sentence_indices
        current_indices=source_sentence_indices(identifier,language,policy_rules)
        if current_indices is not None:indices=current_indices
        if any(index>=len(sentences) for index in indices):
            return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        text=(' ' if language=='en' else '').join(sentences[index] for index in indices)
        if len(text)>(1100 if language=='en' else 650):return missing,{'fallback_scope':'NO_VERIFIED_POLICY','fallback_policy_source_ids':[]}
        rendered.append(text);sources.append(expected['source_id'])
    return '\n\n'.join(rendered),{'fallback_scope':'REGISTERED_SUBJECT_POLICY','fallback_policy_source_ids':sources,
        'question_coverage':{'complete':True,'covered_rule_aspects':rules,'missing_rule_aspects':[]}}
