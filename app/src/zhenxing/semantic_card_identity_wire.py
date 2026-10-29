"""Lossless current-source card identity field codec, never card selection.

Only a complete registered-format platform identifier mistakenly labelled as
card_last4 can become card_number. No bank/card lookup, truncation, source
relocation, continuation, new action, slot value or execution authority.
"""
from copy import deepcopy
import re


VERSION = 'source-bound-card-identity-wire-v1'
ENTITY_SCOPE_VERSION = 'source-bound-card-identity-wire-field-scope-v2'
_NUMBER = r'(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}'
_TOKEN = re.compile(r'(?<![A-Za-z0-9_])' + _NUMBER + r'(?![A-Za-z0-9_])')
_IDENTITY_FIELDS = frozenset({'card_last4','card_number','card_reference','source_card',
    'recipient_card_reference','destination_card_reference','funding_card_number'})
_NON_IDENTITY_ENTITIES = frozenset({('WEALTH_COMPARE','WEALTH','product_name'),
    ('ASSET_POSITIONS_READ','ASSETS','product_name')})


def _selected_non_identity_entities(raw, prepared, changes):
    """Finite provided product types only; selected refs remain untouched.

    This proves only that an unrelated reference cannot select a card. It
    never supplies a product value, fact, source, owner or banking permission.
    Missing/unknown entity contracts remain incompatible with this new path.
    """
    refs=raw.get('entity_refs');entities=prepared.get('entities')
    if not isinstance(refs,list) or not 1<=len(refs)<=8 or any(not isinstance(ref,str) or not ref for ref in refs) \
            or len(set(refs))!=len(refs) or not isinstance(entities,list) or len(entities)>40:
        return False
    for ref in refs:
        offered=[row for row in entities if isinstance(row,dict) and row.get('entity_ref')==ref]
        if len(offered)!=1:return False
        entity=offered[0]
        typed=(entity.get('read_kind'),entity.get('domain'),entity.get('field'))
        if any(not isinstance(value,str) for value in typed) or typed not in _NON_IDENTITY_ENTITIES \
                or entity.get('allowed_fields')!=['product_name'] or entity.get('kind') is not None:
            return False
    for change in changes:
        ref=change.get('entity_ref')
        if ref is not None and (ref not in refs or change.get('field')!='product_name'):
            return False
    return True


def normalize_card_identity_wire(raw, prepared):
    """Return original object on every absent/ambiguous/ineligible condition.

    The caller still validates the full schema, request polarity, current
    bank ownership/instrument/account and normal confirmation. A format match
    is not proof that the identifier exists or belongs to the current actor.
    """
    if not isinstance(raw,dict) or not isinstance(prepared,dict) or raw.get('mode')!='REQUEST' \
            or prepared.get('role') not in {'CUSTOMER','DEV_TEST'} or raw.get('focus_ref') is not None \
            or raw.get('read_targets'):
        return raw,[]
    kinds=raw.get('action_kinds');changes=raw.get('changes');message=prepared.get('message')
    if not isinstance(kinds,list) or not 1<=len(kinds)<=8 or any(not isinstance(kind,str) for kind in kinds) \
            or not isinstance(changes,list) or not 1<=len(changes)<=24 \
            or any(not isinstance(row,dict) or not isinstance(row.get('field'),str)
                or not isinstance(row.get('action_ref'),str) for row in changes) \
            or not isinstance(message,str):
        return raw,[]
    pairs=[(row['action_ref'],row['field']) for row in changes]
    if len(set(pairs))!=len(pairs):return raw,[]
    version=VERSION
    if raw.get('entity_refs',[])!=[]:
        if not _selected_non_identity_entities(raw,prepared,changes):return raw,[]
        version=ENTITY_SCOPE_VERSION
    identities=[(index,row) for index,row in enumerate(changes) if row.get('field') in _IDENTITY_FIELDS]
    if len(identities)!=1:return raw,[]
    index,row=identities[0]
    if row.get('field')!='card_last4' or row.get('operation')!='SET' \
            or row.get('entity_ref') is not None or row.get('selected_options',[])!=[]:
        return raw,[]
    reference=row.get('action_ref');match=re.fullmatch(r'new:([0-7])',reference) if isinstance(reference,str) else None
    if match is None or int(match[1])>=len(kinds):return raw,[]
    kind=kinds[int(match[1])]
    capabilities=prepared.get('capabilities')
    if not isinstance(capabilities,list):return raw,[]
    offered=[cap for cap in capabilities if isinstance(cap,dict) and cap.get('kind')==kind]
    if len(offered)!=1:return raw,[]
    # Reviewed schema/role/tool registries only; these calls do not execute a
    # tool, connect a database, interpret wording, or choose an action/card.
    from .capability_catalog import catalog
    from .dialogue_state import ACTION_ALLOWED_FIELDS
    registered=[cap for cap in catalog(prepared['role'])['capabilities'] if cap['kind']==kind]
    fields=offered[0].get('allowed_fields')
    if len(registered)!=1 or not isinstance(fields,list) or any(not isinstance(field,str) for field in fields) \
            or len(fields)!=len(set(fields)) \
            or not {'card_last4','card_number'}<=set(fields)<=set(ACTION_ALLOWED_FIELDS.get(kind,())) \
            or offered[0].get('tool')!=registered[0]['tool'] \
            or offered[0].get('read_only') is not registered[0]['read_only']:
        return raw,[]
    span=row.get('source_span');text=row.get('source_text')
    if not isinstance(span,dict) or set(span)!={'start','end'} \
            or any(type(span[key]) is not int for key in ('start','end')) \
            or not isinstance(text,str) or re.fullmatch(_NUMBER,text) is None:
        return raw,[]
    start,end=span['start'],span['end']
    if not 0<=start<end<=len(message) or end-start!=16 or message[start:end]!=text:
        return raw,[]
    tokens=list(_TOKEN.finditer(message))
    if len(tokens)!=1 or (tokens[0].start(),tokens[0].end(),tokens[0][0])!=(start,end,text):
        return raw,[]
    canonical=deepcopy(raw)
    canonical['changes'][index]['field']='card_number'
    return canonical,[{'event_kind':'FULL_CARD_IDENTITY_FIELD_RELABELED','version':version,
        'action_ref':reference,'action_kind':kind,'change_index':index,'source_span':dict(span),
        'original_field':'card_last4','canonical_field':'card_number','literal_bytes_preserved':True,
        'bank_write_authorized':False}]
