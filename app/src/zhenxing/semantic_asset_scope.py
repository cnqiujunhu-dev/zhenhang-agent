"""Bounded literal repair for a cloud-selected asset READ, never intent routing."""
from copy import deepcopy
import re

_LITERALS={
    'ALL':r'全部资产|所有资产|全部持仓|所有持仓|\ball\s+(?:assets|holdings|positions)\b',
    'WEALTH':r'理财|\bwealth\b',
    'STOCK':r'股票|\bstocks?\b',
    'PRECIOUS_METAL':r'贵金属|黄金|\bgold\b|\bprecious\s+metals?\b',
}


def inherited_asset_read_class(value,prepared):
    """Only an explicit CURRENT/focus READ scope; never an ALL default."""
    from .intelligence import READ_KINDS
    if value.get('mode')!='REQUEST' or value.get('query_base')!='CURRENT' \
            or not value.get('action_kinds') or set(value['action_kinds'])!={'ASSET_POSITIONS_READ'} \
            or (value.get('query_reference') or {}).get('selection')=='INDEPENDENT':return None
    frame=next((row for row in prepared.get('frames',[]) if row.get('frame_ref')==value.get('focus_ref')),None)
    if not frame or frame.get('state')!='SUCCEEDED' or frame.get('write_effect_possible') is not False \
            or frame.get('paused') or not frame.get('query_scope') or not frame.get('actions') \
            or any(row.get('kind') not in READ_KINDS or row.get('kind')!='ASSET_POSITIONS_READ' for row in frame['actions']):return None
    classes={row.get('slots',{}).get('asset_class') for row in frame['actions']}
    if len(classes)!=1 or not classes<={'WEALTH','STOCK','PRECIOUS_METAL','ALL'}:return None
    return next(iter(classes))


def literal_asset_classes(message):
    from .intelligence import _source_spans
    tainted=_source_spans(message)
    tainted += [(match.start(),match.end()) for match in re.finditer(r'“[^”]*”|「[^」]*」|"[^"\n]*"',message)]
    result=[]
    for value,pattern in _LITERALS.items():
        for match in re.finditer(pattern,message,re.I):
            if any(match.start()<end and match.end()>start for start,end in tainted):continue
            # Negated alternatives are not a positive query selector.
            if re.search(r'不(?:要|查|看)?\s*$|排除\s*$|except\s*$|not\s*$',message[max(0,match.start()-10):match.start()],re.I):continue
            result.append((value,match.start(),match.end()))
    return result


def _compile_asset_collection_before_class(value,prepared):
    """Validate the model's exact CURRENT collection before inheriting scope.

    A set_ref already names its offered pure READ. The normal collection codec
    proves members, target fields and current spans; it is not a name-to-class
    guess or permission to create an implicit ALL query.
    """
    reference=value.get('query_reference') or {}
    if value.get('focus_ref') is not None or value.get('query_base')!='CURRENT' \
            or reference.get('selection') not in {'ALL','SUBSET'} \
            or not value.get('action_kinds') or set(value['action_kinds'])!={'ASSET_POSITIONS_READ'}:
        return value
    frames=prepared.get('frames',[])
    matches=[frame for frame in frames if any(group.get('set_ref')==reference.get('set_ref')
        and (group.get('domain'),group.get('field'))==('ASSETS','product_name')
        for group in (frame.get('query_scope') or {}).get('collections',[]))]
    current=next((frame for frame in frames if frame.get('query_scope')),None)
    if len(matches)!=1 or matches[0] is not current \
            or not inherited_asset_read_class({**value,'focus_ref':matches[0]['frame_ref']},prepared):
        return value
    from .semantic_query_scope import normalize_query_scope
    compiled=normalize_query_scope(value,prepared)
    if compiled.get('focus_ref')!=matches[0]['frame_ref'] or not inherited_asset_read_class(compiled,prepared):
        raise ValueError('SEMANTIC_ASSET_CLASS_SOURCE_MISMATCH')
    return compiled


