"""Shared owned-turn state for production semantics and legacy adapters.

No natural-language classifier or routing rules live in this module.
"""
from .models import utcnow
from .runtime_models import RuntimeRecord
from .services import Principal
from .language import detect_reply_language, english_amount_fragment

IN_FLIGHT = {"EXECUTING", "UNKNOWN", "AUTHORIZED"}


def current_conversation_pause(session, principal, task):
    """Version-aware bridge: only persisted legacy pauses load their adapter."""
    record = session.get(RuntimeRecord, task.id) if task else None
    metadata = record.metadata_json if record else {}
    if metadata.get('semantic_pause'):
        from .semantic_lifecycle import current_semantic_pause
        return current_semantic_pause(session, principal, task)
    if metadata.get('dialogue_pause'):
        from .conversation_service import current_transfer_pause
        return current_transfer_pause(session, principal, task)
    return None


def _turn_language(turn):
    return detect_reply_language(turn.message, (turn.diagnostics_json or {}).get("reply_language"))


def _task_epoch_current(session,principal,task):
    record=session.get(RuntimeRecord,task.id)
    expected=(record.metadata_json or {}).get("dialogue_actor_scope_hash") if record else None
    if not expected:
        return True
    from .dialogue_state import dialogue_actor_scope
    return dialogue_actor_scope(session,principal)[1]==expected


def _attachment_sources(session, principal, refs):
    from .attachments import attachment_evidence_for_conversation
    records = []
    for ref in refs:
        evidence = attachment_evidence_for_conversation(session, principal, ref["review_id"], ref["attachment_version"], ref["sha256"])
        records.append({"source_id": "review:" + ref["review_id"], "title": "本人核对的文档字段（非银行执行依据）",
            "kind": "USER_VALIDATED_DOCUMENT", "reference": "attachment-review:" + ref["review_id"],
            "content": str(evidence.get("fields", {})) + "\n文档事实不证明银行完成，也不授权付款、改绑或跳过确认。",
            "attachment_version": ref["attachment_version"], "sha256": ref["sha256"], "bank_authoritative": False})
    return records


