"""Natural display of server fallback sources, never text-heading authority.

The API caller must supply actor/thread-scoped, currently authorized stored
records. This formatter changes no source, fingerprint, monetary fact or grant.
"""
import json
import hashlib
import re
from uuid import UUID

from .product_knowledge import (answer_from_sources, monetary_fact_catalog, render_monetary_facts,
                                ENGLISH_ENTRIES, KNOWLEDGE_VERSION)
from .redaction import redact_credentials


_BANK_SOURCES = {
    "facts:cash": ("SCOPED_BUSINESS_FACTS", "/api/v1/accounts"),
    "facts:cards": ("SCOPED_BUSINESS_FACTS", "/api/v1/cards"),
    "facts:subscriptions": ("SCOPED_BUSINESS_FACTS", "/api/v1/subscriptions"),
    "facts:goals": ("SCOPED_BUSINESS_FACTS", "/api/v2/extensions/goals"),
    "facts:currencies": ("SCOPED_BUSINESS_FACTS", "/api/v2/fx/wallets"),
    "facts:family-projection": ("SCOPED_BUSINESS_FACTS", "/api/v2/extensions/family"),
    "facts:bill-report": ("SCOPED_BUSINESS_FACTS", "/api/v2/bills/report"),
    "facts:bill-diagnostics": ("SCOPED_BUSINESS_FACTS", "/api/v2/bills/report"),
}
_CARD_STATUS = {"ACTIVE": "状态正常", "LOCKED": "已临时锁定", "LOST": "已挂失", "CLOSED": "已关闭"}
_TASK_STATUS = {"QUEUED": "正在受理", "UNDERSTANDING": "正在整理需求", "PLANNING": "正在准备方案",
    "NEED_CLARIFICATION": "需要补充信息", "AWAITING_CONFIRMATION": "等待本人核对确认",
    "EXECUTING": "正在办理", "UNKNOWN": "结果尚未确定，请先查看原请求", "PARTIAL": "部分步骤已有结果",
    "WAITING": "等待约定条件", "SUCCEEDED": "已完成，请以业务结果为准", "FAILED": "本次未完成", "CLOSED": "已关闭"}
_SCOPE = {"READ_BALANCE": "查看已授权余额", "PROPOSE_TRANSFER": "提出转账建议", "PROPOSE_CARD_LOCK": "提出锁卡建议"}
_SUB_SOURCE = {"BANK_MANDATE": "银行代扣", "MERCHANT_SIM": "商户订阅", "EXTERNAL_UNCONNECTED": "外部服务，仅提供关闭指引"}


def _text(value, fallback="未注明"):
    return redact_credentials(value)[:120] if isinstance(value, str) and value else fallback


def _rows(value):
    return value if isinstance(value, list) and all(isinstance(item, dict) for item in value) else None


def _bank_content(record):
    identity = record.get("source_id")
    if identity=='facts:goal-calculation':
        from .goal_explanation_facts import goal_source_content
        return goal_source_content(record)
    expected = _BANK_SOURCES.get(identity)
    if expected and (record.get("kind"), record.get("reference")) != expected:
        return None
    if identity == "facts:current-task":
        reference = record.get("reference")
        if record.get("kind") != "SCOPED_TASK_STATE" or not isinstance(reference, str) or not reference.startswith("/api/v2/tasks/") or not reference.endswith("/trace"):
            return None
        try:
            task_id = reference.removeprefix("/api/v2/tasks/").removesuffix("/trace")
            if str(UUID(task_id)) != task_id.lower():
                return None
        except (ValueError, TypeError):
            return None
    elif not expected:
        return None
    try:
        return json.loads(record.get("content", ""))
    except (TypeError, ValueError):
        return None


