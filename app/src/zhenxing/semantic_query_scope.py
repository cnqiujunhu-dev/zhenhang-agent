"""Current read scope and source-bound collection reference semantics.

Conversation selectors are not financial facts. Every member still goes
through the original owned result-catalog and publication revalidation.
"""
from copy import deepcopy
import re

VERSION='current-read-query-scope-v1'


def _pair_source_members(model,frame_ref,message):
    """Offer the original explicit pair for former/latter after a narrowed reply.

    The offer grants no selection: _explicit_members still validates the model's
    chosen member against the same user enumeration. Other questions retain the
    last narrowed set and never silently expand it.
    """
    groups={}
    for entity in model['entities']:
        if entity.get('frame_ref')==frame_ref:
            groups.setdefault((entity.get('domain'),entity.get('field')),[]).append(entity)
    named=set()
    for entities in groups.values():
        for e in entities:
            name=e.get('label','')
            if name and message.count(name)==1 and sum(x.get('label')==name for x in entities)==1:
                start=message.index(name)
                try:_source_text({'start':start,'end':start+len(name)},message)
                except ValueError:continue
                named.add(e['entity_ref'])
    if named:return named
    if len(re.findall(r'前者|后者|前一个|后一个|前一项|后一项|前一款|后一款|\bformer\b|\blatter\b',message,re.I))!=1:return set()
    expanded=set()
    for entities in groups.values():
        for h in reversed(model.get('history',[])):
            if h.get('role')!='user':continue
            text=h.get('content','');matched=[]
            for e in entities:
                name=e.get('label','')
                if name and text.count(name)==1 and sum(x.get('label')==name for x in entities)==1:
                    start=text.index(name)
                    try:_source_text({'start':start,'end':start+len(name)},text)
                    except ValueError:continue
                    matched.append(e['entity_ref'])
            if len(matched)>=2:
                if len(matched)==2:expanded.update(matched)
                break
    return expanded


def is_bound_current_read_request(decision,model):
    from .intelligence import READ_KINDS
    value=decision.model_dump() if hasattr(decision,'model_dump') else decision
    from .semantic_asset_scope import inherited_asset_read_class
    if inherited_asset_read_class(value,model) and not value.get('query_reference'):return True
    if value.get('mode')!='REQUEST' or value.get('query_base')!='CURRENT' \
            or (value.get('query_reference') or {}).get('selection') not in {'ALL','SUBSET'}:return False
    frame=next((frame for frame in model.get('frames',[]) if frame['frame_ref']==value.get('focus_ref')),None)
    kinds=value.get('action_kinds',[])
    legacy=bool(frame and frame.get('query_scope') and frame.get('state')=='SUCCEEDED'
        and frame.get('write_effect_possible') is False and not frame.get('paused') and kinds
        and all(kind in READ_KINDS for kind in kinds) and frame.get('actions')
        and all(action['kind'] in READ_KINDS for action in frame['actions'])
        and set(kinds)=={action['kind'] for action in frame['actions']})
    if legacy:return True
    from .semantic_read_collection import current_read_collection
    return current_read_collection(value,model) is not None


