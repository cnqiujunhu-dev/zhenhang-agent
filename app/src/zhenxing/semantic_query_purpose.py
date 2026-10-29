"""Server verification for operation-boundary answers and useful clarification."""
from .contracts import DomainError


def _verified_preference_only_scope(session, principal, tasks, contexts, facts):
    """Never hide a financial, pending or superseded unverified effect.

    The current proposal alone is insufficient: revisions can leave inactive
    operations with real effects. Every write in this scope must correspond to
    one of the independently checked preference receipts.
    """
    from sqlalchemy import select
    from .intelligence import READ_KINDS
    from .preference_status_facts import PREFERENCE_ACTIONS
    from .runtime_models import RuntimeOperation
    if not facts or any(task.state != 'SUCCEEDED' or not context.get('verified')
            or any(kind not in READ_KINDS and kind not in PREFERENCE_ACTIONS for kind in context['action_kinds'])
            for task, context in zip(tasks, contexts)):
        return False
    receipt_ids = {fact['operation_id'] for fact in facts}
    completed_ids = set()
    for operation in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id.in_([task.id for task in tasks]))):
        if operation.sandbox_id != principal.sandbox_id:
            return False
        if operation.action_kind in READ_KINDS:
            continue
        if operation.action_kind not in PREFERENCE_ACTIONS or operation.status != 'SUCCEEDED':
            return False
        completed_ids.add(operation.id)
    return completed_ids == receipt_ids


def checked_clarification(decision, offered, message):
    evidence=decision.clarification_evidence
    if not evidence:
        raise DomainError('SEMANTIC_CLARIFICATION_EVIDENCE_REQUIRED','本轮还不能可靠地确定缺少哪项信息，请明确要查看的对象或问题。',409)
    evidence=evidence.model_dump()
    reason=evidence['reason']
    value_ambiguity = None
    if reason == 'AMBIGUOUS_VALUE':
        from .clarification_value_contract import checked_value_ambiguity
        value_ambiguity = checked_value_ambiguity(evidence, message)
    if reason=='MISSING_FIELD':
        field=evidence.get('field')
        matches=[item for frame in offered['model']['frames'] for item in frame.get('needed_fields',[])
            if isinstance(item,dict) and (field==item.get('field') or field in item.get('missing_fields',[]))]
        if not matches:raise DomainError('SEMANTIC_CLARIFICATION_FIELD_NOT_MISSING','已有资料没有显示缺少这项信息，请按当前结果继续。',409)
    return {'version':'verified-clarification-v1','reason':reason,'field':evidence.get('field'),
        'candidate_refs':evidence.get('candidate_refs',[]),'source_span':evidence['source_span'],
        'source_message_hash':offered['message_hash'],
        **({'value_ambiguity': value_ambiguity} if value_ambiguity is not None else {})}


def _pending_transfer_scope_reply(contexts,language='zh-CN'):
    """Presentation only, after the existing scope/owner/version/effect checks."""
    from .task_dialogue_context import VERSION as CONTEXT_VERSION
    from .intelligence import READ_KINDS
    if len(contexts)!=1:return None
    context=contexts[0]
    if not isinstance(context,dict) or context.get('version')!=CONTEXT_VERSION \
            or context.get('authority')!='OWNED_RUNTIME_FACTS_NOT_USER_AUTHORIZATION' \
            or not isinstance(context.get('task_id'),str) or not context['task_id'] \
            or type(context.get('task_version')) is not int or context['task_version']<1 \
            or context.get('state')!='AWAITING_CONFIRMATION' \
            or context.get('completed_write_present') is not False \
            or context.get('possible_write_effect') is not False:return None
    kinds=context.get('action_kinds')
    if not isinstance(kinds,list) or any(not isinstance(kind,str) for kind in kinds) \
            or [kind for kind in kinds if kind not in READ_KINDS]!=['TRANSFER']:return None
    english=language=='en'
    if context.get('confirmation_status')=='PENDING':
        return 'Not yet. This transfer is waiting for you to review and confirm.' if english else '还没有转出，正在等你核对并确认。'
    if context.get('confirmation_status')=='EXPIRED':
        return 'It has not been sent. The confirmation expired; prepare it again before confirming.' if english else '还没有转出。这次确认已过期，需要重新准备后再确认。'
    if context.get('confirmation_status')=='UNAVAILABLE':
        return 'It has not been sent. The confirmation details need to be checked again.' if english else '还没有转出，当前确认信息需要重新核对。'
    return None


