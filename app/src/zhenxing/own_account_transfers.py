"""Explicit two-owned-DEBIT cash move, not an outward transfer permission."""
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .credit_atomic import atomic_domain
from .credit_models import PaymentJournal, PaymentLedgerEntry
from .customer_profile_common import require_profile_actor
from .models import Account, Card, Confirmation, EvidenceSnapshot, OutboxMessage, Task, TaskNode, ToolCall, Transaction, new_id, utcnow
from .own_account_transfer_models import OwnAccountTransferRecord
from .payment_instruments import (_amount, _journal, _key, cash_reserved_minor,
    resolve_source_instrument, validate_instrument_snapshot)
from .planning import canonical_hash


ACTION="OWN_ACCOUNT_TRANSFER"
TOOL="own.account.transfer.v1"
PREVIEW_VERSION="own-account-transfer-preview-v1"
RECEIPT_VERSION="own-account-transfer-receipt-v1"
SCOPE_VERSION="own-account-transfer-scope-v1"
TOOL_DEFINITIONS={TOOL:{"read_only":False,"risk_level":"L3","owner":"banking.accounts"}}
SINGLE_LIMIT_MINOR=5_000_000


def _deny(code,message="请重新核对本人划转的两张借记卡、金额和当前可支出余额。",status=409):
    raise DomainError(code,message,status)


def _public(scope):
    return {"source_card_last4":scope["source"]["display_last4"],
        "destination_card_last4":scope["destination"]["display_last4"],"currency":"CNY",
        "amount_minor":scope["amount_minor"],"same_owner_internal_movement":True,
        "outward_transfer_daily_quota_consumed":False}


def _scope(source,destination,amount):
    value={"schema_version":SCOPE_VERSION,"source":deepcopy(source),"destination":deepcopy(destination),
        "amount_minor":amount,"currency":"CNY"}
    value["scope_hash"]=canonical_hash(value)
    return value


def _material(scope,task_id):
    return {"source_instrument_id":scope["source"]["instrument_id"],"destination_instrument_id":scope["destination"]["instrument_id"],
        "source_account_id":scope["source"]["account_id"],"destination_account_id":scope["destination"]["account_id"],
        "amount_minor":scope["amount_minor"],"currency":"CNY","task_id":task_id}


def checked_scope(session,principal,scope,*,lock=True):
    if not isinstance(scope,dict) or scope.get("schema_version")!=SCOPE_VERSION \
            or scope.get("scope_hash")!=canonical_hash({key:value for key,value in scope.items() if key!="scope_hash"}) \
            or scope.get("currency")!="CNY":_deny("OWN_TRANSFER_SCOPE_CHANGED")
    amount=scope.get("amount_minor");_amount(amount)
    if amount>SINGLE_LIMIT_MINOR:_deny("OWN_TRANSFER_SINGLE_LIMIT","本人单笔账户划转不超过5万元。",422)
    snapshots=[scope.get(key) for key in ("source","destination")]
    if any(not isinstance(row,dict) or row.get("instrument_type")!="DEBIT" or row.get("currency")!="CNY"
        or (row.get("sandbox_id"),row.get("user_id"))!=(principal.sandbox_id,principal.user_id) for row in snapshots):
        _deny("OWN_TRANSFER_OWN_DEBIT_REQUIRED","只能在本人两张人民币借记卡的独立资金账户之间划转。",403)
    if snapshots[0].get("account_id")==snapshots[1].get("account_id"):_deny("OWN_TRANSFER_SAME_ACCOUNT","两张卡绑定同一资金账户，不需要重复划转。",422)
    if lock:
        from .banking_locks import lock_financial_users
        lock_financial_users(session,{(principal.sandbox_id,principal.user_id)})
        list(session.scalars(select(Account).where(Account.id.in_({row["account_id"] for row in snapshots}))
            .order_by(Account.id).with_for_update().execution_options(populate_existing=True)))
    source=validate_instrument_snapshot(session,principal,snapshots[0],lock=lock,purpose="TRANSFER",amount_minor=amount,version_policy="STRICT")
    destination=validate_instrument_snapshot(session,principal,snapshots[1],lock=lock,purpose="TRANSFER",version_policy="CARD_BINDING")
    if source["display_last4"]!=snapshots[0]["display_last4"] or destination["display_last4"]!=snapshots[1]["display_last4"]:
        _deny("OWN_TRANSFER_SCOPE_CHANGED")
    return source,destination


