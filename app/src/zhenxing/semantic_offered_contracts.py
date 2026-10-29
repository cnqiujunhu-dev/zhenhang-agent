"""Outgoing representations of existing consumers, never execution authority.

These hints/schema do not rewrite a model decision. The original typed source,
owner, scalar-field and domain validators remain the acceptance boundary.
"""
from collections import defaultdict
from copy import deepcopy

VERSION = 'offered-action-entity-contract-v1'

# Exact contracts already enforced by the completed-identity consumers. These
# describe identities, not the old result's amounts, state or authorization.
_COMPLETED = {
    'COMPLETED_LIFE_PLAN_IDENTITY': ('LIFE_PLANS','life_plan_id','LIFE_PLAN_VIEW',('LIFE_PLAN_VIEW','LIFE_PLAN_CANCEL')),
    'COMPLETED_SUBSCRIPTION_IDENTITY': ('SUBSCRIPTIONS','merchant','SUBSCRIPTION_LIST',('SUBSCRIPTION_LIST',)),
    'COMPLETED_REPAYMENT_TARGET_IDENTITY': ('CREDIT','card_reference','CREDIT_VIEW',('CREDIT_REPAY','CREDIT_VIEW','CREDIT_STATEMENT')),
    'COMPLETED_REPAYMENT_FUNDING_IDENTITY': ('CARDS','source_card','CARD_STATUS',('CREDIT_REPAY',)),
    'COMPLETED_REPAYMENT_FUNDING_READ_IDENTITY': ('CARDS','card_number','CARD_STATUS',('CARD_STATUS',)),
    'COMPLETED_CREDIT_INSTALLMENT_IDENTITY': ('CREDIT','installment_id','CREDIT_STATEMENT',('CREDIT_SETTLE_INSTALLMENT',)),
    'COMPLETED_MERCHANT_PAYMENT_IDENTITY': ('CREDIT','payment_id','CREDIT_STATEMENT',('CREDIT_INSTALLMENT',)),
    'COMPLETED_FX_CARD_IDENTITY': ('CARDS','card_number','CARD_STATUS',('CARD_STATUS',)),
    'COMPLETED_WEALTH_CARD_IDENTITY': ('CARDS','card_number','CARD_STATUS',('CARD_STATUS',)),
    'COMPLETED_TRANSFER_RECEIPT_IDENTITY': ('CARDS', 'card_number', 'CARD_STATUS', ('CARD_STATUS',)),
    'COMPLETED_ASSET_RECEIPT_IDENTITY': ('ASSETS', 'product_name', 'ASSET_POSITIONS_READ',
        ('ASSET_POSITIONS_READ',)),
    'COMPLETED_CREDIT_RECEIPT_IDENTITY': ('CREDIT', 'card_reference', 'CREDIT_VIEW',
        ('CREDIT_STATEMENT', 'CREDIT_VIEW')),
    'COMPLETED_OCCASION_RECEIPT_IDENTITY': ('OCCASIONS', 'occasion_id', 'OCCASION_VIEW',
        ('LIFE_PLAN_AUTHORIZE', 'OCCASION_SET', 'OCCASION_VIEW')),
    'COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY': ('OCCASIONS', 'family_member_id', 'OCCASION_MEMBER_VIEW',
        ('LIFE_PLAN_AUTHORIZE', 'OCCASION_MEMBER_SET', 'OCCASION_MEMBER_VIEW', 'OCCASION_SET')),
}


def completed_identity_contract(kind):
    """Opt-in new offers only; never infer metadata on a persisted old offer."""
    if kind == 'COMPLETED_TRANSFER_RECEIPT_IDENTITY':
        from .semantic_transfer_identity import consumer_contract
        return consumer_contract()
    return {'consumer_contract_version': VERSION, 'consumer_kinds': list(_COMPLETED[kind][3])}


