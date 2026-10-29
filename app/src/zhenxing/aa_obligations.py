"""Owned AA read identities: a collector reference is never a payer identity."""
from copy import deepcopy

from sqlalchemy import select

from .aa_participant_payments import own_invitations,payment_attempt_restartable
from .banking_extension_models import AACollectionRequest, AAParticipantIdentity
from .contracts import DomainError
from .models import utcnow, Task
from .planning import canonical_hash
from .customer_history_archive import visible_history


VERSION = "aa-obligations-read-v1"


def aa_obligations_view(session, principal, *, aa_scope="ALL", aa_note=None,aa_collection_ref=None,aa_obligation_ref=None):
    from .accounts import require_account_role
    login = require_account_role(session, principal, {"CUSTOMER", "DEV_TEST"})
    if aa_scope not in {"ALL", "PAYABLE", "COLLECTIBLE", "PENDING"}:
        raise DomainError("AA_READ_SCOPE_INVALID", "请选择本人应付、本人发起或全部AA记录。", 422)
    payable = []
    if aa_scope != "COLLECTIBLE":
        for invitation in own_invitations(session, principal):
            if aa_scope == "PENDING" and (invitation.get('status') not in {'PENDING','PAYMENT_PENDING','UNVERIFIED'} or invitation.get('expired') or invitation.get('request_status')!='ACTIVE'):
                continue
            current_task = session.get(Task, invitation["payment_task_id"]) if invitation.get("payment_task_id") else None
            can_prepare = invitation["status"] == "PENDING" or invitation["status"] == "PAYMENT_PENDING" and payment_attempt_restartable(session,principal,current_task)
            payable.append({**deepcopy(invitation), "obligation_ref": "aa-due:" + invitation["participant_id"],
                "collector_username": invitation["recipient"].get("username"),
                "effective_request_status": "EXPIRED" if invitation["expired"] else invitation["request_status"],
                "paymentable": bool(can_prepare and invitation["request_status"] == "ACTIVE" and not invitation["expired"]
                                    and invitation["recipient_available"]),
                "payment_task_state": current_task.state if current_task else None})
    collections = []
    if aa_scope not in {"PAYABLE", "PENDING"}:
        from .banking_extensions import aa_request_view
        for request in session.scalars(select(AACollectionRequest).where(AACollectionRequest.user_id == principal.user_id,
                AACollectionRequest.sandbox_id == principal.sandbox_id,
                visible_history(session, principal, AACollectionRequest.created_at)).order_by(AACollectionRequest.id)):
            collections.append({**aa_request_view(session, request), "collection_ref": "aa-collection:" + request.id,
                                "authority": "COLLECTOR_STATUS_NOT_PARTICIPANT_PAYMENT_AUTHORITY"})
    if aa_collection_ref is not None and aa_obligation_ref is not None:
        raise DomainError('AA_READ_SCOPE_INVALID','一次查询请选择收款记录或本人应付份额。',422)
    if aa_collection_ref is not None:
        if not isinstance(aa_collection_ref,str) or not aa_collection_ref.startswith('aa-collection:'):
            raise DomainError('AA_READ_SCOPE_INVALID','收款记录标识需要核对。',422)
        collections=[row for row in collections if row['collection_ref']==aa_collection_ref];payable=[]
    if aa_obligation_ref is not None:
        if not isinstance(aa_obligation_ref,str) or not aa_obligation_ref.startswith('aa-due:'):
            raise DomainError('AA_READ_SCOPE_INVALID','应付记录标识需要核对。',422)
        payable=[row for row in payable if row['obligation_ref']==aa_obligation_ref];collections=[]
    if aa_note is not None:
        if not isinstance(aa_note,str) or not aa_note.strip() or len(aa_note)>160:
            raise DomainError('AA_READ_SCOPE_INVALID','请核对完整AA名称。',422)
        note=aa_note.strip()
        payable=[row for row in payable if row.get('memo')==note]
        collections=[row for row in collections if row.get('memo')==note]
    selectors={key:value for key,value in {'aa_collection_ref':aa_collection_ref,'aa_obligation_ref':aa_obligation_ref}.items() if value is not None}
    body = {"schema_version": VERSION, "query_scope": {"aa_scope": aa_scope, **({'aa_note':aa_note.strip()} if aa_note is not None else {}),**selectors},
        "owner_scope_hash": canonical_hash({"user_id": principal.user_id, "sandbox_id": principal.sandbox_id}),
        "actor_epoch_hash": canonical_hash({"login_id": login.id, "version": login.version, "role": login.role}),
        "payable_obligations": sorted(payable, key=lambda row: row["obligation_ref"]), "collections": collections,
        "bank_state_changed": False, "authority": "OWNED_AA_REGISTERED_FACTS_NOT_PAYMENT_AUTHORIZATION",
        "message": f"已查询本人AA：应付记录{len(payable)}项，发起收款{len(collections)}项；查询不会付款。"}
    body["source_fingerprint"] = canonical_hash(body)
    body["observed_at"] = utcnow().isoformat()
    return body


