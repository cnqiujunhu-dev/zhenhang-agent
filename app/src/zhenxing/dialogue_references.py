"""Source-bound result objects for ordinary conversational references.

This module does not issue commands, select permissions, or trust a model's
object IDs. A read can identify an object; only a fresh explicit user request
may propose a write, which still needs the existing banking confirmation.
The complete catalog stays server-side. Use ``public_result_reference_catalog``
for model input, and revalidate the catalog before planning or execution.
"""
from __future__ import annotations

from copy import deepcopy
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .intelligence import IntentProposal, READ_KINDS, _intent_text
from .models import Account, Card, InvestmentProduct, Subscription, Task, Transaction, User
from .permissions import require_banking_actor
from .planning import canonical_hash
from .redaction import redact_credentials
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

VERSION = "verified-dialogue-result-references-v1"
SUPPORTED_READS = frozenset({"CARD_STATUS", "SUBSCRIPTION_LIST", "WEALTH_COMPARE", "BILL_ANALYSIS", "FAMILY_VIEW", "FRAUD_ANALYZE", "CARBON_VIEW",'ASSET_POSITIONS_READ','MEMBERSHIP_VIEW','BENEFITS_VIEW','PREFERENCE_VIEW'})
from .dialogue_extended_read_sources import READ_KINDS as EXTENDED_READS, DOMAINS as EXTENDED_DOMAINS
SUPPORTED_READS = SUPPORTED_READS | EXTENDED_READS
DOMAINS = {"CARD_STATUS": "CARDS", "SUBSCRIPTION_LIST": "SUBSCRIPTIONS", "WEALTH_COMPARE": "WEALTH", "BILL_ANALYSIS": "BILLS", "FAMILY_VIEW": "FAMILY", "FRAUD_ANALYZE": "FRAUD", "CARBON_VIEW": "CARBON",'ASSET_POSITIONS_READ':'ASSETS','MEMBERSHIP_VIEW':'MEMBERSHIP','BENEFITS_VIEW':'BENEFITS','PREFERENCE_VIEW':'PREFERENCES'}
DOMAINS.update(EXTENDED_DOMAINS)
MAX_ENTITIES = 100
RUNTIME_METADATA_PROJECTION = 'result-source-business-metadata-v1'
_PRESENTATION_METADATA = frozenset({'customer_reply', 'conversation_reply_snapshots'})
_FOREIGN = re.compile(r"别人|他人|朋友的|同事的|(?:他|她|他们|她们)的|\b(?:his|her|their|someone\s+else|another\s+user)'?s?\b", re.I)
_INJECTION = re.compile(r"忽略.{0,15}(?:指令|规则|权限)|系统(?:提示|指令)|developer\s+message|ignore.{0,20}(?:rules|instructions)|\b(?:entity|result)_ref\s*[:=]", re.I)
_PRONOUN = re.compile(r"这张|那张|这个|那个|这一项|那一项|它|这类|那类|\b(?:this|that|it|the\s+same)\b", re.I)
_ORDINAL = re.compile(r"第\s*([一二两三四五六七八九十\d]+)\s*(?:个|张|项|笔|款)?|\b(first|second|third|fourth|fifth)\b", re.I)
_WHY = re.compile(r"为什么|为啥|怎么.{0,6}(?:这么|那么|多|算)|怎样算|如何算|解释|怎么样|什么影响|会退款吗|\b(?:why|how|explain|what\s+(?:happened|about))\b", re.I)
_QUESTION = re.compile(r"只是问|只解释|只说说|先只|能否|可以.{0,4}吗|可不可以|如果|假如|\b(?:only\s+explain|just\s+asking|if|could|can\s+(?:I|you))\b", re.I)


def _deny():
    raise DomainError("DIALOGUE_REFERENCE_SOURCE_CHANGED", "刚才查询的对象或权限已经变化，请重新查看后再选择。", 409)


def _safe_name(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 120 and value == redact_credentials(value) and _intent_text(value) == value and not _INJECTION.search(value)


def _actor_epoch(session, principal):
    from .access_models import LoginAccount
    role = require_banking_actor(session, principal)
    user = session.get(User, principal.user_id)
    if not user or user.sandbox_id != principal.sandbox_id:
        _deny()
    account = session.scalar(select(LoginAccount).where(LoginAccount.user_id == principal.user_id, LoginAccount.sandbox_id == principal.sandbox_id))
    return {"role": role, "login_version": account.version if account else None,
            "family_context_hash": canonical_hash(principal.family_context) if getattr(principal, "family_context", None) else None}


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def _metadata_hash(metadata, projection):
    """Two known async presentation caches are not bank-result provenance.

    Every other key, including the immutable read snapshot, semantic binding,
    role/owner context and operation bookkeeping, remains fingerprinted.
    Legacy catalogs deliberately retain their whole-metadata fingerprint.
    """
    if projection is None:
        return canonical_hash(metadata)
    if projection != RUNTIME_METADATA_PROJECTION:
        _deny()
    return canonical_hash({key: value for key, value in metadata.items() if key not in _PRESENTATION_METADATA})


def _catalog_metadata_projection(catalog):
    sources = catalog.get('sources', [])
    key = 'runtime_metadata_projection_version'
    if all(key not in source for source in sources):
        return None
    if all(source.get(key) == RUNTIME_METADATA_PROJECTION for source in sources):
        return RUNTIME_METADATA_PROJECTION
    _deny()  # Unknown or mixed versions cannot become legacy compatibility.


def _source(session, principal, thread, task_id, before_sequence):
    task = session.get(Task, task_id)
    if not _owned(task, principal) or task.state in {"UNKNOWN", "EXECUTING", "FAILED", "CLOSED"}:
        _deny()
    epoch = _actor_epoch(session, principal)
    query = select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.task_id == task.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.status == "ANSWERED", ChatTurn.mode.in_(["NEW_TASK", "REVISE"]))
    if before_sequence is not None:
        query = query.where(ChatTurn.sequence < before_sequence)
    turn = session.scalar(query.order_by(ChatTurn.sequence.desc()).limit(1))
    if not turn or turn.context_policy not in {"AUTO", "INDEPENDENT"} or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get("input_redacted") \
            or turn.diagnostics_json.get("actor_role_at_submission") != epoch["role"] or _intent_text(turn.message) != turn.message:
        _deny()
    record = session.get(RuntimeRecord, task.id)
    if record and not any(record.metadata_json.get(key) for key in
            ('cloud_semantic_intent','owned_user_dialogue','dialogue_slot_update','dialogue_result_reference')) \
            and turn.message!=task.message:
        _deny()  # An unproved legacy query cannot relabel its original raw user source.
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record and record.kind == "PARENT" and record.plan_version else None
    if not snapshot or task.plan_hash != snapshot.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
        _deny()
    from .runtime import task_trace, validate_runtime_family_context
    from .conversation_provenance import validate_current_continuation
    validate_runtime_family_context(session, task)
    validate_current_continuation(session, task)
    proposal = IntentProposal.model_validate(snapshot.proposal_json)
    trace = task_trace(session, principal, task.id)
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id,
        RuntimeOperation.active == 1, RuntimeOperation.plan_version == record.plan_version).order_by(RuntimeOperation.ordinal)))
    if len(operations) != len(proposal.actions):
        _deny()
    if turn.context_policy == 'INDEPENDENT':
        # Independent means this request did not consume old dialogue, not
        # that its newly verified READ result may never be discussed later.
        # Only a complete native pure READ may introduce this source. Do not
        # turn independent WRITE/mixed/legacy preparations into new authority.
        native = (record.metadata_json or {}).get('cloud_semantic_intent') or {}
        model = (native.get('offered_context') or {}).get('model') or {}
        if task.state != 'SUCCEEDED' or native.get('decision', {}).get('mode') != 'REQUEST' \
                or not proposal.actions or any(action.kind not in READ_KINDS for action in proposal.actions) \
                or any(op.status != 'SUCCEEDED' or op.action_kind not in READ_KINDS for op in operations) \
                or any(model.get(key) != [] for key in ('frames', 'entities', 'history')) \
                or session.scalar(select(RuntimeOperation.id).where(RuntimeOperation.parent_task_id == task.id,
                    RuntimeOperation.action_kind.not_in(READ_KINDS)).limit(1)) is not None:
            _deny()
    nodes = {row.get("operation_id"): row for row in trace.get("nodes", []) if isinstance(row, dict)}
    verified = []
    for op, action in zip(operations, proposal.actions):
        if op.action_kind != action.kind or op.sandbox_id != principal.sandbox_id:
            _deny()
        if op.action_kind not in SUPPORTED_READS or op.status != "SUCCEEDED":
            continue
        child = session.get(Task, op.child_task_id) if op.child_task_id else None
        child_record = session.get(RuntimeRecord, child.id) if child else None
        node = nodes.get(op.id)
        if not _owned(child, principal) or child.state != "SUCCEEDED" or not child_record or child_record.root_task_id != task.id \
                or not node or node.get("status") != "SUCCEEDED" or node.get("action") != op.action_kind \
                or not isinstance(child.result_json, dict) or node.get("result") != child.result_json:
            _deny()
        if epoch["role"] == "FAMILY_HELPER" and op.action_kind != "FAMILY_VIEW":
            _deny()
        verified.append((op, child, node))
    return task, turn, record, snapshot, trace, verified


