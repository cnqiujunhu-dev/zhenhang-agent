"""Customer recovery for failed understanding; never retries a bank command."""
from copy import deepcopy
from sqlalchemy import select
from .contracts import DomainError
from .authentication import as_utc
from .conversation_models import ChatTurn

VERSION = 'semantic-recovery-v1'
KINDS = {'SERVICE_UNAVAILABLE','RESPONSE_INVALID','CONTEXT_LIMIT','SELECTION_CHANGED',
         'ACCESS_CHANGED','OUTCOME_UNCERTAIN','RETRY_EXHAUSTED'}


def recovery_for(turn, code, *, english=False):
    from .semantic_reunderstanding import KEY,retry_available
    from .models import utcnow
    diagnostics = turn.diagnostics_json or {}
    model = diagnostics.get('semantic_diagnostics') or {}
    retries = len(diagnostics.get('understanding_retry_history') or [])
    validation = {item.get('code') for item in model.get('semantic_validation_errors',[]) if isinstance(item,dict)}
    if code in {'CHAT_SELECTED_TASK_MISMATCH','CHAT_SELECTED_TASK_CHANGED'} or 'SEMANTIC_SELECTED_FOCUS_MISMATCH' in validation:
        kind, action = 'SELECTION_CHANGED', 'REVIEW_SELECTION'
        text = ('The selected request needs another check. Open the request you mean and tell me what to change.' if english
                else '请先核对要处理的那项办理，再告诉我要怎么调整；我没有修改其他事项。')
    elif code in {'INPUT_INVALID','SEMANTIC_INPUT_TOTAL_BOUND','SEMANTIC_WIRE_BUDGET_EXCEEDED'}:
        kind, action = 'CONTEXT_LIMIT', 'REPHRASE'
        text = ('There is too much context to handle reliably in this turn. Start a new conversation with the one question you want to resolve; existing requests are kept.' if english
                else '这段对话需要整理的内容较多。请发起新会话，告诉我这次最想解决的一件事；已有办理和记录会保留。')
    elif code in {'SEMANTIC_ACTOR_CHANGED','SEMANTIC_EXPLANATION_SOURCE_CHANGED'}:
        kind, action = 'ACCESS_CHANGED', 'CHECK_ORIGINAL'
        text = ('Access or the underlying information changed. Check your current account and request before continuing.' if english
                else '访问权限或相关资料已经变化。请核对当前账户和办理记录后继续，我没有沿用旧资料处理。')
    elif code == 'MODEL_INVALID':
        kind, action = 'RESPONSE_INVALID', 'RETRY_UNDERSTANDING'
        text = ('I could not organize the response reliably. You can retry understanding this message without repeating a banking action.' if english
                else '这次没有可靠地整理好你的需求，可以让我重新理解这条消息，不会重复提交银行操作。')
    elif code == 'MODEL_UNAVAILABLE':
        # An uncertain MODEL response is not an uncertain bank submission.
        # Keep the original no-reissue control for in-flight/unknown inference,
        # but do not tell a read/consult customer a payment might have occurred.
        from .semantic_connect_recovery import verified_connect_retry
        failure_kind=model.get('http_failure_kind')
        known_response=type(model.get('http_status')) is int and (failure_kind is None or failure_kind=='HTTP_STATUS')
        retry_known = ((model.get('http_issued') is False or known_response)
            and model.get('http_outcome_unknown') is not True) or verified_connect_retry(turn)
        kind, action = 'SERVICE_UNAVAILABLE', 'RETRY_UNDERSTANDING' if retry_known else 'WAIT'
        text = (('I could not handle this message just now. Your message is kept; you can retry it here.' if retry_known else
                 'I could not answer this message just now. Please continue here later.') if english else
                ('刚才没能正常处理这条消息，你的原话已保留，可以在这里重试。' if retry_known else
                 '暂时没能回答这条消息，请稍后在这里继续。'))
    else:
        kind, action = 'RESPONSE_INVALID', 'REPHRASE'
        text = ('This message could not be handled reliably. Please continue in the conversation later.' if english
                else '这条消息暂时没能处理好，请稍后在对话中继续。')
    can_retry = action == 'RETRY_UNDERSTANDING' and not turn.task_id and retries < 2
    outer_exhausted=KEY in diagnostics and not retry_available(diagnostics.get(KEY),now=utcnow())
    if action == 'RETRY_UNDERSTANDING' and turn.task_id:
        action='WAIT';can_retry=False
        text=('This message could not be answered reliably. Please continue here later; any existing request retains its own status.' if english else
            '这条消息暂时没能回答，请稍后在这里继续；已有事项仍以各自状态为准。')
    elif action == 'RETRY_UNDERSTANDING' and (retries >= 2 or outer_exhausted):
        kind, action = 'RETRY_EXHAUSTED', 'REPHRASE'
        can_retry=False
        text = ('I could not handle this message just now. Please continue here later.' if english
                else '暂时没能处理好这条消息，请稍后在这里继续。')
    return text, {'version':VERSION,'kind':kind,'action':action,'retry_version':retries,'can_retry':can_retry}


