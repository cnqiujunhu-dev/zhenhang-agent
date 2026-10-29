"""Explicit independent reads may carry redundant recorded-set navigation.

The old set proves target equivalence only; it supplies no price, status,
ownership authority or new literal. Execution still uses fresh owned READs.
"""
from copy import deepcopy

VERSION = 'independent-explicit-read-v1'
TARGET_SET_VERSION = 'independent-explicit-read-targets-v1'
# Opt-in identity roles, not request-phrase classification. More result fields
# must register their own exact identity codec rather than treating every
# unsupported field as a selector or dropping a model-authored instruction.
SELECTOR_ROLES = {'SUBSCRIPTION_LIST': ('SUBSCRIPTIONS', 'merchant')}


def normalize_independent_read(value, prepared):
    reference = value.get('query_reference') or {}
    kinds = value.get('action_kinds', [])
    if value.get('mode') != 'REQUEST' or value.get('query_base') != 'INDEPENDENT' \
            or value.get('focus_ref') is not None or not kinds \
            or not any(kind in SELECTOR_ROLES for kind in kinds) \
            or reference.get('selection') != 'ALL' or reference.get('data_kind', 'RECORDED') != 'RECORDED':
        return value, []
    def invalid():
        raise ValueError('SEMANTIC_INDEPENDENT_READ_SET_NOT_EQUIVALENT')
    from .intelligence import READ_KINDS
    if len(set(kinds)) != 1 or not 1 <= len(kinds) <= 8 or kinds[0] not in READ_KINDS \
            or value.get('dependencies') != [[] for _ in kinds]:
        invalid()
    kind = kinds[0]; domain, field = SELECTOR_ROLES[kind]
    capability = next((row for row in prepared.get('capabilities', []) if row.get('kind') == kind), {})
    if capability.get('read_only') is not True or field not in capability.get('allowed_fields', []): invalid()
    matches = [(frame, group) for frame in prepared.get('frames', [])
        for group in (frame.get('query_scope') or {}).get('collections', [])
        if group.get('set_ref') == reference.get('set_ref')]
    if len(matches) != 1: invalid()
    frame, group = matches[0]
    if frame.get('state') != 'SUCCEEDED' or frame.get('write_effect_possible') is not False \
            or frame.get('paused') or not frame.get('actions') \
            or any(action.get('kind') not in READ_KINDS for action in frame['actions']) \
            or kind not in {action['kind'] for action in frame['actions']} \
            or (group.get('domain'), group.get('field')) != (domain, field): invalid()
    # A single READ with an explicit target set is the same bounded operation
    # as one scalar READ per target. Reuse the registered compiler, then prove
    # the complete original set before removing any redundant navigation.
    # Original scalar entity selectors remain outside this literal-only lane.
    target_sets = value.get('read_targets', [])
    if target_sets and any(change.get('entity_ref') for change in value.get('changes', [])): invalid()
    if target_sets:
        # The shared expander can coalesce an equivalent single-target scalar.
        # Validate every original scalar source before such a field disappears;
        # target validity is not proof for a different original span.
        from .semantic_query_scope import _source_text
        for change in value.get('changes', []):
            raw = _source_text(change.get('source_span'), prepared['message'])
            if change.get('source_text') not in (None, raw): invalid()
        if any(selection.get('action_ref') not in {'new:'+str(i) for i in range(len(kinds))}
                or selection.get('field') != field or any(target.get('selected_options')
                for target in selection.get('targets', [])) for selection in target_sets): invalid()
        from .semantic_read_targets import expand_read_targets
        expanded = expand_read_targets(value, prepared)
    else:
        expanded = value
    expanded_kinds = expanded.get('action_kinds', [])
    if not expanded_kinds or any(item != kind for item in expanded_kinds) \
            or expanded.get('dependencies') != [[] for _ in expanded_kinds]: invalid()
    members = group.get('member_refs', [])
    refs = value.get('entity_refs', [])
    if not isinstance(members, list) or len(members) != len(expanded_kinds) or len(set(members)) != len(members) \
            or len(refs) != len(set(refs)) or set(refs) != set(members): invalid()
    repeated = reference.get('member_refs', [])
    if repeated and (len(repeated) != len(set(repeated)) or set(repeated) != set(members)): invalid()
    entities = prepared.get('entities', [])
    selected = []
    for ref in members:
        found = [entity for entity in entities if entity.get('entity_ref') == ref]
        if len(found) != 1: invalid()
        entity = found[0]
        if entity.get('frame_ref') != frame.get('frame_ref') or entity.get('read_kind') != kind \
                or (entity.get('domain'), entity.get('field')) != (domain, field) \
                or field not in entity.get('allowed_fields', []) or not isinstance(entity.get('label'), str): invalid()
        selected.append(entity)
    from .semantic_query_scope import _source_text
    if reference.get('source_span') is not None: _source_text(reference['source_span'], prepared['message'])
    if value.get('query_base_source') is not None: _source_text(value['query_base_source'], prepared['message'])
    from .subscription_commerce import resolve_subscription_merchant
    labels = [resolve_subscription_merchant(entity['label']) for entity in selected]
    if len(set(labels)) != len(labels): invalid()
    literals = []
    changes = expanded.get('changes', [])
    for ordinal in range(len(expanded_kinds)):
        selector = [change for change in changes if change.get('action_ref') == 'new:' + str(ordinal)
            and change.get('field') == field]
        if len(selector) != 1: invalid()
        selector = selector[0]
        if selector.get('operation', 'SET') != 'SET' or selector.get('selected_options'): invalid()
        raw = _source_text(selector.get('source_span'), prepared['message'])
        if selector.get('source_text') not in (None, raw): invalid()
        literal = resolve_subscription_merchant(raw)
        if selector.get('entity_ref'):
            matching = [entity for entity in selected if entity['entity_ref'] == selector['entity_ref']]
            if not target_sets or len(matching) != 1 or resolve_subscription_merchant(matching[0]['label']) != literal: invalid()
        literals.append(literal)
    if len(set(literals)) != len(literals) or set(literals) != set(labels): invalid()
    # Every field, source span, action, dependency and aspect is retained for
    # the normal semantic/owned-domain validators. Only proved redundant
    # navigation disappears; no bank fact is copied from this old result.
    result = deepcopy(expanded)
    result.pop('query_reference', None); result['entity_refs'] = []
    if target_sets:
        # The symbol is redundant only after its exact current literal and
        # registered collection identity agree. Fresh owned reads retain that
        # literal/span; they do not consume old subscription status or fees.
        for change in result['changes']:
            if change.get('field') == field: change.pop('entity_ref', None)
        result.pop('read_targets', None)
    from .planning import canonical_hash
    event = {'code': 'EXPLICIT_INDEPENDENT_READ_REDUNDANT_SET', 'version': TARGET_SET_VERSION if target_sets else VERSION,
        'read_kind': kind, 'field': field, 'frame_ref': frame['frame_ref'],
        'set_ref': group['set_ref'], 'target_count': len(expanded_kinds),
        'projection_source_hash': canonical_hash({'query_reference': reference, 'entity_refs': refs,
            'registered_collection': group, 'registered_entities': selected,
            **({'read_targets': target_sets, 'changes': value.get('changes', [])} if target_sets else {'changes': changes})})}
    return result, [event]