def attach_current_query_scope(offered,session=None,principal=None,thread=None,turn=None):
    model=offered['model']
    protected=[]
    from .intelligence import READ_KINDS
    selected_member_ids=None;selected_task_id=None
    if session is not None and not model.get('selected_focus_ref'):
        from sqlalchemy import select
        from .conversation_models import ChatTurn
        from .semantic_context_history import bounded_context_turns
        candidates=bounded_context_turns(session,principal,thread.id,select(ChatTurn).where(ChatTurn.thread_id==thread.id,ChatTurn.sequence<turn.sequence,
            ChatTurn.status=='ANSWERED').order_by(ChatTurn.sequence.desc()).limit(1))
        latest=candidates[0] if candidates else None
        selector=((latest.diagnostics_json or {}).get('semantic_knowledge_selection') or {}) if latest and latest.mode=='CONSULT' else {}
        from .semantic_source_bundle import SELECTOR_VERSION
        if selector.get('version')==SELECTOR_VERSION and selector.get('selected_objects'):
            from .semantic_proposal import selected_records_from_bundle
            from .contracts import DomainError
            try:rows=selected_records_from_bundle(session,principal,thread,latest,selector)
            except (DomainError,ValueError):rows=[]  # Unavailable old READ never selects new authority.
        else:rows=selector.get('selected_records',[])
        if rows:
            from .semantic_proposal import semantic_selected_object_sources_current
            if semantic_selected_object_sources_current(session,principal,rows):
                ids={(row.get('source_binding') or {}).get('resolution',{}).get('entity_ref') for row in rows}
                task_ids={(row.get('source_binding') or {}).get('catalog',{}).get('selected_task_id') for row in rows}
                if len(task_ids)==1 and ids and None not in ids:
                    selected_task_id=next(iter(task_ids));selected_member_ids=ids
    for frame in model['frames']:
        chosen=offered['frame_bindings'][frame['frame_ref']]['task_id']==selected_task_id if selected_task_id else frame.get('current')
        if not chosen or frame.get('state')!='SUCCEEDED' or frame.get('write_effect_possible') \
                or not frame.get('actions') or any(action['kind'] not in READ_KINDS for action in frame['actions']):continue
        groups={}
        paired=_pair_source_members(model,frame['frame_ref'],turn.message if turn is not None else model.get('message','')) if selected_member_ids is not None else set()
        for entity in model['entities']:
            if entity.get('frame_ref')!=frame['frame_ref']:continue
            entry=offered['entity_bindings'].get(entity['entity_ref']) or {}
            if not entry.get('actual_entity'):continue
            if paired:
                if entity['entity_ref'] not in paired:continue
            elif selected_member_ids is not None and entry['actual_entity'].get('entity_ref') not in selected_member_ids:continue
            key=(entity.get('domain'),entity.get('field'))
            groups.setdefault(key,[]).append(entity['entity_ref'])
        collections=[{'set_ref':frame['frame_ref']+':set'+str(index),'domain':domain,'field':field,'member_refs':members}
            for index,((domain,field),members) in enumerate(groups.items())]
        frame['query_scope']={'version':VERSION,'collections':collections}
        from .planning import canonical_hash
        offered['frame_bindings'][frame['frame_ref']]['frame_hash']=canonical_hash(frame)
        protected.append(frame['frame_ref'])
    return protected


def validate_scope_shape(scope,frame_ref,entities):
    if not isinstance(scope,dict) or set(scope)!={'version','collections'} or scope['version']!=VERSION \
            or not isinstance(scope['collections'],list) or len(scope['collections'])>16:
        raise ValueError('SEMANTIC_INPUT_QUERY_SCOPE_INVALID')
    seen=set()
    for group in scope['collections']:
        if not isinstance(group,dict) or set(group)!={'set_ref','domain','field','member_refs'} \
                or not isinstance(group['set_ref'],str) or not re.fullmatch(re.escape(frame_ref)+r':set\d{1,2}',group['set_ref']) \
                or group['set_ref'] in seen or not isinstance(group['member_refs'],list) or not group['member_refs'] \
                or len(group['member_refs'])!=len(set(group['member_refs'])):
            raise ValueError('SEMANTIC_INPUT_QUERY_SCOPE_INVALID')
        seen.add(group['set_ref'])
        for ref in group['member_refs']:
            entity=next((item for item in entities if item['entity_ref']==ref),None)
            if not entity or entity.get('frame_ref')!=frame_ref or entity.get('domain')!=group['domain'] or entity.get('field')!=group['field']:
                raise ValueError('SEMANTIC_INPUT_QUERY_SCOPE_INVALID')
    return deepcopy(scope)


def _source_text(span,message):
    if not isinstance(span,dict) or set(span)!={'start','end'} or type(span['start']) is not int or type(span['end']) is not int \
            or not 0<=span['start']<span['end']<=len(message):raise ValueError('SEMANTIC_QUERY_SOURCE_REQUIRED')
    from .intelligence import _source_spans
    if any(span['start']<end and span['end']>start for start,end in _source_spans(message)):
        raise ValueError('SEMANTIC_QUERY_SOURCE_UNTRUSTED')
    return message[span['start']:span['end']]


