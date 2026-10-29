"""Customer-facing projections; persisted plans and financial controls stay intact."""
from copy import deepcopy
from datetime import datetime
import json
import re

from .redaction import redact_credentials


_INTERNAL_FIELDS = {
    "diagnostics", "planning_diagnostics", "model_decisions", "model_label",
    "laya_advice", "field_sources", "fic", "evidence", "sources",
    "source_id", "evidence_id", "source_reference", "source_version",
    "policy", "policies", "policy_version", "plan_version", "audit_chain_valid",
    "idempotency_key", "request_hash", "scope_hash", "view_hash", "metadata_json",
    "reasoning_content", "chain_of_thought", "raw_response", "raw_prompt",
    "customer_reply", "read_selection", "read_coverage", "customer_read_question_facts", "balance_scope_refs",
    "owner_scope_hash", "owner_scope", "source_fingerprint", "read_fingerprint",
    "credit_pool_contract",
    "wealth_suitability_snapshot",
    "_verified_planning_failure",
}
_STATUS = {
    "QUEUED": "正在受理", "UNDERSTANDING": "正在整理您的需求", "PLANNING": "正在准备办理方案",
    "NEED_CLARIFICATION": "需要您补充信息", "AWAITING_CONFIRMATION": "等待您核对并确认",
    "AUTHORIZED": "已收到确认，等待办理", "EXECUTING": "正在办理", "UNKNOWN": "结果尚未确定，请先查询原请求，不要重复付款",
    "PARTIAL": "部分步骤已完成，请查看各项结果", "WAITING": "等待后续办理",
    "SUCCEEDED": "已完成", "FAILED": "本次办理未完成，请查看具体说明", "CLOSED": "请求已关闭，已完成业务保留",
}


def customer_business_value(value):
    """Remove internal observations, not receipt proof or opaque business IDs.

card_version and other business receipt fields are deliberately retained: they
are required by the UI's recorded-result predicates, not shown as diagnostics.
"""
    if isinstance(value, dict):
        if value.get('schema_version') == 'own-account-transfer-receipt-v1':
            public_fields = {'schema_version', 'action', 'status', 'source_card_last4', 'destination_card_last4',
                'currency', 'amount_minor', 'same_owner_internal_movement', 'outward_transfer_daily_quota_consumed',
                'bank_state_changed', 'ledger_balanced', 'authority', 'message', 'posted_at'}
            return {key: customer_business_value(item) for key, item in value.items() if key in public_fields}
        return {key: customer_business_value(item) for key, item in value.items() if key not in _INTERNAL_FIELDS}
    if isinstance(value, list):
        return [customer_business_value(item) for item in value]
    if isinstance(value, str):
        return redact_credentials(value)
    return deepcopy(value)


def customer_answer(answer):
    # Headings, JSON and status strings inside quoted/model text are not facts.
    from .customer_copy import strip_customer_boilerplate
    return strip_customer_boilerplate(redact_credentials(answer)) if isinstance(answer, str) else answer


def customer_thread_view(view):
    return {key: customer_business_value(view[key]) for key in (
        "conversation_id", "title", "status", "version", "current_task_id", "created_at", "updated_at") if key in view}


def _customer_status_projection(view):
    """Only the already-owned server status projection, bound to its live link."""
    meta=view.get('response_meta'); link=view.get('task_link')
    value=meta.get('status_projection') if isinstance(meta,dict) else None
    if not isinstance(value,dict) or not isinstance(link,dict) or view.get('status')!='ANSWERED' or view.get('mode')!='STATUS': return None
    if not all(key in value for key in ('task_id','version','state','outcome_kind','verified','confirmation_status')) \
        or not isinstance(value['task_id'],str) or not value['task_id'] or value.get('task_id')!=link.get('task_id') or type(value.get('version')) is not int or value['version']<1 \
        or value['version']!=link.get('version') or value.get('state')!=link.get('state') or value.get('state') not in _STATUS \
        or type(value.get('verified')) is not bool or value.get('outcome_kind') not in {None,'PROCESS','UNKNOWN','FAILURE','PARTIAL_RESULT','QUERY_RESULT','BUSINESS_RECEIPT','PROPOSAL_SAVED'} \
        or value.get('confirmation_status') not in {'NONE','PENDING','EXPIRED','UNAVAILABLE'}:
        return None
    if value['verified'] and (value['state']!='SUCCEEDED' or value['outcome_kind'] not in {'QUERY_RESULT','BUSINESS_RECEIPT','PROPOSAL_SAVED'}): return None
    return {key:deepcopy(value[key]) for key in ('task_id','version','state','outcome_kind','verified','confirmation_status')}