def _format_bank(record, content, facts):
    identity = record["source_id"]
    if identity in {"facts:bill-report", "facts:bill-diagnostics"} and isinstance(content, dict):
        from .customer_financial_narrative import financial_summary, date_label
        analysis = {"coverage": content.get("coverage"), "financial_insights": content} if identity == "facts:bill-diagnostics" else content
        result = financial_summary(analysis)
        if result:
            return result[0] + "\n" + result[1]
        through = date_label((content.get("coverage") or {}).get("data_through"))
        return "账单资料按已披露期间读取。" + (f"资料截止{through}。" if through else "资料截止日期尚未完整返回。") + "这不是当前余额的变动保证，也不代表执行任何银行操作。"
    money_ids = [key for key, fact in facts.items() if fact["source_id"] == identity]
    if identity == "facts:cash":
        return render_monetary_facts(facts, money_ids) if money_ids else "现金数据的币种或金额尚未完整返回，请查询最新账户信息。"
    if identity == "facts:cards":
        rows = _rows(content)
        if rows is None:
            return "卡片记录尚未完整返回，请稍后查看。"
        lines = ["这次读取了" + str(len(rows)) + "张卡片的状态："] if rows else ["这次读取没有返回卡片记录。"]
        for row in rows:
            tail = row.get("display_last4") or row.get("last4")
            lines.append("尾码" + _text(tail) + "：" + _CARD_STATUS.get(row.get("status"), "状态需要核实") + "。")
        return "\n".join(lines)
    if identity == "facts:subscriptions":
        rows = _rows(content)
        if rows is None:
            return "订阅记录尚未完整返回，请稍后查看。"
        lines = ["这次读取的订阅服务如下："] if rows else ["这次读取没有返回订阅记录。"]
        for row in rows:
            status = {"ACTIVE": "已启用", "CANCELLED": "已取消"}.get(row.get("status"), "状态需要核实")
            source = _SUB_SOURCE.get(row.get("source"), "服务来源需核实")
            lines.append(_text(row.get("merchant")) + "：" + status + "，" + source + "。")
        return "\n".join(lines)  # This bounded source has no price or cycle fields.
    if identity == "facts:goals":
        rows = _rows(content)
        if rows is None:
            return "目标记录尚未完整返回，请稍后查看。"
        lines = ["这次读取的储蓄目标如下："] if rows else ["这次读取没有返回目标记录。"]
        for row in rows:
            value = row.get("reserved_minor")
            state = "已有预留记录" if type(value) is int and value > 0 else "尚无预留资金" if type(value) is int and value == 0 else "预留状态需要核实"
            lines.append(_text(row.get("goal_name")) + "：" + state + "。")
        return "\n".join(lines)  # No explicit currency/due date: do not manufacture either.
    if identity == "facts:currencies" and isinstance(content, dict):
        lines = []
        if money_ids:
            lines.append(render_monetary_facts(facts, money_ids))
        if content.get("dual_card") is True:
            lines.append("已登记双币卡配置，实际付款仍需核对卡片状态、币种和报价。")
        elif content.get("dual_card") is False:
            lines.append("当前没有登记双币卡配置，具体资金来源请以办理方案为准。")
        if content.get("goals_currency") == "CNY":
            lines.append("当前储蓄目标使用人民币记账。")
        return "\n".join(lines) or "币种与钱包的具体信息请查询相应明细。"
    if identity == "facts:family-projection" and isinstance(content, dict):
        rows = _rows(content.get("delegations"))
        lines = []
        if rows is None:
            lines.append("委托记录尚未完整返回。")
        elif not rows:
            lines.append("这次读取没有返回委托记录。")
        else:
            lines.append("这次读取的家庭委托如下：")
            for row in rows:
                valid = "当前有效" if row.get("valid") is True else "当前无效" if row.get("valid") is False else "有效性需要核实"
                scopes = row.get("scopes") if isinstance(row.get("scopes"), list) else []
                labels = [_SCOPE[scope] for scope in scopes if scope in _SCOPE]
                lines.append(_text(row.get("owner_name")) + "委托" + _text(row.get("grantee_name")) + "：" + valid
                             + ("，可" + "、".join(labels) if labels else "，具体范围需要核实") + "。")
        permission = content.get("permissions") if isinstance(content.get("permissions"), dict) else {}
        allowed = [label for key, label in (("can_read_owner_balance", "查看已授权余额"),
                   ("can_propose_transfer", "提出转账建议"), ("can_propose_card_lock", "提出锁卡建议")) if permission.get(key) is True]
        if allowed:
            lines.append("当前获准的协助范围：" + "、".join(allowed) + "。")
        lines.append("协助人不能代本人执行银行操作，建议仍需本人审阅并独立确认。")
        return "\n".join(lines)
    if identity == "facts:current-task" and isinstance(content, dict):
        return "关联业务记录的状态：" + _TASK_STATUS.get(content.get("state"), "状态需要核实") + "。"
    return "相关业务记录的展示字段尚未完整返回，请查看相应明细。"