def checked_consumer_contract(row):
    keys = {'consumer_contract_version', 'consumer_kinds'}
    if row.get('kind') == 'OWNED_RECIPIENT_CHOICE':
        from .recipient_choices import FIELDS, KINDS, VERSION as CHOICE_VERSION
        if row.get('domain') != 'TRANSFER' or row.get('field') not in FIELDS \
                or row.get('allowed_fields') != [row['field']] \
                or row.get('consumer_contract_version') != CHOICE_VERSION \
                or row.get('consumer_kinds') != sorted(KINDS):
            raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
        return {key: deepcopy(row[key]) for key in keys}
    if row.get('kind')=='HISTORICAL_AA_READ_IDENTITY':
        field=row.get('field')
        if row.get('domain')!='AA' or row.get('read_kind')!='AA_VIEW' or field not in {'aa_collection_ref','aa_obligation_ref'} \
                or row.get('allowed_fields')!=[field] or row.get('consumer_contract_version')!='aa-historical-read-identity-v1' \
                or row.get('consumer_kinds')!=['AA_VIEW']:
            raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
        return {key:deepcopy(row[key]) for key in keys}
    if row.get('kind')=='OWNED_AA_RECORD_IDENTITY':
        field=row.get('field');consumers=row.get('consumer_kinds')
        allowed=[['AA_VIEW'],['AA_VIEW','AA_PAY']] if field=='aa_obligation_ref' else [['AA_VIEW']]
        if row.get('domain')!='AA' or row.get('read_kind')!='AA_VIEW' or field not in {'aa_collection_ref','aa_obligation_ref'} \
                or row.get('allowed_fields')!=[field] or row.get('consumer_contract_version')!='aa-record-consumers-v1' or consumers not in allowed:
            raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
        return {key:deepcopy(row[key]) for key in keys}
    from .bill_query_context import IDENTITY_KIND,checked_consumer_contract as bill_identity_contract
    if row.get('kind')==IDENTITY_KIND:
        return bill_identity_contract(row)
    from .credit_statement_identity import KIND, FIELD, CONSUMER_VERSION, CONSUMERS, consumer_contract
    if row.get('kind') == KIND:
        if ((row.get('domain'), row.get('field'), row.get('read_kind')) != ('CREDIT', FIELD, 'CREDIT_STATEMENT')
                or row.get('allowed_fields') != ['card_reference']
                or row.get('consumer_contract_version') != CONSUMER_VERSION
                or type(row.get('consumer_kinds')) is not list or row['consumer_kinds'] != list(CONSUMERS)):
            raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
        return consumer_contract()
    if row.get('kind') == 'COMPLETED_TRANSFER_RECEIPT_IDENTITY' and not keys <= row.keys():
        raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
    if not keys.intersection(row):
        return {}
    registered = _COMPLETED.get(row.get('kind'))
    if (registered is None or not keys <= row.keys()
            or (row.get('domain'), row.get('field'), row.get('read_kind')) != registered[:3]
            or row.get('allowed_fields') != [registered[1]]
            or row.get('consumer_contract_version') != completed_identity_contract(row.get('kind'))['consumer_contract_version']
            or type(row.get('consumer_kinds')) is not list
            or row['consumer_kinds'] != list(registered[3])):
        raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
    return deepcopy(completed_identity_contract(row['kind']))


def _selected(ref):
    return {'properties': {'entity_refs': {'contains': {'const': ref}}}, 'required': ['entity_refs']}


def _change_ref(ref):
    return {'properties': {'entity_ref': {'const': ref}}, 'required': ['entity_ref']}