def _finish_status(session, principal, turn, task):
    """Current runtime outcome, never an old assistant acknowledgement."""
    from .task_dialogue_context import task_context
    context = task_context(session, principal, task)
    english = _turn_language(turn) == "en"
    state, kind = task.state, context.get("outcome_kind") if context else None
    verified = bool(context and context.get("verified"))
    if verified and kind == "QUERY_RESULT":
        answer = "It's ready. The query has completed; you can see the results in the previous message." if english else "已经查好了，结果可以在上一条消息里查看。你也可以继续告诉我想重点看哪一部分。"
    elif verified and kind == "BUSINESS_RECEIPT":
        answer = "The linked operation is complete and its receipt is verified. See its result in the previous message." if english else "这笔业务已办理完成，回执也已核验，可以在上一条办理结果里查看。"
        from .semantic_status_facts import completed_transfer_facts,render_transfer_status
        receipt_facts=completed_transfer_facts(session,principal,task)
        receipt_answer=render_transfer_status(receipt_facts,'en' if english else 'zh-CN')
        if receipt_answer:answer=receipt_answer
        from .own_transfer_status_facts import completed_own_scope_status
        own_status=completed_own_scope_status(session,principal,task,'en' if english else 'zh-CN')
        if own_status is not None:answer,receipt_facts=own_status
    elif verified and kind == "PROPOSAL_SAVED":
        answer = "The suggestion has been saved. The account owner still needs to review any actual banking action." if english else "协助建议已经保存，实际银行业务仍需账户本人核对和办理。"
    elif state == "NEED_CLARIFICATION":
        fields = {field for row in (context or {}).get("needed_fields", []) for field in row.get("missing_fields", [])}
        labels = {"payee_name": "收款人", "product_name": "理财产品", "merchant": "订阅名称", "card_number": "具体卡片", "amount_minor": "金额"}
        needed = "、".join(labels[field] for field in labels if field in fields)
        answer = "I still need the missing details before preparing this request. You can reply here; the information you already provided is retained." if english else (f"还需要补充{needed}，你直接在这里告诉我就行，已经明确的信息会保留。" if needed else "还需要你补充上一条提到的信息，直接在这里回复就行，已经明确的信息会保留。")
    elif state == "AWAITING_CONFIRMATION":
        if (context or {}).get("confirmation_status") == "EXPIRED":
            answer = "The confirmation expired. Prepare and review a fresh plan before confirming; this operation has not been executed." if english else "这份方案的确认有效期已经过了，需要重新准备并核对，不能使用原确认；这笔业务还没有执行。"
        elif (context or {}).get("confirmation_status") == "UNAVAILABLE":
            answer = "The confirmation details are no longer available. Prepare and review the plan again; this operation has not been executed." if english else "确认所需的资料已经变化，需要重新准备并核对方案；这笔业务还没有执行。"
        else:
            answer = "The plan is ready and waiting for your review and confirmation. It has not been executed." if english else "方案已经准备好了，正在等你核对并确认；这笔业务还没有执行。"
        if (context or {}).get("completed_write_present") or (context or {}).get("possible_write_effect"):
            answer = "The current step is waiting for review. Earlier steps may already have completed or have an unverified outcome; check their receipts. This does not mean the whole request was unexecuted." if english else "当前这一步还在等你核对确认；前面已有完成或结果待核验的步骤，请分别查看原回执，不能把整个需求当成尚未执行。"
    elif state == "UNKNOWN":
        answer = "The outcome is not yet verified. Check the original receipt before deciding what to do; do not repeat the payment." if english else "这笔业务的结果还没有确定，需要先核验原回执，请不要重复付款。"
    elif state in IN_FLIGHT:
        answer = "It's still being processed. I'll keep the original request; please do not repeat it." if english else "还在办理中，原请求会保留，请先等待结果，不要重复提交。"
    elif state == "PARTIAL":
        answer = "Some steps completed, but the whole request is not finished. See the individual results before continuing." if english else "部分步骤已经完成，但整个需求还没有办完，可以查看各项结果后继续处理。"
    elif state in {"FAILED", "CLOSED"}:
        answer = "This request did not complete successfully. Any earlier completed steps remain in its record." if english else "这项需求没有完整办成；如果之前有已完成的步骤，记录和回执仍会保留。"
    elif state == "SUCCEEDED":
        answer = "The request is marked complete, but its business result still needs verification. I won't treat it as a completed payment." if english else "请求显示已完成，但业务结果还需要核验，暂时不能据此认定资金操作已经成功。"
    else:
        answer = "I'm still preparing this request. You don't need to send it again." if english else "还在整理和准备这项需求，你不需要再发一次。"
    semantic=(turn.diagnostics_json or {}).get('semantic_receipt') or {}
    from .life_status_summary import completed_life_status_summary
    life_summary=completed_life_status_summary(session,principal,[task],[context],'en' if english else 'zh-CN')
    if life_summary is not None:answer=life_summary['answer']
    if 'STATUS' in (semantic.get('decision') or {}).get('question_aspects',[]):
        from .subscription_status_summary import completed_subscription_status_summary
        subscription_summary=completed_subscription_status_summary(session,principal,[task],[context],'en' if english else 'zh-CN')
        if subscription_summary is not None:answer=subscription_summary['answer']
    _finish(turn, "STATUS", answer, task, "STATUS")
    # English STATUS used to overwrite a factual answer with a generic warning.
    turn.answer = answer
    turn.response_meta_json = {**(turn.response_meta_json or {}), "status_projection": {
        "task_id": task.id, "version": task.version, "state": state,
        "outcome_kind": kind, "verified": verified, "confirmation_status": (context or {}).get("confirmation_status", "NONE")}}
    if 'receipt_facts' in locals() and receipt_facts:
        # Keep the established historical status projection shape unchanged.
        # The verified text is retained by its existing immutable snapshot;
        # receipt provenance is separate and never a client authorization.
        turn.response_meta_json['receipt_facts']=receipt_facts
    from .conversation_status_snapshot import retain_status_snapshot
    retain_status_snapshot(session, principal, turn, task, context)