def selected_completed_asset_read_refs(value,prepared,index):
    """Only product identities actually consumed by this new READ ordinal.

    This is structural eligibility, not a class fact. The DB compiler must
    still validate the posted InvestmentProduct identity before deriving it.
    """
    if value.get('mode')!='REQUEST' or value.get('action_kinds',[])[index]!='ASSET_POSITIONS_READ':return []
    ref='new:'+str(index)
    selected=[change.get('entity_ref') for change in value.get('changes',[]) if change.get('action_ref')==ref
        and change.get('field')=='product_name' and change.get('operation','SET')=='SET']
    selected += [target.get('entity_ref') for selection in value.get('read_targets',[])
        if selection.get('action_ref')==ref and selection.get('field')=='product_name'
        for target in selection.get('targets',[])]
    if not selected or None in selected:return []
    # Eligibility only: the same identity may occur in both scalar and target
    # wire forms. expand_read_targets later proves exact equivalence (or rejects
    # conflicting spans); no field or provenance is discarded here.
    selected=list(dict.fromkeys(selected))
    entities={row['entity_ref']:row for row in prepared.get('entities',[])}
    for candidate in selected:
        row=entities.get(candidate,{})
        if candidate not in value.get('entity_refs',[]) or row.get('kind')!='COMPLETED_ASSET_RECEIPT_IDENTITY' \
                or (row.get('domain'),row.get('field'),row.get('read_kind'))!=('ASSETS','product_name','ASSET_POSITIONS_READ') \
                or row.get('allowed_fields')!=['product_name'] \
                or row.get('consumer_kinds')!=['ASSET_POSITIONS_READ']:
            return []
    return selected


def complete_asset_read_class(value,prepared):
    """Only close a missing enum after the model selected this exact READ.

    Ambiguous/generic scopes fail closed; ALL is never the server default.
    The appended change carries the original current-message literal span.
    """
    if not isinstance(value,dict) or value.get('mode')!='REQUEST':return value,None
    kinds=value.get('action_kinds',[])
    if 'ASSET_POSITIONS_READ' not in kinds:return value,None
    value=_compile_asset_collection_before_class(value,prepared)
    inherited=inherited_asset_read_class(value,prepared)
    if inherited and not literal_asset_classes(prepared['message']):
        supplied=[change for change in value.get('changes',[]) if change.get('field')=='asset_class']
        supplied += [target for selection in value.get('read_targets',[]) if selection.get('field')=='asset_class'
            for target in selection.get('targets',[])]
        if any(change.get('selected_options')!=[inherited] for change in supplied):
            raise ValueError('SEMANTIC_ASSET_CLASS_SOURCE_MISMATCH')
        return value,{'version':'asset-read-current-scope-inheritance-v1','frame_ref':value['focus_ref'],
            'asset_class':inherited,'new_literal_change_created':False,'bank_write_authorized':False}
    result=deepcopy(value);changes=result.setdefault('changes',[]);repairs=[];deferred=[]
    matches=literal_asset_classes(prepared['message'])
    for index,kind in enumerate(kinds):
        if kind!='ASSET_POSITIONS_READ':continue
        ref='new:'+str(index)
        existing=[change for change in changes if change.get('action_ref')==ref and change.get('field')=='asset_class']
        existing += [target for selection in result.get('read_targets',[]) if selection.get('action_ref')==ref
            and selection.get('field')=='asset_class' for target in selection.get('targets',[])]
        if existing:
            for change in existing:
                declared=change.get('selected_options',[])
                literal={item[0] for item in matches}
                # Existing model-understood classes keep the normal semantic
                # span/enum contract, not a new synonym whitelist. Only reject
                # an explicit ALL widening against a sole current named class.
                if declared==['ALL'] and len(literal)==1 and 'ALL' not in literal:
                    raise ValueError('SEMANTIC_ASSET_CLASS_SOURCE_MISMATCH')
            continue
        if len({item[0] for item in matches})!=1:
            identities=selected_completed_asset_read_refs(result,prepared,index)
            if identities:
                deferred.append({'action_ref':ref,'product_identity_refs':identities,'requires_checked_identity':True})
                continue
            raise ValueError('SEMANTIC_ASSET_CLASS_REQUIRED')
        selected,start,end=matches[0]
        changes.append({'action_ref':ref,'field':'asset_class','operation':'SET',
            'source_span':{'start':start,'end':end},'source_text':prepared['message'][start:end],
            'selected_options':[selected]})
        repairs.append({'action_ref':ref,'field':'asset_class','selected_option':selected,
            'source_span':{'start':start,'end':end}})
    if deferred:return result,{'version':'asset-read-owned-identity-class-v1','deferred_classes':deferred,
        'literal_fields':repairs,'intent_changed':False,'bank_write_authorized':False}
    return result,({'version':'asset-read-literal-field-completion-v1','fields':repairs,
        'intent_changed':False,'bank_write_authorized':False} if repairs else None)