def _customer_reply_snapshot(view,language):
    """Shape-only BFF gate after the reader's current ownership/role checks.

    No diagnostics, prompts, source IDs, permissions or confirmation data can
    pass this whitelist. This historical text is not a live banking authority.
    """
    status=view.get('reply_snapshot_status','NOT_RETAINED')
    statuses={'RETAINED','NOT_APPLICABLE','NOT_RETAINED','INVALID_NOT_RETAINED','ROLE_CHANGED','CAPACITY_REACHED','UNAUTHORIZED','INVALID'}
    if status not in statuses: return None,'INVALID'
    if status!='RETAINED': return None,status
    value=view.get('reply_snapshot'); link=view.get('task_link')
    if not isinstance(value,dict) or not isinstance(link,dict) or view.get('status')!='ANSWERED' or view.get('mode') not in {'NEW_TASK','REVISE','CLARIFY'}: return None,'INVALID'
    fields=('schema_version','conversation_id','turn_id','task_id','task_version','state','reply_language','actor_role','fingerprint','intro','detail','text','observed_at','authority')
    if not all(key in value for key in fields) or any(not isinstance(value[key],str) or not value[key] or len(value[key])>120 for key in ('conversation_id','turn_id','task_id')): return None,'INVALID'
    if value.get('schema_version')!='conversation-reply-snapshot-v1' or value.get('authority')!='HISTORICAL_NARRATION_NOT_CURRENT_STATUS_OR_AUTHORIZATION' \
        or value.get('conversation_id')!=view.get('conversation_id') or value.get('turn_id')!=view.get('turn_id') or value.get('task_id')!=link.get('task_id') \
        or type(value.get('task_version')) is not int or type(link.get('version')) is not int or not 1<=value['task_version']<=link['version'] \
        or value.get('state') not in {'NEED_CLARIFICATION','AWAITING_CONFIRMATION','SUCCEEDED','PARTIAL','FAILED','UNKNOWN','WAITING'} \
        or value.get('reply_language')!=language or not isinstance(value.get('actor_role'),str) or not 1<=len(value['actor_role'])<=80 or value['actor_role']=='ADMIN' \
        or not isinstance(value.get('fingerprint'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['fingerprint']): return None,'INVALID'
    intro,detail,text=value.get('intro'),value.get('detail'),value.get('text')
    if not isinstance(intro,str) or not 1<=len(intro)<=5000 or detail is not None and (not isinstance(detail,str) or len(detail)>5000) \
        or not isinstance(text,str) or not 1<=len(text)<=10000 or text!=intro+('\n\n'+detail if detail else '') \
        or any(redact_credentials(item or '')!=(item or '') for item in (intro,detail,text)): return None,'INVALID'
    try:
        if not isinstance(value.get('observed_at'),str) or not datetime.fromisoformat(value['observed_at']).tzinfo: return None,'INVALID'
    except (ValueError,TypeError): return None,'INVALID'
    snapshot={key:deepcopy(value[key]) for key in fields}
    return snapshot,'RETAINED'


def _customer_status_snapshot(view, language):
    """Project the independently authenticated historic observation, never
    use its former version/state as the current request's action authority."""
    from .conversation_status_snapshot import VERSION, AUTHORITY, PUBLIC_FIELDS, STATES
    status = view.get('status_snapshot_status', 'NOT_RETAINED')
    if status not in {'RETAINED', 'NOT_APPLICABLE', 'NOT_RETAINED', 'UNAUTHORIZED', 'INVALID'}:
        return None, 'INVALID'
    if status != 'RETAINED':
        return None, status
    value, link = view.get('status_snapshot'), view.get('task_link')
    if not isinstance(value, dict) or not isinstance(link, dict) or set(value) != set(PUBLIC_FIELDS) \
        or view.get('status') != 'ANSWERED' or view.get('mode') != 'STATUS' or view.get('error_code') \
        or value.get('schema_version') != VERSION or value.get('authority') != AUTHORITY \
        or value.get('conversation_id') != view.get('conversation_id') or value.get('turn_id') != view.get('turn_id') \
        or value.get('task_id') != link.get('task_id') or type(value.get('task_version')) is not int \
        or type(link.get('version')) is not int or not 1 <= value['task_version'] <= link['version'] \
        or value.get('state') not in STATES or value.get('reply_language') != language \
        or not isinstance(value.get('text'), str) or not 1 <= len(value['text']) <= 5000 \
        or value['text'] != view.get('answer') or redact_credentials(value['text']) != value['text'] \
        or not isinstance(value.get('fingerprint'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['fingerprint']):
        return None, 'INVALID'
    try:
        if not isinstance(value.get('observed_at'), str) or not datetime.fromisoformat(value['observed_at']).tzinfo:
            return None, 'INVALID'
    except (ValueError, TypeError):
        return None, 'INVALID'
    projection = value.get('projection')
    observed_link = {'task_id': value['task_id'], 'version': value['task_version'], 'state': value['state']}
    checked = _customer_status_projection({'mode': 'STATUS', 'status': 'ANSWERED', 'task_link': observed_link,
        'response_meta': {'status_projection': projection}})
    if checked is None or checked != projection:
        return None, 'INVALID'
    return {key: deepcopy(value[key]) for key in PUBLIC_FIELDS}, 'RETAINED'


def customer_turn_view(view, *, fallback_sources=None):
    from .language import detect_reply_language
    language = detect_reply_language(view.get("message", ""), preferred=view.get("reply_language"))
    result = {key: customer_business_value(view[key]) for key in (
        "turn_id", "conversation_id", "sequence", "message", "status", "mode", "task_link",
        "created_at", "updated_at") if key in view}
    result["reply_language"] = language
    historic_status, historic_status_state = _customer_status_snapshot(view, language)
    result['status_snapshot'], result['status_snapshot_status'] = historic_status, historic_status_state
    status_projection=_customer_status_projection(view)
    if view.get("status") == "ANSWERED" and view.get("mode") == "STATUS" and view.get("task_link"):
        if historic_status is not None:
            result['answer'] = historic_status['text']
            if status_projection:
                result['response_meta'] = {'status_projection': status_projection}
        elif status_projection and isinstance(view.get('answer'),str):
            result['answer']=customer_answer(view['answer'])
            result['response_meta']={'status_projection':status_projection}
        elif language == "en":
            from .customer_localized_labels import english_status
            result["answer"] = "The linked request is currently: " + english_status(view["task_link"].get("state")) + ". Use the verified business result."
        else:
            result["answer"] = "关联业务当前为：" + _STATUS.get(view["task_link"].get("state"), "请查看办理进度") + "。请以办理结果为准。"
    elif view.get("status") == "ANSWERED" and view.get("mode") == "REVISE":
        result["answer"] = ("Your request was revised using the supplied information. Review the new confirmation card; the previous confirmation is not reused."
                            if language == "en" else "已根据您的补充更新需求。请等待新的确认卡并重新核对，原确认不会复用。")
    else:
        from .customer_consultation import format_server_fallback
        formatted = (format_server_fallback(view.get("answer"), fallback_sources, reply_language=language)
                     if view.get("status") == "ANSWERED" and view.get("mode") == "CONSULT" and fallback_sources is not None else None)
        result["answer"] = customer_answer(formatted if formatted is not None else view.get("answer"))
    # A customer can see the document/terms title, not internal source references.
    result["sources"] = []  # Provenance stays in authenticated administration.
    snapshot,snapshot_status=_customer_reply_snapshot(view,language)
    result['reply_snapshot'],result['reply_snapshot_status']=snapshot,snapshot_status
    # Pass only reviewed navigation, not retrieval traces/model diagnostics.
    # The internal owned turn projection already revalidated source lifecycle.
    from .capability_catalog import suggestion_catalog
    from .customer_capability_data import CUSTOMER_DIRECTORY_VERSION
    supplied_meta = view.get("response_meta")
    meta = supplied_meta if isinstance(supplied_meta, dict) else {}
    from .semantic_recovery import public_recovery
    recovery=public_recovery(meta.get('recovery'))
    if recovery and view.get('status')=='ANSWERED' and view.get('mode')=='NEED_CONTEXT' and not view.get('task_link'):
        result['response_meta']={'recovery':recovery}
    role = meta.get("suggestion_role") if isinstance(meta.get("suggestion_role"), str) else None
    if (view.get("status") == "ANSWERED" and not view.get("task_link") and
        view.get("mode") in {"DISCOVERY", "GREETING", "FEEDBACK", "CONSULT", "KNOWLEDGE", "OUT_OF_SCOPE"} and
        meta.get("suggestion_source") == "CUSTOMER_CAPABILITY_CATALOG" and
        meta.get("suggestion_catalog_version") == CUSTOMER_DIRECTORY_VERSION):
        allowed = suggestion_catalog(role, language)
        if allowed:
            result["suggestions"] = [deepcopy(item) for item in view.get("suggestions", [])
                if isinstance(item, dict) and item == allowed.get(item.get("id"))][:5]
            result["response_meta"] = {key: meta[key] for key in (
                "suggestion_source", "suggestion_catalog_version", "suggestion_role", "dialogue_kind") if key in meta}
            result["response_meta"]["product_panel"] = meta.get("product_panel") is True and view.get("mode") == "CONSULT"
    if view.get("status") == "FAILED":
        result["error_message"] = ("This request was not completed. Check its original status before trying a payment again."
                                   if language == "en" else "本次请求未完成，请稍后重试或重新说明需求。")
    return result


def customer_conversation_view(view, *, fallback_sources_by_turn_id=None):
    supplied = fallback_sources_by_turn_id or {}
    return {**customer_thread_view(view), "turns": [customer_turn_view(turn, fallback_sources=supplied.get(turn.get("turn_id"))) for turn in view.get("turns", [])],
            "has_earlier": view.get("has_earlier", False), "before_cursor": view.get("before_cursor")}


_COMPLETED_READ_FIELDS = {
    "schema_version", "task_id", "task_version", "node_id", "operation_id", "child_task_id",
    "action", "status", "result", "analysis", "observed_at",
}


def _same_business_json(left, right):
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False, separators=(",", ":")) == \
            json.dumps(right, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return False


def completed_reads_for_pending(trace):
    """A strict subset of current, owned server read facts; never parent success.

    Runtime owns child/source permission and lifecycle checks. This projection
    additionally binds each fact to the live task version, operation, node,
    child and exact result. A plausible result or SUCCEEDED label alone cannot
    opt a mixed pending request into this surface.
    """
    from .customer_response import ACTION_PRESENTATIONS, _read_recorded
    from .intelligence import READ_KINDS
    if trace.get("state") != "AWAITING_CONFIRMATION" or type(trace.get("version")) is not int or trace["version"] < 1:
        return []
    pending, outcome = trace.get("pending_confirmation"), trace.get("outcome")
    nodes, facts = trace.get("nodes"), trace.get("completed_read_results")
    if not isinstance(pending, dict) or not isinstance(outcome, dict) or outcome.get("state") != trace["state"] \
            or outcome.get("verified") is not False or not isinstance(nodes, list) or not isinstance(facts, list):
        return []
    if not all(isinstance(pending.get(key), str) and pending[key] for key in ("task_id", "confirmation_id", "plan_hash")) \
            or not re.fullmatch(r"[0-9a-f]{64}", pending["plan_hash"]) \
            or type(pending.get("expected_task_version")) is not int or pending["expected_task_version"] < 1 \
            or not any(isinstance(node, dict) and node.get("child_task_id") == pending["task_id"]
                       and node.get("status") == "WAITING_CONFIRMATION" and isinstance(node.get("action"), str)
                       and node["action"] in ACTION_PRESENTATIONS and node["action"] not in READ_KINDS for node in nodes):
        return []
    completed_ids = outcome.get("completed_operation_ids")
    if not isinstance(completed_ids, list) or any(not isinstance(item, str) for item in completed_ids):
        return []
    selected, used = [], set()
    for fact in facts[:30]:
        if not isinstance(fact, dict) or set(fact) != _COMPLETED_READ_FIELDS \
                or fact.get("schema_version") != "completed-read-result-v1" \
                or fact.get("task_id") != trace.get("task_id") or type(fact.get("task_version")) is not int \
                or fact["task_version"] != trace["version"] or fact.get("status") != "SUCCEEDED" \
                or not isinstance(fact.get("action"), str) or fact["action"] not in READ_KINDS \
                or not all(isinstance(fact.get(key), str) and 0 < len(fact[key]) <= 120
                           for key in ("task_id", "node_id", "operation_id", "child_task_id")) \
                or fact["operation_id"] not in completed_ids or fact["operation_id"] in used \
                or not isinstance(fact.get("result"), dict) or fact["result"].get("error"):
            continue
        matches = [node for node in nodes if isinstance(node, dict) and node.get("node_id") == fact["node_id"]]
        if len(matches) != 1:
            continue
        node = matches[0]
        if any(node.get(key) != fact[key] for key in ("operation_id", "child_task_id", "action", "status")) \
                or not _same_business_json(node.get("result"), fact["result"]):
            continue
        try:
            if not isinstance(fact.get("observed_at"), str) or not datetime.fromisoformat(fact["observed_at"]).tzinfo:
                continue
        except (TypeError, ValueError):
            continue
        analysis = fact.get("analysis")
        if fact["action"] == "BILL_ANALYSIS":
            if not isinstance(analysis, dict) or not _same_business_json(analysis, trace.get("analysis")):
                continue
        elif analysis is not None:
            continue
        if not _read_recorded(fact["action"], fact["result"], {"analysis": analysis}):
            continue
        selected.append(deepcopy(fact))
        used.add(fact["operation_id"])
    return selected


def validated_customer_read_coverage(session, principal, trace):
    """After owned runtime/original READ gates, recheck actual scoped origin.

    No domain reload occurs here: historical prices, points and expiry stay
    those of the retained read. This check is not a model/metadata permission.
    """
    from .contracts import DomainError
    from .read_selection import (FIELDS, validate_result_origin_selection, read_selection_parameters,
                                 safe_selection_summary)
    coverage = {}
    for node in trace.get('nodes', []):
        if not isinstance(node, dict) or node.get('action') not in FIELDS:
            continue
        value = node.get('result')
        if not isinstance(value, dict):
            continue
        try:
            validate_result_origin_selection(session, principal, node['action'], value, trace, node=node)
            if read_selection_parameters(session, principal, node['action'], value) is None:
                continue  # Genuine original legacy evidence, not a removed proof.
        except DomainError as error:
            if error.status_code != 409:
                raise  # Permission denial remains 401/403, never missing data.
            raise DomainError('UNAVAILABLE_SCOPE', '原查询范围暂时无法核对，请重新查询。', 409) from error
        summary = safe_selection_summary(node['action'], value)
        operation = node.get('operation_id')
        if summary['status'] != 'FILTERED' or not isinstance(operation, str) or not operation or operation in coverage:
            raise DomainError('UNAVAILABLE_SCOPE', '原查询范围暂时无法核对，请重新查询。', 409)
        coverage[operation] = summary
    return coverage


def _customer_scoped_result(kind, value, operation, supplied):
    from .read_selection import FIELDS
    result = customer_business_value(value)
    if not isinstance(value, dict) or kind not in FIELDS or 'read_selection' not in value:
        return result
    from .read_selection import safe_selection_summary
    expected = safe_selection_summary(kind, value)
    checked = supplied.get(operation) if isinstance(supplied, dict) else None
    if expected['status'] != 'FILTERED' or checked != expected:
        return {'read_coverage': {'status': 'UNAVAILABLE_SCOPE'}}
    if kind == 'FAMILY_VIEW':
        from .family_balance_access_display import negative_balance_authorization
        negative = negative_balance_authorization(value, expected)
        if negative is not None:
            return {**result, 'read_coverage': {**deepcopy(expected), 'balance_authorization': negative}}
    return {**result, 'read_coverage': deepcopy(expected)}


def customer_task_view(trace, reply_language=None, *, supplementary_bill=None, supplementary_bill_observed_at=None,
                       paused_plan=None, read_coverage_by_operation=None):
    from .customer_response import build_assistant_response, card_owned_question_overview
    result = {key: customer_business_value(trace[key]) for key in (
        "task_id", "version", "state", "message", "summary", "status_message", "analysis", "receipt",
        "allowed_actions", "clarification_questions", "compensation_source", "outcome",
        "created_at", "updated_at", "result_observed_at", "reply_language") if key in trace}
    if trace.get("state") == "AWAITING_CONFIRMATION" and trace.get("pending_confirmation") is None \
            and trace.get("confirmation_unavailable_reason") == "EXPIRED":
        result["confirmation_unavailable_reason"] = "EXPIRED"
    result["nodes"] = [{key: customer_business_value(node[key]) for key in (
        "node_id", "operation_id", "action", "label", "status", "depends_on", "child_task_id",
        "result", "wait_reason", "error", "can_compensate", "compensation_task_id") if key in node}
        for node in trace.get("nodes", [])]
    for original, projected in zip(trace.get('nodes', []), result['nodes']):
        if 'result' in original:
            projected['result'] = _customer_scoped_result(original.get('action'), original['result'],
                original.get('operation_id'), read_coverage_by_operation)
    pending = trace.get("pending_confirmation")
    if isinstance(pending, dict):
        # These exact values remain the sole inputs to existing v2 confirmation.
        result["pending_confirmation"] = {key: deepcopy(pending[key]) for key in (
            "task_id", "confirmation_id", "plan_hash", "expected_task_version", "node_key",
            "label", "summary", "requires_step_up", "expires_at") if key in pending}
        if "scope" in pending:
            result["pending_confirmation"]["scope"] = customer_business_value(pending["scope"])
    else:
        result["pending_confirmation"] = None
    # The API supplies this only after a fresh database/source/actor check.
    # Never echo a marker injected into raw metadata/tool/model trace text.
    pause_fields = {"version", "task_id", "task_version", "paused", "confirmation_available"}
    empty_pause_values = all(value is None or type(value) is dict and not value
                             for value in (result.get("receipt"), result.get("analysis")))
    pause_outcome = result.get("outcome")
    safe_pause_outcome = pause_outcome is None or type(pause_outcome) is dict \
        and type(pause_outcome.get("completed_operation_ids")) is list and not pause_outcome["completed_operation_ids"] \
        and pause_outcome.get("state") == result.get("state") and pause_outcome.get("kind") == "PROCESS" \
        and pause_outcome.get("verified") is False
    if isinstance(paused_plan, dict) and set(paused_plan) == pause_fields \
            and paused_plan.get("version") == "customer-paused-plan-v1" \
            and paused_plan.get("paused") is True and paused_plan.get("confirmation_available") is False \
            and paused_plan.get("task_id") == result.get("task_id") \
            and type(paused_plan.get("task_version")) is int and paused_plan["task_version"] >= 1 \
            and paused_plan["task_version"] == result.get("version") \
            and result.get("state") in {"NEED_CLARIFICATION", "AWAITING_CONFIRMATION"} and result["pending_confirmation"] is None \
            and result["nodes"] == [] and empty_pause_values and safe_pause_outcome and not result.get("compensation_source"):
        result["paused_plan"] = deepcopy(paused_plan)
    completed_reads = completed_reads_for_pending(trace)
    if not completed_reads:
        from .customer_bill_read_projection import completed_bill_results
        completed_reads = completed_bill_results(trace)
    if completed_reads:
        result["completed_read_results"] = customer_business_value(completed_reads)
        for original, projected in zip(completed_reads, result['completed_read_results']):
            projected['result'] = _customer_scoped_result(original['action'], original['result'],
                original['operation_id'], read_coverage_by_operation)
    # A mixed pending request only exposes current checked READ payloads. Its
    # write previews and original confirmation values keep their usual shape.
    from .intelligence import READ_KINDS
    if trace.get("state") == "AWAITING_CONFIRMATION" and any(node.get("action") in READ_KINDS for node in result["nodes"]) \
            and any(node.get("action") not in READ_KINDS for node in result["nodes"]):
        checked_ids = {item["operation_id"] for item in completed_reads}
        for node in result["nodes"]:
            if node.get("action") in READ_KINDS and node.get("operation_id") not in checked_ids:
                node["result"] = None
        if not any(item["action"] == "BILL_ANALYSIS" for item in completed_reads):
            result["analysis"] = None
    from .customer_reference_views import add_spending_reference
    if not completed_reads:
        add_spending_reference(result)
    from .visitor_review import attach_renewal_review
    attach_renewal_review(result)
    from .customer_bill_fallback import checked_bill_fallback,matches_supplement_source
    supplemental = (customer_business_value(supplementary_bill)
                    if checked_bill_fallback(supplementary_bill) else None)
    if supplemental:
        original = result.get("analysis")
        requested = supplemental["period_fallback"]["requested"]
        nodes = result["nodes"]
        if (result.get("state") != "SUCCEEDED" or result.get("pending_confirmation") is not None
                or (result.get("outcome") or {}).get("verified") is not True
                or len(nodes) != 1 or nodes[0].get("action") != "BILL_ANALYSIS" or nodes[0].get("status") != "SUCCEEDED"
                or not matches_supplement_source(original,supplemental)):
            supplemental = None
    if supplemental:
        result["supplementary_bill"] = supplemental
        result["supplementary_bill_observed_at"] = supplementary_bill_observed_at
    # Missing-field facts are trusted runtime context, not customer DTO fields.
    # Use them for an accurate fallback question without publishing internals.
    narration_trace={**result,'customer_needed_fields':deepcopy(trace.get('customer_needed_fields')),
        'customer_query_focus':trace.get('customer_query_focus'),
        'customer_read_question_facts':deepcopy(trace.get('customer_read_question_facts')),
        '_verified_planning_failure':deepcopy(trace.get('_verified_planning_failure')),
        'reviewed_bill_comparison':deepcopy(trace.get('reviewed_bill_comparison'))}
    from .customer_bill_read_projection import has_multiple_bills
    if has_multiple_bills(trace):
        # The runtime's owned, version-bound child analyses are private display
        # sources. Public DTOs above remain stripped of internal source proof.
        narration_trace['completed_read_results'] = deepcopy(trace.get('completed_read_results'))
        narration_trace['nodes'] = [deepcopy(original) if original.get('action') == 'BILL_ANALYSIS' else projected
            for original, projected in zip(trace.get('nodes', []), result['nodes'])]
    from .credit_status_display import KEY as CREDIT_DISPLAY_KEY, credit_status_response, public_credit_status_display
    credit_response=None
    if CREDIT_DISPLAY_KEY in trace:
        credit_private_trace={**narration_trace,'nodes':deepcopy(trace.get('nodes')),CREDIT_DISPLAY_KEY:trace[CREDIT_DISPLAY_KEY]}
        credit_response=credit_status_response(credit_private_trace,reply_language or result.get('reply_language') or 'zh-CN')
    if credit_response:
        narration_trace=credit_private_trace
        public_status=public_credit_status_display(credit_private_trace)
        if public_status and len(result['nodes'])==1 and isinstance(result['nodes'][0].get('result'),dict):
            result['nodes'][0]['result']={**result['nodes'][0]['result'],'credit_status_display':public_status}
    from .customer_read_question_facts import read_question_response
    from .credit_repayment_closure import KEY as CLOSURE_KEY, repayment_closure_response
    closure_trace = {**narration_trace, 'nodes': deepcopy(trace.get('nodes')), CLOSURE_KEY: trace.get(CLOSURE_KEY)}
    closure_response = repayment_closure_response(closure_trace, reply_language or result.get('reply_language') or 'zh-CN')
    if closure_response:
        narration_trace = closure_trace
    private_question_trace={**narration_trace,'nodes':deepcopy(trace.get('nodes'))}
    question_reply=read_question_response(private_question_trace,reply_language or result.get('reply_language') or 'zh-CN')
    if question_reply:
        # Only the sealed, source-audited display consumer gets original nodes.
        # Public result cards above remain stripped of opaque account refs;
        # deleting those refs must not invalidate the private source proof.
        narration_trace=private_question_trace
    from .reviewed_bill_reply import comparison_reply
    result["assistant_response"] = build_assistant_response(narration_trace, reply_language=reply_language)
    material_reply=comparison_reply(narration_trace,result['assistant_response'].get('reply_language','zh-CN'))
    if material_reply: result['reviewed_material_reply']=material_reply
    # Current typed compact notices own their surface. A cloud reply created
    # from raw tool results can predate read-time visitor annotations even
    # when its business fingerprint and layout version are still valid.
    fresh_card_notice=result['assistant_response'].get('card_detail')
    fresh_card_intro=result['assistant_response'].get('card_intro')
    fresh_response=deepcopy(result['assistant_response'])
    fresh_summary_owned=card_owned_question_overview(narration_trace,question_reply)
    from .subscription_question_display import subscription_question_display
    fresh_subscription_display=subscription_question_display(narration_trace,question_reply)
    from .customer_reply_jobs import reply_for_current_trace
    reply, reply_status = reply_for_current_trace(trace)
    result["customer_reply_status"] = reply_status
    if reply and not supplemental and (reply_language is None or result["assistant_response"]["reply_language"] == trace.get("reply_language")):
        result["assistant_response"] = {**result["assistant_response"], "intro": reply["intro"],
            "detail": reply.get("detail")}
        if result['assistant_response'].get('query_layout_version') and reply.get('query_layout_version')==result['assistant_response']['query_layout_version']:
            fresh_problem_focus=fresh_response.get('query_focus') in {'CARBON_CLAIMABLE_RECORDS','CARBON_ELIGIBLE_REWARDS'}
            result['assistant_response']={**result['assistant_response'],'card_intro':fresh_card_intro or reply['intro'],
                'card_detail':fresh_card_notice if fresh_card_notice or fresh_problem_focus or fresh_summary_owned else reply.get('detail')}
        if result.get('state')=='NEED_CLARIFICATION' and fresh_response.get('detail_kind')=='GOALS':
            from .customer_reply_gateway import goal_clarification_has_direction
            if goal_clarification_has_direction((reply.get('intro') or '')+' '+(reply.get('detail') or '')):
                result['assistant_response']['intro']=fresh_response['intro']
                result['assistant_response']['detail']=fresh_response.get('detail')
    if completed_reads:
        # Persisted narration can predate an independently completed read.
        # Current deterministic mixed-status copy owns this short bridge.
        result["assistant_response"] = fresh_response
    if result.get("confirmation_unavailable_reason") == "EXPIRED":
        result["assistant_response"] = fresh_response  # Current TTL cannot be erased by an earlier pending narration.
    from .family_balance_access_display import valid_negative_balance_authorization
    from .customer_response import read_query_coverage
    if any(node.get('action')=='FAMILY_VIEW'
            and valid_negative_balance_authorization(node.get('result'),read_query_coverage('FAMILY_VIEW',node.get('result')))
            for node in result.get('nodes',[])):
        # A current verified access denial owns both narration and card copy.
        # An older cached reply cannot claim balances were obtained.
        result['assistant_response']=fresh_response
    if credit_response:
        result['assistant_response']=fresh_response  # Required typed state owns this surface, not cached free prose.
    if closure_response:
        result['assistant_response']=fresh_response  # Never restore pre-payment debt from cached narration.
    if fresh_subscription_display:
        result['assistant_response']=fresh_response  # Revalidated subscription wording, not old field-by-field cache copy.
    from .customer_response import _aa_payment_recorded
    aa_nodes = narration_trace.get('nodes') or []
    if fresh_response.get('state_kind') == 'COMMITTED' and len(aa_nodes) == 1 \
            and 'currency' not in (aa_nodes[0].get('result') or {}) and _aa_payment_recorded(aa_nodes[0], narration_trace):
        # A READY narration can predate this finite native-AA result adapter.
        # Its unchanged business hash is not permission to erase a currently
        # verified payment. Keep the source-bound amount, not the old prose.
        result['assistant_response'] = fresh_response
    from .planning_failure_facts import controlled_failure_response
    if controlled_failure_response(narration_trace,fresh_response.get('reply_language','zh-CN')):
        # A cached generic/model reply cannot erase a current audited denial.
        result['assistant_response']=fresh_response
        result['status_message']=fresh_response['intro']+' '+(fresh_response.get('detail') or '')
    from .customer_deferred_preparation import deferred_preparation_notice
    later=deferred_preparation_notice(trace,english=result['assistant_response'].get('reply_language')=='en')
    if later and later not in (result['assistant_response'].get('detail') or ''):
        result['assistant_response']['detail']=((result['assistant_response'].get('detail') or '')+' '+later).strip()
    # Clean the customer projection after merging persisted narration; old
    # replies remain intact in administration and need no migration/reissue.
    from .customer_copy import strip_internal_result_copy
    for key in ("intro", "detail", "card_detail"):
        result["assistant_response"][key] = strip_internal_result_copy(result["assistant_response"].get(key))
    result["reply_language"] = result["assistant_response"]["reply_language"]
    if result["reply_language"] == "en":
        from .language import localize_clarification
        result["clarification_questions"] = [localize_clarification(item, "en") for item in result.get("clarification_questions", [])]
        from .customer_localized_labels import localize_customer_task_labels
        localize_customer_task_labels(result)
    return result