def _entity(kind, field, value, ordinal, row, *, facts=None):
    return {"read_kind": kind, "domain": DOMAINS[kind], "field": field, "value": value,
            "ordinal": ordinal, "record_id": row.id, "record_version": getattr(row, "version", None),
            "live_record_hash": canonical_hash(facts or {"value": value, "version": getattr(row, "version", None)}),
            "facts": facts or {}}


def entity_allowed_fields(entity):
    """Identity fields only; facts/balances never become writable slot values.

    A platform-card result exposes its canonical card_number field, not a
    simultaneous card_last4 alias that could silently truncate the target.
    Non-target summaries remain selectable consultation objects only.
    """
    if not isinstance(entity, dict):
        return []
    from .bill_query_context import identity_fields
    if identity_fields(entity):
        return identity_fields(entity)
    if entity.get("read_kind") in EXTENDED_READS:
        from .dialogue_extended_read_sources import extended_identity_fields
        return extended_identity_fields(entity)
    if entity.get("read_kind") == "CARD_STATUS" and entity.get("field") == "card_number":
        return ["card_number", "card_last4", "source_card", "destination_card_reference"]
    from .family_read_contract import SELECTOR_FIELDS as FAMILY_READ_FIELDS
    permitted = {"CARD_STATUS": {"card_number", "card_last4"}, "SUBSCRIPTION_LIST": {"merchant"},
        "WEALTH_COMPARE": {"product_name"}, "BILL_ANALYSIS": {"category_filter"},
        'ASSET_POSITIONS_READ':{'product_name'},'BENEFITS_VIEW':{'benefit_name'},'PREFERENCE_VIEW':{'preference_kind'},
        "FAMILY_VIEW": FAMILY_READ_FIELDS, "CARBON_VIEW": {"reward_name", "behavior_name"}}
    field = entity.get("field")
    return [field] if field in permitted.get(entity.get("read_kind"), set()) else []