def aa_read_result_current(session, principal, result):
    if (not isinstance(result, dict) or result.get("schema_version") != VERSION
            or result.get("source_fingerprint") != canonical_hash({key: value for key, value in result.items()
                if key not in {"observed_at", "source_fingerprint"}})):
        raise DomainError("AA_READ_SOURCE_CHANGED", "AA结果来源发生变化，请重新查询。", 409)
    fresh = aa_obligations_view(session, principal, **(result.get("query_scope") or {}))
    if fresh["source_fingerprint"] != result["source_fingerprint"]:
        raise DomainError("AA_READ_SOURCE_CHANGED", "AA份额、状态或有效期已经变化，请重新查询。", 409)
    return fresh


def aa_result_fact_entities(result):
    if (not isinstance(result, dict) or result.get("schema_version") != VERSION
            or result.get("source_fingerprint") != canonical_hash({key: value for key, value in result.items()
                if key not in {"observed_at", "source_fingerprint"}})):
        return []
    entities = []
    for ordinal, row in enumerate(result.get("payable_obligations", []), 1):
        facts = {key: deepcopy(row.get(key)) for key in ("collector_username", "collector_display_name", "recipient", "amount_minor", "currency", "status",
            "effective_request_status", "expires_at", "paymentable", "payment_task_state", "receipt_verified")}
        if "memo" in row:
            facts["memo"] = row["memo"]
        label = (f"{row.get('memo') or 'AA收款'} · 发起人{row.get('collector_display_name') or row['collector_username']} · "
                 f"{row['amount_minor']/100:.2f}元 · {'待支付' if row.get('paymentable') else row['status']} · 截止{row['expires_at']}")[:160]
        if "memo" not in row:
            label = f"向{row.get('collector_display_name') or row['collector_username']}（{row['collector_username']}）支付的AA份额"
        entities.append({"read_kind": "AA_VIEW", "domain": "AA", "field": "aa_obligation_ref", "value": row["obligation_ref"],
            "label": label,
            "ordinal": ordinal, "record_id": row["participant_id"],
            "record_version": row["version"], "live_record_hash": canonical_hash(row), "facts": facts})
    for ordinal, row in enumerate(result.get("collections", []), 1):
        facts = {key: deepcopy(row.get(key)) for key in ("memo","total_minor", "received_minor", "status", "request_status", "expires_at", "recipient")}
        facts["currency"] = "CNY"  # The AA obligation/settlement domain is explicitly CNY-only.
        facts["participants"] = [{key: deepcopy(participant.get(key)) for key in ("name", "amount_minor", "status", "paid_at")}
                                 for participant in row.get("participants", [])]
        entities.append({"read_kind": "AA_VIEW", "domain": "AA", "field": "aa_collection_ref", "value": row["collection_ref"],
            "label": f"本人发起的AA收款 · {row.get('memo') or '未备注'}", "ordinal": ordinal, "record_id": row["request_id"],
            "record_version": row.get("version"), "live_record_hash": canonical_hash(row),
            "facts": facts})
    if not entities:
        facts = {"aa_scope": result["query_scope"]["aa_scope"], "payable_count": 0, "collection_count": 0}
        entities.append({"read_kind": "AA_VIEW", "domain": "AA", "field": "aa_collection_summary", "value": "本人AA查询范围",
            "label": "本人AA查询结果为空", "ordinal": None, "record_id": result["owner_scope_hash"],
            "record_version": result["source_fingerprint"], "live_record_hash": canonical_hash(facts), "facts": facts})
    return entities