def public_recovery(value):
    if not isinstance(value,dict) or set(value) != {'version','kind','action','retry_version','can_retry'}:
        return None
    if value['version'] != VERSION or value['kind'] not in KINDS or value['action'] not in {
            'RETRY_UNDERSTANDING','REVIEW_SELECTION','REPHRASE','CHECK_ORIGINAL','WAIT'} \
            or type(value['retry_version']) is not int or not 0 <= value['retry_version'] <= 2 \
            or type(value['can_retry']) is not bool:
        return None
    if value['can_retry'] != (value['action']=='RETRY_UNDERSTANDING' and value['kind'] in {
            'SERVICE_UNAVAILABLE','RESPONSE_INVALID'} and value['retry_version'] < 2):
        return None
    return deepcopy(value)


def display_recovery(turn, *, english=False):
    """Withdraw expired saved controls on an authorized, bounded read only.

    History supplies a size-checked projection rather than a ChatTurn ORM row.
    It must never mint new retry authority or reload the original JSON package.
    The stored reply/evidence remains unchanged; POST retains its own full gate.
    """
    from .semantic_reunderstanding import KEY, retry_available, valid_ledger
    from .models import utcnow
    meta = turn.response_meta_json or {}
    saved = public_recovery(meta.get('recovery'))
    if turn.status != 'ANSWERED' or turn.mode != 'NEED_CONTEXT' or turn.task_id \
            or saved is None or not saved['can_retry']:
        return turn.answer, meta
    diagnostics = turn.diagnostics_json or {}
    ledger = diagnostics.get(KEY)
    if diagnostics.get('semantic_processing_error') not in {'MODEL_INVALID', 'MODEL_UNAVAILABLE'} \
            or not valid_ledger(ledger) or retry_available(ledger, now=utcnow()):
        return turn.answer, meta
    # Monotonic display projection: only remove a previously issued control.
    # In particular, legacy records, unknown outcomes and other modes cannot
    # acquire a retry merely because their JSON resembles current evidence.
    saved.update(kind='RETRY_EXHAUSTED', action='REPHRASE', can_retry=False)
    answer = ('This message can no longer be continued in place. Please tell me what you need again.' if english
        else '这条消息已不能原位继续，可重新告诉我需求。')
    return answer, {**meta, 'recovery': saved}


def retry_understanding(session, principal, conversation_id, turn_id, expected_retry_version):
    from .conversation_service import owned_thread
    from .dialogue_state import dialogue_actor_scope
    thread = owned_thread(session, principal, conversation_id, lock=True)
    turn = session.scalar(select(ChatTurn).where(ChatTurn.id==turn_id,
        ChatTurn.thread_id==thread.id,ChatTurn.user_id==principal.user_id,
        ChatTurn.sandbox_id==principal.sandbox_id).with_for_update())
    if turn is None:
        raise DomainError('CHAT_TURN_NOT_FOUND','找不到这条消息。',404)
    diagnostics = turn.diagnostics_json or {}
    history = diagnostics.get('understanding_retry_history') or []
    if thread.status != 'ACTIVE' or diagnostics.get('dialogue_actor_scope_hash') != dialogue_actor_scope(session,principal)[1]:
        raise DomainError('CHAT_RETRY_NOT_AVAILABLE','请先核对当前账户与会话。',409)
    if type(expected_retry_version) is not int or not 0 <= expected_retry_version < 2:
        raise DomainError('CHAT_RETRY_INVALID','请先查看这条消息的最新回复。',409)
    if len(history) == expected_retry_version + 1 and history[-1].get('retry_version') == expected_retry_version:
        return turn  # Duplicate click never consumes another retry or model call.
    if thread.status != 'ACTIVE' or turn.status != 'ANSWERED' or turn.mode != 'NEED_CONTEXT' or turn.task_id \
            or len(history) != expected_retry_version \
            or diagnostics.get('dialogue_actor_scope_hash') != dialogue_actor_scope(session,principal)[1]:
        raise DomainError('CHAT_RETRY_NOT_AVAILABLE','请先核对当前会话和办理记录，不能重复处理这条消息。',409)
    if session.scalar(select(ChatTurn.id).where(ChatTurn.thread_id==thread.id,ChatTurn.sequence>turn.sequence).limit(1)):
        raise DomainError('CHAT_RETRY_NOT_LATEST','对话已有新的内容，请在当前消息框继续说明需求。',409)
    _text, recovery = recovery_for(turn,diagnostics.get('semantic_processing_error'))
    if not recovery['can_retry']:
        raise DomainError('CHAT_RETRY_NOT_AVAILABLE','这条消息需要先核实原结果，不能直接重试。',409)
    # Preserve the original model/processing evidence, with no credential or
    # raw response copy. Only this semantic job is requeued; no banking job is.
    history = [*history, {'retry_version':expected_retry_version,
        'observed_at':as_utc(turn.completed_at or turn.updated_at).isoformat(),
        'failure_code':diagnostics.get('semantic_processing_error'),
        'model_diagnostics':deepcopy(diagnostics.get('semantic_diagnostics') or {})}]
    for key in ('semantic_cloud_reserved','semantic_processing_error','semantic_receipt','semantic_mode',
                'semantic_diagnostics','processing_failure','semantic_selected_actions'):
        diagnostics.pop(key,None)
    turn.diagnostics_json = {**diagnostics,'understanding_retry_history':history}
    turn.status='QUEUED';turn.mode=None;turn.answer=None;turn.completed_at=None
    turn.lease_owner=None;turn.lease_expires_at=None;turn.error_code=None;turn.response_meta_json={}
    return turn
