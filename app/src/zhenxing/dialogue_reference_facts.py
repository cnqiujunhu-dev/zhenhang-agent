"""Registered, source-rechecked facts for natural followups on queried objects."""
import json
import re
from decimal import Decimal
from .dialogue_reference_integration import reference_explanation_sources
from .planning import canonical_hash

SOURCE_ID="facts:selected-dialogue-object"
AUTHORITY="RECORDED_TOOL_FACTS_NOT_INFERRED_CAUSES_OR_WRITE_AUTHORIZATION"
MONEY_FIELDS={"amount_minor","spending_minor","balance_minor","limit_minor","unit_price_minor","maximum_amount_minor"}
BILL_SELECTION_ID=SOURCE_ID+".verified_largest_expense"
POINT_FIELDS={"points":"可用低碳积分","cost_points":"兑换所需积分","estimated_points":"行为估算积分"}


def is_selected_source_id(value):
    return value == SOURCE_ID or isinstance(value,str) and bool(re.fullmatch(re.escape(SOURCE_ID)+r'\.[a-f0-9]{24}',value))


def selected_source_id(proof):
    return SOURCE_ID+'.'+canonical_hash({'entity_ref':proof['resolution']['entity_ref'],
        'catalog_hash':proof['catalog']['catalog_hash']})[:24]


def _registered_content(row):
    if not isinstance(row,dict) or not is_selected_source_id(row.get("source_id")) or row.get("kind")!="SCOPED_BUSINESS_FACTS" or row.get("version")!="selected-dialogue-object-v1":
        return None
    try:
        content=json.loads(row["content"])
        proof=row.get("source_binding")
        if row['source_id']!=SOURCE_ID and row['source_id']!=selected_source_id(proof):return None
        if not isinstance(content,dict) or not isinstance(proof,dict) or proof.get("mode")!="EXPLANATION" \
            or content.get("authority")!=AUTHORITY or content.get("bank_state_changed") is not False \
            or canonical_hash(content)!=row.get("content_hash") or not isinstance(content.get("facts"),list):
            return None
        if not all(isinstance(fact,dict) and isinstance(fact.get("field"),str) and isinstance(fact.get("fact_ref"),str)
            and "value" in fact for fact in content["facts"]):
            return None
        if len({fact["field"] for fact in content["facts"]})!=len(content["facts"]):
            return None
        matches=[item for item in proof["catalog"]["entities"] if item["entity_ref"]==proof["resolution"]["entity_ref"]]
        if len(matches)!=1:
            return None
        entity=matches[0]
        expected=[{"fact_ref":fact["fact_ref"],"field":fact["field"],"value":fact["value"]}
            for fact in proof["catalog"]["fact_refs"] if fact["entity_ref"]==entity["entity_ref"] and fact["fact_ref"] in entity["fact_refs"]]
        if content["facts"]!=expected or any(content.get(key)!=entity.get(key) for key in ("domain","label","record_version","result_ref")):
            return None
        return content
    except (ValueError,TypeError,KeyError,AttributeError):
        return None


def selected_object_sources(session,principal,thread,binding):
    rows=reference_explanation_sources(session,principal,thread,binding)
    if len(rows)!=1:
        return []
    content=rows[0]["content"]
    return [{"source_id":SOURCE_ID,"title":"刚才查询到的这项资料","kind":"SCOPED_BUSINESS_FACTS",
        "reference":"conversation:"+thread.id,"version":"selected-dialogue-object-v1",
        "content":json.dumps(content,ensure_ascii=False,sort_keys=True),"source_binding":binding,
        "content_hash":canonical_hash(content)}]


