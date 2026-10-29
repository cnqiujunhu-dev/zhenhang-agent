"""Typed queried-record state, never a claim that this turn performed a write.

Publication must first call selected_object_sources_current with the live
principal. The structural registry below also rechecks the exact selected
entity/facts/hash contract; client-shaped states are not accepted as facts.
Root integrates these fact references without relaxing completion validators.
"""
from __future__ import annotations

from copy import deepcopy

SOURCE_ID="facts:selected-dialogue-object"
STATUS_ID=SOURCE_ID+".subscription_record_status"
CAPABILITY_ID=SOURCE_ID+".subscription_cancel_capability"
UNIT="RESULT_STATE"
AUTHORITY="QUERIED_RECORD_STATE_NOT_CURRENT_USER_EXECUTION"
_CONNECTED=frozenset({"BANK_MANDATE","MERCHANT_SIM"})


def selected_result_status_catalog(row):
    """Return only exact registered selected-subscription state references."""
    from .dialogue_reference_facts import _registered_content
    content=_registered_content(row)
    if content is None or content.get("domain")!="SUBSCRIPTIONS":return {}
    fields={fact["field"]:fact["value"] for fact in content["facts"]}
    status=fields.get("status");can_cancel=fields.get("can_cancel_here");source_type=fields.get("source_type")
    merchant=fields.get("merchant") or content.get("label")
    from .redaction import redact_credentials
    from .intelligence import _intent_text
    if status not in {"ACTIVE","CANCELLED"} or type(can_cancel) is not bool or not isinstance(merchant,str) \
        or not 0<len(merchant.strip())<=120 or merchant!=redact_credentials(merchant) or _intent_text(merchant)!=merchant:return {}
    if status=="CANCELLED" and can_cancel or status=="ACTIVE" and can_cancel and source_type not in _CONNECTED:return {}
    state={"status":status,"merchant":merchant,"result_ref":content["result_ref"],"source_type":source_type,
        "can_cancel_here":can_cancel,"record_version":content.get("record_version"),
        "read_only":True,"bank_state_changed":False,"authority":AUTHORITY}
    catalog={STATUS_ID:{"fact_id":STATUS_ID,"source_id":SOURCE_ID,"field":"subscription_record_status","unit":UNIT,
        "currency":"NONE","value":state,"label":"查询到的订阅状态","label_en":"Queried subscription state"}}
    capability="ALREADY_CANCELLED_NO_REPEAT" if status=="CANCELLED" else "AVAILABLE_REQUIRES_CONFIRMATION" if can_cancel \
        else "PROVIDER_CHANNEL_REQUIRED" if source_type=="EXTERNAL_UNCONNECTED" else "NOT_AVAILABLE_REASON_UNCONFIRMED"
    catalog[CAPABILITY_ID]={"fact_id":CAPABILITY_ID,"source_id":SOURCE_ID,"field":"subscription_cancel_capability","unit":UNIT,
        "currency":"NONE","value":{**deepcopy(state),"capability":capability},
        "label":"取消能力与确认边界","label_en":"Cancellation capability and confirmation boundary"}
    return catalog


def render_selected_result_status(fact,reply_language=None):
    """Render server-registered fact data, not model-authored completion text."""
    if not isinstance(fact,dict) or fact.get("fact_id") not in {STATUS_ID,CAPABILITY_ID} or fact.get("source_id")!=SOURCE_ID \
        or fact.get("unit")!=UNIT or fact.get("currency")!="NONE":return None
    value=fact.get("value")
    if not isinstance(value,dict) or value.get("authority")!=AUTHORITY or value.get("bank_state_changed") is not False \
        or value.get("read_only") is not True or value.get("status") not in {"ACTIVE","CANCELLED"} or type(value.get("can_cancel_here")) is not bool:return None
    name=value.get("merchant")
    from .redaction import redact_credentials
    from .intelligence import _intent_text
    if not isinstance(name,str) or not 0<len(name.strip())<=120 or name!=redact_credentials(name) or _intent_text(name)!=name:return None
    english=reply_language=="en"
    if value["status"]=="CANCELLED":
        if value["can_cancel_here"]:return None
        return (f'The queried record shows “{name}” is already cancelled. There is no need to cancel it again; this reply did not perform a cancellation.' if english else
            f'查询记录显示“{name}”已经取消了，不需要再取消一次。这条回复只是说明已有状态，没有执行取消。')
    if fact["fact_id"]==STATUS_ID:
        return (f'The queried record shows “{name}” is active. This reply has not cancelled it.' if english else
            f'查询记录显示“{name}”仍在启用。这条回复没有取消它。')
    capability=value.get("capability")
    if capability=="AVAILABLE_REQUIRES_CONFIRMATION" and value["can_cancel_here"] and value.get("source_type") in _CONNECTED:
        return (f'You can prepare a cancellation of “{name}” here. You will need to review and confirm it; nothing has been cancelled by this reply.' if english else
            f'“{name}”可以在这里发起取消，办理前仍需要你核对确认。这条回复没有执行取消。')
    if capability=="PROVIDER_CHANNEL_REQUIRED" and value.get("source_type")=="EXTERNAL_UNCONNECTED" and not value["can_cancel_here"]:
        return (f'Cancellation of “{name}” is not connected here; use the provider’s own channel. This reply has not cancelled it.' if english else
            f'这里尚未接入“{name}”的取消办理，需要到服务商渠道操作。这条回复没有取消它。')
    if capability=="NOT_AVAILABLE_REASON_UNCONFIRMED" and not value["can_cancel_here"]:
        return (f'Cancellation of “{name}” is currently unavailable here. The reason needs to be checked; this reply has not cancelled it.' if english else
            f'“{name}”目前不能在这里取消，具体原因还需要核对。这条回复没有取消它。')
    return None