def owned_obligation(session, principal, obligation_ref):
    if isinstance(obligation_ref,str) and not obligation_ref.startswith('aa-due:'):
        # A name locates only this actor's invitation, never a collector's
        # authority. No fuzzy/substring match or newest-record selection.
        matches=[row for row in own_invitations(session,principal) if row.get('memo')==obligation_ref.strip()]
        if len(matches)==1:obligation_ref='aa-due:'+matches[0]['participant_id']
        elif len(matches)>1:raise DomainError('AA_OBLIGATION_AMBIGUOUS','有多笔同名AA，请按发起人或截止时间选择具体一笔。',422)
    if not isinstance(obligation_ref, str) or not obligation_ref.startswith("aa-due:"):
        raise DomainError("AA_OWNED_OBLIGATION_REQUIRED", "请先查询并选择本人应付AA份额，收款集合不能作为付款授权。", 409)
    participant_id = obligation_ref[7:]
    identity = session.scalar(select(AAParticipantIdentity).where(AAParticipantIdentity.participant_id == participant_id,
        AAParticipantIdentity.user_id == principal.user_id, AAParticipantIdentity.sandbox_id == principal.sandbox_id))
    if identity is None:
        raise DomainError("AA_INVITATION_NOT_FOUND", "该份额不属于本人。", 404)
    return identity


def prepare_aa_view_task(session, *, task, conversation_id, proposed_action):
    from .services import Principal
    from .models import EvidenceSnapshot
    from .contracts import FinancialIntentContract
    from .platform import add_task_nodes, append_message
    from .workflow import transition_task
    from .audit import append_audit
    values = (proposed_action or {}).get("slots") or {}
    if (proposed_action or {}).get("kind") != "AA_VIEW" or {key for key, value in values.items() if value is not None} - {"aa_scope","aa_note","aa_collection_ref","aa_obligation_ref"}:
        raise DomainError("AA_READ_SCOPE_INVALID", "AA查询参数范围不一致。", 409)
    result = aa_obligations_view(session, Principal(task.user_id, task.sandbox_id), aa_scope=values.get("aa_scope") or "ALL",aa_note=values.get('aa_note'),
        aa_collection_ref=values.get('aa_collection_ref'),aa_obligation_ref=values.get('aa_obligation_ref'))
    evidence = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="OWNED_AA_OBLIGATION_SNAPSHOT",
        source_version=VERSION, payload_json=result)
    session.add(evidence); session.flush()
    task.plan_json = {"nodes": [{"node_id": "read-aa-obligations", "tool_name": "transfer.aa.view.v1", "read_only": True,
        "status": "SUCCEEDED", "evidence_id": evidence.id, "args": result["query_scope"]}]}
    task.plan_hash = canonical_hash(task.plan_json)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.requested_capabilities, fic.status = ["transfer.aa.view.v1"], "READY"
    task.fic_json, task.result_json = fic.model_dump(), result
    transition_task(task, "SUCCEEDED")
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="AA_OBLIGATIONS_READ",
        payload={"scope": result["query_scope"], "source_fingerprint": result["source_fingerprint"]})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id, task_id=task.id,
        role="ASSISTANT", content=result["message"])
    return task


from .aa_obligation_identity import aa_obligation_identity_current  # noqa: E402,F401