def _objects(session, principal, kind, result, trace):
    """Result array order is the UI order, except the explicit product ranking."""
    out = []
    if kind in EXTENDED_READS:
        from .dialogue_extended_read_sources import extended_read_entities
        return extended_read_entities(session, principal, kind, result)
    if kind in {'MEMBERSHIP_VIEW','BENEFITS_VIEW','PREFERENCE_VIEW'}:
        from .customer_profile_facts import profile_read_result_current,profile_result_fact_entities
        if not profile_read_result_current(session,principal,kind,result):_deny()
        return profile_result_fact_entities(kind,result)
    if kind=='ASSET_POSITIONS_READ':
        from .asset_positions import asset_read_result_current
        current=asset_read_result_current(session,principal,result)
        for index,row in enumerate(current['positions'],1):
            facts={key:deepcopy(value) for key,value in row.items() if key not in {'position_id','instrument_id','product_id','source'}}
            facts['synthetic']=row['source'].get('synthetic') is True
            out.append({'read_kind':kind,'domain':'ASSETS','field':'product_name','value':row['instrument_id'],
                'label':row['product_name'],'ordinal':index,'record_id':row['position_id'],
                'record_version':row['source'].get('version'),'live_record_hash':canonical_hash(row),'facts':facts})
        if not out:
            facts={'position_count':current['summary']['position_count'],'status':current['status'],
                'asset_class':current['query_scope']['asset_class']}
            out.append({'read_kind':kind,'domain':'ASSETS','field':'position_collection','value':'本人持仓查询范围',
                'label':'本人持仓查询结果','ordinal':None,'record_id':'positions:'+current['owner_scope_hash'],
                'record_version':current['source_fingerprint'],'live_record_hash':canonical_hash(facts),'facts':facts})
        return out
    if kind in {"CARBON_VIEW", "WEALTH_COMPARE", "FAMILY_VIEW"}:
        from .read_selection import validate_result_origin_selection
        validate_result_origin_selection(session, principal, kind, result, trace)
    if kind == "CARD_STATUS":
        from .card_identifiers import card_identifier_view
        from .cards import _balance_projection, card_status_scope_current
        if not card_status_scope_current(session, principal, result): _deny()
        for index, value in enumerate(result.get("cards", []), 1):
            row = session.get(Card, value.get("card_id"))
            scoped_balance = result.get('card_balance_scope')
            if not _owned(row, principal) or row.version != value.get("version") or row.status != value.get("status") or (not scoped_balance and row.limit_minor != value.get("limit_minor")):
                _deny()
            identifier = card_identifier_view(session, row) or {}
            if value.get("card_number") != identifier.get("card_number"):
                _deny()
            projection = _balance_projection(session, row)
            if scoped_balance:
                from .card_balance_scope import project_balance_row
                projection = project_balance_row(projection, scoped_balance['requested_currencies'])
            if any(value.get(key) != projection.get(key) for key in ("balances_by_currency", "supported_currencies", "currency_mode", "usd_wallet_version", "fx_profile_version")):
                _deny()
            number = identifier.get("card_number")
            target = number if number else row.last4
            facts = {"status": row.status, **({'limit_minor': row.limit_minor} if not scoped_balance or 'CNY' in scoped_balance['requested_currencies'] else {}),
                     "balances_by_currency": projection.get("balances_by_currency", {}), "supported_currencies": projection.get("supported_currencies", [])}
            available_keys={"reserved_cash_minor","available_by_currency"}
            if scoped_balance and 'CNY' not in scoped_balance['requested_currencies']:
                available_keys={'available_by_currency'}
            if available_keys.intersection(value):
                if not available_keys.issubset(value) or ('reserved_cash_minor' in available_keys and type(value["reserved_cash_minor"]) is not int) \
                        or not isinstance(value["available_by_currency"],dict) \
                        or any(type(amount) is not int or amount<0 for amount in value["available_by_currency"].values()):_deny()
                from .cards import debit_card_status_row
                current_money=debit_card_status_row(session,row,include_internal=True)
                if scoped_balance:
                    from .card_balance_scope import project_balance_row
                    current_money = project_balance_row(current_money, scoped_balance['requested_currencies'])
                if any(value[key]!=current_money[key] for key in available_keys):_deny()
                account=session.get(Account,row.account_id)
                if not _owned(account,principal) or account.currency!="CNY":_deny()
                facts["available_by_currency"]=deepcopy(current_money["available_by_currency"])
                # Central cash reservations are CNY only. Keep their currency
                # explicit even on a card that also exposes a USD wallet.
                if 'reserved_cash_minor' in current_money:
                    facts["reserved_cash_by_currency"]={"CNY":current_money["reserved_cash_minor"]}
            if 'balance_scope_refs' in value:
                if value['balance_scope_refs']!=projection.get('balance_scope_refs'):_deny()
                facts['balance_scope_refs']=deepcopy(projection['balance_scope_refs'])
            if 'card_tier_name' in value:
                from .customer_membership import card_tier_display
                tiers=card_tier_display(session,row)
                if any(value.get(key)!=tiers.get(key) for key in tiers):_deny()
                facts['card_tier_name']=tiers['card_tier_name']
            item = _entity(kind, "card_number" if number else "card_last4", target, index, row, facts=facts)
            item["label"] = "尾号" + target[-4:]
            item["account_ref_hash"] = canonical_hash({"account_id": row.account_id})
            out.append(item)
    elif kind == "SUBSCRIPTION_LIST":
        for index, value in enumerate(result.get("subscriptions", []), 1):
            name = value.get("merchant")
            rows = list(session.scalars(select(Subscription).where(Subscription.sandbox_id == principal.sandbox_id,
                Subscription.user_id == principal.user_id, Subscription.merchant == name)))
            if len(rows) != 1 or not _safe_name(name):
                _deny()
            row = rows[0]
            from .simulation import as_utc
            from datetime import datetime
            try:
                due_same = as_utc(datetime.fromisoformat(value["next_due_at"])) == as_utc(row.next_due_at)
            except (ValueError, TypeError, KeyError):
                due_same = False
            if not due_same or any(value.get(key) != getattr(row, key) for key in ("amount_minor", "status", "source_type")):
                _deny()
            if value.get("version") is not None and value["version"] != row.version:
                _deny()
            facts = {key: value.get(key) for key in ("amount_minor", "status", "next_due_at", "source_type", "can_cancel_here")}
            # Subscription amounts are the registered CNY period debit, not
            # the public catalogue's current monthly activation price.
            facts.update(currency='CNY',billing_months=row.billing_months,charge_basis='RECORDED_SUBSCRIPTION_PERIOD')
            # Paid rights and renewal preference are source facts, never
            # purchase authority. Old payloads stay compatible but cannot
            # fabricate these new facts. Compare UTC instants, not formatting.
            for key in ("auto_renew", "billing_months"):
                if key in value:
                    actual=bool(row.auto_renew) if key=="auto_renew" else row.billing_months
                    if type(value[key]) is not type(actual) or value[key]!=actual:_deny()
                    facts[key]=actual
            for key in ("entitlement_end_at", "last_paid_at"):
                if key in value:
                    actual=getattr(row,key)
                    try:
                        stated=as_utc(datetime.fromisoformat(value[key])) if value[key] is not None else None
                    except (ValueError,TypeError):_deny()
                    actual=as_utc(actual) if actual is not None else None
                    if stated!=actual:_deny()
                    facts[key]=actual.isoformat() if actual is not None else None
            if "entitled" in value:
                from .subscription_timing import subscription_calendar_context
                current_calendar, _through = subscription_calendar_context(session, principal.sandbox_id)
                actual=bool(row.entitlement_end_at and as_utc(row.entitlement_end_at)>current_calendar)
                if type(value["entitled"]) is not bool or value["entitled"] is not actual:_deny()
                facts["entitled"]=actual
            # A recorded debit amount is not today's purchasable month price.
            # This query has no SubscriptionProduct version metadata: fresh
            # public registry facts supply prices instead of copying its
            # catalog_monthly_price_minor into the selectable result facts.
            out.append(_entity(kind, "merchant", name, index, row, facts=facts))
    elif kind == "WEALTH_COMPARE":
        from .wealth import list_wealth_products
        from .read_selection import read_selection_parameters, same_scoped_read_result
        from .wealth_suitability_facts import KEY, VERSION as SUITABILITY_VERSION, checked_profile_snapshot, make_product_fact
        saved_profile = result.get("risk_profile")
        marked = isinstance(saved_profile, dict) and KEY in saved_profile
        # A new producer's marker cannot be removed to impersonate legacy.
        # Direct domain primitives (trace={}) have no task evidence; their old
        # shape remains unmarked, while runtime sources bind the exact receipt.
        evidence = []
        wealth_nodes = [node for node in trace.get("nodes", []) if node.get("action") == kind
            and node.get("status") == "SUCCEEDED" and node.get("result") == result]
        if wealth_nodes:
            from .models import EvidenceSnapshot
            if len(wealth_nodes) != 1: _deny()
            child = session.get(Task, wealth_nodes[0].get("child_task_id"))
            if not _owned(child, principal) or not isinstance(child.plan_json, dict): _deny()
            identifiers = [node.get("evidence_id") for node in child.plan_json.get("nodes", [])
                if node.get("tool_name") == "wealth.compare.v1"]
            evidence = [session.get(EvidenceSnapshot, identifier) for identifier in identifiers if identifier]
            if len(evidence) != 1 or evidence[0] is None or evidence[0].task_id != child.id \
                    or evidence[0].sandbox_id != principal.sandbox_id: _deny()
            payload = evidence[0].payload_json
            if not isinstance(payload, dict): _deny()
            evidence_marked = isinstance(payload.get("risk_profile"), dict) and KEY in payload["risk_profile"]
            if marked != evidence_marked or evidence[0].source_version == SUITABILITY_VERSION and not marked: _deny()
            if marked and (payload.get("risk_profile") != saved_profile or payload.get("products") != result.get("products")): _deny()
            expected_version=(result.get('read_selection') or {}).get('schema_version',SUITABILITY_VERSION)
            if marked and evidence[0].source_version != expected_version: _deny()
        elif marked and trace.get("nodes"):
            _deny()
        selected = read_selection_parameters(session, principal, kind, result)
        current = list_wealth_products(session, principal.sandbox_id, principal.user_id,
            _suitability_snapshot=marked, **(selected or {}))
        if marked:
            try:
                checked_profile_snapshot(saved_profile, current["risk_profile"], principal.sandbox_id, principal.user_id)
            except (ValueError, TypeError, KeyError):
                _deny()
        if selected is not None:
            same_scoped_read_result(kind, result, current)
        current_rows = {row["product_id"]: row for row in current["products"]}
        values = sorted(result.get("products", []), key=lambda row: not (row.get("suitable") is True and row.get("status") == "AVAILABLE"))
        for index, value in enumerate(values, 1):
            row = session.get(InvestmentProduct, value.get("product_id"))
            live = current_rows.get(value.get("product_id"))
            if not row or row.sandbox_id != principal.sandbox_id or not _safe_name(row.name) or not live \
                    or any(value.get(key) != live.get(key) for key in ("name", "version", "risk_level", "unit_price_minor", "status", "suitable", "holding_units", "terms", "suitability_reason_codes")):
                _deny()
            facts = {key: value.get(key) for key in ("risk_level", "unit_price_minor", "currency", "status", "suitable", "holding_units", "terms", "suitability_reason_codes")}
            if marked:
                try:
                    facts["wealth_suitability"] = make_product_fact(saved_profile, value)
                except (ValueError, TypeError, KeyError):
                    _deny()
            out.append(_entity(kind, "product_name", row.name, index, row, facts=facts))
    elif kind == "BILL_ANALYSIS":
        analysis = trace.get("analysis") or result.get("analysis")
        if not isinstance(analysis, dict):
            _deny()
        from .billing_diagnostics import VERSION as OWNED_BILL_VERSION
        owned_bill = analysis.get("billing_schema_version") == OWNED_BILL_VERSION
        from .customer_bill_fallback import prepare_customer_bill_supplement
        if owned_bill or (analysis.get("financial_insights") or {}).get("largest_expense"):
            from .models import EvidenceSnapshot
            bill_nodes=[node for node in trace.get("nodes",[]) if node.get("action")=="BILL_ANALYSIS" and node.get("status")=="SUCCEEDED"]
            if len(bill_nodes)!=1:_deny()
            child=session.get(Task,bill_nodes[0].get("child_task_id"))
            if not _owned(child,principal):_deny()
            evidence_ids=[node.get("evidence_id") for node in child.plan_json.get("nodes",[]) if node.get("tool_name")=="bill.analyze.v1"]
            evidence=[session.get(EvidenceSnapshot,identifier) for identifier in evidence_ids if identifier]
            if len(evidence)!=1 or not evidence[0] or evidence[0].task_id!=child.id or evidence[0].sandbox_id!=principal.sandbox_id \
                or any(evidence[0].payload_json.get(key)!=analysis.get(key) for key in ("period_start","period_end","financial_insights","category_filter","period_fallback")):_deny()
            if owned_bill:
                # Match the exact finite record_bill_evidence service contract.
                # Its financial_insights seals the full analysis; the child's
                # original full body remains independently bound here.
                expected_evidence={key:analysis.get(key) for key in ("transaction_count","spending_minor",
                    "period_start","period_end","coverage","financial_insights","period_fallback","category_filter")}
                expected_evidence["anomaly_ids"]=[item["transaction_id"] for item in analysis.get("anomalies",[])]
                if canonical_hash(evidence[0].payload_json)!=canonical_hash(expected_evidence) \
                        or canonical_hash(child.analysis_json)!=canonical_hash(analysis):_deny()
                from .billing_diagnostics import checked_owned_bill_analysis, bill_result_fact_entities
                try:
                    checked=checked_owned_bill_analysis(session,principal.sandbox_id,principal.user_id,analysis)
                except DomainError:
                    _deny()
                from .bill_query_context import card_entities
                # New, explicitly marked analyses preserve query identity even
                # when the selected instrument/currency has no economic rows.
                facts = bill_result_fact_entities(checked)
                identities = card_entities(session, principal, checked)
                # Identity/summary navigation has no transaction ordinal. Keep
                # it before optional displayed detail so the forty-entity
                # language budget cannot silently lose the selected card.
                return [*[row for row in facts if row['field'] == 'bill_query_summary'],
                    *identities, *[row for row in facts if row['field'] != 'bill_query_summary']]
        nodes=trace.get('nodes',[])
        retained=None
        if len(nodes)==1 and nodes[0].get('action')=='BILL_ANALYSIS':
            from .customer_read_history import retained_bill_read_scope
            origin=session.get(Task,trace.get('task_id'))
            retained=retained_bill_read_scope(session,principal,origin)
        if retained:
            if canonical_hash(analysis)!=canonical_hash(retained['original_analysis']):_deny()
            # Period, rows, category objects and ordinal facts must all use
            # the same once-recorded display as the inherited bill scope.
            analysis=retained['displayed_analysis']
        else:
            alternative = prepare_customer_bill_supplement(session, principal, trace)
            if alternative:analysis = alternative
        from .financial_diagnostics import checked_largest_expense_snapshot
        try:
            largest=checked_largest_expense_snapshot(session,principal.sandbox_id,principal.user_id,analysis)
        except DomainError:
            _deny()
        for index, value in enumerate(analysis.get("statement_transactions", []), 1):
            row = session.get(Transaction, value.get("transaction_id"))
            account = session.get(Account, row.account_id) if row else None
            if not row or row.sandbox_id != principal.sandbox_id or not _owned(account, principal) \
                    or row.amount_minor != value.get("amount_minor") or redact_credentials(row.merchant) != value.get("merchant") or redact_credentials(row.category) != value.get("category"):
                _deny()
            from .simulation import as_utc
            if as_utc(row.occurred_at).isoformat() != value.get("occurred_at"):
                _deny()
            facts = {key: value.get(key) for key in ("amount_minor", "currency", "merchant", "category", "occurred_at", "cash_flow_type")}
            facts.update({key: analysis.get(key) for key in ("period_start", "period_end")})
            facts["statement_transactions_truncated"] = analysis.get("statement_transactions_truncated") is True
            item = _entity(kind, "transaction", value["transaction_id"], index, row, facts=facts)
            item["label"] = value.get("merchant") if _safe_name(value.get("merchant")) else "账单记录"
            item["card_ref_hash"] = canonical_hash({"card_id": row.card_id}) if row.card_id else None
            out.append(item)
        if largest and largest["status"]=="FOUND":
            if largest["tie_count"]==1:
                value=largest["records"][0]
                existing=next((item for item in out if item["field"]=="transaction" and item["record_id"]==value["transaction_id"]),None)
                if existing is None:
                    row=session.get(Transaction,value["transaction_id"])
                    facts={**value,"cash_flow_type":"EXPENSE","period_start":analysis.get("period_start"),"period_end":analysis.get("period_end"),
                        "statement_transactions_truncated":analysis.get("statement_transactions_truncated") is True}
                    facts.pop("transaction_id")
                    existing=_entity(kind,"transaction",value["transaction_id"],None,row,facts=facts)
                    existing["label"]=value["merchant"] if _safe_name(value["merchant"]) else "账单记录"
                    existing["card_ref_hash"]=canonical_hash({"card_id":row.card_id}) if row.card_id else None
                    out.append(existing)  # Additional aggregate target never shifts original UI ordinals.
                existing["authoritative_largest"]=True
                existing["facts"]["largest_scope"]="FULL_RECORDED_WINDOW"
                existing["facts"]["largest_snapshot_hash"]=largest["snapshot_hash"]
                existing["facts"]["coverage"]=largest["coverage"]
                existing["live_record_hash"]=canonical_hash(existing["facts"])
            else:
                facts={"amount_minor":-largest["maximum_expense_minor"],"currency":"CNY","tie_count":largest["tie_count"],
                    "records":[{key:record[key] for key in ("merchant","category","occurred_at","amount_minor","currency")} for record in largest["records"]],
                    "period_start":analysis.get("period_start"),"period_end":analysis.get("period_end"),"coverage":largest["coverage"],
                    "largest_scope":"FULL_RECORDED_WINDOW","largest_snapshot_hash":largest["snapshot_hash"]}
                out.append({"read_kind":kind,"domain":"BILLS","field":"largest_expense_group","value":"并列最大支出",
                    "label":"并列最大支出","ordinal":None,"record_id":"largest-group:"+largest["snapshot_hash"],"record_version":None,
                    "authoritative_largest":True,"live_record_hash":canonical_hash(facts),"facts":facts})
        categories = analysis.get("category_spending_minor") or {}
        for index, (name, amount) in enumerate(sorted(categories.items(), key=lambda pair: pair[1], reverse=True), 1):
            if not _safe_name(name) or type(amount) is not int or amount < 0:
                _deny()
            facts = {"category": name, "spending_minor": amount, "currency": "CNY",
                     "period_start": analysis.get("period_start"), "period_end": analysis.get("period_end"),
                     "coverage": analysis.get("coverage"), "category_filter": analysis.get("category_filter")}
            # Categories are result aggregates, never arbitrary database IDs.
            out.append({"read_kind": kind, "domain": "BILLS", "field": "category_filter", "value": name,
                        "ordinal": index, "record_id": "category:" + name, "record_version": None,
                        "live_record_hash": canonical_hash(facts), "facts": facts})
    elif kind == "FAMILY_VIEW":
        from .extensions import family_view
        from .read_selection import read_selection_parameters, same_scoped_read_result
        selected = read_selection_parameters(session, principal, kind, result)
        live = family_view(session, principal.sandbox_id, principal.user_id, **(selected or {}), _principal=principal)
        # The read child also appends its human bridge ``message``. That is
        # not a family fact or a target source; compare the exact bank fields.
        if selected is not None:
            same_scoped_read_result(kind, result, live)
        elif canonical_hash(live) != canonical_hash({key: result.get(key) for key in live}):
            _deny()
        from .extensions_models import FamilyMember
        for value in result.get("actors", []):
            if value.get("user_id") == principal.user_id:
                continue
            actor = session.get(User, value.get("user_id"))
            member = session.scalar(select(FamilyMember).where(FamilyMember.sandbox_id == principal.sandbox_id,
                ((FamilyMember.owner_user_id == principal.user_id) & (FamilyMember.helper_user_id == value.get("user_id"))) |
                ((FamilyMember.helper_user_id == principal.user_id) & (FamilyMember.owner_user_id == value.get("user_id")))))
            if not actor or actor.sandbox_id != principal.sandbox_id or not member or not _safe_name(actor.display_name) \
                    or (member.helper_user_id == actor.id and member.display_name != actor.display_name):
                _deny()
            field = "grantee_name" if value.get("role") == "FAMILY_HELPER" else "owner_name"
            facts = {"display_name": actor.display_name, "relationship_role": value.get("role"),
                     "delegations": [{key: row.get(key) for key in ("scopes", "maximum_amount_minor", "limit_period", "expires_at", "status", "valid", "version")}
                         for row in result.get("delegations", []) if actor.id in (row.get("owner_user_id"), row.get("grantee_user_id"))]}
            out.append(_entity(kind, field, actor.display_name, len(out) + 1, member, facts=facts))
    elif kind == "FRAUD_ANALYZE":
        risk = result.get("risk")
        if not isinstance(risk, dict) or (risk.get("risk_level"), risk.get("decision")) not in {
                ("HIGH", "BLOCK"), ("LOW", "ALLOW"), ("UNKNOWN", "HOLD")}:
            _deny()
        facts = {key: deepcopy(risk.get(key)) for key in ("risk_level", "decision", "reason_codes", "matched_rules") if key in risk}
        out.append({"read_kind": kind, "domain": "FRAUD", "field": "risk_case", "value": "刚才的风险核查", "label": "刚才的风险核查",
                    "ordinal": 1, "record_id": "risk:" + canonical_hash(risk), "record_version": None,
                    "live_record_hash": canonical_hash(risk), "facts": facts})
    elif kind == "CARBON_VIEW":
        from .extensions import carbon_view
        from .extensions_models import CarbonWallet, CarbonReward, CarbonBehavior
        from .read_selection import read_selection_parameters, same_scoped_read_result
        selected = read_selection_parameters(session, principal, kind, result)
        live = carbon_view(session, principal.sandbox_id, principal.user_id, **(selected or {}))
        # Only the successful owned tool result supplies these objects. The
        # tool's human bridge message is not a point balance or reward term.
        if selected is not None:
            same_scoped_read_result(kind, result, live)
        elif canonical_hash(live) != canonical_hash({key: result.get(key) for key in live}):
            _deny()
        wallet = session.scalar(select(CarbonWallet).where(CarbonWallet.sandbox_id == principal.sandbox_id,
            CarbonWallet.user_id == principal.user_id))
        points = (live.get("wallet") or {}).get("points")
        wallet_version = (live.get("wallet") or {}).get("version")
        if type(points) is not int or points < 0 or wallet_version != (wallet.version if wallet else None):
            _deny()
        # Reward ordinals retain the actual reward card order. Wallet and
        # behavior objects do not shift that list's first/second positions.
        for index, value in enumerate(result.get("rewards", []), 1):
            row = session.get(CarbonReward, value.get("reward_id"))
            if not row or row.sandbox_id != principal.sandbox_id or not _safe_name(row.name) \
                    or any(value.get(key) != getattr(row, attr) for key, attr in
                        (("reward_name", "name"), ("cost_points", "cost_points"), ("stock", "stock"), ("version", "version"))):
                _deny()
            facts = {"reward_name": row.name, "cost_points": row.cost_points, "stock": row.stock,
                "can_redeem": value["can_redeem"], "points": points, "wallet_version": wallet_version,
                "currency": "POINTS", "unit": "POINTS"}
            from .carbon_settlement import valid_reward_settlement
            if 'settlement' in value:
                if not valid_reward_settlement(value['settlement']):_deny()
                facts['settlement']=deepcopy(value['settlement'])
            out.append(_entity(kind, "reward_name", row.name, index, row, facts=facts))
        wallet_facts = {"points": points, "wallet_version": wallet_version, "currency": "POINTS", "unit": "POINTS"}
        out.append({"read_kind": kind, "domain": "CARBON", "field": "points_wallet", "value": "低碳积分",
            "label": "我的低碳积分", "ordinal": None, "record_id": wallet.id if wallet else "unopened-points-wallet",
            "record_version": wallet_version, "live_record_hash": canonical_hash(wallet_facts), "facts": wallet_facts})
        for value in result.get("events", []):
            row = session.get(CarbonBehavior, value.get("behavior_id"))
            if not _owned(row, principal) or not _safe_name(row.name) or row.name != value.get("behavior_name"):
                _deny()
            facts = {key: deepcopy(value.get(key)) for key in ("behavior_name", "mode", "distance_m", "source", "status",
                "occurred_at", "estimated_avoided_grams", "estimated_points", "claimed", "method_supported", "claimable")}
            facts.update({"currency": "POINTS", "unit": "POINTS", "points": points, "wallet_version": wallet_version,
                "methodology_version": (live.get("methodology") or {}).get("methodology_version")})
            out.append(_entity(kind, "behavior_name", row.name, None, row, facts=facts))
    return out


