"""Owned, bounded frames offered to cloud semantics; never an intent classifier."""
from copy import deepcopy
from sqlalchemy import select
from .contracts import DomainError
from .conversation_models import ChatTurn
from .dialogue_state import dialogue_actor_scope,ACTION_ALLOWED_FIELDS
from .intelligence import IntentProposal,READ_KINDS
from .models import Task
from .runtime_models import RuntimeRecord,PlanSnapshot,RuntimeOperation
from .planning import canonical_hash


def _completed_bill_read_hint(session,principal,task,record,snapshot,proposal,operations):
    """Navigation of one completed owned bill READ, not a writable date slot.

    Use the checked original/displayed query result, never the current clock,
    prior assistant text, task summary or the latest user's date wording.
    Missing scope leaves the legacy frame projection unchanged.
    """
    if task.state == 'SUCCEEDED' and snapshot and len(proposal.actions) > 1 \
            and all(action.kind == 'BILL_ANALYSIS' for action in proposal.actions):
        from .bill_query_context import completed_multi_bill_scope
        shared = completed_multi_bill_scope(session, principal, task)
        if shared is not None:
            return {'label': '已完成的多卡账单查询 · 各卡原统计期间一致',
                'read_scope': {key: shared[key] for key in ('period_start', 'period_end')} | {'read_kind': 'BILL_ANALYSIS'}}
    if task.state!='SUCCEEDED' or not snapshot or [action.kind for action in proposal.actions]!=['BILL_ANALYSIS'] \
            or any(op.action_kind not in READ_KINDS for op in operations) \
            or (record.metadata_json or {}).get('family_origin') \
            or (record.metadata_json or {}).get('family_context_origin') \
            or getattr(principal,'family_context',None):
        return None
    from .permissions import require_banking_actor
    if require_banking_actor(session,principal)=='FAMILY_HELPER':return None
    from .models import EvidenceSnapshot
    active=[op for op in operations if op.active==1]
    if len(active)!=1 or active[0].action_kind!='BILL_ANALYSIS' or active[0].status!='SUCCEEDED':return None
    child=session.get(Task,active[0].child_task_id) if active[0].child_task_id else None
    child_record=session.get(RuntimeRecord,child.id) if child else None
    if not child or (child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
            or child.state!='SUCCEEDED' or not child_record or child_record.root_task_id!=task.id \
            or child_record.kind!='CHILD' or child_record.metadata_json.get('operation_id')!=active[0].id:
        return None
    evidence_ids=[node.get('evidence_id') for node in (child.plan_json or {}).get('nodes',[])
        if isinstance(node,dict) and node.get('tool_name')=='bill.analyze.v1']
    evidence=session.get(EvidenceSnapshot,evidence_ids[0]) if len(evidence_ids)==1 and evidence_ids[0] else None
    if not evidence or evidence.task_id!=child.id or evidence.sandbox_id!=principal.sandbox_id \
            or any(evidence.payload_json.get(key)!=child.analysis_json.get(key) for key in ('period_start','period_end')):
        return None
    from .owned_user_dialogue import _bill_result_scope
    try:scope=_bill_result_scope(session,principal,task)
    except DomainError as error:
        if error.code.startswith('CUSTOMER_READ_') or error.status_code in {401,403}:raise
        return None
    except (ValueError,TypeError,KeyError):return None
    if not isinstance(scope,dict) or scope.get('action')!='BILL_ANALYSIS' \
            or scope.get('origin_task_id')!=task.id \
            or scope.get('origin_analysis_hash')!=canonical_hash(child.analysis_json) \
            or scope.get('source_kind') not in {'ORIGINAL_QUERY_RESULT','OWNED_SUPPLEMENTARY_READ'}:
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    start,end=scope.get('period_start'),scope.get('period_end')
    if not isinstance(start,str) or not isinstance(end,str) or len(start)>64 or len(end)>64:return None
    try:
        left,right=datetime.fromisoformat(start),datetime.fromisoformat(end)
        if left.tzinfo is None or right.tzinfo is None or left>=right:return None
        local_left,local_right=left.astimezone(ZoneInfo('Asia/Shanghai')),right.astimezone(ZoneInfo('Asia/Shanghai'))
    except (ValueError,TypeError):return None
    if local_left.day==local_right.day==1 \
            and all(value==0 for value in (local_left.hour,local_left.minute,local_left.second,local_left.microsecond,
                local_right.hour,local_right.minute,local_right.second,local_right.microsecond)) \
            and (local_right.year*12+local_right.month)-(local_left.year*12+local_left.month)==1:
        label=f"已完成的账单查询 · 实际统计期间{local_left.year}年{local_left.month}月"
    else:
        label='已完成的账单查询 · 实际统计期间'+local_left.strftime('%Y-%m-%d %H:%M')+'至'+local_right.strftime('%Y-%m-%d %H:%M')+'（结束时刻不含）'
    return {'label':label,'read_scope':{'read_kind':'BILL_ANALYSIS','period_start':start,'period_end':end}}


def semantic_context(session,principal,thread,turn):
    from .permissions import require_banking_actor
    from .conversation_turn_state import _task_epoch_current
    from .semantic_history import semantic_history
    from .semantic_context_budget import budget_semantic_context
    from .semantic_router import field_options_for
    from .capability_catalog import catalog,customer_directory
    role=require_banking_actor(session,principal)
    if (thread.user_id,thread.sandbox_id)!=(principal.user_id,principal.sandbox_id) or thread.status!='ACTIVE' \
        or (turn.user_id,turn.sandbox_id)!=(principal.user_id,principal.sandbox_id) or turn.thread_id!=thread.id:
        raise DomainError('SEMANTIC_CONTEXT_NOT_OWNED','会话不属于当前身份。',404)
    _scope,scope_hash=dialogue_actor_scope(session,principal)
    entries=catalog(role)['capabilities']
    capabilities=[{**entry,'allowed_fields':sorted(ACTION_ALLOWED_FIELDS.get(entry['kind'],set())),
        'field_options':field_options_for(ACTION_ALLOWED_FIELDS.get(entry['kind'],set()))} for entry in entries]
    # New settlement proposals have two distinct business identities: the
    # installment being settled and the debit instrument paying it. Its
    # credit card is derived and verified from that installment by the domain.
    # Keep legacy fields in ACTION_ALLOWED_FIELDS and stored frames for old
    # receipt validation; do not offer ambiguous target-card aliases anew.
    for capability in capabilities:
        if capability['kind']=='CREDIT_SETTLE_INSTALLMENT':
            capability['allowed_fields']=['installment_id','source_card','source_currency']
            capability['field_options']=field_options_for(capability['allowed_fields'])
    history=[];history_groups=[];history_diagnostics={}
    if turn.context_policy!='INDEPENDENT':
        history,history_groups,history_diagnostics=semantic_history(session,principal,thread,turn,
            role=role,actor_scope_hash=scope_hash,with_scope_bindings=True)
    history_scope_bindings=history_diagnostics.pop('_history_read_scope_bindings',[])
    model={'message':turn.message,'role':role,'capabilities':capabilities,'frames':[],'entities':[],
        'history':history,
        'locale':(turn.diagnostics_json or {}).get('reply_language','zh-CN')}
    from .semantic_read_selector_contract import VERSION as SELECTOR_CONTRACT_VERSION
    model['read_selector_contract']=SELECTOR_CONTRACT_VERSION
    if history_diagnostics.get('history_unavailable_source_count'):
        model['source_bundle_unavailable']={'version':'source-bundle-availability-v1','status':'SOURCE_UNAVAILABLE',
            'read_kinds':[],'bank_write_authorized':False}
    if turn.context_policy!='INDEPENDENT':
        # Extract only the finite marker, not an old turn's potentially huge
        # source/diagnostic JSON. This hint never removes a financial frame.
        marker=session.scalar(select(ChatTurn.diagnostics_json['source_bundle_unavailable']).where(
            ChatTurn.thread_id==thread.id,ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id,
            ChatTurn.sequence<turn.sequence,ChatTurn.diagnostics_json['dialogue_actor_scope_hash'].as_string()==scope_hash,
            ChatTurn.diagnostics_json['source_bundle_unavailable'].as_string().is_not(None))
            .order_by(ChatTurn.sequence.desc()).limit(1))
        if isinstance(marker,dict):
            model['source_bundle_unavailable']=deepcopy(marker)
    from .semantic_retrieval_query import public_retrieval_wire_subjects
    model['retrieval_subjects']=public_retrieval_wire_subjects()
    offered={'schema_version':'semantic-offered-context-v2','model':model,'frame_bindings':{},'entity_bindings':{},'catalog_pool':{},
        'actor_scope_hash':scope_hash,'thread_id':thread.id,'thread_version':thread.version,'turn_id':turn.id,
        'message_hash':canonical_hash({'message':turn.message}),'directory':customer_directory(role,model['locale'])}
    if turn.context_policy=='INDEPENDENT':
        offered['context_budget_diagnostics']=budget_semantic_context(offered,history_groups)
        offered['context_hash']=canonical_hash({'scope':scope_hash,'frames':{},'entities':{},'catalog_pool':{}})
        return offered
    from .conversation_memory import build_memory_context
    memory_context=build_memory_context(session,principal,thread,turn)
    model['memory']=memory_context['memory']
    offered['memory_bindings']=memory_context['memory_bindings']
    offered['memory_diagnostics']=memory_context['memory_diagnostics']
    from .semantic_materials import build_analysis_material_catalog
    # Analysis material is a user-owned original message, not a successful
    # tool result or a bank fact. Reserve a slot before projecting old results
    # so a long result list cannot hide the user's quoted risk message.
    material_catalog=build_analysis_material_catalog(session,principal,thread,turn)
    material_slots=min(len(material_catalog),1)
    from .recipient_choices import reserved_slots
    reserved_entity_slots = material_slots + reserved_slots(session, thread, turn)
    receipts=list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id==thread.id,
        ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id,
        ChatTurn.sequence<turn.sequence,ChatTurn.status=='ANSWERED',ChatTurn.task_id.is_not(None))
        .order_by(ChatTurn.sequence.desc()).limit(100)))
    seen=set();ordered=[]
    for receipt in receipts:
        if receipt.task_id in seen:continue
        seen.add(receipt.task_id);ordered.append(receipt)
    from .semantic_task_focus import selected_task_id, bind_selected_focus
    navigation_task_id = selected_task_id(turn) or thread.current_task_id
    ordered.sort(key=lambda row:row.task_id!=navigation_task_id)
    protected_frame_refs=set();unavailable_read_sources=[];unavailable_read_diagnostics=[]
    for receipt in ordered[:32]:
        task=session.get(Task,receipt.task_id)
        if not task or (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) or not _task_epoch_current(session,principal,task):continue
        record=session.get(RuntimeRecord,task.id)
        if not record or record.kind!='PARENT':continue
        if receipt.diagnostics_json.get('input_redacted') or receipt.diagnostics_json.get('dialogue_actor_scope_hash') not in (None,scope_hash):continue
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version)) if record.plan_version else None
        proposal=None
        if snapshot:
            if snapshot.plan_hash!=task.plan_hash or canonical_hash(snapshot.plan_json)!=snapshot.plan_hash or canonical_hash(task.plan_json)!=task.plan_hash:continue
            try:proposal=IntentProposal.model_validate(snapshot.proposal_json)
            except (ValueError,TypeError):continue
            try:
                from .conversation_provenance import validate_current_continuation
                if record.metadata_json.get('cloud_semantic_intent'):
                    from .semantic_proposal import validate_semantic_source_snapshot
                    navigation=validate_semantic_source_snapshot(session,principal,task,record.metadata_json,snapshot)
                    proposal=IntentProposal.model_validate(navigation['proposal_json'])
                else:
                    validate_current_continuation(session,task)
                if not any(record.metadata_json.get(key) for key in ('cloud_semantic_intent','owned_user_dialogue','dialogue_slot_update','dialogue_result_reference')) \
                    and (record.metadata_json.get('conversation_context') or {}).get('authority')!='SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS':
                    origin_id=(record.metadata_json.get('conversation_context') or {}).get('turn_id',receipt.id)
                    origin=session.get(ChatTurn,origin_id)
                    if not origin or origin.thread_id!=thread.id or origin.message!=task.message or origin.status!='ANSWERED' \
                        or (origin.user_id,origin.sandbox_id)!=(principal.user_id,principal.sandbox_id):continue
                    from .intelligence import ground_proposal
                    if ground_proposal(proposal,task.message).actions!=proposal.actions:continue
            except (DomainError,ValueError,TypeError,KeyError):continue
        elif record.metadata_json.get('cloud_semantic_intent'):
            try:proposal=IntentProposal.model_validate(record.metadata_json['cloud_semantic_intent']['expected_proposal'])
            except (ValueError,TypeError,KeyError):continue
        if proposal is None:continue
        pending=(record.metadata_json.get('semantic_pause') or {}).get('pending_binding')
        if pending:
            from .semantic_proposal import validate_paused_semantic_intent
            try:
                validate_paused_semantic_intent(session,principal,task,pending)
                public_proposal=IntentProposal.model_validate(pending['expected_proposal'])
            except (DomainError,ValueError,TypeError,KeyError):continue
        else:public_proposal=proposal
        operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id)))
        effects=task.state in {'AUTHORIZED','EXECUTING','UNKNOWN','PARTIAL'} or any(op.action_kind not in READ_KINDS and
            (op.status in {'SUCCEEDED','UNKNOWN','AUTHORIZED','RUNNING'} or op.child_task_id and
            (child:=session.get(Task,op.child_task_id)) and child.state in {'SUCCEEDED','UNKNOWN','EXECUTING','AUTHORIZED'}) for op in operations)
        frame_ref='f'+str(len(model['frames']))
        actions=[]
        for index,action in enumerate(public_proposal.actions):
            public_slots=action.slots.model_dump(exclude_none=True)
            public_slots.pop('card_number',None)
            public_slots.pop('funding_card_number',None)
            # A literal destination card is also a private instrument selector,
            # even when the original user supplied it in the payee slot. Keep
            # the exact value in the owned server binding, not duplicated in a
            # public frame label/slot (where the existing PAN gate rejects it).
            from .card_identifiers import CARD_NUMBER_PATTERN
            import re
            payee_selector=public_slots.get('payee_name')
            if isinstance(payee_selector,str) and re.fullmatch(r'(?:'+CARD_NUMBER_PATTERN+r'|[0-9]{12,19})',payee_selector):
                public_slots.pop('payee_name')
            # Full instrument selectors stay in the owned server binding.
            # Navigation uses scoped entity refs, not copied payment numbers.
            for private_selector in ('source_card', 'card_reference', 'recipient_card_reference', 'destination_card_reference'):
                public_slots.pop(private_selector, None)
            actions.append({'action_ref':frame_ref+':a'+str(index),'kind':action.kind,
                'slots':public_slots,'allowed_fields':sorted(ACTION_ALLOWED_FIELDS.get(action.kind,set())),
                'field_options':field_options_for(ACTION_ALLOWED_FIELDS.get(action.kind,set())),
                'depends_on':list(action.depends_on)})
        # A proposal rejected before any compiled plan is not a reusable plan.
        # Keep the failed task as a status/navigation anchor, but do not feed
        # its unvalidated card/amount guesses back as established context.
        if task.state=='FAILED' and snapshot is None and not effects:
            actions=[]
        mutable=task.state in {'QUEUED','UNDERSTANDING','PLANNING','NEED_CLARIFICATION','AWAITING_CONFIRMATION'} and not effects
        from .conversation_turn_state import current_conversation_pause
        frame={'frame_ref':frame_ref,'state':task.state,'actions':actions,'continuation_allowed':mutable,
            'read_only':bool(actions) and all(a['kind'] in READ_KINDS for a in actions),
            'write_effect_possible':bool(effects),'paused':bool(current_conversation_pause(session,principal,task)),
            'current':task.id==navigation_task_id}
        if task.state=='NEED_CLARIFICATION':
            from .task_dialogue_context import task_context
            current_context=task_context(session,principal,task) or {}
            needed=[]
            for group in current_context.get('needed_fields',[]):
                for field in group.get('missing_fields',[]):
                    if isinstance(field,str):needed.append({'field':field})
            frame['needed_fields']=needed
        try:bill_hint=_completed_bill_read_hint(session,principal,task,record,snapshot,public_proposal,operations)
        except DomainError as error:
            # This helper is entered only for a SUCCEEDED pure bill READ with
            # no write/family history. Omit that unusable source before adding
            # a frame, bindings or entities; never omit UNKNOWN/WRITE guards.
            marker={'read_kind':'BILL_ANALYSIS','status':'SOURCE_UNAVAILABLE'}
            if marker not in unavailable_read_sources:unavailable_read_sources.append(marker)
            unavailable_read_diagnostics.append({'task_id':task.id,'read_kind':'BILL_ANALYSIS',
                'status':'SOURCE_UNAVAILABLE','reason':error.code})
            continue
        if bill_hint:frame.update(bill_hint)
        model['frames'].append(frame)
        uncertain_effects=task.state in {'AUTHORIZED','EXECUTING','UNKNOWN','PARTIAL'} or any(
            op.action_kind not in READ_KINDS and (op.status in {'UNKNOWN','AUTHORIZED','RUNNING'} or op.child_task_id and
            (child:=session.get(Task,op.child_task_id)) and child.state in {'UNKNOWN','EXECUTING','AUTHORIZED'}) for op in operations)
        if mutable or uncertain_effects or frame['current']:
            protected_frame_refs.add(frame_ref)
        binding={'task_id':task.id,'task_version':task.version,'plan_version':record.plan_version,
            'snapshot_id':snapshot.id if snapshot else None,'plan_hash':snapshot.plan_hash if snapshot else None,
            'proposal_json':proposal.model_dump(),'action_refs':{a['action_ref']:i for i,a in enumerate(actions)},
            'source_turn_id':receipt.id,'message_hash':canonical_hash({'message':task.message}),
            'input_revision':record.metadata_json.get('input_revision',0),'frame_hash':canonical_hash(frame)}
        if not snapshot and record.metadata_json.get('cloud_semantic_intent'):
            binding['prior_semantic']=deepcopy(record.metadata_json['cloud_semantic_intent'])
        if pending:binding['pending_semantic']=deepcopy(pending)
        offered['frame_bindings'][frame_ref]=binding
        if task.state=='SUCCEEDED' and effects and len(model['entities'])<40-reserved_entity_slots:
            from .semantic_transfer_identity import card_identities as transfer_card_identities, KIND as TRANSFER_IDENTITY_KIND
            from .semantic_offered_contracts import completed_identity_contract
            try:transfer_cards=transfer_card_identities(session,principal,thread,task,turn.sequence)
            except DomainError:transfer_cards=[]
            from .semantic_transfer_identity import receipt_identities, RECEIPT_KIND
            try:transfer_receipts=receipt_identities(session,principal,thread,task,turn.sequence,card_sources=transfer_cards)
            except DomainError:transfer_receipts=[]
            for identity in transfer_receipts:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'TRANSFER',
                    'kind':RECEIPT_KIND,'label':identity['label'],'allowed_fields':[],
                    'available_aspects':['SUMMARY','STATUS']})
                offered['entity_bindings'][entity_ref]=identity
            for identity in transfer_cards:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CARDS',
                    'kind':TRANSFER_IDENTITY_KIND,'label':identity['label'],'ordinal':None,
                    'field':'card_number','read_kind':'CARD_STATUS','allowed_fields':['card_number'],'available_aspects':[],
                    **completed_identity_contract(TRANSFER_IDENTITY_KIND)})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_asset_identity import order_identities
            from .semantic_merchant_payment_identity import payment_identities,KIND as PURCHASE_KIND
            try:purchases=payment_identities(session,principal,thread,task,turn.sequence)
            except DomainError:purchases=[]
            for identity in purchases:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CREDIT','kind':PURCHASE_KIND,
                    'label':identity['label'],'ordinal':None,'field':'payment_id','read_kind':'CREDIT_STATEMENT',
                    'allowed_fields':['payment_id'],'available_aspects':[],**completed_identity_contract(PURCHASE_KIND)})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_fx_identity import card_identities as fx_card_identities,KIND as FX_KIND
            try:fx_cards=fx_card_identities(session,principal,thread,task,turn.sequence)
            except DomainError:fx_cards=[]
            for identity in fx_cards:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CARDS','kind':FX_KIND,
                    'label':identity['label'],'ordinal':None,'field':'card_number','read_kind':'CARD_STATUS',
                    'allowed_fields':['card_number'],'available_aspects':[],**completed_identity_contract(FX_KIND)})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_offered_contracts import completed_identity_contract
            try:identities=order_identities(session,principal,thread,task,turn.sequence)
            except DomainError:identities=[]
            for identity in identities:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'ASSETS',
                    'kind':'COMPLETED_ASSET_RECEIPT_IDENTITY','label':identity['product_name'],'ordinal':None,
                    'field':'product_name','read_kind':'ASSET_POSITIONS_READ',
                    'allowed_fields':['product_name'],'available_aspects':[],
                    **completed_identity_contract('COMPLETED_ASSET_RECEIPT_IDENTITY')})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_asset_identity import cash_card_identities, CASH_KIND
            try:cash_cards=cash_card_identities(session,principal,thread,task,turn.sequence)
            except DomainError:cash_cards=[]
            for identity in cash_cards:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CARDS',
                    'kind':CASH_KIND,'label':identity['label'],'ordinal':None,'field':'card_number',
                    'read_kind':'CARD_STATUS','allowed_fields':['card_number'],'available_aspects':[],
                    **completed_identity_contract(CASH_KIND)})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_credit_identity import card_identities
            from .semantic_subscription_identity import subscription_identities
            try:subscriptions=subscription_identities(session,principal,thread,task,turn.sequence)
            except DomainError:subscriptions=[]
            for identity in subscriptions:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'SUBSCRIPTIONS',
                    'kind':identity['source_kind'],'label':identity['label'],'ordinal':None,'field':'merchant',
                    'read_kind':'SUBSCRIPTION_LIST','allowed_fields':['merchant'],'available_aspects':['STATUS','TIME_SCOPE'],
                    **completed_identity_contract(identity['source_kind'])})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_life_plan_identity import plan_identities
            try:life_plans=plan_identities(session,principal,thread,task,turn.sequence)
            except DomainError:life_plans=[]
            for identity in life_plans:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'LIFE_PLANS',
                    'kind':identity['source_kind'],'label':identity['label'],'ordinal':None,'field':'life_plan_id',
                    'read_kind':'LIFE_PLAN_VIEW','allowed_fields':['life_plan_id'],'available_aspects':['STATUS','TIME_SCOPE'],
                    **completed_identity_contract(identity['source_kind'])})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_credit_identity import installment_identities,INSTALLMENT_KIND
            try:installments=installment_identities(session,principal,thread,task,turn.sequence)
            except DomainError:installments=[]
            for identity in installments:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CREDIT','kind':INSTALLMENT_KIND,
                    'label':identity['label'],'ordinal':None,'field':'installment_id','read_kind':'CREDIT_STATEMENT',
                    'allowed_fields':['installment_id'],'available_aspects':[],**completed_identity_contract(INSTALLMENT_KIND)})
                offered['entity_bindings'][entity_ref]=identity
            try:credit_cards=card_identities(session,principal,thread,task,turn.sequence)
            except DomainError:credit_cards=[]
            from .semantic_repayment_source_identity import target_identities
            try:repayment_targets=target_identities(session,principal,thread,task,turn.sequence)
            except DomainError:repayment_targets=[]
            target_operations={row['operation_id'] for row in repayment_targets}
            credit_cards=[row for row in credit_cards if row['operation_id'] not in target_operations]+repayment_targets
            for identity in credit_cards:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CREDIT',
                    'kind':identity['source_kind'],'label':identity['label'],'ordinal':None,
                    'field':'card_reference','read_kind':'CREDIT_VIEW','allowed_fields':['card_reference'],'available_aspects':['BALANCE'],
                    **completed_identity_contract(identity['source_kind'])})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_repayment_source_identity import card_identities as repayment_sources
            try:funding_cards=repayment_sources(session,principal,thread,task,turn.sequence)
            except DomainError:funding_cards=[]
            for identity in funding_cards:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'CARDS',
                    'kind':identity['source_kind'],'label':identity['label'],'ordinal':None,
                    'field':identity['field'],'read_kind':'CARD_STATUS','allowed_fields':[identity['field']],
                    'available_aspects':[],**completed_identity_contract(identity['source_kind'])})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_occasion_identity import occasion_identities
            try:occasions=occasion_identities(session,principal,thread,task,turn.sequence)
            except DomainError:occasions=[]
            for identity in occasions:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'OCCASIONS',
                    'kind':'COMPLETED_OCCASION_RECEIPT_IDENTITY','label':identity['label'],'ordinal':None,
                    'field':'occasion_id','read_kind':'OCCASION_VIEW','allowed_fields':['occasion_id'],'available_aspects':[],
                    **completed_identity_contract('COMPLETED_OCCASION_RECEIPT_IDENTITY')})
                offered['entity_bindings'][entity_ref]=identity
            from .semantic_occasion_identity import member_identities
            try:members=member_identities(session,principal,thread,task,turn.sequence)
            except DomainError:members=[]
            for identity in members:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'OCCASIONS',
                    'kind':'COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY','label':identity['label'],'ordinal':None,
                    'field':'family_member_id','read_kind':'OCCASION_MEMBER_VIEW','allowed_fields':['family_member_id'],'available_aspects':[],
                    **completed_identity_contract('COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY')})
                offered['entity_bindings'][entity_ref]=identity
        if task.state == 'PARTIAL' and len(model['entities']) < 40-material_slots:
            from .credit_recovery_context import append_partial_repayment_reads
            append_partial_repayment_reads(session, principal, thread, task, turn.sequence,
                model, offered, frame_ref, 40-material_slots)
        # A successful READ node can offer its current verified objects while
        # a separate unexecuted WRITE waits for review. This does not turn the
        # mixed parent into a completed READ or authorize/change that WRITE.
        # Executed/authorized/unknown effects and paused parents offer none.
        if task.state not in {'SUCCEEDED','AWAITING_CONFIRMATION'} or effects or frame['paused'] \
                or not any(op.action_kind in READ_KINDS and op.status=='SUCCEEDED' for op in operations):continue
        from .dialogue_references import build_result_reference_catalog
        try:reference=build_result_reference_catalog(session,principal,thread,task_id=task.id,before_sequence=turn.sequence)
        except DomainError:
            from .semantic_aa_read_identity import identities as historical_aa_identities,KIND as AA_READ_IDENTITY
            try:aa_identities=historical_aa_identities(session,principal,thread,task,turn.sequence)
            except DomainError:aa_identities=[]
            for identity in aa_identities:
                if len(model['entities'])>=40-reserved_entity_slots:break
                entity_ref='e'+str(len(model['entities']))
                model['entities'].append({'entity_ref':entity_ref,'frame_ref':frame_ref,'domain':'AA',
                    'kind':AA_READ_IDENTITY,'label':identity['label'],'ordinal':None,
                    'field':identity['field'],'read_kind':'AA_VIEW','allowed_fields':[identity['field']],
                    'available_aspects':[],'consumer_contract_version':'aa-historical-read-identity-v1',
                    'consumer_kinds':['AA_VIEW']})
                offered['entity_bindings'][entity_ref]=identity
            continue
        from .semantic_source_bundle import add_catalog
        catalog_ref=add_catalog(offered,reference) if reference.get('entities') and len(model['entities'])<40-reserved_entity_slots else None
        from .credit_statement_identity import visible_catalog_entities
        from .semantic_catalog_priority import prioritize_catalog_entities
        for actual in prioritize_catalog_entities(visible_catalog_entities(reference.get('entities',[]))):
            if len(model['entities'])>=40-reserved_entity_slots:break
            entity_ref='e'+str(len(model['entities']))
            from .dialogue_references import entity_allowed_fields
            item={'entity_ref':entity_ref,'frame_ref':frame_ref,'label':actual['label'],'ordinal':actual['ordinal'],
                'domain':actual['domain'],'field':actual['field'],'read_kind':actual['read_kind'],
                'allowed_fields':entity_allowed_fields(actual)}
            from .credit_statement_identity import semantic_identity_metadata
            item.update(semantic_identity_metadata(actual))
            if actual.get('read_kind')=='AA_VIEW' and actual.get('field') in {'aa_collection_ref','aa_obligation_ref'}:
                item.update(kind='OWNED_AA_RECORD_IDENTITY',consumer_contract_version='aa-record-consumers-v1',
                    consumer_kinds=['AA_VIEW']+(['AA_PAY'] if actual['field']=='aa_obligation_ref' and actual.get('facts',{}).get('paymentable') is True else []))
            from .bill_query_context import semantic_identity_metadata as bill_identity_metadata
            item.update(bill_identity_metadata(actual))
            from .dialogue_result_aspects import available_aspects
            item['available_aspects']=available_aspects(actual.get('facts',{}),actual.get('domain'))
            from .semantic_read_aliases import merchant_read_aliases
            aliases=merchant_read_aliases(session,principal,actual)
            if aliases:item['read_aliases']=aliases
            from .semantic_router import FIELD_OPTIONS
            if actual.get('value') in FIELD_OPTIONS.get(actual['field'],[]):
                item['registered_options']=[actual['value']]
            model['entities'].append(item)
            offered['entity_bindings'][entity_ref]={'field':actual['field'],'value':deepcopy(actual['value']),
                'actual_entity':deepcopy(actual),'catalog_ref':catalog_ref,'frame_ref':frame_ref}
    for material in material_catalog[:material_slots]:
        entity_ref='e'+str(len(model['entities']))
        model['entities'].append({**deepcopy(material['public']),'entity_ref':entity_ref})
        offered['entity_bindings'][entity_ref]=deepcopy(material['binding'])
    if unavailable_read_sources:
        model['unavailable_read_sources']=unavailable_read_sources
        offered['unavailable_read_diagnostics']=unavailable_read_diagnostics
    bind_selected_focus(offered, turn)
    from .recipient_choices import attach_choices
    attach_choices(session, principal, thread, turn, offered)
    from .semantic_query_scope import attach_current_query_scope
    protected_frame_refs.update(attach_current_query_scope(offered,session,principal,thread,turn))
    offered['context_budget_diagnostics']={**history_diagnostics,
        **budget_semantic_context(offered,history_groups,protected_frame_refs=protected_frame_refs)}
    from .semantic_read_history_scope import attach_history_sources
    attach_history_sources(offered,history_groups,history_scope_bindings)
    if unavailable_read_diagnostics:
        # The existing conversation entry persists these internal diagnostics
        # for all modes, including a natural CLARIFY with no new bank task.
        offered['context_budget_diagnostics'].update({'unavailable_read_source_count':len(unavailable_read_diagnostics),
            'unavailable_read_sources':deepcopy(unavailable_read_diagnostics)})
    # Whole-frame budgeting already removed the corresponding model entities
    # and bindings. Keep exactly their referenced catalogs, never cut evidence
    # from a surviving entity or protected financial frame.
    retained_catalogs={entry.get('catalog_ref') for entry in offered['entity_bindings'].values() if entry.get('catalog_ref')}
    offered['catalog_pool']={key:value for key,value in offered['catalog_pool'].items() if key in retained_catalogs}
    offered['context_hash']=canonical_hash({'scope':scope_hash,'frames':offered['frame_bindings'],'entities':offered['entity_bindings'],
        'catalog_pool':offered['catalog_pool']})
    return offered