def _format_bank_english(record, content, facts):
    from .customer_financial_narrative import financial_summary, date_label
    from .customer_localized_labels import english_status
    identity = record["source_id"]
    money_ids = [key for key, fact in facts.items() if fact["source_id"] == identity]
    if identity in {"facts:bill-report", "facts:bill-diagnostics"} and isinstance(content, dict):
        analysis = {"coverage": content.get("coverage"), "financial_insights": content} if identity == "facts:bill-diagnostics" else content
        result = financial_summary(analysis, english=True)
        if result:
            return result[0] + "\n" + result[1]
        through = date_label((content.get("coverage") or {}).get("data_through"))
        return "The disclosed bank records were read." + (f" Data runs through {through} (China time)." if through else " The data watermark is not available.") + " This is not proof of a current balance change or any banking execution."
    if identity == "facts:cash":
        return render_monetary_facts(facts, money_ids, reply_language="en") if money_ids else "The cash amount or currency is incomplete. Query the latest account information."
    if identity == "facts:cards":
        rows = _rows(content)
        if rows is None:
            return "Card records are incomplete. Check the verified details."
        status = {"ACTIVE": "active", "LOCKED": "temporarily locked", "LOST": "reported lost", "CLOSED": "closed"}
        return "\n".join(["The recorded card statuses are:"] + ["Card ending " + _text(row.get("display_last4") or row.get("last4"), "not returned") + ": " + status.get(row.get("status"), "status needs verification") + "." for row in rows]) if rows else "No card record was returned by this read."
    if identity == "facts:subscriptions":
        rows = _rows(content)
        if rows is None:
            return "Subscription records are incomplete. Check the verified details."
        status = {"ACTIVE": "active", "CANCELLED": "cancelled"}
        source = {"BANK_MANDATE": "bank mandate", "MERCHANT_SIM": "merchant subscription", "EXTERNAL_UNCONNECTED": "external provider; cancellation is not connected here"}
        return "\n".join(["The recorded subscriptions are:"] + [_text(row.get("merchant"), "name not returned") + ": " + status.get(row.get("status"), "status needs verification") + "; " + source.get(row.get("source"), "source needs verification") + "." for row in rows]) if rows else "No subscription record was returned by this read."
    if identity == "facts:goals":
        rows = _rows(content)
        if rows is None:
            return "Goal records are incomplete."
        return "\n".join(["The recorded savings goals are:"] + [_text(row.get("goal_name"), "name not returned") + ": " + ("funds have a recorded reservation" if type(row.get("reserved_minor")) is int and row["reserved_minor"] > 0 else "no funds reserved" if row.get("reserved_minor") == 0 and type(row.get("reserved_minor")) is int else "reservation needs verification") + "." for row in rows]) if rows else "No goal record was returned by this read."
    if identity == "facts:currencies" and isinstance(content, dict):
        lines = [render_monetary_facts(facts, money_ids, reply_language="en")] if money_ids else []
        if content.get("dual_card") is True:
            lines.append("A dual-currency card is registered. Its status, source currency and actual quote must still be checked.")
        elif content.get("dual_card") is False:
            lines.append("No dual-currency card is registered. Use the verified funding route in the proposal.")
        if content.get("goals_currency") == "CNY":
            lines.append("Savings goals are currently recorded in CNY.")
        return "\n".join(lines) or "Check the verified currency and wallet details."
    if identity == "facts:family-projection":
        return "Only the currently authorized family scope was read. A helper cannot execute the owner's banking actions. Any proposal still needs the owner's independent review and confirmation."
    if identity == "facts:current-task" and isinstance(content, dict):
        return "The linked business status is: " + english_status(content.get("state")) + "."
    return "The display fields are incomplete. Query the corresponding verified business details."