def selected_object_sources_current(session,principal,records):
    selected=[row for row in records if is_selected_source_id(row.get('source_id'))]
    from .dialogue_reference_integration import VERSION as LEGACY_SOURCE_VERSION
    supported={LEGACY_SOURCE_VERSION,'semantic-selected-result-source-v1','semantic-selected-result-source-v2'}
    if any(not isinstance(row.get('source_binding'),dict) or not isinstance(row['source_binding'].get('version'),str)
        or row['source_binding']['version'] not in supported for row in selected):return False
    semantic=[row for row in selected if (row.get('source_binding') or {}).get('version') in {'semantic-selected-result-source-v1','semantic-selected-result-source-v2'}]
    if semantic:
        if len(selected)!=len(semantic) or len({row['source_id'] for row in selected})!=len(selected):return False
        from .semantic_proposal import semantic_selected_object_sources_current
        return semantic_selected_object_sources_current(session,principal,semantic)
    from .conversation_models import ChatThread
    for row in records:
        if not is_selected_source_id(row.get("source_id")):
            continue
        proof=row.get("source_binding")
        if not isinstance(proof,dict):
            return False
        thread=session.get(ChatThread,proof.get("conversation_id"))
        try:
            if thread is None or selected_object_sources(session,principal,thread,proof)!=[row]:
                return False
        except Exception:
            return False
    return True


def selected_object_money(row):
    content=_registered_content(row)
    if content is None:
        return {}
    fields={fact["field"]:fact["value"] for fact in content.get("facts",[]) if isinstance(fact,dict) and "field" in fact and "value" in fact}
    currency=fields.get("currency")
    if currency not in {"CNY","USD"}:
        return {}
    labels={"amount_minor":"记录金额","spending_minor":"这类支出","balance_minor":"余额","limit_minor":"单笔限额",
        "unit_price_minor":"份额价格","maximum_amount_minor":"建议金额上限"}
    return {SOURCE_ID+"."+field:{"fact_id":SOURCE_ID+"."+field,"source_id":SOURCE_ID,"field":field,
        "currency":currency,"unit":"MINOR","value":abs(value) if field=="amount_minor" else value,
        "label":labels[field],"label_en":"Recorded amount"}
        for field,value in fields.items() if field in MONEY_FIELDS and type(value) is int}


def selected_object_points(row):
    """Source-bound integer points, never a CNY/USD minor-unit amount.

    Public KB text, model prose and unregistered dictionaries have no numeric
    authority here. Publishing callers must additionally recheck the current
    source through selected_object_sources_current.
    """
    content=_registered_content(row)
    if content is None or content.get("domain")!="CARBON":
        return {}
    fields={fact["field"]:fact["value"] for fact in content["facts"]}
    if fields.get("currency")!="POINTS" or fields.get("unit")!="POINTS":
        return {}
    if any(field in fields and (type(fields[field]) is not int or fields[field]<0
            or field=="cost_points" and fields[field]==0) for field in POINT_FIELDS):
        return {}
    return {SOURCE_ID+"."+field:{"fact_id":SOURCE_ID+"."+field,"source_id":SOURCE_ID,"field":field,
        "currency":"POINTS","unit":"POINTS","value":fields[field],"label":label,
        "label_en":{"points":"Available low-carbon points","cost_points":"Required redemption points",
            "estimated_points":"Estimated behavior points"}[field]}
        for field,label in POINT_FIELDS.items() if field in fields}


def render_selected_points(fact,reply_language="zh-CN"):
    """Render exact integers without dividing by 100 or promising redemption."""
    if not isinstance(fact,dict) or fact.get("source_id")!=SOURCE_ID or fact.get("field") not in POINT_FIELDS \
            or fact.get("fact_id")!=SOURCE_ID+"."+fact["field"] or fact.get("currency")!="POINTS" \
            or fact.get("unit")!="POINTS" or type(fact.get("value")) is not int or fact["value"]<0 \
            or fact["field"]=="cost_points" and fact["value"]==0:
        return None
    labels={"points":("你当前可用的低碳积分是","You currently have"),
        "cost_points":("这项权益需要","This reward requires"),
        "estimated_points":("这项行为的估算积分是","The estimated points for this behavior are")}
    chinese,english=labels[fact["field"]]
    return f"{english} {fact['value']} points." if reply_language=="en" else f"{chinese}{fact['value']}积分。"