def _finish(turn, mode, answer, task=None, relationship=None, fields=None, sources=None):
    if _turn_language(turn) == "en" and mode != "CONSULT":
        if mode == "CONFIRM_HINT":
            answer = "Chat messages, passwords and verification codes cannot authorize a bank operation. Review the current confirmation card and use its dedicated confirmation and verification controls. No bank operation was performed by this message."
        elif mode == "NEW_TASK":
            answer = "I've received your request and am preparing the relevant information. Any change to funds, cards or permissions still requires you to review and confirm the actual plan."
        elif mode == "REVISE":
            answer = "I've updated this request using your original instruction and this correction. The previous confirmation cannot be reused. Please review the new confirmation card."
        elif mode == "CANCEL_HINT":
            answer = ("This request is still processing or its outcome is not yet verified. Check the original request and receipt; do not repeat the payment. " if task and task.state in IN_FLIGHT else "Open the relevant request to check whether cancellation is currently available. A completed operation cannot be reversed by a chat message. ") + "This message did not cancel, reverse or reissue a bank operation."
        elif mode == "STATUS":
            state = {"UNKNOWN": "not yet verified", "EXECUTING": "processing", "AUTHORIZED": "authorized; processing is not yet confirmed", "SUCCEEDED": "completed", "FAILED": "failed", "NEED_CLARIFICATION": "waiting for details", "AWAITING_CONFIRMATION": "waiting for your confirmation", "CLOSED": "closed"}.get(task.state if task else "", "waiting for the original request status")
            answer = f"The linked request is {state}. Check its actual business receipt for the result; a chat message or screenshot is not proof of payment."
        elif mode == "NEED_CONTEXT":
            if task and task.state in IN_FLIGHT:
                answer = "Check the original request first. It is still processing or its result is not yet verified; this message will not repeat a payment or change its scope."
            elif "收款人" in answer and "还需要" in answer:
                answer = "I still need the exact recipient. Your existing amount and currency are preserved. Provide the recipient's exact account name or card number."
            elif "产品" in answer and "还需要" in answer:
                answer = "I still need the exact investment product name. Your existing amount is preserved. Please use its original name."
            elif "卡片" in answer and "还需要" in answer:
                answer = "I still need the exact card to change. Your amount is preserved. Provide your full card number or a unique last-four identifier."
            elif "金额" in answer or english_amount_fragment(turn.message):
                answer = "Your original request is preserved. Provide one exact positive amount and an explicit CNY or USD denomination. Approximate amounts, ranges and 'all my balance' cannot be used for a bank operation. Funding and receiving currencies are separate instructions."
            else:
                answer = "Please identify the one unfinished request and the exact detail you want to change. Completed, processing or unverified requests cannot be repeated or modified with a short reply. No bank operation was performed."
    turn.status, turn.mode, turn.answer, turn.completed_at = "ANSWERED", mode, answer, utcnow()
    turn.task_id = task.id if task else None
    turn.task_relationship = relationship
    turn.field_sources_json = fields or []
    turn.sources_json = sources or []
    if task and mode in {"NEW_TASK","REVISE"}:
        from sqlalchemy.orm import object_session
        session=object_session(turn)
        record=session.get(RuntimeRecord,task.id) if session is not None else None
        epoch=(turn.diagnostics_json or {}).get("dialogue_actor_scope_hash")
        if record is not None and epoch:
            record.metadata_json={**record.metadata_json,"dialogue_actor_scope_hash":epoch}


def _turn_principal(session, turn):
    from .runtime import trusted_family_context
    principal = Principal(turn.user_id, turn.sandbox_id,
        family_context=(turn.diagnostics_json or {}).get("family_context_origin"))
    trusted_family_context(session, principal)
    return principal