@atomic_domain
def post_own_account_transfer(session,principal,*,source_snapshot,destination_snapshot,amount_minor,currency="CNY",operation_key,task_id=None):
    require_profile_actor(session,principal,lock=True);_key(operation_key);_amount(amount_minor)
    if currency!="CNY":_deny("OWN_TRANSFER_CURRENCY_UNSUPPORTED","本人账户划转当前仅支持人民币，不会自动换汇。",422)
    scope=_scope(source_snapshot,destination_snapshot,amount_minor)
    digest=canonical_hash(_material(scope,task_id))
    prior=session.scalar(select(OwnAccountTransferRecord).where(OwnAccountTransferRecord.sandbox_id==principal.sandbox_id,
        OwnAccountTransferRecord.user_id==principal.user_id,OwnAccountTransferRecord.operation_key==operation_key))
    if prior:
        if prior.request_hash!=digest:_deny("OWN_TRANSFER_IDEMPOTENCY_CONFLICT")
        if not own_transfer_receipt_integrity(session,principal,prior.receipt_json):_deny("OWN_TRANSFER_RECEIPT_UNVERIFIED")
        return deepcopy(prior.receipt_json)
    source,destination=checked_scope(session,principal,scope)
    first,second=session.get(Account,source["account_id"]),session.get(Account,destination["account_id"])
    if first.balance_minor-cash_reserved_minor(session,principal,first.id)<amount_minor:_deny("OWN_TRANSFER_INSUFFICIENT_FUNDS")
    if second.balance_minor+amount_minor>2_147_483_647:_deny("OWN_TRANSFER_DESTINATION_CAPACITY")
    from .operational_clock import posting_now
    posted_at=posting_now(session,principal.sandbox_id,principal.user_id)
    journal=_journal(session,principal,operation_key=operation_key,request_hash=digest,kind=ACTION,
        entries=[(first.id,"CNY",-amount_minor),(second.id,"CNY",amount_minor)])
    record=OwnAccountTransferRecord(id=new_id(),sandbox_id=principal.sandbox_id,user_id=principal.user_id,task_id=task_id,
        source_account_id=first.id,destination_account_id=second.id,source_card_id=source["card_id"],destination_card_id=destination["card_id"],
        amount_minor=amount_minor,currency="CNY",operation_key=operation_key,request_hash=digest,journal_id=journal.id,scope_json=scope,receipt_json={},created_at=posted_at)
    first.balance_minor-=amount_minor;first.version+=1;second.balance_minor+=amount_minor;second.version+=1
    from .posting import record_cash_transaction
    for account,delta,side,card_id in ((first,-amount_minor,"OUT",source["card_id"]),(second,amount_minor,"IN",destination["card_id"])):
        posted=record_cash_transaction(session,sandbox_id=principal.sandbox_id,account_id=account.id,delta_minor=delta,
            merchant="本人账户内部划转",category="本人账户划转",source_kind=ACTION,operation_ref=f"own-transfer:{record.id}:{side}")
        posted.card_id,posted.occurred_at=card_id,posted_at
    receipt={"schema_version":RECEIPT_VERSION,"action":ACTION,"status":"POSTED",**_public(scope),
        "own_transfer_id":record.id,"journal_id":journal.id,"operation_key":operation_key,
        "bank_state_changed":True,"ledger_balanced":True,"authority":"VERIFIED_SAME_OWNER_CASH_MOVEMENT"}
    # The immutable record is first flushed only after this complete body exists.
    record.receipt_json=receipt;session.add(record)
    journal.result_json=receipt
    session.flush()
    if not own_transfer_receipt_integrity(session,principal,receipt):_deny("OWN_TRANSFER_RECEIPT_UNVERIFIED",status=500)
    return deepcopy(receipt)