def _neutral_uncompleted_scope_reply(language='zh-CN'):
    return ('No completed banking action is recorded here. Check the linked item\'s current status for its progress.'
        if language=='en' else '目前未查到已完成的办理记录，请以对应事项的当前状态为准。')


def finish_operation_scope(session,principal,turn,decision,offered):
    from .conversation_turn_state import _finish,_turn_language
    from .services import owned_task
    from .task_dialogue_context import task_context
    from .intelligence import READ_KINDS
    refs=decision.scope_frame_refs or ([decision.focus_ref] if decision.focus_ref else
        [frame['frame_ref'] for frame in offered['model']['frames'] if frame.get('current')])
    conversation_tasks=[]
    if decision.operation_scope=='CONVERSATION':
        from sqlalchemy import select
        from .conversation_models import ChatTurn
        conversation_tasks=list(dict.fromkeys(session.scalars(select(ChatTurn.task_id).where(ChatTurn.thread_id==turn.thread_id,
            ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id,
            ChatTurn.sequence<turn.sequence,ChatTurn.task_id.is_not(None)).order_by(ChatTurn.sequence))))
    if not refs and not conversation_tasks:raise DomainError('SEMANTIC_SCOPE_FOCUS_REQUIRED','请指出要核对哪次查询或办理记录。',409)
    contexts=[];tasks=[]
    bindings=[{'task_id':task_id} for task_id in conversation_tasks] if decision.operation_scope=='CONVERSATION' else [offered['frame_bindings'].get(ref) for ref in refs]
    for binding in bindings:
        if not binding:raise DomainError('SEMANTIC_SCOPE_SOURCE_CHANGED','要核对的办理来源已经变化，请查看当前记录。',409)
        task=owned_task(session,principal,binding['task_id'],lock=True)
        if binding.get('task_version') is not None and task.version!=binding['task_version']:
            raise DomainError('SEMANTIC_SCOPE_SOURCE_CHANGED','要核对的办理已经更新，请查看当前记录。',409)
        context=task_context(session,principal,task)
        if not context or not context.get('action_kinds'):
            raise DomainError('SEMANTIC_SCOPE_SOURCE_CHANGED','目前没有足够的银行记录核对这次处理范围。',409)
        contexts.append(context)
        tasks.append(task)
    english=_turn_language(turn)=='en'
    completed=any(item['completed_write_present'] for item in contexts)
    uncertain=any(item['possible_write_effect'] for item in contexts)
    unverified=any(item['completed_write_present'] and not item.get('verified') for item in contexts)
    read_only=all(all(kind in READ_KINDS for kind in item['action_kinds']) for item in contexts)
    verified=all(item['verified'] and item['outcome_kind']=='QUERY_RESULT' for item in contexts)
    from .scheduled_transfer_status_summary import scheduled_transfer_scope_summary
    scheduled_summary=(scheduled_transfer_scope_summary(session,principal,tasks,contexts,'en' if english else 'zh-CN')
        if not uncertain and not unverified else None)
    from .life_status_summary import completed_life_status_summary
    life_summary=completed_life_status_summary(session,principal,tasks,contexts,'en' if english else 'zh-CN') if not uncertain and not unverified else None
    if scheduled_summary is not None:
        answer=scheduled_summary['answer']
    elif life_summary is not None:
        answer=life_summary['answer']
    elif uncertain or unverified:
        answer='Some linked changes are authorized, processing or unverified. Their outcome must be checked; I cannot call this a query-only request.' if english else '这组办理有变更动作的授权、处理或结果待核验记录，不能认定为只是查询；请先核对对应记录。'
    elif completed:
        answer='The linked records include completed changes, so this was not query-only. This reply does not repeat those actions.' if english else '这组记录包含已经完成的变更动作，不能说全部只是查询。这条回复仅核对记录，没有重复办理。'
        from .completed_transfer_status_summary import completed_transfer_scope_summary
        transfer_summary=completed_transfer_scope_summary(session,principal,tasks,contexts,'en' if english else 'zh-CN')
        if transfer_summary is not None:
            answer=transfer_summary['answer']
        from .own_transfer_status_facts import completed_own_transfer_facts,render_own_transfer_status
        own_facts=[]
        own_scope=all(all(kind in READ_KINDS or kind=='OWN_ACCOUNT_TRANSFER' for kind in context['action_kinds'])
            for context in contexts)
        if own_scope:
            for task,context in zip(tasks,contexts):
                if 'OWN_ACCOUNT_TRANSFER' in context['action_kinds']:
                    own_facts.extend(completed_own_transfer_facts(session,principal,task) or [])
            own_answer=render_own_transfer_status(own_facts,'en' if english else 'zh-CN')
            if own_answer:answer=own_answer
        from .preference_status_facts import (PREFERENCE_ACTIONS,completed_preference_facts,
            render_preference_status,render_preference_conversation_status)
        preference_facts=[fact for task,context in zip(tasks,contexts)
            if context['completed_write_present'] and context.get('verified')
                and any(kind in PREFERENCE_ACTIONS for kind in context['action_kinds'])
            for fact in completed_preference_facts(session,principal,task)]
        preference_answer=render_preference_status(preference_facts,'en' if english else 'zh-CN')
        if preference_answer:
            concise=(render_preference_conversation_status(preference_facts,'en' if english else 'zh-CN')
                if decision.operation_scope=='CONVERSATION'
                    and _verified_preference_only_scope(session,principal,tasks,contexts,preference_facts) else None)
            if concise:
                answer=concise
            elif decision.operation_scope!='CONVERSATION' and all(kind in READ_KINDS or kind in PREFERENCE_ACTIONS
                    for context in contexts for kind in context['action_kinds']):
                answer=preference_answer+(' This reply checks the completed preference record and does not save it again.' if english
                    else '这条回复只核对已完成的偏好记录，没有再次修改。')
            else:answer=preference_answer+'\n'+answer
    elif read_only:
        answer=('Yes. The linked steps are read-only queries; they do not change subscriptions, redeem points or move funds.' if english else '对，这组步骤只有只读查询，没有修改订阅、兑换积分或变动资金。')
        if not verified:answer+=(' Some query results are still being prepared.' if english else '其中尚未完成的查询结果仍需等待。')
    else:
        language='en' if english else 'zh-CN'
        answer=_pending_transfer_scope_reply(contexts,language) or _neutral_uncompleted_scope_reply(language)
    subscription_summary=None
    if not uncertain and not unverified:
        from .subscription_status_summary import completed_subscription_status_summary
        subscription_summary=completed_subscription_status_summary(session,principal,tasks,contexts,'en' if english else 'zh-CN')
        if subscription_summary is not None:answer=subscription_summary['answer']
    _finish(turn,'STATUS',answer)
    turn.answer=answer
    turn.response_meta_json={**turn.response_meta_json,'operation_scope':{'version':'verified-operation-scope-v1',
        'scope':decision.operation_scope or 'CURRENT_REQUEST',
        'read_only':read_only,'completed_write_present':completed,'possible_write_effect':uncertain,
        'result_evidence_verified':not unverified and (transfer_summary.get('verified') is True
            if 'transfer_summary' in locals() and transfer_summary is not None else True),
        **({'subscription_status_summary':subscription_summary} if subscription_summary is not None else {}),
        **({'scheduled_transfer_status_summary':{key:scheduled_summary[key] for key in
            ('version','verified','schedule_id','state','registration_plan_hash')}} if scheduled_summary is not None else {}),
        **({'transfer_receipt_summary':{'version':transfer_summary['version'],
            'verified':transfer_summary['verified'],'receipts':transfer_summary['receipts']}}
            if 'transfer_summary' in locals() and transfer_summary is not None else {}),
        **({'preference_receipts':[{'source_id':fact['source_id'],'receipt_hash':fact['receipt_hash'],
            'operation_id':fact['operation_id']} for fact in preference_facts]} if 'preference_facts' in locals() and preference_facts else {}),
        'source_task_versions':{item['task_id']:item['task_version'] for item in contexts}}}
    turn.diagnostics_json={**turn.diagnostics_json,'bank_task_created':False,'bank_write_authorized':False,
        'preserve_goal_context':True,'operation_scope_verified':True}