def _apply_read_set_consumers(schema, prepared, read_reference_clause):
    """Outgoing-only mirror of existing READ set field/kind requirements."""
    frames = [frame for frame in prepared.get('frames', [])
        if (frame.get('query_scope') or {}).get('collections')]
    if not frames:
        return schema  # Preserve the entire prior schema, including prose.
    from .intelligence import READ_KINDS
    from .semantic_read_targets import TARGET_FIELDS
    entities = {row['entity_ref']: row for row in prepared['entities']}
    compatible = {}
    for capability in prepared['capabilities']:
        kind = capability['kind']
        if kind not in READ_KINDS or capability.get('read_only') is not True:
            continue
        refs = [group['set_ref'] for frame in frames for group in frame['query_scope']['collections']
            if group.get('field') in TARGET_FIELDS and group.get('field') in capability['allowed_fields'] and group.get('member_refs')
            and all(entities.get(ref, {}).get('read_kind') == kind for ref in group['member_refs'])]
        if refs:
            compatible[kind] = refs
    original_read_only = deepcopy(read_reference_clause['then'])
    request_sets = False
    if compatible:
        request_sets = {'properties': {
            'action_kinds': {'items': {'enum': list(compatible)}},
            'query_reference': {'properties': {'set_ref': {'enum': sorted({
                ref for refs in compatible.values() for ref in refs})}}}}}
        by_sets = {}
        for kind, refs in compatible.items():
            by_sets.setdefault(tuple(refs), []).append(kind)
        clauses = []
        if len(by_sets) > 1:
            for refs, kinds in by_sets.items():
                clauses.append({'if': {'properties': {'action_kinds': {
                    'contains': {'enum': kinds}}}, 'required': ['action_kinds']},
                    'then': {'properties': {'query_reference': {
                        'properties': {'set_ref': {'enum': list(refs)}}}}}})
        # Preserve the old unique completed-READ closure without allowing an
        # empty action list to borrow a summary or a different source kind.
        current = frames[0]
        inherited = {action['kind'] for action in current.get('actions', [])}
        empty_refs = compatible.get(next(iter(inherited)), []) if len(inherited) == 1 else []
        empty = False
        if (empty_refs and current.get('state') == 'SUCCEEDED'
                and current.get('write_effect_possible') is False and current.get('paused') is not True):
            empty = {'properties': {'focus_ref': {'const': current['frame_ref']},
                'dependencies': {'enum': [[], [[]]]},
                'query_reference': {'properties': {'set_ref': {'enum': empty_refs}}}}}
        clauses.append({'if': {'properties': {'action_kinds': {'maxItems': 0}},
            'required': ['action_kinds']}, 'then': empty})
        request_sets['allOf'] = clauses
    read_reference_clause['then'] = {
        'if': {'properties': {'query_reference': {'properties': {
            'selection': {'const': 'INDEPENDENT'}}, 'required': ['selection']}},
            'required': ['query_reference']},
        'then': original_read_only, 'else': request_sets}
    schema['properties']['query_reference']['description'] = (
        'Read-only collection metadata, never a payment or WRITE selector. '
        'REQUEST ALL/SUBSET requires a set compatible with that READ selector field; '
        'summary sets remain available to CONSULT. A current category-wide new query '
        'may use its current message without borrowing a historical summary identity. '
        'INDEPENDENT keeps the original current-source checks.')
    return schema