def build_result_reference_catalog(session, principal, thread, *, task_id=None, before_sequence=None,
        metadata_projection_version=RUNTIME_METADATA_PROJECTION):
    """Build from one selected owned task; no automatic jump to an old request."""
    if isinstance(thread, str):
        thread = session.get(ChatThread, thread)
    if not _owned(thread, principal) or thread.status != "ACTIVE":
        _deny()
    if metadata_projection_version not in (None, RUNTIME_METADATA_PROJECTION):
        _deny()
    epoch = _actor_epoch(session, principal)
    body = {"version": VERSION, "conversation_id": thread.id, "owner_hash": canonical_hash({"user_id": principal.user_id, "sandbox_id": principal.sandbox_id}),
            "actor_epoch": epoch, "source_role": epoch["role"], "selected_task_id": task_id or thread.current_task_id,
            "before_sequence": before_sequence, "sources": [], "entities": [], "fact_refs": [],
            "authority": "VERIFIED_RESULT_OBJECTS_NOT_WRITE_AUTHORIZATION",
            "freshness": "RECORDED_QUERY_SOURCE_REVALIDATED_NOT_A_NEW_TOOL_QUERY", "bank_state_changed": False}
    if not body["selected_task_id"]:
        body["catalog_hash"] = canonical_hash(body)
        return body
    task, turn, record, snapshot, trace, verified = _source(session, principal, thread, body["selected_task_id"], before_sequence)
    for op, child, node in verified:
        if op.action_kind == "CARD_STATUS":
            from .cards import card_status_scope_matches_action
            if not card_status_scope_matches_action(child.result_json, op.args_json): _deny()
        source = {"origin_task_id": task.id, "origin_task_version": task.version, "source_turn_id": turn.id,
            "source_message_hash": canonical_hash({"message": turn.message}), "origin_snapshot_id": snapshot.id,
            "origin_snapshot_hash": canonical_hash(snapshot.proposal_json), "origin_plan_hash": snapshot.plan_hash,
            "runtime_metadata_hash": _metadata_hash(record.metadata_json, metadata_projection_version),
            **({'runtime_metadata_projection_version': metadata_projection_version} if metadata_projection_version is not None else {}),
            "operation_id": op.id,
            "operation_hash": canonical_hash({"kind": op.action_kind, "args": op.args_json, "ordinal": op.ordinal, "version": op.plan_version}),
            "child_task_id": child.id, "child_task_version": child.version, "read_result_hash": canonical_hash(child.result_json),
            "analysis_hash": canonical_hash(child.analysis_json), "read_kind": op.action_kind,
            "observed_at": child.updated_at.isoformat()}
        source["result_ref"] = "result_" + canonical_hash(source)[:24]
        body["sources"].append(source)
        object_trace = trace
        if op.action_kind == 'BILL_ANALYSIS':
            from .bill_query_context import context_version
            try:
                query_context = context_version(child.analysis_json or {})
            except (ValueError, TypeError, AttributeError):
                _deny()
            if query_context is not None:
                # A multi-card parent has one independent analysis per child.
                # The original operation/child/hash are already verified above;
                # never borrow the parent's first bill as another card's facts.
                object_trace = {**trace, 'analysis': deepcopy(child.analysis_json), 'nodes': [deepcopy(node)]}
        for item in _objects(session, principal, op.action_kind, child.result_json, object_trace):
            item["result_ref"] = source["result_ref"]
            item["entity_ref"] = "entity_" + canonical_hash({"source": source, "field": item["field"], "record_id": item["record_id"]})[:24]
            from .customer_query_layout import customer_display_name
            item["label"] = item.get("label", customer_display_name(item["value"]))
            item["fact_refs"] = []
            for key, value in item["facts"].items():
                ref = "fact_" + canonical_hash({"entity": item["entity_ref"], "field": key, "value": value})[:24]
                item["fact_refs"].append(ref)
                body["fact_refs"].append({"fact_ref": ref, "entity_ref": item["entity_ref"], "field": key, "value": deepcopy(value), "result_ref": source["result_ref"]})
            body["entities"].append(item)
    from .bill_query_context import IDENTITY_KIND as BILL_IDENTITY_KIND
    if any(entity.get('field') == 'bill_query_summary' for entity in body['entities']):
        # Optional per-row details must not fill the language budget before a
        # later card's query/identity. Preserve each actual transaction ordinal;
        # these navigation objects deliberately have ordinal=None.
        navigation = lambda entity: entity.get('field') == 'bill_query_summary' or entity.get('kind') == BILL_IDENTITY_KIND
        body['entities'] = [entity for entity in body['entities'] if navigation(entity)] + [
            entity for entity in body['entities'] if not navigation(entity)]
    if len(body["entities"]) > MAX_ENTITIES:
        _deny()  # No silent omission which changes displayed ordinals.
    body["catalog_hash"] = canonical_hash(body)
    return body