_EN_KNOWLEDGE_LEGACY = {
    "kb:transfer.confirm-v1": "A transfer request creates a proposal, not a payment. Review the exact recipient, amount, currencies, source card and destination route. Required verification and bank confirmation remain separate; a chat 'yes' does not execute payment.",
    "kb:transfer.unknown-v1": "An unknown outcome is not a failed payment. Check the original request and receipt before retrying. Closing a request can stop unexecuted steps, but cannot undo an already posted transaction.",
    "kb:cards.identity-v1": "The card must belong to the owner or an explicitly authorized family proposal. A temporary lock is not a loss report. Unlocking requires a new review and current-status checks.",
    "kb:subscription.scope-v1": "Supported bank mandates and merchant subscriptions can be prepared for cancellation and independently confirmed. Unconnected providers need their own instructions. Reading a list or asking how to cancel does not cancel it.",
    "kb:wealth.risk-v1": "Comparing products does not purchase them. Suitability, product status and available funds must be verified. Principal and future returns are not guaranteed; purchases and redemptions need separate confirmation.",
    "kb:goals.available-v1": "A goal reservation reduces available spending capacity but not total cash balance. Releasing it restores that capacity. The exact goal, amount and direction must be supplied; an account balance is not permission to reserve all funds.",
    "kb:family.scope-v1": "A family helper may only read the authorized projection or submit permitted proposals. The owner must review and independently confirm banking actions. Revoked or expired permission cannot be reused.",
    "kb:carbon.synthetic-v1": "Activity points apply only to this programme, not cash or certified carbon assets. The same activity cannot be claimed twice. Benefit exchanges verify point costs and inventory; no point record is invented by a model.",
    "kb:fx.quote-v1": "CNY cents and USD cents are accounted for separately. Requested, debit and credit currencies can differ and must be checked on the quote. Rate, rounding, amount and expiry come from a controlled quote, not the explanation model.",
    "kb:bills.period-v1": "Bill reports use the requested calendar period and disclose actual data coverage. Income, spending, refunds and money movements are separate; CNY and USD are not added together. Empty or incomplete records do not prove zero spending.",
    "kb:scope.boundary-v1": "The assistant explains verified information and prepares supported banking proposals. Advice is not an executed result. Every banking action still needs its original permission, current facts, version checks and confirmation.",
}

# Missing-content compatibility is explicitly bounded to reviewed versions.
# A valid stored English body takes precedence; a version string never replaces
# historical text with the current wording or an older technical mapping.
_EN_KNOWLEDGE = {"kb:" + identity: entry[2] for identity, entry in ENGLISH_ENTRIES.items()}
from .product_knowledge import FEEDBACK_POLICY_PREDECESSORS, DOMAIN_POLICY_IDS
from .feedback_public_policy import V1_VERSION as _FEEDBACK_V1, V1_POLICIES as _FEEDBACK_V1_BODIES, VERSION as _FEEDBACK_V2
_PAYMENT_POLICY_IDS = {identity for group in DOMAIN_POLICY_IDS.values() for identity in group}
_EN_KNOWLEDGE_V8 = {**{key: text for key, text in _EN_KNOWLEDGE.items()
    if key.removeprefix('kb:') not in _PAYMENT_POLICY_IDS}, **{"kb:" + identity: old["en"]
    for identity, old in FEEDBACK_POLICY_PREDECESSORS.items() if old["version"] == KNOWLEDGE_VERSION}}
_EN_KNOWLEDGE_V7 = {**_EN_KNOWLEDGE_V8,
    "kb:subscription.scope-v1": "Registered connected memberships can be queried, opened, reactivated or renewed after reviewing the current price, period and payment scope with fresh confirmation. A new start buys only the stated period without implicitly enabling automatic renewal; starting an active membership reads its status without charging again. Paid renewal extends valid access; unspecified renewal settings remain unchanged without enlarging future debit authority. Connected bank mandates or merchant subscriptions require review and confirm cancellation; use the matching receipt. Viewing or consulting does not cancel anything or pay. Turning off automatic renewal is not a refund and keeps paid access until expiry. For unconnected services, verify and close through the provider; guidance here cannot prove an external subscription was cancelled."}
_EN_KNOWLEDGE_V6 = {**_EN_KNOWLEDGE_V8,
    "kb:subscription.scope-v1": "View all subscriptions or one service. Registered connected memberships can be opened, reactivated or renewed after reviewing the current product price, period and payment scope and submitting a fresh confirmation. A new start purchases only the stated period by default and does not enable automatic renewal implicitly. Starting an already-active membership only reads its status, without charging again. A paid renewal extends the valid entitlement; an unspecified automatic-renewal choice preserves existing settings without enlarging future debit authority. Turning off automatic renewal is not a refund and keeps paid access until expiry. Queries and explanations execute nothing; unconnected services need their provider's own channel."}