def _registered_card_tail(entity,group):
    if (group.get('domain'),group.get('field'))!=('CARDS','card_number') \
            or entity.get('domain')!='CARDS' or entity.get('field')!='card_number' \
            or entity.get('read_kind')!='CARD_STATUS' or 'card_last4' not in entity.get('allowed_fields',[]):
        return None
    label=entity.get('label','')
    full=re.fullmatch(r'(?:zhenhang|xiaozhao)([0-9a-fA-F]{8})',label)
    masked=re.fullmatch(r'(?:借记卡|信用卡)?尾号([0-9a-fA-F]{4})',label)
    return full[1][-4:] if full else masked[1] if masked else None


def _current_card_tail_slice(ref,group,prepared,span,raw):
    """Exact numeric slice inside THIS trusted tail phrase, not any bare hex."""
    if not isinstance(span,dict) or re.fullmatch(r'[0-9a-fA-F]{4}',raw) is None:
        return False
    entities={row['entity_ref']:row for row in prepared['entities']}
    tail=_registered_card_tail(entities.get(ref,{}),group)
    if tail is None or tail.casefold()!=raw.casefold():return False
    same=[member for member in group['member_refs']
        if (_registered_card_tail(entities[member],group) or '').casefold()==tail.casefold()]
    if same!=[ref]:return False
    pattern=r'(?:尾号|末四位|(?:card\s+)?ending(?:\s+in)?)\s*(?P<tail>'+re.escape(tail)+r')(?![A-Za-z0-9_])'
    for match in re.finditer(pattern,prepared['message'],re.I):
        if match.span('tail')!=(span['start'],span['end']):continue
        _source_text({'start':match.start(),'end':match.end()},prepared['message'])
        return True
    return False