def _legacy_preference_collection_catalog(actual, catalog):
    """Reproduce only the former caption of an otherwise identical catalog.

    ``actual`` must already have passed every fresh owner/source recheck. No
    stored reference is rewritten, and no other display alias is accepted.
    """
    if catalog.get("catalog_hash") != canonical_hash({key: value for key, value in catalog.items() if key != "catalog_hash"}):
        return None
    current_entities, old_entities = actual.get("entities"), catalog.get("entities")
    if not isinstance(current_entities, list) or not isinstance(old_entities, list) or len(current_entities) != len(old_entities):
        return None
    projection = deepcopy(actual)
    changed = False
    for current, old in zip(projection["entities"], old_entities):
        if current == old:
            continue
        if (not isinstance(current, dict) or not isinstance(old, dict)
                or current.get("read_kind") != "PREFERENCE_VIEW" or current.get("domain") != "PREFERENCES"
                or current.get("field") != "preference_collection"
                or current.get("label") not in {"我当前保存的出行偏好", "我当前保存的消费偏好"}
                or old.get("label") != "我当前保存的偏好"):
            return None
        current["label"] = "我当前保存的偏好"
        if current != old:
            return None
        changed = True
    if not changed:
        return None
    projection.pop("catalog_hash", None)
    projection["catalog_hash"] = canonical_hash(projection)
    return projection if projection == catalog else None