def selected_bill_selection_catalog(row):
    content=_registered_content(row)
    if content is None or content.get("domain")!="BILLS":
        return {}
    fields={fact["field"]:fact["value"] for fact in content["facts"]}
    if fields.get("largest_scope")!="FULL_RECORDED_WINDOW" or fields.get("currency")!="CNY" \
            or type(fields.get("amount_minor")) is not int or fields["amount_minor"]>=0 \
            or not isinstance(fields.get("largest_snapshot_hash"),str) \
            or not re.fullmatch(r"[0-9a-f]{64}",fields["largest_snapshot_hash"]):
        return {}
    ties=fields.get("tie_count",1)
    if type(ties) is not int or ties<1:
        return {}
    records=fields.get("records") if ties>1 else [fields]
    if not isinstance(records,list) or len(records)!=ties \
            or any(not isinstance(item,dict) or item.get("amount_minor")!=fields["amount_minor"] \
                or item.get("currency")!="CNY" or not isinstance(item.get("merchant"),str) for item in records):
        return {}
    return {BILL_SELECTION_ID:{"fact_id":BILL_SELECTION_ID,"source_id":SOURCE_ID,
        "field":"verified_largest_expense","currency":"CNY","unit":"BILL_SELECTION",
        "value":{**fields,"tie_count":ties,"records":records},
        "label":"已有账单记录中的最大支出（实际期间及并列说明）","label_en":"Largest expense in recorded bills, with period and ties"}}


def render_selected_bill_selection(fact,reply_language="zh-CN"):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fields=fact["value"]
    english=reply_language=="en"
    try:
        start=datetime.fromisoformat(fields["period_start"])
        end=datetime.fromisoformat(fields["period_end"])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError()
        start=start.astimezone(ZoneInfo("Asia/Shanghai"))
        end=end.astimezone(ZoneInfo("Asia/Shanghai"))
        period=(start.strftime("%Y年%m月") if start.day==1 and end.day==1 and (end.year*12+end.month)-(start.year*12+start.month)==1
            else start.strftime("%Y-%m-%d")+" 至 "+end.strftime("%Y-%m-%d")+"（结束日不含）")
        if english:period=start.strftime("%Y-%m-%d")+" to "+end.strftime("%Y-%m-%d")+" (end exclusive)"
    except (KeyError,TypeError,ValueError):
        period="the selected recorded period" if english else "所选期间"
    amount=f"{Decimal(abs(fields['amount_minor']))/100:.2f}"
    from .redaction import redact_credentials
    names=[redact_credentials(item["merchant"]).replace("\n"," ").replace("\r"," ")[:100] for item in fields["records"][:5]]
    if fields["tie_count"]==1:
        return (
            f"In the recorded bills for {period}, the largest expense is {names[0]}: CNY {amount}." if english else
            f"在{period}的已有账单中，最大一笔支出是{names[0]}，金额为{amount}元。")
    remaining=fields["tie_count"]-len(names)
    listed=(", ".join(names)+(f" and {remaining} more" if remaining else "")) if english else (
        "、".join(names)+(f"等{fields['tie_count']}笔" if remaining else ""))
    return (f"In the recorded bills for {period}, {fields['tie_count']} expenses tie for the largest amount, CNY {amount} each: {listed}."
        if english else f"在{period}的已有账单中，有{fields['tie_count']}笔支出并列最大，每笔{amount}元：{listed}。")