def _explicit_members(refs,group,prepared,span=None):
    text=_source_text(span or {'start':0,'end':len(prepared['message'])},prepared['message'])
    entities={row['entity_ref']:row for row in prepared['entities']}
    from .customer_query_layout import customer_display_name
    names={ref:{entities[ref].get('label',''),customer_display_name(entities[ref].get('label',''))}
        for ref in group['member_refs']}
    for ref in group['member_refs']:
        entity=entities[ref]
        if _current_card_tail_slice(ref,group,prepared,span,text):
            # Keep the model's exact 4HEX span/text. The registered prefix in
            # the SAME trusted message proves what this numeric slice names.
            # It never becomes a literal full card number or WRITE selector.
            names[ref].add(text)
        if group.get('domain')=='MERCHANTS' and group.get('field')=='merchant_product_id':
            names[ref].update(entity.get('read_aliases',[]))
        if group.get('domain')=='BILLS' and group.get('field')=='category_filter':
            names[ref].update(entity.get('registered_options',[]))
        if (group.get('domain'),group.get('field'))==('SUBSCRIPTIONS','merchant'):
            # Catalog aliases identify the same registered service. They do
            # not supply price, entitlement, availability or write authority.
            from .subscription_catalog_data import MERCHANT_ALIASES
            names[ref].update(alias for alias,target in MERCHANT_ALIASES.items()
                if target in names[ref])
        if (tail:=_registered_card_tail(entity,group)) is not None:
            # A published owned card can be named by its explicitly spoken
            # tail, including English. Colliding offered tails remain
            # ambiguous below; the normal source/owner/card resolver still runs.
            names[ref].update(match[0] for match in re.finditer(
                r'(?:尾号|末四位|(?:card\s+)?ending(?:\s+in)?)\s*'+re.escape(tail)+r'(?![A-Za-z0-9_])',text,re.I))
        # This new, closed source type publishes one exact selected card even
        # when its bill contains no rows. The registered label's tail is a
        # navigation alias only; every selected identity is independently
        # source/owner checked before a new READ. A colliding tail is not an
        # alias for either card and cannot silently choose the first match.
        from .bill_query_context import IDENTITY_KIND
        if entity.get('kind')==IDENTITY_KIND and (group.get('domain'),group.get('field'))==('CARDS','card_number'):
            match=re.fullmatch(r'(?:借记卡|信用卡)尾号([0-9a-fA-F]{4}) · 查询中的卡片身份',entity.get('label',''))
            if match:
                tail=match[1]
                same=[row for row in entities.values() if row.get('frame_ref')==entity.get('frame_ref')
                    and row.get('kind')==IDENTITY_KIND and row.get('field')=='card_number'
                    and re.fullmatch(r'(?:借记卡|信用卡)尾号'+re.escape(tail)+r' · 查询中的卡片身份',row.get('label',''),re.I)]
                if len(same)==1:
                    names[ref].update(alias[0] for alias in re.finditer(
                        r'(?:尾号|末四位|(?:card\s+)?ending(?:\s+in)?)\s*'+re.escape(tail)+r'(?![A-Za-z0-9_])',text,re.I))
    # Names shown on the actual query cards are registered navigation aliases,
    # not different objects. Activity noun phrases additionally admit a unique
    # registered prefix + their resource head (e.g. tier + activity), without
    # requiring every internal qualifier in the full registered name.
    heads={('BENEFITS','benefit_name'):('活动',),('CARBON','reward_name'):('权益券','权益','券')}.get((group.get('domain'),group.get('field')),())
    if heads:
        labels={ref:customer_display_name(entities[ref].get('label','')) for ref in group['member_refs']}
        for ref,label in labels.items():
            for head in heads:
                if not label.endswith(head):continue
                stem=label[:-len(head)]
                for size in range(2,len(stem)+1):
                    prefix=stem[:size]
                    if sum(other.startswith(prefix) for other in labels.values())==1:
                        names[ref].add(prefix+head)
    fold=(lambda name:name.casefold()) if (group.get('domain'),group.get('field')) in {
        ('MERCHANTS','merchant_product_id'),('SUBSCRIPTIONS','merchant')} else (lambda name:name)
    owners={fold(name):{ref for ref,aliases in names.items() if any(fold(candidate)==fold(name) for candidate in aliases)} for aliases in names.values() for name in aliases if name}
    expressed=set()
    for ref in group['member_refs']:
        item=entities[ref]
        if any(name and owners[fold(name)]=={ref} and fold(name) in fold(text) for name in names[ref]):
            expressed.add(ref);continue
        ordinals=re.findall(r'第\s*([一二两三四五六七八九十\d]+)\s*(?:个|项|张|笔|款)',text)
        from .intelligence import _chinese_integer
        if any((int(raw) if raw.isdigit() else _chinese_integer(raw))==item.get('ordinal') for raw in ordinals):expressed.add(ref)
    if expressed:return expressed==set(refs)
    # Former/latter is an index into an explicit two-object user enumeration,
    # not an inferred intent or the ordinal of a separate tool's one-row list.
    # The model has already selected the offered set and member. Prove that
    # selection against the latest bounded user enumeration, never reorder it.
    tokens=re.findall(r'前者|后者|前一个|后一个|前一项|后一项|前一款|后一款|\bformer\b|\blatter\b',text,re.I)
    if len(tokens)!=1 or len(refs)!=1 or re.search(r'不要|不是|别|除了|\bnot\b',text,re.I):return False
    side=0 if tokens[0].casefold() in {'前者','前一个','前一项','前一款','former'} else 1
    for history in reversed(prepared.get('history',[])):
        if history.get('role')!='user':continue
        message=history.get('content','');positions=[]
        for ref,aliases in names.items():
            matches=[(message.find(name),name) for name in aliases if name and message.count(name)==1 and owners.get(fold(name))=={ref}]
            if matches:
                at,name=min(matches)
                _source_text({'start':at,'end':at+len(name)},message)
                positions.append((at,ref))
        if len(positions)>=2:
            return len(positions)==2 and sorted(positions)[side][1]==refs[0]
    return False