def validate_result_reference_catalog(session, principal, thread, catalog):
    if not isinstance(catalog, dict) or catalog.get("version") != VERSION:
        _deny()
    actual = build_result_reference_catalog(session, principal, thread,
        task_id=catalog.get("selected_task_id"), before_sequence=catalog.get("before_sequence"),
        metadata_projection_version=_catalog_metadata_projection(catalog))
    if actual != catalog:
        legacy = _legacy_preference_collection_catalog(actual, catalog)
        if legacy is None:
            _deny()
        return legacy
    return actual


def public_result_reference_catalog(catalog):
    """Opaque model references, no raw source/account/card/transaction IDs."""
    if not isinstance(catalog, dict) or catalog.get("version") != VERSION:
        return None
    return {"version": VERSION, "authority": catalog["authority"], "freshness": catalog.get("freshness"),
        "entities": [{**{key: deepcopy(row.get(key)) for key in ("entity_ref", "result_ref", "domain", "read_kind", "field", "label", "ordinal", "record_version", "fact_refs")},
            "allowed_fields": entity_allowed_fields(row)}
            for row in catalog.get("entities", [])], "fact_refs": deepcopy(catalog.get("fact_refs", [])),
        "write_authorized": False}


def result_reference_explanation_sources(session, principal, thread, catalog, binding):
    """Only the selected object's typed bank facts, freshly source-checked.

    An LLM can phrase these facts, not calculate money or infer causation.
    For example a high dining total proves recorded category spending, not
    why someone spent it, and a risk result is not a safety guarantee.
    """
    validate_result_reference_catalog(session, principal, thread, catalog)
    if not isinstance(binding, dict) or binding.get("status") != "RESOLVED" \
            or (binding.get("source_binding") or {}).get("catalog_hash") != catalog["catalog_hash"]:
        _deny()
    matches = [row for row in catalog["entities"] if row["entity_ref"] == binding.get("entity_ref")]
    if len(matches) != 1 or matches[0]["fact_refs"] != binding.get("fact_refs"):
        _deny()
    entity = matches[0]
    allowed = set(entity["fact_refs"])
    facts = [{"fact_ref": row["fact_ref"], "field": row["field"], "value": deepcopy(row["value"])}
             for row in catalog["fact_refs"] if row["fact_ref"] in allowed and row["entity_ref"] == entity["entity_ref"]]
    return [{"source_id": "facts:dialogue-reference." + entity["entity_ref"], "kind": "OWNED_VERIFIED_RESULT_FACTS",
        "content": {"domain": entity["domain"], "label": entity["label"], "facts": facts,
            "record_version": entity["record_version"], "result_ref": entity["result_ref"], "bank_state_changed": False,
            "freshness": catalog.get("freshness"),
            "authority": "RECORDED_TOOL_FACTS_NOT_INFERRED_CAUSES_OR_WRITE_AUTHORIZATION"}}]


