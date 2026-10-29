"""One explicit collection on a successful pure-READ frame, not the whole DAG.

This pure shape proof identifies the source kind/field only. DB ownership,
immutable operations and fresh result catalogues remain mandatory downstream.
"""
def current_read_collection(decision, model):
    from .intelligence import READ_KINDS
    value=decision.model_dump() if hasattr(decision,'model_dump') else decision
    reference=value.get('query_reference') or {}
    if value.get('mode')!='REQUEST' or value.get('query_base')!='CURRENT' \
            or reference.get('selection') not in {'ALL','SUBSET'}:return None
    frame=next((row for row in model.get('frames',[]) if row.get('frame_ref')==value.get('focus_ref')),None)
    if not frame or frame.get('state')!='SUCCEEDED' or frame.get('write_effect_possible') is not False \
            or frame.get('paused') or not frame.get('actions') \
            or any(row.get('kind') not in READ_KINDS for row in frame['actions']):return None
    group=next((row for row in (frame.get('query_scope') or {}).get('collections',[])
        if row.get('set_ref')==reference.get('set_ref')),None)
    if not group:return None
    offered=group.get('member_refs',[])
    members=offered if reference['selection']=='ALL' else reference.get('member_refs') or value.get('entity_refs',[])
    if not members or len(members)>8 or len(members)!=len(set(members)) or not set(members)<=set(offered) \
            or set(value.get('entity_refs',[]))!=set(members):return None
    if reference.get('member_refs') and set(reference['member_refs'])!=set(members):return None
    entities={row['entity_ref']:row for row in model.get('entities',[])}
    selected=[entities.get(ref) for ref in members]
    if any(not row or row.get('frame_ref')!=frame['frame_ref'] or row.get('field')!=group.get('field') \
            or row.get('domain')!=group.get('domain') or group['field'] not in row.get('allowed_fields',[]) for row in selected):return None
    kinds={row.get('read_kind') for row in selected}
    if len(kinds)!=1:return None
    kind=next(iter(kinds));actions=value.get('action_kinds',[])
    capability=next((row for row in model.get('capabilities',[]) if row.get('kind')==kind),{})
    if kind not in READ_KINDS or kind not in {row['kind'] for row in frame['actions']} \
            or len(actions)!=len(members) or set(actions)!={kind} \
            or group['field'] not in capability.get('allowed_fields',[]):return None
    targets=[]
    for ordinal in range(len(actions)):
        changes=[row for row in value.get('changes',[]) if row.get('action_ref')=='new:'+str(ordinal)
            and row.get('field')==group['field']]
        if len(changes)!=1 or changes[0].get('operation','SET')!='SET' \
                or changes[0].get('entity_ref') not in members:return None
        targets.append({'action_ordinal':ordinal,'entity_ref':changes[0]['entity_ref']})
    if {row['entity_ref'] for row in targets}!=set(members):return None
    return {'frame_ref':frame['frame_ref'],'set_ref':group['set_ref'],'domain':group['domain'],
        'field':group['field'],'read_kind':kind,'targets':targets}
