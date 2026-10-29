"""Read-only review of frozen scheduled-transfer and AA invitation authority.

Never reparses a summary, writes a plan, reserves money, or authorizes a payer.
Execution continues to use the existing confirmation/OTP/domain gates.
"""
from copy import deepcopy
from datetime import datetime,timedelta
import re

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .models import TaskNode,Payee,utcnow
from .planning import canonical_hash

VERSION='customer-banking-confirmation-v1'
TOOLS={'TRANSFER_SCHEDULE':'transfer.schedule.v1','AA_REQUEST':'transfer.aa.v1'}
SCOPE_FIELDS={'TRANSFER_SCHEDULE':{'source_account_id','payee_id','payee_name','transfer_channel',
    'recipient_username','recipient_account_id','payee_last4','amount_minor','memo','due_at','max_amount_minor',
    'clock_basis','time_zone','transfer_payment_scope','recurrence','recipient_context','replaces_schedule','timing_policy'},
    'AA_REQUEST':{'account_id','total_minor','participant_count','participants','remainder_rule',
        'aa_collector_scope','aa_expires_at','aa_note'}}


def _deny():
    raise DomainError('BANKING_CONFIRMATION_SCOPE_UNAVAILABLE','本次确认范围暂时无法完整核验，请重新准备；尚未执行。',409)


def _date(value):
    if not isinstance(value,str):_deny()
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:_deny()
    return as_utc(result)


def _money(value):
    if type(value) is not int or not 0<value<=2**53-1:_deny()
    return value


def _card(value):
    if not isinstance(value,dict) or value.get('instrument_type')!='DEBIT' or value.get('currency')!='CNY' \
            or not isinstance(value.get('display_last4'),str) or not re.fullmatch(r'[0-9a-fA-F]{4}',value['display_last4']):_deny()
    return {key:value[key] for key in ('display_last4','instrument_type','currency')}


def _base(session,principal,parent,operation,child,confirmation,action,now):
    from .customer_profile_common import require_profile_actor
    from .life_confirmation import _runtime_binding
    owner=require_profile_actor(session,principal)
    if action not in TOOLS or operation.action_kind!=action:_deny()
    _runtime_binding(session,principal,parent,operation,child)
    from .conversation_provenance import validate_current_continuation
    from .runtime_models import RuntimeRecord,RuntimeOperation
    record=session.get(RuntimeRecord,parent.id);phase=None
    if (record.metadata_json or {}).get('cloud_semantic_intent'):
        from .intelligence import READ_KINDS
        previous=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,
            RuntimeOperation.active==1,RuntimeOperation.status=='SUCCEEDED')))
        if any(row.action_kind not in READ_KINDS for row in previous):
            from .semantic_phase_context import build_semantic_phase_context
            phase=build_semantic_phase_context(session,parent,operation,'CONFIRM_NEXT_STEP')
    validate_current_continuation(session,parent,phase_context=phase)
    rows=(child.plan_json or {}).get('nodes',[])
    row=rows[0] if len(rows)==1 else {}
    scope={key:deepcopy(value) for key,value in row.items() if key not in {'node_id','tool_name','read_only','requires_confirmation'}}
    fic=child.fic_json or {};slots=fic.get('slots') or {}
    if ((child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id)
            or child.intent_type!=action or child.state!='AWAITING_CONFIRMATION'
            or child.plan_json.get('task_id')!=child.id or child.plan_json.get('sandbox_id')!=child.sandbox_id
            or child.plan_json.get('policy_version')!='banking-extra-v1' or not set(scope)<=SCOPE_FIELDS[action]
            or canonical_hash(child.plan_json)!=child.plan_hash or row.get('tool_name')!=TOOLS[action]
            or row.get('read_only') is not False or row.get('requires_confirmation') is not True
            or confirmation is None or confirmation.task_id!=child.id or confirmation.user_id!=principal.user_id
            or confirmation.sandbox_id!=principal.sandbox_id or confirmation.status!='PENDING'
            or confirmation.action_type!=TOOLS[action] or confirmation.node_key!=row.get('node_id')
            or confirmation.plan_hash!=child.plan_hash or as_utc(confirmation.expires_at)<=now
            or confirmation.view_hash!=canonical_hash({'plan_hash':child.plan_hash,'scope':scope})
            or fic.get('status')!='READY' or fic.get('requested_capabilities')!=[TOOLS[action]] or set(slots)!=set(scope)
            or any(not isinstance(slots[key],dict) or slots[key].get('value')!=value for key,value in scope.items())):_deny()
    node=session.scalar(select(TaskNode).where(TaskNode.task_id==child.id,TaskNode.sandbox_id==principal.sandbox_id,
        TaskNode.node_key==confirmation.node_key))
    if node is None or node.tool_name!=TOOLS[action] or node.status!='WAITING_CONFIRMATION':_deny()
    return owner,scope