def _write_kind(message):
    from .language import polite_operational_body
    # A bounded "Can you freeze ...?" is a polite request, not a tutorial.
    # This changes classification only: the raw current text stays hashed.
    message = polite_operational_body(message) or message
    from .subscription_intent import subscription_request, COMMERCE_KINDS
    subscription=subscription_request(_intent_text(message))
    if subscription and subscription["kind"] in COMMERCE_KINDS and subscription["mode"] == "COMMERCE":
        return subscription["kind"]
    if _QUESTION.search(message) or _WHY.search(message) or re.search(r"不要|先别|别|不让|暂不|不想|\b(?:do\s+not|don't|not|never)\b", message, re.I):
        return None
    for kind, pattern in (("SUBSCRIPTION_CANCEL", r"取消|退订|关闭.{0,8}(?:续费|会员)|停止.{0,8}(?:续费|订阅)|\b(?:cancel|unsubscribe)\b"),
            ("CARD_UNLOCK", r"解锁|\bunlock\b"), ("CARD_LOCK", r"锁|冻结|\b(?:lock|freeze)\b"),
            ("CARD_REPORT_LOSS", r"挂失|\breport.{0,10}lost\b"), ("CARD_REPLACEMENT", r"补卡|换卡|\breplace.{0,10}card\b"),
            ("CARD_SET_LIMIT", r"调整.{0,8}限额|限额.{0,6}(?:改成|设为)|\b(?:set|change).{0,20}limit\b"),
            ("WEALTH_REDEEM", r"赎回|\bredeem\b"), ("WEALTH_BUY", r"买|申购|购买|\bbuy\b"),
            ("FAMILY_GRANT", r"授权|建立委托|\bgrant\b")):
        if re.search(pattern, message, re.I):
            return kind
    return None