def own_transfer_receipt_integrity(session,principal,result):
    try:
        if not isinstance(result,dict) or result.get("schema_version")!=RECEIPT_VERSION:return False
        record=session.get(OwnAccountTransferRecord,result.get("own_transfer_id"))
        if not record or (record.sandbox_id,record.user_id)!=(principal.sandbox_id,principal.user_id) or record.receipt_json!=result:return False
        scope=record.scope_json
        if scope.get("scope_hash")!=canonical_hash({key:value for key,value in scope.items() if key!="scope_hash"}) or record.request_hash!=canonical_hash(_material(scope,record.task_id)):return False
        if (record.source_account_id,record.destination_account_id,record.amount_minor,record.currency)!=(scope["source"]["account_id"],scope["destination"]["account_id"],scope["amount_minor"],"CNY"):return False
        if (record.source_card_id,record.destination_card_id)!=(scope["source"]["card_id"],scope["destination"]["card_id"]):return False
        from .card_identifiers import card_identifier_view
        for side,identifier in (("source",record.source_card_id),("destination",record.destination_card_id)):
            snapshot=scope[side];card=session.get(Card,identifier)
            if not card or (card.user_id,card.sandbox_id)!=(principal.user_id,principal.sandbox_id):return False
            if snapshot.get("instrument_type")!="DEBIT" or snapshot.get("instrument_id")!="debit:"+identifier \
                    or snapshot.get("scope_hash")!=canonical_hash({key:value for key,value in snapshot.items() if key!="scope_hash"}):return False
            identity=card_identifier_view(session,card) or {}
            if snapshot.get("card_number")!=identity.get("card_number") or snapshot.get("display_last4")!=identity.get("display_last4",card.last4):return False
        if any(result.get(key)!=value for key,value in _public(scope).items()) or result.get("status")!="POSTED" or result.get("action")!=ACTION:return False
        if result.get("bank_state_changed") is not True or result.get("ledger_balanced") is not True \
                or result.get("authority")!="VERIFIED_SAME_OWNER_CASH_MOVEMENT" or result.get("journal_id")!=record.journal_id \
                or result.get("operation_key")!=record.operation_key:return False
        journal=session.get(PaymentJournal,record.journal_id)
        if not journal or (journal.user_id,journal.sandbox_id,journal.kind,journal.operation_key,journal.request_hash)!=(principal.user_id,principal.sandbox_id,ACTION,record.operation_key,record.request_hash) or journal.result_json!=result:return False
        legs=list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id==journal.id)))
        if len(legs)!=2 or any((row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id) for row in legs):return False
        if sorted((row.account_ref,row.currency,row.delta_minor) for row in legs)!=sorted([(record.source_account_id,"CNY",-record.amount_minor),(record.destination_account_id,"CNY",record.amount_minor)]):return False
        rows=list(session.scalars(select(Transaction).where(Transaction.operation_ref.in_([f"own-transfer:{record.id}:OUT",f"own-transfer:{record.id}:IN"]))))
        if len(rows)!=2:return False
        for account_id,side,amount,card_id in ((record.source_account_id,"OUT",-record.amount_minor,record.source_card_id),(record.destination_account_id,"IN",record.amount_minor,record.destination_card_id)):
            account=session.get(Account,account_id)
            if not account or (account.user_id,account.sandbox_id,account.currency)!=(principal.user_id,principal.sandbox_id,"CNY"):return False
            matched=[row for row in rows if (row.account_id,row.card_id,row.sandbox_id,row.source_kind,row.amount_minor,row.operation_ref)==(account_id,card_id,principal.sandbox_id,ACTION,amount,f"own-transfer:{record.id}:{side}")]
            if len(matched)!=1 or as_utc(matched[0].occurred_at)!=as_utc(record.created_at):return False
        return True
    except (KeyError,TypeError,AttributeError,ValueError):return False


def own_transfer_posting_integrity(session,record):
    """Observer proof only; does not imply a task or authority to transfer."""
    from .services import Principal
    return isinstance(record,OwnAccountTransferRecord) and own_transfer_receipt_integrity(session,
        Principal(record.user_id,record.sandbox_id),record.receipt_json)