def _schedule(session,principal,child,confirmation,scope,selected):
    replacement = scope.get('replaces_schedule')
    if replacement:
        from .schedule_amendments import validate_replacement
        validate_replacement(session, principal, replacement)
    from .banking_extensions import _schedule_clock,TRANSFER_LIMIT_MINOR
    from .transfer_payment_scope import (validate_transfer_payment_scope,transfer_confirmation_scope,
        selected_source_reference,selected_recipient_reference)
    from .payment_instruments import resolve_source_instrument
    from .internal_banking import _validate_expected_scope
    payment=scope.get('transfer_payment_scope');display=transfer_confirmation_scope(payment)
    if display is None or not isinstance(payment.get('recipient'),dict):_deny()
    amount=_money(scope.get('amount_minor'))
    if (scope.get('max_amount_minor')!=TRANSFER_LIMIT_MINOR or amount>TRANSFER_LIMIT_MINOR
            or display['amount_minor']!=amount or scope.get('transfer_channel')!='INTERNAL_BANK'
            or scope.get('source_account_id')!=payment['source'].get('account_id')
            or scope.get('recipient_account_id')!=payment['recipient'].get('account_id')
            or scope.get('recipient_username')!=payment['recipient'].get('username')
            or selected.get('amount_minor') not in {None,amount}):_deny()
    source=validate_transfer_payment_scope(session,principal,payment,scheduled=True,require_funds=False,lock=False)
    reference=selected_source_reference(session,principal,selected)
    if replacement and reference is None: reference = 'debit:' + source['card_id']
    if resolve_source_instrument(session,principal,reference,'CNY',purpose='TRANSFER')['instrument_id']!=source['instrument_id']:_deny()
    payee=session.get(Payee,scope.get('payee_id'))
    if not payee or (payee.user_id,payee.sandbox_id)!=(principal.user_id,principal.sandbox_id) or payee.status!='ACTIVE' \
            or confirmation.target_id!=payee.id or payee.account_last4!=scope.get('payee_last4'):_deny()
    _validate_expected_scope(session,payee,{key:scope.get(key) for key in ('transfer_channel','recipient_username','recipient_account_id')})
    from .contacts import recipient_context
    bound=scope.get('recipient_context')
    if not isinstance(bound,dict) or recipient_context(session,principal.sandbox_id,principal.user_id,bound.get('requested_reference'))!=bound:_deny()
    if selected.get('payee_name'):
        requested=selected_recipient_reference(session,principal,selected['payee_name'],selected.get('recipient_card_reference'))
        current=recipient_context(session,principal.sandbox_id,principal.user_id,requested)
        if (current.get('recipient_username'),current.get('recipient_bank_account_id'))!=(scope['recipient_username'],scope['recipient_account_id']):_deny()
    basis=scope.get('clock_basis');due=_date(scope.get('due_at'))
    if basis not in {'CALENDAR_REAL','BANK_CONTROLLED'} or scope.get('time_zone')!='Asia/Shanghai':_deny()
    _,clock=_schedule_clock(session,child,basis=basis)
    if due<=as_utc(clock):_deny()
    recurrence=scope.get('recurrence');frequency='ONCE';dates=[due]
    if recurrence is not None:
        if not isinstance(recurrence,dict) or recurrence.get('version')!='bounded-transfer-recurrence-v1' \
                or recurrence.get('frequency') not in {'DAILY','WEEKLY','MONTHLY','EXPLICIT'} \
                or type(recurrence.get('occurrence_count')) is not int or not 1<=recurrence['occurrence_count']<=366 \
                or not isinstance(recurrence.get('dates'),list) or len(recurrence['dates'])!=recurrence['occurrence_count'] \
                or recurrence.get('failure_policy')!='STOP_REMAINING' \
                or recurrence.get('month_end_policy')!=('EXPLICIT_USER_TIMES' if recurrence.get('frequency')=='EXPLICIT' else 'CLAMP_ORIGINAL_DAY_NO_DRIFT'):_deny()
        frequency=recurrence['frequency'];dates=[_date(value) for value in recurrence['dates']]
        from .transfer_recurrence import occurrence_dates
        if frequency=='EXPLICIT':
            from .transfer_recurrence import explicit_occurrence_dates
            if replacement and not selected.get('schedule_occurrence_times'):
                if dates != [_date(value) for value in replacement['remaining_dates']]: _deny()
            elif dates!=explicit_occurrence_dates(selected.get('schedule_occurrence_times'),due):_deny()
        elif dates!=occurrence_dates(due,frequency,len(dates),anchor_day=recurrence.get('anchor_day')): _deny()
    if selected.get('schedule_frequency') not in {None,frequency} \
            or selected.get('schedule_occurrence_count') not in {None,len(dates)}: _deny()
    memo=scope.get('memo')
    if not isinstance(memo,str):_deny()
    recipient={**_card(payment['recipient']),'username':scope['recipient_username'],
        'display_name':scope.get('payee_name')}
    # Read the name of the already verified instrument owner; do not resolve a
    # new recipient or mutate the frozen authorization for presentation.
    from .models import User,Account
    receiver=payment['recipient']
    receiver_account=session.get(Account,receiver.get('account_id'))
    receiver_user=session.get(User,receiver.get('user_id'))
    if receiver_account is not None and receiver_user is not None and (
            receiver_account.user_id,receiver_account.sandbox_id,receiver_user.sandbox_id)==(
            receiver_user.id,receiver.get('sandbox_id'),receiver.get('sandbox_id')) \
            and isinstance(receiver_user.display_name,str) and receiver_user.display_name.strip():
        recipient['display_name']=receiver_user.display_name
    if not isinstance(recipient['username'],str) or not recipient['username'] or not isinstance(recipient['display_name'],str) or not recipient['display_name']:_deny()
    return {'source':_card(source),'recipient':recipient,'amount_minor':amount,'memo':memo,'due_at':due.isoformat(),
        'timezone':'Asia/Shanghai','clock_basis':basis,'frequency':frequency,'total_occurrences':len(dates),
        'end_at':None if frequency=='ONCE' else dates[-1].isoformat(),'last_due_at':dates[-1].isoformat(),
        'total_budget_minor':_money(amount*len(dates)),'funds_reserved':False,'future_execution_revalidation':True,
        'failure_policy':'STOP_REMAINING','month_end_policy':'CLAMP_ORIGINAL_DAY_NO_DRIFT' if frequency=='MONTHLY' else None,
        **({'occurrence_dates':[value.isoformat() for value in dates]} if frequency=='EXPLICIT' else {}),
        **({'replaces_future_occurrences':True} if replacement else {}),
        **({'default_execution_time':scope['timing_policy'].get('time_origin')=='DEFAULT_09_00'} if scope.get('timing_policy') else {})}


