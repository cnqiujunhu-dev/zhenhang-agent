"""Expand explicit read target sets into source-bound scalar read nodes.

This is a typed compiler, not a sentence classifier. It never creates writes,
widens an empty selection to ALL, or repairs duplicate scalar field changes.
"""
from copy import deepcopy
import re


TARGET_FIELDS = frozenset({'merchant','product_name','merchant_product_id','occasion_id','life_plan_id','card_number','card_last4','reward_name','behavior_name','owner_name','grantee_name','benefit_name','asset_class','preference_kind','aa_collection_ref','aa_obligation_ref'})


def validate_request_change_ownership(value, prepared):
    """No pre-validation codec may discard an unowned/invalid original field."""
    if value.get('mode')!='REQUEST':return
    capabilities={row['kind']:row for row in prepared['capabilities']}
    kinds=value.get('action_kinds',[])
    for change in value.get('changes',[]):
        match=re.fullmatch(r'new:(\d+)',change.get('action_ref',''))
        ordinal=int(match[1]) if match else -1
        if not 0<=ordinal<len(kinds) or change['action_ref']!=f'new:{ordinal}':
            raise ValueError('SEMANTIC_NEW_ACTION_REFERENCE_INVALID')
        if change.get('field') not in capabilities.get(kinds[ordinal],{}).get('allowed_fields',[]):
            raise ValueError('SEMANTIC_FIELD_NOT_OFFERED')
        if change.get('operation','SET')!='SET':raise ValueError('SEMANTIC_NEW_ACTION_CLEAR_INVALID')


def expand_read_targets(value, prepared):
    targets = value.get('read_targets', [])
    if not targets:
        return value
    from .intelligence import READ_KINDS
    if value.get('mode') != 'REQUEST' or any(kind not in READ_KINDS for kind in value.get('action_kinds', [])):
        raise ValueError('SEMANTIC_TARGET_SET_READ_ONLY')
    validate_request_change_ownership(value,prepared)
    value=deepcopy(value)
    capabilities = {row['kind']: row for row in prepared['capabilities']}
    def selector(item):
        if item.get('entity_ref'):return ('ENTITY',item['entity_ref'])
        if item.get('selected_options'):return ('OPTIONS',tuple(item['selected_options']))
        span=item.get('source_span') or {}
        raw=item.get('source_text') or prepared['message'][span.get('start',0):span.get('end',0)]
        return ('LITERAL',raw)
    def equivalent(left,right,field):
        first,second=selector(left),selector(right)
        if first==second:return True
        if field!='product_name' or {first[0],second[0]}!={'ENTITY','LITERAL'}:return False
        entity_key=first[1] if first[0]=='ENTITY' else second[1]
        literal=first[1] if first[0]=='LITERAL' else second[1]
        entity=next((row for row in prepared['entities'] if row['entity_ref']==entity_key),None)
        if not entity or entity.get('field')!=field or entity.get('domain') not in {'WEALTH','ASSETS'}:return False
        from .wealth_identity import product_reference_matches
        matches=[row for row in prepared['entities'] if row.get('frame_ref')==entity.get('frame_ref')
            and row.get('field')==field and product_reference_matches({'name':row['label']},literal)]
        return len(matches)==1 and matches[0]['entity_ref']==entity_key
    groups = {}
    for selection in targets:
        match = re.fullmatch(r'new:(\d+)', selection['action_ref'])
        ordinal = int(match[1]) if match else -1
        if not 0 <= ordinal < len(value['action_kinds']) or ordinal in groups:
            raise ValueError('SEMANTIC_TARGET_SET_ACTION_INVALID')
        kind = value['action_kinds'][ordinal]
        field = selection['field']
        if field not in TARGET_FIELDS or field not in capabilities.get(kind, {}).get('allowed_fields', []):
            raise ValueError('SEMANTIC_TARGET_SET_FIELD_INVALID')
        if field == 'life_plan_id':
            # This newly supported READ target must not turn an explicitly
            # refused query into a fresh task. Reuse the existing refusal-only
            # source operator grammar; it never selects a positive action.
            from .semantic_selected_read_annotation import _denied_read_source
            if _denied_read_source(prepared['message']):
                raise ValueError('SEMANTIC_QUERY_SOURCE_UNTRUSTED')
        scalar=[row for row in value.get('changes',[]) if row['action_ref']==selection['action_ref'] and row['field']==field]
        if scalar:
            if len(scalar)!=1 or len(selection['targets'])!=1 or scalar[0].get('operation','SET')!='SET' \
                    or not equivalent(scalar[0],selection['targets'][0],field):
                raise ValueError('SEMANTIC_TARGET_SET_SCALAR_CONFLICT')
            value['changes'].remove(scalar[0])
        seen=set()
        for item in selection['targets']:
            identity=('ENTITY',item['entity_ref']) if item.get('entity_ref') else (
                'LITERAL',item.get('source_text'),tuple(item.get('selected_options',[]))) if item.get('source_text') else (
                'SPAN',item['source_span']['start'],item['source_span']['end'])
            if identity in seen:raise ValueError('SEMANTIC_TARGET_SET_DUPLICATED')
            seen.add(identity)
        groups[ordinal] = selection
    actions, dependencies, changes, mapping = [], [], [], {}
    if len(value.get('dependencies', [])) != len(value['action_kinds']):
        raise ValueError('SEMANTIC_DAG_LENGTH_INVALID')
    for ordinal, kind in enumerate(value['action_kinds']):
        selection = groups.get(ordinal)
        items = selection['targets'] if selection else [None]
        mapping[ordinal] = []
        for item in items:
            index = len(actions)
            if index >= 8:
                raise ValueError('SEMANTIC_TARGET_SET_BOUND')
            mapping[ordinal].append(index)
            actions.append(kind)
            parents = value['dependencies'][ordinal]
            if any(type(parent) is not int or parent not in mapping or parent >= ordinal for parent in parents):
                raise ValueError('SEMANTIC_DAG_NOT_PRIOR_ONLY')
            dependencies.append([parent_index for parent in parents for parent_index in mapping[parent]])
            for row in value.get('changes', []):
                if row['action_ref'] == f'new:{ordinal}':
                    changes.append({**deepcopy(row), 'action_ref': f'new:{index}'})
            if item is not None:
                changes.append({**deepcopy(item), 'action_ref': f'new:{index}', 'field': selection['field'], 'operation': 'SET'})
    # Canonical receipts retain all original literal/entity sources in changes.
    # Clearing this syntactic sugar makes receipt revalidation idempotent.
    result={**value, 'action_kinds': actions, 'dependencies': dependencies, 'changes': changes, 'read_targets': []}
    if value.get('history_read_sources'):
        result['history_read_sources']=[{**deepcopy(source),'action_ref':'new:'+str(index)}
            for source in value['history_read_sources'] for index in mapping[int(source['action_ref'].split(':')[1])]]
    return result