def prepare_own_account_transfer_task(session,task,action,context=None):
    from .platform import add_task_nodes,record_policy
    from .services import Principal
    from .workflow import transition_task
    principal=Principal(task.user_id,task.sandbox_id);require_profile_actor(session,principal)
    action=action.model_dump() if hasattr(action,"model_dump") else action
    if not isinstance(action,dict) or action.get("kind")!=ACTION:_deny("OWN_TRANSFER_ACTION_INVALID")
    values=action.get("slots") or {}
    if hasattr(values,"model_dump"):values=values.model_dump(exclude_none=True)
    if set(key for key,value in values.items() if value is not None)-{"card_number","card_last4","destination_card_reference","amount_minor","currency"}:
        _deny("OWN_TRANSFER_FIELDS_INVALID")
    missing=[]
    if not values.get("card_number") and not values.get("card_last4"):missing.append("card_number")
    if not values.get("destination_card_reference"):missing.append("destination_card_reference")
    if values.get("amount_minor") is None:missing.append("amount_minor")
    if missing:
        labels={"card_number":"付款借记卡","destination_card_reference":"接收借记卡","amount_minor":"划转金额"}
        task.result_json={"error":"OWN_TRANSFER_SELECTION_REQUIRED","message":"请明确本人的"+"、".join(labels[field] for field in missing)+"，不会自动换卡。",
            "missing_fields":missing,"bank_state_changed":False};return task
    try:
        if values.get("currency") not in {None,"CNY"}:_deny("OWN_TRANSFER_CURRENCY_UNSUPPORTED",status=422)
        source=resolve_source_instrument(session,principal,values.get("card_number") or values["card_last4"],purpose="TRANSFER")
        if values.get("card_number") and values.get("card_last4"):
            second=resolve_source_instrument(session,principal,values["card_last4"],purpose="TRANSFER")
            if second["instrument_id"]!=source["instrument_id"]:_deny("OWN_TRANSFER_SOURCE_CONFLICT")
        destination=resolve_source_instrument(session,principal,values["destination_card_reference"],purpose="TRANSFER")
        scope=_scope(source,destination,values["amount_minor"])
        checked_scope(session,principal,scope,lock=False)
    except DomainError as error:
        task.result_json={"error":error.code,"message":error.message,"bank_state_changed":False}
        if error.status_code!=422:transition_task(task,"FAILED")
        return task
    args={"scope":scope}
    node={"node_id":"own-account-transfer","tool_name":TOOL,"action_kind":ACTION,"args":args,
        "read_only":False,"requires_confirmation":True,"status":"WAITING_CONFIRMATION"}
    task.plan_json={"task_id":task.id,"sandbox_id":task.sandbox_id,"policy_version":"own-account-transfer-v1","nodes":[node]}
    task.plan_hash=canonical_hash(task.plan_json)
    task.fic_json={**task.fic_json,"requested_capabilities":[TOOL],"status":"READY"}
    task.result_json={"schema_version":PREVIEW_VERSION,"action":ACTION,"preview":{**_public(scope),"bank_state_changed":False},
        "message":"请核对两张本人借记卡。确认后仅在这两个独立人民币资金账户间划转，不向他人付款，不使用信用额度。","bank_state_changed":False}
    add_task_nodes(session,task_id=task.id,sandbox_id=task.sandbox_id,plan=task.plan_json)
    record_policy(session,task_id=task.id,sandbox_id=task.sandbox_id,node_key=node["node_id"],decision="REQUIRE_CONFIRMATION",
        reason_codes=["EXPLICIT_TWO_OWNED_DEBIT_ACCOUNTS"],policy_version="own-account-transfer-v1")
    session.add(EvidenceSnapshot(task_id=task.id,sandbox_id=task.sandbox_id,source_kind="OWNED_ACCOUNT_TRANSFER_SCOPE",source_version=SCOPE_VERSION,payload_json=scope))
    session.add(Confirmation(task_id=task.id,sandbox_id=task.sandbox_id,user_id=task.user_id,node_key=node["node_id"],
        action_type=TOOL,target_id=destination["card_id"],plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash":task.plan_hash,"action":ACTION,"args":args}),expires_at=utcnow()+timedelta(minutes=5)))
    transition_task(task,"AWAITING_CONFIRMATION");session.flush();return task


def _challenge(session,task,confirmation,identifier=None,*,historical=False):
    from .runtime_models import AuthChallenge
    query=select(AuthChallenge).where(AuthChallenge.task_id==task.id,AuthChallenge.confirmation_id==confirmation.id,
        AuthChallenge.user_id==task.user_id,AuthChallenge.sandbox_id==task.sandbox_id,AuthChallenge.plan_hash==task.plan_hash,
        AuthChallenge.node_key==confirmation.node_key,AuthChallenge.status.in_({"CONSUMED"} if historical else {"VERIFIED","CONSUMED"}),
        AuthChallenge.verified_at.is_not(None))
    if identifier:query=query.where(AuthChallenge.id==identifier)
    challenges=list(session.scalars(query))
    for value in challenges:
        if historical and value.consumed_at is not None or not historical and as_utc(value.expires_at)>utcnow():return value
    _deny("OWN_TRANSFER_STEP_UP_REQUIRED","本次本人账户划转仍需完成绑定本次方案的二次验证。",403)


@atomic_domain
def commit_own_account_transfer_task(session,task,principal,confirmation,operation_key,*,request_hash=None,auth_challenge_id=None,node=None):
    from .audit import append_audit
    from .workflow import finish_node
    require_profile_actor(session,principal,lock=True)
    rows=task.plan_json.get("nodes") or [];row=rows[0] if len(rows)==1 else {}
    args=row.get("args") or {}
    if (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) or row.get("action_kind")!=ACTION or row.get("tool_name")!=TOOL \
            or canonical_hash(task.plan_json)!=task.plan_hash or not confirmation or task.state not in {"AWAITING_CONFIRMATION","PARTIAL"} \
            or (confirmation.task_id,confirmation.user_id,confirmation.sandbox_id,confirmation.node_key,confirmation.action_type,confirmation.status,confirmation.plan_hash)!=(
                task.id,task.user_id,task.sandbox_id,row.get("node_id"),TOOL,"PENDING",task.plan_hash) \
            or as_utc(confirmation.expires_at)<=utcnow() or confirmation.view_hash!=canonical_hash({"plan_hash":task.plan_hash,"action":ACTION,"args":args}):
        _deny("OWN_TRANSFER_CONFIRMATION_INVALID")
    if confirmation.target_id!=(args.get("scope",{}).get("destination") or {}).get("card_id"):_deny("OWN_TRANSFER_CONFIRMATION_INVALID")
    challenge=_challenge(session,task,confirmation,auth_challenge_id)
    node=node or session.scalar(select(TaskNode).where(TaskNode.task_id==task.id,TaskNode.node_key==row["node_id"]))
    if not node or node.status!="WAITING_CONFIRMATION" or node.tool_name!=TOOL:_deny("OWN_TRANSFER_CONFIRMATION_INVALID")
    scope=args["scope"]
    result=post_own_account_transfer(session,principal,source_snapshot=scope["source"],destination_snapshot=scope["destination"],
        amount_minor=scope["amount_minor"],currency=scope["currency"],operation_key=operation_key,task_id=task.id)
    confirmation.status,confirmation.consumed_at="CONSUMED",utcnow()
    if challenge.status=="VERIFIED":challenge.status,challenge.consumed_at="CONSUMED",utcnow()
    task.result_json=result;finish_node(session,task,node,result)
    session.add(ToolCall(sandbox_id=task.sandbox_id,task_id=task.id,tool_name=TOOL,idempotency_key=operation_key,
        request_hash=request_hash or canonical_hash({"plan_hash":task.plan_hash,"operation_key":operation_key}),status="SUCCESS",result_json=result))
    append_audit(session,sandbox_id=task.sandbox_id,task_id=task.id,event_type="OWN_ACCOUNT_TRANSFER_POSTED",payload={"own_transfer_id":result["own_transfer_id"],"currency":"CNY","amount_minor":scope["amount_minor"]})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id,task_id=task.id,event_type="TASK_COMPLETED",payload_json={"task_id":task.id,"action":ACTION}))
    return task


