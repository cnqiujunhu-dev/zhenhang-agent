"""Owned positions, with explicit units, missing facts and recorded-price basis.

The query never seeds data, fetches a market feed, submits an order or commits.
Wealth quantities always come from the current Holding, not a prior receipt.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from sqlalchemy import inspect, select

from .asset_models import AssetSamplePosition, AssetSnapshotPrice
from .contracts import DomainError
from .models import Holding, InvestmentProduct, utcnow
from .permissions import require_sandbox_owner
from .planning import canonical_hash
from .simulation import as_utc


ASSET_RESULT_VERSION = "asset-positions-read-v1"
ASSET_CLASSES = frozenset({"WEALTH", "STOCK", "PRECIOUS_METAL", "ALL"})
ASSET_TOOL = "asset.positions.v1"
MAX_SAFE_INTEGER = 2**53 - 1


def _scope(asset_class, product_name):
    if not isinstance(asset_class, str) or asset_class not in ASSET_CLASSES:
        raise DomainError("ASSET_CLASS_INVALID", "请选择理财、股票、贵金属或全部资产。", 422)
    if product_name is not None and (not isinstance(product_name, str) or not product_name.strip()
            or product_name != product_name.strip() or len(product_name) > 100):
        raise DomainError("ASSET_PRODUCT_INVALID", "请提供完整且唯一的资产名称。", 422)
    return {"asset_class": asset_class, "product_name": product_name}


def _number(value, label, *, nonnegative=True):
    try:
        if isinstance(value, (bool, float)):
            raise ValueError
        result = Decimal(value)
        if not result.is_finite() or nonnegative and result < 0 or result.as_tuple().exponent < -8:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise DomainError("ASSET_FACT_INVALID", f"资产{label}资料无法核实。", 409) from None


def _text_number(value):
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _money(value):
    with localcontext() as context:
        context.prec = 60
        result = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if abs(result) > MAX_SAFE_INTEGER:
        raise DomainError("ASSET_VALUE_OUT_OF_RANGE", "资产金额超出可安全展示的范围。", 409)
    return result


def _position(*, identifier, instrument, name, asset_class, unit, quantity, available, frozen,
              currency, price, price_time, price_source, cost, source, product_id=None):
    quantity = _number(quantity, "总数量")
    available = _number(available, "可用数量") if available is not None else None
    frozen = _number(frozen, "冻结数量") if frozen is not None else None
    if available is not None and available > quantity or frozen is not None and frozen > quantity \
            or available is not None and frozen is not None and available + frozen != quantity:
        raise DomainError("ASSET_QUANTITY_INCONSISTENT", "总数量、可用数量与冻结数量不一致，请先核验。", 409)
    if currency not in {"CNY", "USD"} or unit not in {"UNIT", "SHARE", "GRAM"}:
        raise DomainError("ASSET_FACT_INVALID", "资产单位或币种无法核实。", 409)
    if asset_class == "STOCK" and quantity != quantity.to_integral_value():
        raise DomainError("ASSET_FACT_INVALID", "当前股票样例仅登记整数股数。", 409)
    if cost is not None and (type(cost) is not int or not 0 <= cost <= MAX_SAFE_INTEGER):
        raise DomainError("ASSET_FACT_INVALID", "持仓成本无法核实。", 409)
    price = _number(price, "参考价格") if price is not None else None
    with localcontext() as context:
        context.prec = 60
        market = _money(quantity * price * 100) if price is not None else None
        average = (Decimal(cost) / 100 / quantity).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP) if cost is not None and quantity else None
        pnl = market - cost if market is not None and cost is not None else None
        rate = (Decimal(pnl) / cost * 100).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP) if pnl is not None and cost else None
    missing = []
    for field, value in (("available_quantity", available), ("frozen_quantity", frozen),
                         ("remaining_cost_minor", cost), ("reference_price", price), ("price_effective_at", price_time)):
        if value is None:
            missing.append(field)
    return {
        "position_id": identifier, "instrument_id": instrument, "product_id": product_id,
        "product_name": name, "asset_class": asset_class, "quantity_unit": unit,
        "total_quantity": _text_number(quantity), "available_quantity": _text_number(available),
        "frozen_quantity": _text_number(frozen),
        "availability_status": "RECORDED" if available is not None and frozen is not None else "NOT_TRACKED",
        "currency": currency, "reference_price": _text_number(price), "price_unit": f"{currency}/{unit}",
        "price_observed_at": price_time, "price_effective_at": price_time, "price_source": price_source,
        "price_is_live": False, "remaining_cost_minor": cost, "market_value_minor": market,
        "average_cost": _text_number(average), "unrealized_pnl_minor": pnl, "return_pct": _text_number(rate),
        "realized_pnl_minor": None, "cost_basis": "SYNTHETIC_REMAINING_POSITION_COST" if cost is not None else "NOT_RECORDED",
        "valuation_basis": "RECORDED_REFERENCE_PRICE_NOT_EXECUTABLE_QUOTE" if price is not None else "PRICE_UNAVAILABLE",
        "missing_fields": [*missing, "realized_pnl_minor"], "source": source,
    }


def _matches(row, reference):
    from .wealth_identity import product_reference_matches
    if row["asset_class"] == "WEALTH":
        return product_reference_matches({"product_id": row["instrument_id"], "name": row["product_name"]}, reference)
    aliases = {row["instrument_id"], row["product_name"], row["product_name"].removesuffix("（只读样例）")}
    return reference.casefold() in {alias.casefold() for alias in aliases}


def _summary(rows):
    values = []
    for currency in sorted({row["currency"] for row in rows}):
        selected = [row for row in rows if row["currency"] == currency]
        priced = [row for row in selected if row["market_value_minor"] is not None]
        costed = [row for row in selected if row["remaining_cost_minor"] is not None]
        complete_value = len(priced) == len(selected)
        complete_cost = len(costed) == len(selected)
        values.append({"currency": currency, "market_value_minor": _money(Decimal(sum(row["market_value_minor"] for row in priced))) if complete_value else None,
            "valuation_status": "COMPLETE_RECORDED_PRICES" if complete_value else "PARTIAL_PRICE_COVERAGE",
            "priced_position_count": len(priced), "cost_position_count": len(costed),
            "remaining_cost_minor": _money(Decimal(sum(row["remaining_cost_minor"] for row in costed))) if complete_cost else None,
            "unrealized_pnl_minor": _money(Decimal(sum(row["unrealized_pnl_minor"] for row in selected))) if complete_cost and complete_value else None})
    return {"position_count": len(rows), "values_by_currency": values,
        "quantity_totals_combined": False, "cross_currency_total_provided": False}


def _stable_result(result):
    stable = deepcopy(result)
    stable.pop("observed_at", None)
    stable.pop("source_fingerprint", None)
    return stable


def asset_positions_view(session, principal, *, asset_class="ALL", product_name=None):
    require_sandbox_owner(session, principal)
    scope = _scope(asset_class, product_name)
    rows, candidates = [], []
    if asset_class in {"ALL", "WEALTH"}:
        holdings = {row.product_id: row for row in session.scalars(select(Holding).where(
            Holding.sandbox_id == principal.sandbox_id, Holding.user_id == principal.user_id))}
        for product in session.scalars(select(InvestmentProduct).where(InvestmentProduct.sandbox_id == principal.sandbox_id).order_by(InvestmentProduct.id)):
            candidates.append({"instrument_id": product.id, "product_name": product.name, "asset_class": "WEALTH"})
            holding = holdings.get(product.id)
            if holding is None or holding.units == 0:
                continue
            rows.append(_position(identifier=holding.id, instrument=product.id, product_id=product.id, name=product.name,
                asset_class="WEALTH", unit="UNIT", quantity=holding.units, available=None, frozen=None, currency="CNY",
                price=Decimal(product.price_minor) / 100, price_time=None, price_source="SIMULATION_PRODUCT_RECORD_NO_MARKET_TIMESTAMP",
                cost=None, source={"kind": "OWNED_HOLDING", "record_id": holding.id, "version": holding.version,
                    "product_version": product.version, "synthetic": True}))
    if asset_class != "WEALTH":
        if not {"asset_sample_positions", "asset_snapshot_prices"} <= set(inspect(session.connection()).get_table_names()):
            raise DomainError("ASSET_SCHEMA_UNAVAILABLE", "样例资产资料尚未完成初始化，请稍后查询。", 503)
        statement = select(AssetSamplePosition).where(AssetSamplePosition.sandbox_id == principal.sandbox_id,
            AssetSamplePosition.user_id == principal.user_id)
        if asset_class != "ALL":
            statement = statement.where(AssetSamplePosition.asset_class == asset_class)
        for position in session.scalars(statement.order_by(AssetSamplePosition.instrument_id)):
            candidates.append({"instrument_id": position.instrument_id, "product_name": position.product_name, "asset_class": position.asset_class})
            if position.total_quantity == 0:
                continue
            snapshot = session.get(AssetSnapshotPrice, position.snapshot_id) if position.snapshot_id else None
            if snapshot and (snapshot.instrument_id != position.instrument_id or snapshot.quantity_unit != position.quantity_unit
                    or snapshot.currency != position.currency or snapshot.source != "SYNTHETIC_SNAPSHOT_NOT_MARKET_DATA"):
                raise DomainError("ASSET_PRICE_SCOPE_MISMATCH", "资产快照价格的对象、单位或币种不一致。", 409)
            rows.append(_position(identifier=position.id, instrument=position.instrument_id, name=position.product_name,
                asset_class=position.asset_class, unit=position.quantity_unit, quantity=position.total_quantity,
                available=position.available_quantity, frozen=position.frozen_quantity, currency=position.currency,
                price=snapshot.price if snapshot else None, price_time=as_utc(snapshot.observed_at).isoformat() if snapshot else None,
                price_source=snapshot.source if snapshot else "SNAPSHOT_UNAVAILABLE", cost=position.remaining_cost_minor,
                source={"kind": "SYNTHETIC_SAMPLE_POSITION", "record_id": position.id, "version": position.version,
                    "price_record_id": snapshot.id if snapshot else None, "price_version": snapshot.version if snapshot else None, "synthetic": True}))
    if product_name is not None:
        targets = [row for row in candidates if _matches(row, product_name)]
        if len(targets) != 1:
            raise DomainError("ASSET_PRODUCT_AMBIGUOUS" if targets else "ASSET_PRODUCT_NOT_FOUND",
                "请明确唯一资产名称或登记代码；未找到唯一的本人资产对象。", 422)
        rows = [row for row in rows if row["instrument_id"] == targets[0]["instrument_id"]]
    rows.sort(key=lambda row: (row["asset_class"], row["instrument_id"]))
    result = {"schema_version": ASSET_RESULT_VERSION, "query_scope": scope,
        "owner_scope_hash": canonical_hash({"sandbox_id": principal.sandbox_id, "user_id": principal.user_id}),
        "positions": rows, "summary": _summary(rows), "read_only": True, "bank_write_authorized": False,
        "status": "AVAILABLE" if rows else "EMPTY", "observed_at": utcnow().isoformat(), "simulated": True,
        "message": "已读取本人持仓；股票和贵金属是只读样例，价格为记录快照，不是外部实时行情。"}
    result["source_fingerprint"] = canonical_hash(_stable_result(result))
    return result


def asset_read_result_current(session, principal, result):
    """Rebuild the exact owner/filter/set and reject changed or forged facts."""
    if not isinstance(result, dict) or result.get("schema_version") != ASSET_RESULT_VERSION:
        raise DomainError("ASSET_READ_SOURCE_CHANGED", "原持仓查询来源无法核实，请重新查询。", 409)
    scope = result.get("query_scope")
    if not isinstance(scope, dict) or set(scope) != {"asset_class", "product_name"}:
        raise DomainError("ASSET_READ_SOURCE_CHANGED", "原持仓查询范围无法核实，请重新查询。", 409)
    current = asset_positions_view(session, principal, **scope)
    if result.get("source_fingerprint") != canonical_hash(_stable_result(result)) \
            or result["source_fingerprint"] != current["source_fingerprint"]:
        raise DomainError("ASSET_READ_SOURCE_CHANGED", "持仓数量、价格或查询范围已变化，请重新查询。", 409)
    return current


def prepare_asset_positions_task(session, *, task, conversation_id, requested_action):
    from .audit import append_audit
    from .contracts import FinancialIntentContract
    from .intelligence import ProposedAction
    from .models import EvidenceSnapshot
    from .platform import add_task_nodes, append_message
    from .services import Principal
    from .workflow import transition_task

    action = ProposedAction.model_validate(requested_action) if isinstance(requested_action, dict) else requested_action
    if action is None or action.kind != "ASSET_POSITIONS_READ" or set(action.slots.model_dump(exclude_none=True)) - {"asset_class", "product_name"}:
        raise DomainError("ASSET_ACTION_SCOPE_MISMATCH", "资产查询动作或参数范围不一致。", 409)
    if action.slots.asset_class is None:
        raise DomainError("ASSET_CLASS_REQUIRED", "资产查询需要明确理财、股票、贵金属或全部资产的范围。", 422)
    try:
        result = asset_positions_view(session, Principal(task.user_id, task.sandbox_id),
            asset_class=action.slots.asset_class, product_name=action.slots.product_name)
    except DomainError as error:
        if error.status_code != 422:
            raise
        task.result_json = {"message": error.message, "error": error.code}
        return task
    evidence = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="OWNED_ASSET_POSITION_SNAPSHOT",
        source_version=ASSET_RESULT_VERSION, payload_json=result)
    session.add(evidence)
    session.flush()
    task.plan_json = {"nodes": [{"node_id": "read-asset-positions", "tool_name": ASSET_TOOL, "read_only": True,
        "status": "SUCCEEDED", "evidence_id": evidence.id, "args": result["query_scope"]}]}
    task.plan_hash = canonical_hash(task.plan_json)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.requested_capabilities, fic.status = [ASSET_TOOL], "READY"
    task.fic_json = fic.model_dump()
    task.result_json = result
    transition_task(task, "SUCCEEDED")
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="ASSET_POSITIONS_READ",
        payload={"scope": result["query_scope"], "position_count": len(result["positions"]), "source_fingerprint": result["source_fingerprint"]})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
        task_id=task.id, role="ASSISTANT", content=result["message"])
    return task