def apply_outgoing_contracts(schema, prepared):
    """Bounded single-action fields + generic identity/collection contracts.

    A multi-action graph keeps the original full validator/DAG contract. Its
    fieldsets are still in capabilities; do not duplicate 49 sets eight times
    or trim context to make a larger schema fit the unchanged wire ceiling.
    """
    definitions = schema['$defs']; clauses = schema['allOf']
    clauses.append({'if':{'properties':{'question_aspects':{'contains':{'const':'OPERATION_SCOPE'}}},'required':['question_aspects']},
        'then':{'properties':{'mode':{'enum':['STATUS','CONSULT']}}}})
    schema['properties']['question_aspects']['description']='STATUS/BALANCE describe current business data. OPERATION_SCOPE only audits execution of already selected tasks in STATUS or CONSULT; it cannot accompany a new REQUEST. Read current account facts through their registered READ action.'
    # This is the same read-only boundary already enforced by the validator,
    # not a model-output repair or a new source of payment authority. Empty
    # action lists keep the existing source-bound READ kind closure legal.
    from .intelligence import READ_KINDS
    read_kinds=[row['kind'] for row in prepared['capabilities'] if row['kind'] in READ_KINDS]
    definitions['OfferedReadKinds']={'enum':read_kinds}
    read_kind_ref={'$ref':'#/$defs/OfferedReadKinds'}
    read_reference_clause = {'if':{'properties':{'mode':{'const':'REQUEST'},'query_reference':{'type':'object'}},
        'required':['mode','query_reference']},'then':{'properties':{'action_kinds':{'items':read_kind_ref}}}}
    clauses.append(read_reference_clause)
    schema['properties']['query_reference']['description']=(
        'Read-only collection metadata, never a payment or other WRITE selector. '
        'For REQUEST with any WRITE action, omit this optional field or set it to null. '
        'Select a write target with its registered source-bound fields and entity consumers instead.')
    groups = defaultdict(list)
    for capability in prepared['capabilities']:
        groups[tuple(capability['allowed_fields'])].append(capability['kind'])
    for ordinal, (fields, kinds) in enumerate(groups.items()):
        name = 'OfferedActionFields' + str(ordinal)
        definitions[name] = {'properties': {'field': {'enum': list(fields)}}}
        clauses.append({'if': {'properties': {'mode': {'const': 'REQUEST'}, 'action_kinds': {
            'minItems': 1, 'maxItems': 1, 'items': {'enum': kinds}}}, 'required': ['mode', 'action_kinds']},
            'then': {'properties': {'changes': {'items': {'allOf': [
                {'$ref': '#/$defs/' + name}, {'properties': {'action_ref': {'const': 'new:0'}}}]}},
                'read_targets': {'items': {'allOf': [{'$ref': '#/$defs/' + name},
                    {'properties': {'action_ref': {'const': 'new:0'}}}]}}}}})
    # Canonical new credit repayment target: one role-named selector avoids
    # overloading generic card_number/card_last4 with the paying debit card.
    # Historical valid decisions keep their existing validator compatibility.
    for index in range(8):
        clauses.append({'if':{'properties':{'mode':{'const':'REQUEST'},'action_kinds':{
            'minItems':index+1,'prefixItems':[{}]*index+[{'const':'CREDIT_REPAY'}]}},'required':['mode','action_kinds']},
            'then':{'properties':{'changes':{'items':{'not':{'properties':{'action_ref':{'const':'new:'+str(index)},
                'field':{'enum':['card_last4','card_number']}},'required':['action_ref','field']}}}}}})

    refs = [row['entity_ref'] for row in prepared['entities']]
    completed_reads = [row['entity_ref'] for row in prepared['entities'] if row.get('kind') in {
        'COMPLETED_TRANSFER_RECEIPT_IDENTITY','COMPLETED_ASSET_RECEIPT_IDENTITY',
        'COMPLETED_CREDIT_RECEIPT_IDENTITY','COMPLETED_WEALTH_CARD_IDENTITY','COMPLETED_FX_CARD_IDENTITY','COMPLETED_SUBSCRIPTION_IDENTITY','COMPLETED_LIFE_PLAN_IDENTITY','HISTORICAL_AA_READ_IDENTITY'}]
    subscription_refs=[row['entity_ref']for row in prepared['entities']if row.get('kind')=='COMPLETED_SUBSCRIPTION_IDENTITY']
    if subscription_refs:
        clauses.append({'if':{'properties':{'entity_refs':{'contains':{'enum':subscription_refs}}},'required':['entity_refs']},
            'then':{'properties':{'mode':{'const':'REQUEST'},'query_base':{'enum':[None,'INDEPENDENT']}}}})
    if completed_reads:
        clauses.append({'if':{'properties':{'mode':{'const':'REQUEST'},'action_kinds':{'minItems':1,'items':read_kind_ref},
            'entity_refs':{'contains':{'enum':completed_reads}}},'required':['mode','action_kinds','entity_refs']},
            'then':{'properties':{'query_base':{'enum':[None,'INDEPENDENT']}}}})
    schema['properties']['entity_refs']['uniqueItems'] = True
    # Mirror the existing query-set validator on the outgoing schema: a
    # recorded subset cannot mix unrelated collections in top-level refs.
    # This describes available choices to the model; it does not rewrite a
    # returned selection or discard a source that the model has selected.
    for frame in prepared.get('frames', []):
        for group in (frame.get('query_scope') or {}).get('collections', []):
            members = group.get('member_refs') or []
            if not members:
                continue
            clauses.append({'if': {'properties': {'query_reference': {'type': 'object',
                'properties': {'set_ref': {'const': group['set_ref']}, 'data_kind': {'const': 'RECORDED'},
                    'selection': {'const': 'SUBSET'}},
                'required': ['set_ref', 'data_kind', 'selection']}}, 'required': ['query_reference']},
                'then': {'properties': {'entity_refs': {'items': {'enum': members}},
                    'query_reference': {'properties': {'member_refs': {'items': {'enum': members}}}}}}})
    definitions['SemanticFieldChange']['properties']['entity_ref']['anyOf'] = [
        {'type': 'string', 'enum': refs}, {'type': 'null'}]
    definitions['SemanticReadTarget']['properties']['entity_ref']['anyOf'] = [
        {'type': 'string', 'enum': refs}, {'type': 'null'}]
    schema['properties']['entity_refs']['description'] += (
        ' Current category-wide READ is not a selection of historical objects: leave refs empty, '
        'preserve the current explicit category as its registered field (CARD_STATUS debit-only '
        'uses card_type=DEBIT with its current source). Referencing several particular old objects '
        'uses source-bound read_targets per object, never an unqualified list of scalar identities.')

    # One offered wire vocabulary: explicit selectors live in read_targets;
    # changes retain shared period/currency/category filters. Historical valid
    # receipts remain accepted by the runtime; this only guides new proposals.
    from .semantic_read_targets import TARGET_FIELDS
    definitions['OfferedReadTargetField']={'enum':sorted(TARGET_FIELDS)}
    definitions['SemanticReadSelection']['properties']['field']={'$ref':'#/$defs/OfferedReadTargetField'}
    clauses.append({'if':{'properties':{'read_targets':{'minItems':1}},'required':['read_targets']},
        'then':{'properties':{'mode':{'const':'REQUEST'},'action_kinds':{'items':read_kind_ref},
            'changes':{'items':{'properties':{'field':{'not':{'$ref':'#/$defs/OfferedReadTargetField'}}}}}}}})
    from .semantic_read_target_contract import apply_card_target_sources
    apply_card_target_sources(definitions,prepared)
    clauses.append({'if':{'properties':{'question_aspects':{'not':{'contains':{'const':'OPERATION_SCOPE'}}}}},
        'then':{'properties':{'scope_frame_refs':{'maxItems':0},'operation_scope':{'type':'null'}}}})
    schema['properties']['scope_frame_refs']['description']=(
        'Only operation-boundary questions with question_aspects containing OPERATION_SCOPE use these refs. '
        'For ordinary query navigation or a changed annual/monthly period use focus_ref/query_base instead; leave this empty.')

    # ALL is a preferred *outgoing* representation, not a newly imposed
    # historical validator rule. The old validator still accepts its legal
    # redundant refs and expands the complete checked set server-side.
    all_set = {'properties': {'selection': {'const': 'ALL'}, 'set_ref': {'type': 'string'}},
        'required': ['selection', 'set_ref']}
    definitions['SemanticQueryReference'].setdefault('allOf', []).append({
        'if': all_set, 'then': {'properties': {'member_refs': {'maxItems': 0}}}})
    clauses.append({'if': {'properties': {'query_reference': {'type': 'object', **all_set}},
        'required': ['query_reference']}, 'then': {'properties': {'entity_refs': {'maxItems': 0}}}})
    definitions['SemanticQueryReference']['description'] = (
        'Outgoing ALL/set_ref canonical form: entity_refs=[] and member_refs=[]. '
        'The server expands the entire checked collection; never enumerate or truncate its members. '
        'This is an outgoing representation restriction, not a rewrite of legacy internal proofs.')

    consumer_groups = defaultdict(list)
    transfer_refs = [row['entity_ref'] for row in prepared['entities']
        if row.get('kind') == 'COMPLETED_TRANSFER_RECEIPT_IDENTITY']
    if transfer_refs:
        # One selected receipt card / one new READ is the canonical outgoing
        # shape. A shared ref group avoids repeating a large clause per old
        # receipt while the compiler still proves every explicit consumption.
        definitions['OfferedTransferIdentityRefs'] = {'enum':transfer_refs}
        transfer_ref = {'$ref':'#/$defs/OfferedTransferIdentityRefs'}
        clauses.append({'if':{'properties':{'entity_refs':{'contains':transfer_ref}},'required':['entity_refs']},
            'then':{'properties':{'mode':{'const':'REQUEST'},'action_kinds':{'const':['CARD_STATUS']},
                'entity_refs':{'maxItems':1}},'anyOf':[
                    {'properties':{'changes':{'contains':{'properties':{'entity_ref':transfer_ref,
                        'field':{'const':'card_number'}},'required':['entity_ref','field']}}},'required':['changes']},
                    {'properties':{'read_targets':{'contains':{'properties':{'field':{'const':'card_number'},
                        'targets':{'contains':{'properties':{'entity_ref':transfer_ref},'required':['entity_ref']}}},
                        'required':['field','targets']}}},'required':['read_targets']}]}})
    for entity in prepared['entities']:
        ref = entity['entity_ref']
        # Mirror the existing explicit selection requirement without adding
        # a ref or fixing an invalid field for the model.
        clauses.append({'if': {'properties': {'changes': {'contains': _change_ref(ref)}}, 'required': ['changes']},
            'then': _selected(ref)})
        if entity.get('allowed_fields') is not None:
            definitions['SemanticReadSelection'].setdefault('allOf', []).append({
                'if': {'properties': {'targets': {'contains': _change_ref(ref)}}, 'required': ['targets']},
                'then': {'properties': {'field': {'enum': entity['allowed_fields']}}}})
        consumers = entity.get('consumer_kinds')
        from .bill_query_context import IDENTITY_KIND
        if entity.get('kind')==IDENTITY_KIND:
            # Selecting a card fact without consuming its precise READ field
            # must not accidentally broaden the request to all owned cards.
            # CONSULT remains free to explain the checked identity as a fact.
            clauses.append({'if':{'properties':{'mode':{'const':'REQUEST'},**_selected(ref)['properties']},
                'required':['mode','entity_refs']},'then':{'anyOf':[
                    {'properties':{'changes':{'contains':{'properties':{'entity_ref':{'const':ref},
                        'field':{'const':entity['field']}},'required':['entity_ref','field']}}},'required':['changes']},
                    {'properties':{'read_targets':{'contains':{'properties':{'field':{'const':entity['field']},
                        'targets':{'contains':_change_ref(ref)}},'required':['field','targets']}}},'required':['read_targets']},
                    {'properties':{'query_base':{'const':'CURRENT'},'query_reference':{'type':'object',
                        'properties':{'selection':{'const':'SUBSET'},'member_refs':{'contains':{'const':ref}}},
                        'required':['selection','member_refs']}},'required':['query_base','query_reference']} ]}})
        if consumers is None:
            continue
        consumer_groups[tuple(consumers)].append(ref)
    for ordinal,(consumers, selected_refs) in enumerate(consumer_groups.items()):
        # The identical reference vocabulary was repeated seventeen times per
        # group. Share it losslessly; never trim offered entities to fit wire.
        ref_name='OfferedConsumerRefs'+str(ordinal)
        definitions[ref_name]={'enum':selected_refs}
        selected_ref_schema={'$ref':'#/$defs/'+ref_name}
        clauses.append({'if': {'properties': {'mode': {'const': 'REQUEST'}, 'entity_refs': {
            'contains': selected_ref_schema}}, 'required': ['mode', 'entity_refs']},
            'then': {'properties': {'action_kinds': {'contains': {'enum': list(consumers)}}}}})
        for index in range(8):
            clauses.append({'if': {'properties': {'mode': {'const': 'REQUEST'}, 'action_kinds': {
                'minItems': index + 1, 'prefixItems': [{}] * index + [{'not': {'enum': list(consumers)}}]}},
                'required': ['mode', 'action_kinds']}, 'then': {'properties': {
                    'changes': {'items': {'not': {'properties': {'action_ref': {'const': 'new:' + str(index)},
                        'entity_ref': selected_ref_schema}, 'required': ['action_ref', 'entity_ref']}}},
                    'read_targets': {'items': {'not': {'properties': {'action_ref': {'const': 'new:' + str(index)},
                        'targets': {'contains': {'properties': {'entity_ref': selected_ref_schema},
                            'required': ['entity_ref']}}}, 'required': ['action_ref', 'targets']}}}}}})

    # Reuse the registered compiler scope consumers, not a second vocabulary
    # of user sentences. No current category or entity is selected here.
    from .semantic_proposal import _READ_ENTITY_SCOPE_TARGETS
    for kind, (domain, fields) in _READ_ENTITY_SCOPE_TARGETS.items():
        candidates = [row for row in prepared['entities'] if row.get('domain') == domain
            and row.get('read_kind') == kind and row.get('field') in fields
            and row.get('field') in row.get('allowed_fields', [])]
        if len(candidates) < 2:
            continue
        matching = [row['entity_ref'] for row in candidates]
        condition = {'properties': {'mode': {'const': 'REQUEST'}, 'action_kinds': {'const': [kind]},
            'entity_refs': {'contains': {'enum': matching}, 'minContains': 2}},
            'required': ['mode', 'action_kinds', 'entity_refs']}
        target_clauses = []
        for entity in candidates:
            target_clauses.append({'if': _selected(entity['entity_ref']), 'then': {'properties': {
                'read_targets': {'contains': {'properties': {'action_ref': {'const': 'new:0'},
                    'field': {'enum': [field for field in entity['allowed_fields'] if field in fields]},
                    'targets': {'contains': _change_ref(entity['entity_ref'])}},
                    'required': ['action_ref', 'field', 'targets']}}}, 'required': ['read_targets']}})
        clauses.append({'if': condition, 'then': {'allOf': target_clauses}})
    from .semantic_read_selector_contract import apply_selector_schema
    schema=apply_selector_schema(schema,prepared)
    return _apply_read_set_consumers(schema, prepared, read_reference_clause)