def resolve_user_reference(session, principal, thread, message, catalog, *, domain=None, focus_entity_ref=None):
    """Resolve explicit names/ordinals or an unambiguous previously focused item.

    ``focus_entity_ref`` must be from server DialogueState (a prior resolved
    user mention), not a model's guess or an untrusted client-supplied ref.
    """
    validate_result_reference_catalog(session, principal, thread, catalog)
    if not isinstance(message, str) or not message.strip() or len(message) > 400:
        return None
    text = message.strip()
    if _intent_text(text) != text or redact_credentials(text) != text or _FOREIGN.search(text) or _INJECTION.search(text):
        return {"status": "DENIED", "reason": "UNTRUSTED_OR_FOREIGN_REFERENCE", "write_authorized": False}
    kind = _write_kind(text)
    inferred = {"SUBSCRIPTION_CANCEL": "SUBSCRIPTIONS", "SUBSCRIPTION_START": "SUBSCRIPTIONS", "SUBSCRIPTION_RENEW": "SUBSCRIPTIONS", "CARD_LOCK": "CARDS", "CARD_UNLOCK": "CARDS", "CARD_REPORT_LOSS": "CARDS", "CARD_REPLACEMENT": "CARDS", "CARD_SET_LIMIT": "CARDS",
                "WEALTH_BUY": "WEALTH", "WEALTH_REDEEM": "WEALTH", "FAMILY_GRANT": "FAMILY"}.get(kind)
    inferred = domain or inferred
    if inferred is None:
        hints = {"CARDS": r"卡|\bcards?\b", "SUBSCRIPTIONS": r"会员|订阅|续费|\b(?:subscriptions?|renewals?)\b", "WEALTH": r"理财|基金|产品|\b(?:products?|funds?)\b",
                 "BILLS": r"账单|扣款|消费|最大.{0,3}笔|这类|那类|\b(?:transactions?|bills?|spending)\b", "FAMILY": r"孩子|家人|父母|委托|授权|\bfamily\b",
                 "FRAUD": r"风险|反诈|诈骗|\b(?:fraud|scam|risk)\b"}
        found = [key for key, pattern in hints.items() if re.search(pattern, text, re.I)]
        if len(found) == 1:
            inferred = found[0]
    candidates = [row for row in catalog["entities"] if inferred is None or row["domain"] == inferred]
    literal_card=re.search(r"(?:尾号|末四位|ending\s+in)\s*[:：]?\s*([0-9a-fA-F]{4})(?![0-9a-fA-F])|(?<![A-Za-z0-9_])((?:zhenhang|xiaozhao)[0-9a-fA-F]{8})(?![0-9a-fA-F])",text,re.I)
    if literal_card:
        tail=literal_card.group(1)
        number=literal_card.group(2)
        exact=[row for row in candidates if row["domain"]=="CARDS" and (
            number and row["value"].lower()==number.lower() or tail and row["value"][-4:].lower()==tail.lower())]
        if not exact:
            return {"status":"MISSING","reference_kind":"NAMED","reason":"CURRENT_LITERAL_CARD_NOT_IN_SOURCE","write_authorized":False}
    # A whole account/summary is not an individual result object reference.
    named = [row for row in candidates if _safe_name(row["value"]) and row["field"] not in {"card_number", "transaction"}
             and (row["value"] in text or _safe_name(row["label"]) and row["label"] in text)]
    if literal_card:
        named=exact
    if not named:
        named = [row for row in candidates if row["field"] in {"card_number", "card_last4"} and re.search(r"(?:尾号|ending\s+in)\s*" + re.escape(row["value"][-4:]) + r"(?![0-9a-f])", text, re.I)]
    ordinal = _ORDINAL.search(text)
    reference = "NAMED" if named else "ORDINAL" if ordinal else "PRONOUN" if _PRONOUN.search(text) else None
    if named:
        candidates = named
    elif ordinal:
        number = ordinal.group(1) or ordinal.group(2)
        numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                   "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
        value = int(number) if number.isdigit() else numbers.get(number.lower())
        candidates = [row for row in candidates if row["ordinal"] == value]
        if re.search(r"笔|transactions?", text, re.I):
            candidates = [row for row in candidates if row["field"] == "transaction"]
        elif re.search(r"类|categor", text, re.I):
            candidates = [row for row in candidates if row["field"] == "category_filter"]
    elif re.search(r"最大.{0,3}笔|最多.{0,3}笔|\blargest\b", text, re.I):
        reference = "LARGEST_TRANSACTION"
        authoritative=[row for row in candidates if row.get("authoritative_largest") is True]
        expenses = [row for row in candidates if row["field"] == "transaction" and row["facts"].get("cash_flow_type") == "EXPENSE"]
        if authoritative:
            candidates=authoritative
        elif any(row["facts"].get("statement_transactions_truncated") for row in expenses):
            return {"status": "MISSING", "reference_kind": reference, "reason": "SOURCE_DETAILS_TRUNCATED", "write_authorized": False}
        else:
            maximum = max((abs(row["facts"]["amount_minor"]) for row in expenses), default=None)
            candidates = [row for row in expenses if abs(row["facts"]["amount_minor"]) == maximum]
    elif reference == "PRONOUN":
        if focus_entity_ref:
            candidates = [row for row in candidates if row["entity_ref"] == focus_entity_ref]
        if re.search(r"这类|那类", text):
            candidates = [row for row in candidates if row["field"] == "category_filter"]
        elif re.search(r"这张|那张|\bcard\b", text, re.I):
            candidates = [row for row in candidates if row["domain"] == "CARDS"]
    else:
        return None
    if not candidates:
        return {"status": "MISSING", "reference_kind": reference, "write_authorized": False}
    if len(candidates) != 1:
        return {"status": "AMBIGUOUS", "reference_kind": reference, "candidate_refs": [row["entity_ref"] for row in candidates],
                "candidate_labels": [row["label"] for row in candidates], "write_authorized": False}
    row = candidates[0]
    # Never borrow a name from a different kind or introduce a bank-ID slot.
    expected_fields = {"SUBSCRIPTION_CANCEL": {"merchant"}, "SUBSCRIPTION_START": {"merchant"}, "SUBSCRIPTION_RENEW": {"merchant"}, "CARD_LOCK": {"card_number", "card_last4"}, "CARD_UNLOCK": {"card_number", "card_last4"},
                       "CARD_REPORT_LOSS": {"card_number", "card_last4"}, "CARD_REPLACEMENT": {"card_number", "card_last4"}, "CARD_SET_LIMIT": {"card_number", "card_last4"},
                       "WEALTH_BUY": {"product_name"}, "WEALTH_REDEEM": {"product_name"}, "FAMILY_GRANT": {"grantee_name"}}
    if kind and row["field"] not in expected_fields[kind]:
        return {"status": "DENIED", "reason": "REFERENCE_ACTION_MISMATCH", "write_authorized": False}
    slots = {row["field"]: row["value"]} if entity_allowed_fields(row) else {}
    return {"version": VERSION, "status": "RESOLVED", "reference_kind": reference, "entity_ref": row["entity_ref"],
        "result_ref": row["result_ref"], "domain": row["domain"], "read_kind": row["read_kind"],
        "expected_action_kind": kind or row["read_kind"], "expected_slots": slots,
        "explanation_requested": bool(_WHY.search(text)) or row["field"] in {"transaction","largest_expense_group"}, "fact_refs": row["fact_refs"],
        "source_binding": {"catalog_hash": catalog["catalog_hash"], "entity_ref": row["entity_ref"],
            "result_ref": row["result_ref"], "record_version": row["record_version"], "source_role": catalog["source_role"]},
        "current_message_hash": canonical_hash({"message": text}), "write_instruction_current": kind is not None,
        "write_authorized": False, "bank_state_changed": False}
