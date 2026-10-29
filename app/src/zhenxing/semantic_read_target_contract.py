"""Compact outgoing mirrors of existing READ selector consumers.

No target is supplied, discarded or broadened here. A category-wide READ can
omit selectors; a particular identity still needs its own literal/entity proof.
"""
import re

VERSION='offered-read-target-grammar-v1'
CODE='SEMANTIC_TARGET_SET_FIELD_INVALID'


def apply_card_target_sources(definitions,prepared):
    """Bare current identifiers or a compatible offered identity, not a noun.

    The runtime still accepts its old closed decorated literals. New proposals
    can copy the bare identifier's exact span, or retain an offered entity with
    an ordinal/anaphoric source such as "second card". No identity is inferred.
    """
    message=prepared['message']
    identifiers=re.findall(r'(?<![a-zA-Z0-9])(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}(?![a-zA-Z0-9])|(?<![a-zA-Z0-9])[0-9a-fA-F]{4}(?![a-zA-Z0-9])',message,re.I)
    selection=definitions['SemanticReadSelection']
    for field,length in (('card_number',16),('card_last4',4)):
        literals=sorted({token for token in identifiers if len(token)==length})
        refs=[row['entity_ref'] for row in prepared['entities']
            if field in row.get('allowed_fields',[row.get('field')])]
        sources=[]
        if refs:sources.append({'properties':{'entity_ref':{'enum':refs}},'required':['entity_ref']})
        if literals:sources.append({'properties':{'entity_ref':{'type':'null'},'source_text':{'enum':literals}},'required':['source_text']})
        selection.setdefault('allOf',[]).append({'if':{'properties':{'field':{'const':field}},'required':['field']},
            'then':{'properties':{'targets':{'items':{'anyOf':sources} if sources else False}}}})


def target_field_grammar_witness(value,prepared):
    """Recheck one narrow outbound field-vocabulary violation, not any error.

    Only a well-typed, independent pure READ with exact current literals and
    a registered scalar mistakenly placed in the unsupported collection lane
    qualifies. Source errors, unknown fields/entities and WRITE never qualify.
    This returns evidence only; normal execution continues to reject the value.
    """
    from .semantic_router import SemanticDecision
    from .semantic_read_targets import TARGET_FIELDS,expand_read_targets
    from .semantic_read_subject import validate_executable_read_subjects
    from .semantic_query_scope import _source_text
    from .semantic_proposal import _trusted_span
    from .contracts import DomainError
    from .intelligence import READ_KINDS
    from .planning import canonical_hash
    try:
        parsed=SemanticDecision.model_validate(value)
        if parsed.mode!='REQUEST' or not parsed.action_kinds or any(kind not in READ_KINDS for kind in parsed.action_kinds) \
                or parsed.changes or parsed.entity_refs or parsed.history_read_sources or parsed.query_reference is not None \
                or parsed.focus_ref is not None or parsed.memory_updates or parsed.query_base not in (None,'INDEPENDENT','EXPLICIT'):
            return None
        capabilities={row['kind']:row for row in prepared['capabilities']}
        if len(parsed.dependencies)!=len(parsed.action_kinds) or any(
                any(type(parent) is not int or not 0<=parent<index for parent in parents)
                for index,parents in enumerate(parsed.dependencies)):return None
        validate_executable_read_subjects(parsed.read_subjects,parsed.model_dump(),prepared)
        violations=[];seen=set()
        for index,selection in enumerate(parsed.read_targets):
            match=re.fullmatch(r'new:(\d+)',selection.action_ref)
            ordinal=int(match[1]) if match else -1
            if not 0<=ordinal<len(parsed.action_kinds) or selection.action_ref!=f'new:{ordinal}' or ordinal in seen:return None
            seen.add(ordinal)
            capability=capabilities.get(parsed.action_kinds[ordinal],{})
            if capability.get('read_only') is not True or selection.field not in capability.get('allowed_fields',[]):return None
            for target in selection.targets:
                # An unoffered ref cannot be recast as an ordinary grammar fix.
                if target.entity_ref is not None or target.selected_options:return None
                start,end=target.source_span.start,target.source_span.end
                if not 0<=start<end<=len(prepared['message']):return None
                literal=_source_text({'start':start,'end':end},prepared['message'])
                # Mirror the compiler's original field-sensitive quote/taint
                # check too: a generic quoted scalar is not a grammar retry.
                _trusted_span(prepared['message'],{'start':start,'end':end},selection.field)
                if target.source_text is not None and target.source_text!=literal:return None
            if selection.field not in TARGET_FIELDS:violations.append(index)
        if len(violations)!=1:return None
        # Prove this is the real compiler's exact field gate, not caller JSON.
        try:expand_read_targets(parsed.model_dump(),prepared)
        except ValueError as error:
            if str(error)!=CODE:return None
        else:return None
        return {'version':VERSION,'classification':'WIRE_GRAMMAR','phase':'READ_TARGET_FIELD',
            'validation_code':CODE,'reason':'UNSUPPORTED_COLLECTION_FIELD',
            'location':['read_targets',violations[0],'field'],
            'input_hash':canonical_hash(prepared),'source_message_hash':canonical_hash({'message':prepared['message']}),
            'decoder_input_hash':canonical_hash(value),'bank_write_authorized':False}
    except (DomainError,ValueError,TypeError,KeyError,AttributeError,IndexError):
        return None