def _complete_named_catalog_set(value,prepared,current):
    """Close an explicit multi-product CONSULT against the current read catalog.

    No arbitrary search text, write kind or new object is synthesized. A partial
    public retrieval selector cannot discard the other explicitly named item.
    """
    if value.get('entity_refs') or value.get('query_reference') or not value.get('question_aspects') \
            or value.get('action_kinds') or value.get('changes') or value.get('query_base')=='INDEPENDENT' \
            or current.get('state')!='SUCCEEDED' or current.get('write_effect_possible') is not False or current.get('paused'):
        return
    from .intelligence import READ_KINDS
    if not current.get('actions') or any(a['kind'] not in READ_KINDS for a in current['actions']):return
    entities={r['entity_ref']:r for r in prepared['entities']};candidates=[]
    message=prepared['message']
    for group in current['query_scope']['collections']:
        if (group.get('domain'),group.get('field'))!=('MERCHANTS','merchant_product_id'):continue
        selected=[ref for ref in group['member_refs'] if entities[ref].get('label') and entities[ref]['label'] in message]
        if len(selected)>=2 and _explicit_members(selected,group,prepared):candidates.append((group,selected))
    if len(candidates)!=1:return
    group,selected=candidates[0]
    retrieval=value.get('retrieval_query')
    if retrieval:
        from .semantic_retrieval_query import public_retrieval_subjects
        subject=next((r for r in public_retrieval_subjects() if r['ref']==retrieval.get('subject_ref')),None)
        if not subject or not subject['ref'].startswith('merchant:') or subject['label'] not in {entities[r]['label'] for r in selected}:return
    value.update(entity_refs=selected,focus_ref=current['frame_ref'],query_reference={
        'selection':'SUBSET','set_ref':group['set_ref'],'member_refs':selected,'data_kind':'RECORDED',
        'source_span':{'start':0,'end':len(message)}})
    value.pop('retrieval_query',None)