def _aa(session,principal,owner,confirmation,scope,selected,now):
    from .banking_extensions import TRANSFER_LIMIT_MINOR
    from .payment_instruments import validate_recipient_snapshot,resolve_recipient_instrument
    from .access_models import LoginAccount
    from .internal_banking import _bound_entities,_validate_expected_scope
    collector=validate_recipient_snapshot(session,scope.get('aa_collector_scope'),lock=False)
    login=session.scalar(select(LoginAccount).where(LoginAccount.user_id==principal.user_id,LoginAccount.sandbox_id==principal.sandbox_id))
    if not login or login.status!='ACTIVE' or login.role not in {'CUSTOMER','DEV_TEST'} \
            or collector.get('username')!=login.username or collector.get('user_id')!=principal.user_id \
            or collector.get('sandbox_id')!=principal.sandbox_id or collector.get('account_id')!=scope.get('account_id') \
            or confirmation.target_id!=scope.get('account_id'):_deny()
    expected=resolve_recipient_instrument(session,login.username,selected.get('recipient_card_reference'),'CNY',caller_principal=principal)
    if expected.get('instrument_id')!=collector.get('instrument_id'):_deny()
    total=_money(scope.get('total_minor'));people=scope.get('participants');count=scope.get('participant_count')
    if total>TRANSFER_LIMIT_MINOR or type(count) is not int or not 2<=count<=20 or not isinstance(people,list) or len(people)!=count \
            or scope.get('remainder_rule')!='名单前序参与者各多一分' or selected.get('amount_minor') not in {None,total} \
            or selected.get('participant_count') not in {None,count}:_deny()
    raw_names=selected.get('participant_names')
    if raw_names is not None and (not isinstance(raw_names,list) or len(raw_names)!=count):_deny()
    output=[];seen=set();base,remainder=divmod(total,count)
    for index,person in enumerate(people):
        if not isinstance(person,dict) or set(person)!={'payee_id','name','last4','amount_minor','recipient_scope'} \
                or _money(person.get('amount_minor'))!=base+(index<remainder):_deny()
        payee=session.get(Payee,person.get('payee_id'))
        if not payee or (payee.sandbox_id,payee.user_id)!=(principal.sandbox_id,principal.user_id) or payee.status!='ACTIVE' \
                or payee.display_name!=person.get('name') or payee.account_last4!=person.get('last4'):_deny()
        _validate_expected_scope(session,payee,person.get('recipient_scope'))
        binding,participant,bank=_bound_entities(session,payee)
        if not binding or not participant or participant.status!='ACTIVE' or participant.role not in {'CUSTOMER','DEV_TEST'} \
                or participant.id in seen:_deny()
        if raw_names is not None:
            from .contacts import recipient_context
            from .aa_participant_reference import verified_aa_participant_reference
            reference=verified_aa_participant_reference(session,raw_names[index])
            current=recipient_context(session,principal.sandbox_id,principal.user_id,reference)
            if current.get('recipient_login_account_id')!=participant.id or current.get('recipient_username')!=participant.username:_deny()
        seen.add(participant.id)
        output.append({'username':participant.username,'display_name':person['name'],'amount_minor':person['amount_minor']})
    expiry=_date(scope.get('aa_expires_at'))
    if not now<expiry<=now+timedelta(days=30):_deny()
    if scope.get('aa_note') != selected.get('aa_note'):
        _deny()
    note = scope.get('aa_note')
    if note is not None and (not isinstance(note,str) or not 1 <= len(note) <= 160):
        _deny()
    return {**({'memo':note} if note else {}),'collector':{**_card(collector),'username':login.username,'display_name':owner.display_name},
        'total_minor':total,'participant_count':count,'participants':output,'expires_at':expiry.isoformat(),
        'timezone':'Asia/Shanghai','remainder_rule':'LIST_ORDER_EXTRA_CENT','invitation_only':True,'automatic_debit':False}


def customer_banking_confirmation(session,principal,child,confirmation,action,*,parent,operation,now=None):
    try:
        now=as_utc(now or utcnow())
        owner,scope=_base(session,principal,parent,operation,child,confirmation,action,now)
        selected=(operation.args_json or {}).get('slots') or {}
        fields=(_schedule(session,principal,child,confirmation,scope,selected) if action=='TRANSFER_SCHEDULE'
            else _aa(session,principal,owner,confirmation,scope,selected,now))
        return {'schema_version':VERSION,'action':action,'scope_verified':True,'currency':'CNY',
            'confirmation_expires_at':as_utc(confirmation.expires_at).isoformat(),**fields}
    except (DomainError,KeyError,TypeError,ValueError,AttributeError,IndexError):
        _deny()