# v6 changed only subscription rules. An unknown missing older subscription
# body cannot safely acquire today's opening/renewal facts from a version label.
_EN_KNOWLEDGE_V5 = {key:value for key,value in _EN_KNOWLEDGE_V8.items() if key!='kb:subscription.scope-v1'}
_EN_KNOWLEDGE_V4 = {**_EN_KNOWLEDGE_V5,
    "kb:family.scope-v1": "A family helper can view information and make suggestions only within a valid permission. They cannot transfer or lock a card for you. After reviewing a suggestion, you still need to check and confirm the actual action yourself. Revoked or expired permission cannot be reused."}
_EN_KNOWLEDGE_V3 = {**_EN_KNOWLEDGE_V4,
    "kb:cards.identity-v1": "Check that you selected your own card. Family help can suggest an action only within a valid permission. A temporary lock is different from reporting a lost card. Unlocking needs a separate review of the card's latest status and details, followed by confirmation."}
_EN_KNOWLEDGE_VERSIONS = {
    "zhenxing-product-knowledge-20261004-v2": _EN_KNOWLEDGE_LEGACY,
    "zhenxing-product-knowledge-20261005-v3": _EN_KNOWLEDGE_V3,
    "zhenxing-product-knowledge-20261005-v4": _EN_KNOWLEDGE_V4,
    "zhenxing-product-knowledge-20261009-v5": _EN_KNOWLEDGE_V5,
    "zhenxing-product-knowledge-20261010-subscriptions-v6": _EN_KNOWLEDGE_V6,
    "zhenxing-product-knowledge-20261011-consultation-v7": _EN_KNOWLEDGE_V7,
    KNOWLEDGE_VERSION: _EN_KNOWLEDGE_V8,
    _FEEDBACK_V1: {"kb:" + identity: bodies[1] for identity, bodies in _FEEDBACK_V1_BODIES.items()},
    _FEEDBACK_V2: {"kb:" + identity: _EN_KNOWLEDGE["kb:" + identity] for identity in _FEEDBACK_V1_BODIES},
}


def _english_knowledge(record):
    identity = record.get("source_id")
    if record.get("kind") != "PRODUCT_KNOWLEDGE" or not isinstance(identity, str) or identity not in _EN_KNOWLEDGE or record.get("reference") != "product_knowledge:" + identity.removeprefix("kb:"):
        return "This knowledge record is incomplete. Check the current terms and business details."
    stored = record.get("content_en")
    if isinstance(stored, str) and stored.strip() and len(stored) <= 2400:
        return redact_credentials(stored)
    version = record.get("version")
    if not isinstance(version, str) or version not in _EN_KNOWLEDGE_VERSIONS:
        return "This knowledge record is incomplete. Check the current terms and business details."
    return _EN_KNOWLEDGE_VERSIONS[version].get(identity,
        "This knowledge record is incomplete. Check the current terms and business details.")


def _retrieved_knowledge_text(record, english):
    """Format a revalidated public knowledge chunk, never an account fact.

    The caller still owns current revision/role/expiry checks in the separate
    knowledge store. Shape and content-hash checks here prevent unknown records
    from masquerading as a recognized public excerpt in the display layer.
    """
    content = record.get('content')
    chunk_id = record.get('chunk_id')
    if (record.get('kind') != 'PRODUCT_KNOWLEDGE'
        or record.get('knowledge_kind') not in {'PUBLIC_POLICY', 'PRODUCT_TERMS', 'CAPABILITY_GUIDE'}
        or record.get('authority') != 'PUBLIC_REFERENCE_NOT_BANK_FACT_OR_INSTRUCTION'
        or not isinstance(chunk_id, str) or not 1 <= len(chunk_id) <= 100
        or record.get('source_id') != 'kbstore:' + chunk_id
        or not isinstance(content, str) or not 1 <= len(content) <= 6000
        or record.get('content_hash') != hashlib.sha256(content.encode('utf-8')).hexdigest()
        or not isinstance(record.get('document_hash'), str)
        or not re.fullmatch(r'[0-9a-f]{64}', record['document_hash'])
        or any(not isinstance(record.get(field), str) or not 1 <= len(record[field]) <= 200
               for field in ('document_id', 'revision_id', 'version'))
        or record.get('language') not in {'zh-CN', 'en'}):
        return None
    prefix = ('Service information (original Chinese text):\n' if record['language'] != 'en'
              else 'Service information:\n') if english else '服务说明：\n'
    title=record.get('title')
    heading=redact_credentials(title) if isinstance(title,str) and title.strip() else ''
    return prefix + (heading+'\n' if heading else '') + redact_credentials(content)