def normalize_query_scope(value,prepared):
    frames=prepared['frames'];by_ref={frame['frame_ref']:frame for frame in frames}
    current=next((frame for frame in frames if frame.get('query_scope')),None)
    if not current:return value
    value=deepcopy(value)
    from .semantic_bill_query_context import complete_bill_query_context
    if complete_bill_query_context(value,prepared):
        # Verify before focus remapping. This ALL names the original complete
        # query scope, not N writable transaction identities. Keep the whole
        # receipt/reference and let the existing READ revision compiler work.
        return value
    if value['mode']=='REQUEST' and value.get('query_base')=='CURRENT' and not value.get('query_reference'):
        from .intelligence import READ_KINDS
        if value.get('action_kinds')==[action['kind'] for action in current['actions']] \
                and all(kind in READ_KINDS for kind in value['action_kinds']) and not value.get('read_targets'):
            for change in value.get('changes',[]):
                match=re.fullmatch(r'new:(\d+)',change['action_ref'])
                if not match or int(match[1])>=len(current['actions']):raise ValueError('SEMANTIC_QUERY_BASE_ACTION_INVALID')
                change['action_ref']=current['actions'][int(match[1])]['action_ref']
            value.update(mode='REVISE',focus_ref=current['frame_ref'],dependencies=[])
    # A continuation is an incremental change to the current verified READ.
    # An explicit UI target or source-backed older scope is an independent choice.
    if value['mode'] in {'CONTINUE','REVISE'}:
        if value.get('query_base')=='INDEPENDENT':raise ValueError('SEMANTIC_INDEPENDENT_QUERY_REQUIRES_REQUEST')
        target=by_ref.get(value.get('focus_ref'))
        from .intelligence import READ_KINDS
        if target and target.get('state')=='SUCCEEDED' and target.get('actions') \
                and all(action['kind'] in READ_KINDS for action in target['actions']) \
                and [a['kind'] for a in target['actions']]==[a['kind'] for a in current['actions']]:
            explicit=prepared.get('selected_focus_ref')
            if explicit:value['focus_ref']=explicit
            elif value.get('query_base')=='EXPLICIT':
                text=_source_text(value.get('query_base_source'),prepared['message'])
                matching=[frame for frame in frames if frame.get('label') and frame['label'] in text]
                if target['frame_ref']!=current['frame_ref'] and (len(matching)!=1 or matching[0]['frame_ref']!=target['frame_ref']):
                    raise ValueError('SEMANTIC_QUERY_BASE_SOURCE_AMBIGUOUS')
            else:
                old_ref=value['focus_ref'];new_ref=current['frame_ref']
                old_actions={action['action_ref']:index for index,action in enumerate(target['actions'])}
                for change in value.get('changes',[]):
                    if change['action_ref'] in old_actions:
                        change['action_ref']=current['actions'][old_actions[change['action_ref']]]['action_ref']
                value['focus_ref']=new_ref
        if value.get('query_reference'):return compile_reference_read(value,prepared,current)
        return value
    if value['mode']=='REQUEST' and value.get('query_reference'):
        return compile_reference_read(value,prepared,current)
    if value['mode']!='CONSULT':return value
    _complete_named_catalog_set(value,prepared,current)
    groups=[group for frame in frames for group in (frame.get('query_scope') or {}).get('collections',[])]
    reference=value.get('query_reference')
    if reference and value.get('question_aspects')==['ACCOUNT_RELATIONSHIP'] and value.get('entity_refs'):
        # A facility plus its per-currency facts are complementary projections,
        # not members of one selectable currency/product collection. Preserve
        # every explicit model-selected fact; drop only the redundant set marker.
        group=next((g for g in groups if g['set_ref']==reference.get('set_ref')),None)
        selected=[next((e for e in prepared['entities'] if e['entity_ref']==ref),None) for ref in value['entity_refs']]
        if group and (group.get('domain'),group.get('field'))==('CREDIT','credit_facility') \
                and reference.get('selection') in {'ALL','SUBSET'} and reference.get('data_kind','RECORDED')=='RECORDED' \
                and by_ref.get(value.get('focus_ref'),{}).get('state')=='SUCCEEDED' \
                and by_ref.get(value.get('focus_ref'),{}).get('write_effect_possible') is False \
                and set(reference.get('member_refs') or group['member_refs'])==set(group['member_refs']) \
                and set(group['member_refs'])<set(value['entity_refs']) \
                and all(e and e.get('frame_ref')==value.get('focus_ref') and e.get('domain')=='CREDIT'
                    and e.get('read_kind')=='CREDIT_VIEW' and e.get('field') in {'credit_facility','credit_currency_debt'} for e in selected):
            value.pop('query_reference',None)
            return value
    if reference and reference.get('selection')=='INDEPENDENT':
        _source_text(reference.get('source_span'),prepared['message'])
        retrieval=value.get('retrieval_query') or {}
        if retrieval.get('subject_ref','').startswith(('subscription:','wealth:')) and not any(
                source.get('source')=='CURRENT_MESSAGE' for source in retrieval.get('sources',[])):
            raise ValueError('SEMANTIC_QUERY_SET_NARROWING_UNPROVED')
        return value
    if reference:
        group=next((group for group in groups if group['set_ref']==reference.get('set_ref')),None)
        if not group:raise ValueError('SEMANTIC_QUERY_SET_NOT_OFFERED')
    else:
        refs=value.get('entity_refs',[])
        # A topic is a knowledge-retrieval scope, not a selected owned object
        # or a question aspect. Preserve old public consultations instead of
        # promoting a topic-only decision into private recorded charges.
        if not refs and not value.get('question_aspects'):return value
        candidates=[group for group in groups if refs and set(refs)<=set(group['member_refs'])]
        if not candidates:
            topics=set(value.get('knowledge_topics',[]))
            candidates=[group for group in current['query_scope']['collections'] if group['domain'] in topics]
        if len(candidates)!=1:return value
        # A registered public product explicitly named now is a new target;
        # a history-only product reference cannot erase an active collection.
        retrieval=value.get('retrieval_query') or {}
        if not refs and any(source.get('source')=='CURRENT_MESSAGE' for source in retrieval.get('sources',[])):
            return value
        group=candidates[0]
        subset=bool(refs) and _explicit_members(refs,group,prepared)
        reference={'set_ref':group['set_ref'],'selection':'SUBSET' if subset else 'ALL',
            'member_refs':refs if subset else [],'data_kind':'RECORDED'}
        if subset:reference['source_span']={'start':0,'end':len(prepared['message'])}
        value['query_reference']=reference
    refs=value.get('entity_refs',[])
    if len(refs)!=len(set(refs)) or any(ref not in {row['entity_ref'] for row in prepared['entities']} for ref in refs):
        raise ValueError('SEMANTIC_ENTITY_NOT_OFFERED')
    if refs and not set(refs)<=set(group['member_refs']):
        raise ValueError('SEMANTIC_QUERY_SET_NARROWING_UNPROVED')
    if reference.get('member_refs') and (len(reference['member_refs'])!=len(set(reference['member_refs']))
            or not set(reference['member_refs'])<=set(group['member_refs'])
            or reference['selection']=='ALL' and set(reference['member_refs'])!=set(group['member_refs'])):
        raise ValueError('SEMANTIC_QUERY_SET_NARROWING_UNPROVED')
    if reference['selection']=='SUBSET' and refs and reference.get('member_refs') \
            and set(refs)!=set(reference['member_refs']):
        # Both lists were proved members of this offered group. Conflicting
        # declarations must be reinterpreted, not silently dropped/widened.
        raise ValueError('SEMANTIC_QUERY_REFERENCE_MEMBERS_CONFLICT')
    if reference['selection']=='ALL':members=group['member_refs']
    else:
        members=reference.get('member_refs') or value.get('entity_refs',[])
        if not members or not set(members)<=set(group['member_refs']):
            raise ValueError('SEMANTIC_QUERY_SET_NARROWING_UNPROVED')
        if set(members)==set(group['member_refs']):
            # A SUBSET representation of the entire set does not narrow it.
            reference.update(selection='ALL',member_refs=[])
            members=list(group['member_refs'])
        elif not _explicit_members(members,group,prepared,reference.get('source_span')):
            # A valid in-bounds span may clip a registered name. Re-anchor
            # only when the full trusted CURRENT message proves exactly the
            # same selected member set; never use history or change members.
            full={'start':0,'end':len(prepared['message'])}
            if not _explicit_members(members,group,prepared,full):
                raise ValueError('SEMANTIC_QUERY_SET_NARROWING_UNPROVED')
            reference.update(member_refs=list(members),source_span=full)
        else:reference['member_refs']=list(members)
    value['entity_refs']=list(members)
    if group['domain']=='SUBSCRIPTIONS' and reference.get('data_kind','RECORDED')=='RECORDED':
        value['question_aspects']=['CHARGE' if aspect=='PRICE' else aspect for aspect in value.get('question_aspects',[])]
        # Recorded charges are not a lookup in the public product-price index.
        value.pop('retrieval_query',None)
    if (group['domain'],group['field'])==('BILLS','category_filter') and reference.get('data_kind','RECORDED')=='RECORDED':
        # A recorded category amount is spending, never a public unit price.
        value['question_aspects']=list(dict.fromkeys('CHARGE' if aspect=='PRICE' else aspect
            for aspect in value.get('question_aspects',[])))
    return value