def completed_own_account_transfer_receipt(session,parent,operation,child,call,confirmation):
    """Immutable actual receipt, never a new transfer or current balance."""
    from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
    from .services import Principal
    try:
        actual_parent=session.get(Task,parent.id);actual_op=session.get(RuntimeOperation,operation.id);actual_child=session.get(Task,child.id)
        actual_call=session.get(ToolCall,call.id) if call is not None else None
        actual_confirmation=session.get(Confirmation,confirmation.id) if confirmation is not None else None
        if not actual_parent or not actual_op or not actual_child \
                or not actual_call or not actual_confirmation \
                or (parent.user_id,parent.sandbox_id)!=(actual_parent.user_id,actual_parent.sandbox_id):return None
        parent,operation,child=actual_parent,actual_op,actual_child
        call,confirmation=actual_call,actual_confirmation
        if parent.state=="UNKNOWN" or operation.action_kind!=ACTION or operation.status!="SUCCEEDED" \
                or operation.parent_task_id!=parent.id or operation.child_task_id!=child.id or child.state!="SUCCEEDED" \
                or (child.user_id,child.sandbox_id)!=(parent.user_id,parent.sandbox_id):return None
        root=session.get(RuntimeRecord,parent.id)
        if not root or root.kind!="PARENT" or operation.active!=1 or operation.plan_version!=root.plan_version:return None
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==root.plan_version))
        if not snapshot or snapshot.plan_hash!=parent.plan_hash or canonical_hash(snapshot.plan_json)!=parent.plan_hash:return None
        from .intelligence import action_signature
        if operation.args_json.get("kind")!=ACTION or operation.fingerprint!=canonical_hash(action_signature(operation.args_json)):return None
        matches=[row for row in snapshot.plan_json.get("nodes",[]) if row.get("operation_id")==operation.id]
        if len(matches)!=1 or matches[0].get("kind")!=ACTION or canonical_hash(matches[0].get("args"))!=canonical_hash(operation.args_json):return None
        record=session.get(RuntimeRecord,child.id)
        if not record or record.root_task_id!=parent.id or record.metadata_json.get("operation_id")!=operation.id:return None
        nodes=child.plan_json.get("nodes") or []
        if len(nodes)!=1 or canonical_hash(child.plan_json)!=child.plan_hash:return None
        node=nodes[0];args=node.get("args") or {}
        if node.get("action_kind")!=ACTION or node.get("tool_name")!=TOOL or not confirmation \
                or (confirmation.task_id,confirmation.user_id,confirmation.sandbox_id,confirmation.node_key,confirmation.action_type,confirmation.status,confirmation.plan_hash)!=(
                    child.id,parent.user_id,parent.sandbox_id,node.get("node_id"),TOOL,"CONSUMED",child.plan_hash) \
                or not confirmation.consumed_at or confirmation.view_hash!=canonical_hash({"plan_hash":child.plan_hash,"action":ACTION,"args":args}):return None
        if not call or (call.sandbox_id,call.task_id,call.tool_name,call.status,call.idempotency_key)!=(child.sandbox_id,child.id,TOOL,"SUCCESS",operation.idempotency_key) or call.result_json!=child.result_json:return None
        result=child.result_json;movement=session.get(OwnAccountTransferRecord,result.get("own_transfer_id"))
        if not movement or movement.task_id!=child.id or movement.operation_key!=operation.idempotency_key or movement.scope_json!=args.get("scope"):return None
        _challenge(session,child,confirmation,historical=True)
        return deepcopy(result) if own_transfer_receipt_integrity(session,Principal(parent.user_id,parent.sandbox_id),result) else None
    except (DomainError,KeyError,AttributeError,TypeError,ValueError):return None