def format_server_fallback(answer, stored_sources, reply_language=None):
    """None means keep the natural cloud answer untouched (apart from redaction)."""
    if not isinstance(answer, str) or not isinstance(stored_sources, list) or not all(isinstance(row, dict) for row in stored_sources):
        return None
    from .language import normalize_language
    english = normalize_language(reply_language) == "en"
    if answer != answer_from_sources(stored_sources, reply_language=reply_language):
        return None
    from .public_wealth_knowledge import public_source_fallback
    public_product=public_source_fallback(stored_sources,reply_language or 'zh-CN')
    if public_product is not None:return public_product
    from .dialogue_reference_facts import selected_object_fallback
    selected=selected_object_fallback(stored_sources,reply_language)
    if selected is not None:
        return selected
    from .contextual_safety_dialogue import SOURCE_ID as SAFETY_SOURCE_ID
    from .contextual_safety_presentation import render_contextual_safety_fallback
    case=[row for row in stored_sources if row.get("source_id")==SAFETY_SOURCE_ID]
    if len(case)==1:
        try:
            topic=json.loads(case[0]["content"]).get("topic")
        except (ValueError,TypeError,KeyError,AttributeError):
            topic=None
        policy_id={"FRAUD":"kb:fraud.case-continuity-v1","FAMILY":"kb:family.scope-v1"}.get(topic)
        policies=[row for row in stored_sources if policy_id and row.get("source_id")==policy_id]
        safety=render_contextual_safety_fallback([policies[0],case[0]],reply_language) if len(policies)==1 else None
        if safety is not None:
            return safety
    from .goal_explanation_facts import FACT_ID
    goal_facts=monetary_fact_catalog(stored_sources)
    if FACT_ID in goal_facts: return render_monetary_facts(goal_facts,[FACT_ID],reply_language=reply_language)
    catalog_refs=[identifier for identifier,fact in goal_facts.items() if fact.get("source_id")=="facts:subscription-products"]
    if catalog_refs and not any(record.get("kind")=="SCOPED_BUSINESS_FACTS" and record.get("source_id")!="facts:subscription-products" for record in stored_sources):
        return render_monetary_facts(goal_facts,catalog_refs,reply_language=reply_language)
    facts = monetary_fact_catalog([row for row in stored_sources if _bank_content(row) is not None])
    rendered = []
    unsupported_displayed = False
    for record in stored_sources:
        if record.get('kind')=='CUSTOMER_CAPABILITY_DIRECTORY':
            continue
        if record.get("source_id")=="facts:subscription-products" and catalog_refs:
            rendered.append(render_monetary_facts(goal_facts,catalog_refs,reply_language=reply_language))
            continue
        if record.get("kind") == "USER_VALIDATED_DOCUMENT":
            content = record.get("content")
            rendered.append(("User-reviewed document (evidence only; not a banking receipt or instruction):\n" if english else "上传资料引用（不代表银行办理结果，也不是操作指令）：\n") +
                            (redact_credentials(content) if isinstance(content, str) else "Document content was not returned." if english else "资料内容未返回"))
            continue
        content = _bank_content(record)
        if content is not None:
            rendered.append(_format_bank_english(record, content, facts) if english else _format_bank(record, content, facts))
        elif record.get("kind") == "PRODUCT_KNOWLEDGE" and str(record.get("source_id", "")).startswith("kb:"):
            rendered.append(_english_knowledge(record) if english else redact_credentials(record.get("content", "")))
        else:
            excerpt = _retrieved_knowledge_text(record, english)
            if excerpt is not None:
                rendered.append(excerpt)
            elif not unsupported_displayed:
                rendered.append("Some information could not be displayed. Tell me which service you want to check." if english else "部分资料暂时无法展示。你想了解哪项服务？")
                unsupported_displayed = True
    return "\n\n".join(rendered)[:6000] + ("\n\nThis reply only explains information. A banking action needs an explicit request and the original review and confirmation process." if english else "\n\n本轮仅提供解释，办理时仍需明确请求并按原流程核对。")