def compile_reference_read(value,prepared,current):
    """A set used by READ requests has a real fresh-read consumer.

    CONSULT keeps the already-read facts; this path instead compiles precise
    owned identities into new read nodes. Neither path authorizes a write.
    """
    from .intelligence import READ_KINDS
    frames={row['frame_ref']:row for row in prepared['frames']}
    base=frames.get(value.get('focus_ref')) or current
    kinds=value.get('action_kinds') if value['mode']=='REQUEST' else [a['kind'] for a in base['actions']]
    if value['mode']=='REQUEST' and not kinds:
        offered_sets={group['set_ref'] for group in (current.get('query_scope') or {}).get('collections',[])}
        inherited=[action['kind'] for action in base.get('actions',[])]
        if value.get('focus_ref')!=current['frame_ref'] or value.get('dependencies') not in ([],[[]]) \
                or base.get('state')!='SUCCEEDED' or base.get('write_effect_possible') is not False or base.get('paused') is True \
                or value['query_reference'].get('selection') not in {'ALL','SUBSET'} \
                or value['query_reference'].get('set_ref') not in offered_sets or not inherited or len(set(inherited))!=1:
            raise ValueError('SEMANTIC_QUERY_REFERENCE_READ_ONLY')
        kinds=[inherited[0]]  # Unique kind of this explicit successful READ scope.
    if not kinds or any(kind not in READ_KINDS for kind in kinds) or len(set(kinds))!=1:
        raise ValueError('SEMANTIC_QUERY_REFERENCE_READ_ONLY')
    if value['query_reference']['selection']=='INDEPENDENT':
        if value['mode']!='REQUEST':raise ValueError('SEMANTIC_INDEPENDENT_QUERY_REQUIRES_REQUEST')
        _source_text(value['query_reference'].get('source_span'),prepared['message'])
        return value
    proxy={**value,'mode':'CONSULT','action_kinds':[],'dependencies':[],'changes':[],'read_targets':[]}
    selected=normalize_query_scope(proxy,prepared)
    reference=selected['query_reference'];members=selected.get('entity_refs',[])
    groups=[group for frame in prepared['frames'] for group in (frame.get('query_scope') or {}).get('collections',[])]
    group=next((group for group in groups if group['set_ref']==reference.get('set_ref')),None)
    entities={row['entity_ref']:row for row in prepared['entities']}
    kind=kinds[0]
    if not group or not members or any(entities[ref].get('read_kind')!=kind for ref in members):
        raise ValueError('SEMANTIC_QUERY_REFERENCE_READ_KIND_MISMATCH')
    # Supplied set syntax is redundant, not permission to discard an invalid
    # field/source or silently replace a different selection with this set.
    selections=value.get('read_targets',[])
    if len(selections)>1:raise ValueError('SEMANTIC_QUERY_REFERENCE_TARGET_CONFLICT')
    for selection in selections:
        valid_refs={'new:'+str(index) for index in range(len(kinds))} | {
            row['action_ref'] for row in base.get('actions',[]) if row['kind']==kind}
        items=selection.get('targets',[])
        identity_alias=(kind=='CARD_STATUS' and (group.get('domain'),group.get('field'))==('CARDS','card_number')
            and selection.get('field')=='card_last4' and all(_registered_card_tail(entities[ref],group) is not None
                and selection['field'] in entities[ref].get('allowed_fields',[]) for ref in members))
        if selection.get('action_ref') not in valid_refs or selection.get('field')!=group['field'] and not identity_alias \
                or len(items)!=len(members) or {row.get('entity_ref') for row in items}!=set(members):
            raise ValueError('SEMANTIC_QUERY_REFERENCE_TARGET_CONFLICT')
        for item in items:
            raw=_source_text(item.get('source_span'),prepared['message'])
            if item.get('source_text') not in (None,raw) or item.get('selected_options') \
                    or reference['selection']!='ALL' and not _explicit_members([item['entity_ref']],group,prepared,item['source_span']):
                raise ValueError('SEMANTIC_QUERY_REFERENCE_TARGET_SOURCE_INVALID')
    scoped_changes=[change for change in value.get('changes',[]) if change['field']==group['field']]
    canonical_targets=[('new:'+str(index),ref) for index,ref in enumerate(members)]
    if value['mode']=='REQUEST' and value.get('query_base')=='CURRENT' and value.get('focus_ref')==current['frame_ref'] \
            and not value.get('read_targets') and len(kinds)==len(members) \
            and [(change['action_ref'],change.get('entity_ref')) for change in scoped_changes]==canonical_targets:
        return {**value,'entity_refs':members,'query_reference':reference}
    if len(kinds)!=1 and value['mode']=='REQUEST':raise ValueError('SEMANTIC_QUERY_REFERENCE_ACTION_AMBIGUOUS')
    capability=next(row for row in prepared['capabilities'] if row['kind']==kind)
    target_field=selections[0]['field'] if selections else group['field']
    if target_field not in capability['allowed_fields']:raise ValueError('SEMANTIC_QUERY_REFERENCE_FIELD_INVALID')
    span=reference.get('source_span') or {'start':0,'end':len(prepared['message'])}
    _source_text(span,prepared['message'])
    changes=[]
    for change in value.get('changes',[]):
        if change['field']==group['field']:
            if len(members)!=1 or change.get('entity_ref')!=members[0] or change.get('operation','SET')!='SET':
                raise ValueError('SEMANTIC_QUERY_REFERENCE_SCALAR_CONFLICT')
            continue
        changes.append({**change,'action_ref':'new:0'})
    # Inherited non-target filters are reconstructed from the checked base
    # frame by semantic_proposal, not copied from model-authored metadata.
    original_targets={item['entity_ref']:item for item in selections[0]['targets']} if selections else {}
    return {**value,'mode':'REQUEST','action_kinds':[kind],'dependencies':[[]],
        'focus_ref':current['frame_ref'],'query_base':'CURRENT','query_reference':reference,
        'entity_refs':members,'changes':changes,'read_targets':[{'action_ref':'new:0','field':target_field,
            'targets':[deepcopy(original_targets[ref]) if ref in original_targets else {'entity_ref':ref,'source_span':span} for ref in members]}]}