def selected_object_fallback(records,reply_language=None):
    rows=[row for row in records if isinstance(row,dict) and row.get("source_id")==SOURCE_ID]
    if len(rows)!=1:
        return None
    row=rows[0]
    content=_registered_content(row)
    if content is None:
        return None
    facts={fact["field"]:fact["value"] for fact in content.get("facts",[]) if isinstance(fact,dict) and "field" in fact and "value" in fact}
    english=reply_language=="en"
    label=str(content.get("label") or ("this item" if english else "这项记录"))[:100]
    from .redaction import redact_credentials
    label=redact_credentials(str(facts.get("merchant") or label))[:100].replace("\n"," ").replace("\r"," ")
    money=selected_object_money(row)
    bill=selected_bill_selection_catalog(row)
    if bill:
        return render_selected_bill_selection(bill[BILL_SELECTION_ID],reply_language)
    amounts=[f"{Decimal(value['value'])/100:.2f} {value['currency']}" for value in money.values()]
    if content["domain"]=="SUBSCRIPTIONS":
        if facts.get("status")=="CANCELLED":
            return ("The latest queried record shows “"+label+"” is already cancelled. You do not need to cancel it again; this reply did not perform another cancellation.") if english else (
                "最新查询记录显示“"+label+"”已经取消了，不需要再取消一次。这条回复只是说明当前记录，没有重复执行取消。")
        cancel=facts.get("can_cancel_here")
        if cancel is True:
            return ("You can prepare a cancellation for “"+label+"” here. Review the cancellation details before confirming; it has not been cancelled.") if english else (
                "“"+label+"”可以在这里发起取消续费，确认前会先让你核对信息，目前还没有取消。")
        if cancel is False:
            return ("“"+label+"” needs cancellation through the service provider's own channel. I can explain the recorded subscription details.") if english else (
                "“"+label+"”需要在服务商自己的渠道取消。这里可以帮你查看已有的扣款和续费资料。")
    if content["domain"]=="CARDS":
        status={"ACTIVE":"正常使用","LOCKED":"已临时锁定","FROZEN":"已冻结","LOST":"已挂失"}.get(facts.get("status"),"需要核对当前状态")
        return ("“"+label+"”: "+str(facts.get("status") or "status unavailable")+("; "+", ".join(amounts) if amounts else "")+".") if english else (
            "“"+label+"”目前"+status+("，"+"、".join(amounts) if amounts else "")+"。")
    if content["domain"]=="WEALTH":
        risk=str(facts.get("risk_level") or "需另行核对")
        return ("“"+label+"” has recorded risk level "+risk+". Suitability and product terms need review; returns and principal are not guaranteed.") if english else (
            "“"+label+"”的风险等级是"+risk+"。是否适合你还要结合适当性和产品条款核对，不能把它理解为保本或保证收益。")
    if content["domain"]=="CARBON":
        points=selected_object_points(row)
        if not points:return None
        available=points.get(SOURCE_ID+".points");cost=points.get(SOURCE_ID+".cost_points")
        if cost:
            text=(f"“{label}” requires {cost['value']} points" if english else f"“{label}”需要{cost['value']}积分")
            if available:text+=(f"; you currently have {available['value']} points" if english else f"，你目前有{available['value']}积分")
            if facts.get("can_redeem") is True:
                text+=(". You can prepare a redemption after reviewing the details; no points have been deducted." if english else
                    "。可以先准备兑换，核对确认后才办理；目前没有扣除积分。")
            else:
                text+=(". The recorded points or stock do not currently meet the redemption requirements." if english else
                    "。当前记录的积分或库存还不满足兑换条件。")
            return text
        estimated=points.get(SOURCE_ID+".estimated_points")
        if estimated:
            return render_selected_points(estimated,reply_language)+(f" This is an estimate, not an issued credit." if english else
                "这是估算，不代表已经领取到账。")
        return render_selected_points(available,reply_language) if available else None
    if content["domain"]!="BILLS":
        return ("The recorded details for “"+label+"” are available.") if english else ("已查到“"+label+"”的资料。")
    if english:
        return "The record is for "+str(label)+(": "+", ".join(amounts) if amounts else ".")+". This explains the recorded data; it does not establish the reason for the spending."
    return "这笔记录是"+str(label)+("，金额为"+"、".join(amounts) if amounts else "")+"。这是账单中已有的记录；仅凭记录不能判断消费原因。"
