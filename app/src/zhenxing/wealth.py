from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .contracts import DomainError, FinancialIntentContract
from .models import (
    Account, BudgetEnvelope, Confirmation, EvidenceSnapshot, Holding,
    InvestmentProduct, RiskProfile, Task, User, utcnow,
)
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .workflow import transition_task
from .simulation import as_utc
from .operational_clock import posting_now, registered_customer
from .simulation_models import SimulationState
from .wealth_models import RiskAssessmentRecord


QUESTIONNAIRE_VERSION = "risk-assessment-v3"
PRODUCT_TERMS_VERSION = "simulation-terms-v1"
_QUESTIONS = [
    {"question_id": "investment_horizon", "title": "这笔拟投资资金预计多久内需要使用？",
     "labels": ["3个月内", "3至12个月", "1至3年", "3年以上"]},
    {"question_id": "liquidity", "title": "扣除应急金后，您对这笔资金的流动性要求是？",
     "labels": ["随时要用于基本生活", "短期可能需要大部分", "可以保留部分长期投入", "可长期投入且有独立应急资金"]},
    {"question_id": "experience", "title": "您本人有过哪些投资经验？",
     "labels": ["没有投资经验", "主要为存款和现金类", "有债券或低中风险产品经验", "有股票或高波动产品经验"]},
    {"question_id": "loss_tolerance", "title": "若本金短期下降，您能接受的最大波动范围是？",
     "labels": ["不能接受本金下降", "约5%以内", "约15%以内", "超过15%也能承担并理解风险"]},
    {"question_id": "income_stability", "title": "您当前可用于投资的收入来源情况是？",
     "labels": ["暂无稳定收入", "收入波动且备用资金较少", "收入相对稳定且有备用资金", "收入来源分散且有充足备用资金"]},
    {"question_id": "asset_buffer", "title": "扣除负债和拟投资金额后，可随时使用的应急资产能覆盖多久基本生活？",
     "labels": ["不足1个月，或投资需要借款及动用基本生活资金", "1至3个月", "3至6个月", "6个月以上，且不影响基本生活"]},
    {"question_id": "liability_burden", "title": "您当前全部负债与全部资产的比例大约是？",
     "labels": ["负债超过资产，或暂时无法确认", "负债超过资产的一半但未超过全部资产", "有负债，但不超过资产的一半", "没有负债"]},
    {"question_id": "repayment_pressure", "title": "每月固定还款约占稳定可支配收入的多少？",
     "labels": ["暂无稳定收入但需要还款，或已有逾期", "超过一半", "超过五分之一但不超过一半", "不超过五分之一，或没有还款义务"]},
]

_BANDS = [(0, 1), (6, 2), (11, 3), (16, 4), (21, 5)]
_REASON_EXPLANATIONS = {
    "DETERMINISTIC_SUM_BANDS": "按八题固定分档计算，再独立施加保守上限；资产多不等同于偏好高风险。",
    "CAPITAL_LIQUIDITY_OR_LOSS_CAP_1": "基本生活资金、期限或损失承受能力不足，限制为R1。",
    "SHORT_HORIZON_OR_LOW_LOSS_CAP_2": "期限较短或损失承受能力较低，最高R2。",
    "MODERATE_LOSS_CAP_3": "损失承受范围有限，最高R3。",
    "NO_EXPERIENCE_CAP_2": "没有投资经验，最高R2。",
    "UNSTABLE_INCOME_CAP_2": "暂无稳定收入，最高R2。",
    "LIQUIDITY_HORIZON_CONFLICT_CAP_2": "长期投资期限与近期资金需要不一致，保守限制至R2。",
    "ASSET_BUFFER_CAP_1": "应急资产不足或投资需借款及动用生活资金，限制为R1。",
    "LIMITED_ASSET_BUFFER_CAP_2": "应急资产缓冲较少，最高R2。",
    "HIGH_LIABILITY_CAP_2": "负债占比较高或尚不明确，最高R2。",
    "MODERATE_LIABILITY_CAP_3": "仍有负债，最高R3。",
    "REPAYMENT_PRESSURE_CAP_1": "存在无稳定收入还款或逾期压力，限制为R1。",
    "HIGH_REPAYMENT_PRESSURE_CAP_2": "固定还款超过稳定可支配收入的一半，最高R2。",
    "MODERATE_REPAYMENT_PRESSURE_CAP_3": "固定还款占收入比例仍较高，最高R3。",
    "LIABILITY_REPAYMENT_CONFLICT_CAP_2": "没有负债与仍有较高固定还款的答案不兼容，保守限制至R2，请按实际情况核对。",
    "ASSET_LIQUIDITY_CONFLICT_CAP_1": "应急资产不足与长期独立资金的答案不一致，保守限制为R1。",
    "QUESTIONNAIRE_REASSESSMENT_REQUIRED": "旧版问卷未覆盖当前资产、负债及偿债压力，需要本人重新测评；旧记录和到期日保留。",
    "INDEPENDENT_ASSESSMENT_MISSING": "当前只有初始风险档案，尚无本人独立提交的新版测评。",
}


def _risk_clock(session, sandbox_id, user_id):
    return as_utc(posting_now(session, sandbox_id, user_id))


def _risk_time_basis(session, sandbox_id, user_id):
    return "CURRENT_UTC" if registered_customer(session, sandbox_id, user_id) else "SIMULATION_BUSINESS_TIME"


def simulation_product_terms() -> dict:
    return {"terms_version": PRODUCT_TERMS_VERSION, "fee_minor": 0,
            "redemption_timing": "记账成功即到账", "term": "开放式，无锁定期",
            "minimum_units": 1, "capital_guaranteed": False, "yield_prediction_provided": False}


def risk_assessment_questions(session=None, sandbox_id=None, user_id=None) -> dict:
    return {
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "provider": "圳行Agent", "simulated": True,
        "independent_submission_required": True,
        "questions": [{"question_id": question["question_id"], "title": question["title"],
                       "options": [{"value": index, "label": label} for index, label in enumerate(question["labels"])]}
                      for question in _QUESTIONS],
        "scoring": {"max_score": 24, "risk_levels": [1, 2, 3, 4, 5],
                    "bands": [{"minimum_score": score, "risk_level": level}
                              for score, level in _BANDS],
                    "method": "八题固定选项求和分档，再按损失、基本生活资金、经验、收入、资产缓冲、负债及偿债压力施加保守上限；不兼容答案保守限制。"},
        "validity_days": 365, "clock": _risk_time_basis(session, sandbox_id, user_id) if session is not None else "ACTOR_SPECIFIC",
        "explanation": "请由您本人选择答案并确认提交。结果用于当前产品适配；Agent不能代填或通过对话修改等级。",
    }


def score_risk_answers(answers: dict) -> tuple[int, int, list[str]]:
    expected = {question["question_id"] for question in _QUESTIONS}
    if not isinstance(answers, dict) or set(answers) != expected or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3
                                     for value in answers.values()):
        raise DomainError("INVALID_RISK_ANSWERS", "请独立填写全部8题，每题选择0至3中的一个选项", 422)
    score = sum(answers.values())
    level = max(level for minimum, level in _BANDS if score >= minimum)
    reasons = ["DETERMINISTIC_SUM_BANDS"]
    if any(answers[key] == 0 for key in ("investment_horizon", "liquidity", "loss_tolerance")):
        level = 1
        reasons.append("CAPITAL_LIQUIDITY_OR_LOSS_CAP_1")
    elif answers["loss_tolerance"] == 1 or answers["investment_horizon"] == 1:
        level = min(level, 2)
        reasons.append("SHORT_HORIZON_OR_LOW_LOSS_CAP_2")
    elif answers["loss_tolerance"] == 2:
        level = min(level, 3)
        reasons.append("MODERATE_LOSS_CAP_3")
    if answers['experience'] == 0:
        level = min(level, 2)
        reasons.append('NO_EXPERIENCE_CAP_2')
    if answers['income_stability'] == 0:
        level = min(level, 2)
        reasons.append('UNSTABLE_INCOME_CAP_2')
    if answers['liquidity'] <= 1 and answers['investment_horizon'] >= 2:
        level = min(level, 2)
        reasons.append('LIQUIDITY_HORIZON_CONFLICT_CAP_2')
    for field, caps in (
        ("asset_buffer", {0: (1, "ASSET_BUFFER_CAP_1"), 1: (2, "LIMITED_ASSET_BUFFER_CAP_2")}),
        ("liability_burden", {0: (2, "HIGH_LIABILITY_CAP_2"), 1: (2, "HIGH_LIABILITY_CAP_2"), 2: (3, "MODERATE_LIABILITY_CAP_3")}),
        ("repayment_pressure", {0: (1, "REPAYMENT_PRESSURE_CAP_1"), 1: (2, "HIGH_REPAYMENT_PRESSURE_CAP_2"), 2: (3, "MODERATE_REPAYMENT_PRESSURE_CAP_3")}),
    ):
        if answers[field] in caps:
            cap, reason = caps[answers[field]]
            level = min(level, cap)
            reasons.append(reason)
    if answers["liability_burden"] == 3 and answers["repayment_pressure"] < 3:
        level = min(level, 2)
        reasons.append("LIABILITY_REPAYMENT_CONFLICT_CAP_2")
    if answers["asset_buffer"] == 0 and answers["liquidity"] == 3:
        level = 1
        reasons.append("ASSET_LIQUIDITY_CONFLICT_CAP_1")
    return score, level, reasons


def _owned_user(session: Session, sandbox_id: str, user_id: str, *, lock: bool = False) -> User:
    query = select(User).where(User.id == user_id, User.sandbox_id == sandbox_id)
    if lock:
        query = query.with_for_update()
    user = session.scalar(query)
    if user is None:
        raise DomainError("USER_NOT_FOUND", "当前用户不存在", 404)
    return user


def risk_profile_view(session: Session, sandbox_id: str, user_id: str) -> dict:
    _owned_user(session, sandbox_id, user_id)
    clock = _risk_clock(session, sandbox_id, user_id)
    profile = session.scalar(select(RiskProfile).where(RiskProfile.sandbox_id == sandbox_id, RiskProfile.user_id == user_id))
    latest = session.scalar(select(RiskAssessmentRecord).where(
        RiskAssessmentRecord.sandbox_id == sandbox_id, RiskAssessmentRecord.user_id == user_id,
    ).order_by(RiskAssessmentRecord.security_received_at.desc(), RiskAssessmentRecord.id.desc()))
    valid = bool(profile and as_utc(profile.expires_at) > clock)
    from .access_models import LoginAccount, ScenarioEditEvent
    override = session.scalar(select(ScenarioEditEvent).join(LoginAccount,
        LoginAccount.id == ScenarioEditEvent.actor_account_id).where(
        ScenarioEditEvent.sandbox_id == sandbox_id, ScenarioEditEvent.user_id == user_id,
        ScenarioEditEvent.entity == "risk_profile",
        ScenarioEditEvent.row_id == (profile.id if profile else None),
        LoginAccount.role == "DEV_TEST", LoginAccount.sandbox_id == sandbox_id, LoginAccount.user_id == user_id,
    ).order_by(ScenarioEditEvent.created_at.desc(), ScenarioEditEvent.id.desc()).limit(1))
    overridden = bool(override and (latest is None or as_utc(override.created_at) >= as_utc(latest.security_received_at)))
    assessment_reasons = []
    reassessment_required = False
    if profile is not None and latest is None and not overridden and registered_customer(session, sandbox_id, user_id):
        valid = False
        reassessment_required = True
        assessment_reasons.append("INDEPENDENT_ASSESSMENT_MISSING")
    if latest and not overridden:
        if latest.questionnaire_version != QUESTIONNAIRE_VERSION:
            valid = False
            reassessment_required = True
            assessment_reasons.append('QUESTIONNAIRE_REASSESSMENT_REQUIRED')
        else:
            try:
                recorded_score, recorded_level, caps = score_risk_answers(latest.answers_json)
                assessment_reasons.extend(caps)
                if not profile or (latest.risk_profile_id, latest.score, latest.risk_level) != (profile.id, recorded_score, recorded_level) \
                        or profile.risk_level != recorded_level or as_utc(profile.expires_at) != as_utc(latest.expires_at) \
                        or latest.submission_source != "INDEPENDENT_USER_FORM" \
                        or as_utc(latest.expires_at) != as_utc(latest.business_assessed_at) + timedelta(days=365):
                    valid = False
                    reassessment_required = True
                    assessment_reasons.append('ASSESSMENT_PROFILE_INCONSISTENT')
            except DomainError:
                valid = False
                reassessment_required = True
                assessment_reasons.append('ASSESSMENT_RECORD_INVALID')
    return {
        "simulated": True, "provider": "圳行Agent", "business_time": clock.isoformat(),
        "clock": _risk_time_basis(session, sandbox_id, user_id), "profile_id": profile.id if profile else None,
        "risk_level": profile.risk_level if profile else None,
        "expires_at": as_utc(profile.expires_at).isoformat() if profile else None,
        "valid": valid, "status": "VALID" if valid else ('REASSESSMENT_REQUIRED' if reassessment_required else "EXPIRED" if profile else "MISSING"),
        "assessment_id": latest.id if latest else None,
        "assessment_source": "DEV_TEST_OVERRIDE" if overridden else ("INDEPENDENT_USER_FORM" if latest else ("SYNTHETIC_SEED" if profile else "NONE")),
        "test_override_event_id": override.id if overridden else None,
        "questionnaire_version": latest.questionnaire_version if latest else None,
        "independent_assessment_url": "/api/v2/wealth/risk-assessment",
        "reason_codes": assessment_reasons,
        "reason_explanations": [_REASON_EXPLANATIONS.get(code, "测评原记录与当前档案未核对一致，请本人重新测评。") for code in assessment_reasons],
        "assessment_policy_version": QUESTIONNAIRE_VERSION,
        "scope": "INVESTMENT_SUITABILITY_NOT_CREDIT_APPROVAL_OR_FRAUD_CLEARANCE",
    }


def submit_risk_assessment(
    session: Session, sandbox_id: str, user_id: str,
    *, answers: dict, questionnaire_version: str, independent_confirmation: bool,
) -> dict:
    if questionnaire_version != QUESTIONNAIRE_VERSION:
        raise DomainError("RISK_QUESTIONNAIRE_CHANGED", "测评版本已变化，请重新打开独立测评表单", 409)
    if independent_confirmation is not True:
        raise DomainError("INDEPENDENT_ASSESSMENT_REQUIRED", "请确认答案由您本人独立选择后提交", 422)
    score, level, reasons = score_risk_answers(answers)
    _owned_user(session, sandbox_id, user_id, lock=True)
    clock = _risk_clock(session, sandbox_id, user_id)
    expires = clock + timedelta(days=365)
    profile = session.scalar(select(RiskProfile).where(
        RiskProfile.sandbox_id == sandbox_id, RiskProfile.user_id == user_id,
    ).with_for_update())
    if profile is None:
        profile = RiskProfile(sandbox_id=sandbox_id, user_id=user_id, risk_level=level, expires_at=expires)
        session.add(profile)
        session.flush()
    else:
        profile.risk_level = level
        profile.expires_at = expires
    record = RiskAssessmentRecord(
        sandbox_id=sandbox_id, user_id=user_id, risk_profile_id=profile.id,
        questionnaire_version=QUESTIONNAIRE_VERSION, answers_json=dict(answers), score=score, risk_level=level,
        submission_source="INDEPENDENT_USER_FORM", business_assessed_at=clock, expires_at=expires,
    )
    session.add(record)
    session.flush()
    return {**risk_profile_view(session, sandbox_id, user_id), "assessment_id": record.id,
            "score": score, "reason_codes": reasons,
            "message": "独立测评已保存，后续交易仍需重新核对产品、测评有效期及应急金。"}


def list_wealth_products(session: Session, sandbox_id: str, user_id: str, *, product_name=None,
                        _suitability_snapshot=False) -> dict:
    profile = risk_profile_view(session, sandbox_id, user_id)
    if _suitability_snapshot:
        from .wealth_suitability_facts import KEY, make_snapshot
        profile = {**profile, KEY: make_snapshot(profile, sandbox_id, user_id)}
    holdings = {item.product_id: item.units for item in session.scalars(select(Holding).where(
        Holding.sandbox_id == sandbox_id, Holding.user_id == user_id,
    ))}
    state = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == sandbox_id))
    products = list(session.scalars(select(InvestmentProduct).where(
        InvestmentProduct.sandbox_id == sandbox_id,
    ).order_by(InvestmentProduct.risk_level, InvestmentProduct.name)))
    rows = []
    for product in products:
        reasons = []
        if not profile["valid"]:
            from .wealth_risk_state import suitability_rejection_reason
            reasons.append(suitability_rejection_reason(profile))
        elif product.risk_level > profile["risk_level"]:
            reasons.append("PRODUCT_RISK_EXCEEDS_PROFILE")
        if product.status != "AVAILABLE" or product.price_minor <= 0:
            reasons.append("PRODUCT_UNAVAILABLE")
        rows.append({
            "product_id": product.id, "name": product.name, "risk_level": product.risk_level,
            "unit_price_minor": product.price_minor, "currency": "CNY", "version": product.version,
            "status": product.status, "holding_units": holdings.get(product.id, 0),
            "suitable": not reasons, "suitability_reason_codes": reasons,
            "suitability_explanation": "当前等级与有效期允许进入交易核验；金额、持仓、价格及应急金仍须在提交前复查。" if not reasons
                                       else "当前测评或产品状态不符合交易准备条件，请查看原因并独立完成必要测评。",
            "terms": simulation_product_terms(),
            "sources": [
                {"kind": "SIMULATION_PRODUCT_RECORD", "record_id": product.id, "version": product.version,
                 "seed_version": state.seed_version if state else None,
                 "fields": ["name", "risk_level", "unit_price_minor", "status"]},
                {"kind": "SIMULATION_FIXED_TERMS", "version": PRODUCT_TERMS_VERSION,
                 "fields": ["fee_minor", "redemption_timing", "term", "capital_guaranteed"]},
            ],
        })
    result = {"products": rows, "risk_profile": profile, "simulated": True,
            "provider": "圳行Agent", "terms_version": PRODUCT_TERMS_VERSION,
            "business_time": profile["business_time"],
            "explanation": "价格、风险和版本来自当前产品记录；条款适用范围以本平台记录为准，未提供收益预测或本金保证。"}
    if product_name is None:
        return result
    _owned_user(session, sandbox_id, user_id)
    if session.scalar(select(Account.id).where(Account.sandbox_id == sandbox_id, Account.user_id == user_id)) is None:
        raise DomainError("WEALTH_READ_OWNER_REQUIRED", "只能查询本人的产品适配与持仓。", 403)
    from .read_selection import validate_selectors, make_read_selection
    selectors = validate_selectors("WEALTH_COMPARE", {"product_name": product_name})
    from .wealth_identity import matching_owned_products
    selected = matching_owned_products(rows, product_name)
    if len(selected) != 1:
        raise DomainError("WEALTH_READ_TARGET_REQUIRED", "没有找到唯一的当前产品，请核对名称。", 422)
    row = selected[0]
    result.update(products=selected, risk_profile_level=profile.get("risk_level"))
    records = [{"field": "product_name", "record_id": row["product_id"], "record_version": row["version"],
                "record_hash": canonical_hash(row)}]
    selection = make_read_selection(session, sandbox_id, user_id, "WEALTH_COMPARE", selectors, records, result)
    return {**result, "read_selection": selection}


def _amount_minor(message: str) -> int | None:
    match = re.search(r"(?<![\d.])(\d+(?:\.\d{1,2})?)(?![\d.])\s*(万|元)", message)
    if match is None:
        return None
    try:
        value = Decimal(match.group(1)) * (10_000 if match.group(2) == "万" else 1)
        return int(value * 100)
    except (InvalidOperation, ValueError):
        return None


def prepare_wealth_task(session: Session, *, task: Task, conversation_id: str, requested_product_name=None, requested_action=None) -> Task:
    # A position query is a fresh READ, never a continuation of an old BUY.
    kind = requested_action.get("kind") if isinstance(requested_action, dict) else getattr(requested_action, "kind", None)
    if kind == "ASSET_POSITIONS_READ":
        from .asset_positions import prepare_asset_positions_task
        return prepare_asset_positions_task(session, task=task, conversation_id=conversation_id, requested_action=requested_action)
    products = list(session.scalars(select(InvestmentProduct).where(
        InvestmentProduct.sandbox_id == task.sandbox_id,
    )))
    profile = session.scalar(select(RiskProfile).where(
        RiskProfile.sandbox_id == task.sandbox_id,
        RiskProfile.user_id == task.user_id,
    ))
    account = session.scalar(select(Account).where(
        Account.sandbox_id == task.sandbox_id,
        Account.user_id == task.user_id,
    ))
    if account is None:
        task.result_json = {"message": "未找到当前资金账户。"}
        transition_task(task, "FAILED")
        return task
    holdings = {item.product_id: item for item in session.scalars(select(Holding).where(
        Holding.sandbox_id == task.sandbox_id,
        Holding.user_id == task.user_id,
    ))}
    fic = FinancialIntentContract.model_validate(task.fic_json)
    if requested_action is not None:
        from .intelligence import ProposedAction
        requested_action=ProposedAction.model_validate(requested_action) if isinstance(requested_action,dict) else requested_action
        if requested_action.kind not in {"WEALTH_COMPARE","WEALTH_BUY","WEALTH_REDEEM"} \
                or requested_product_name is not None and requested_product_name!=requested_action.slots.product_name:
            raise DomainError("WEALTH_ACTION_SCOPE_MISMATCH","理财操作范围不一致，请重新核对。",409)
        requested_product_name=requested_action.slots.product_name
        is_redeem=requested_action.kind=="WEALTH_REDEEM"
        is_buy=requested_action.kind=="WEALTH_BUY"
    else:
        is_redeem = "赎回" in task.message or "卖出" in task.message
        is_buy = any(term in task.message for term in ("申购", "买入", "购买", "买成")) or bool(
            re.search(r"买.{0,12}(理财|产品)", task.message)
        )
    if not is_buy and not is_redeem:
        try:
            catalog = list_wealth_products(session, task.sandbox_id, task.user_id, product_name=requested_product_name,
                _suitability_snapshot=True)
        except DomainError as error:
            task.result_json = {"message": error.message, "error": error.code}
            if error.status_code != 422:
                transition_task(task, "FAILED")
            return task
        rows = catalog["products"]
        from .wealth_suitability_facts import VERSION as SUITABILITY_VERSION
        snapshot = EvidenceSnapshot(
            task_id=task.id, sandbox_id=task.sandbox_id,
            source_kind="SYNTHETIC_INVESTMENT_CATALOG",
            source_version=catalog["read_selection"]["schema_version"] if requested_product_name is not None else SUITABILITY_VERSION,
            payload_json=catalog if requested_product_name is not None else {"products": rows, "risk_profile": catalog["risk_profile"]},
        )
        session.add(snapshot)
        session.flush()
        task.plan_json = {"nodes": [{
            "node_id": "compare-products", "tool_name": "wealth.compare.v1",
            "status": "SUCCEEDED", "evidence_id": snapshot.id,
            **({"args": {"product_name": requested_product_name}} if requested_product_name is not None else {}),
            **({"read_selection_version": catalog["read_selection"]["schema_version"],
                "read_selection_hash": catalog["read_selection"]["scope_hash"]} if requested_product_name is not None else {}),
        }]}
        if requested_product_name is not None:
            task.plan_hash = canonical_hash(task.plan_json)
        add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
        fic.requested_capabilities = ["wealth.compare.v1"]
        fic.status = "READY"
        task.fic_json = fic.model_dump()
        transition_task(task, "SUCCEEDED")
        task.result_json = {**(catalog if requested_product_name is not None else {}),
            "message": "已列出理财产品与当前风险适配结果。价格以当前产品记录为准，不代表收益预测或保本承诺。",
            "products": rows,
            "risk_profile": catalog["risk_profile"],
            "risk_profile_level": catalog["risk_profile"]["risk_level"],
            "simulated": True,
        }
        append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                     event_type="WEALTH_PRODUCTS_COMPARED", payload={"count": len(rows)})
        append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                       task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
        return task
    if is_buy and is_redeem:
        task.result_json = {"message": "请一次只指定申购或赎回一种操作。"}
        return task
    if requested_action is not None or requested_product_name is not None:
        from .wealth_identity import product_reference_matches
        matches = [product for product in products if product_reference_matches(
            {"product_id": product.id, "name": product.name}, requested_product_name)]
    else:
        # Unversioned legacy callers keep their original adapter. Current
        # typed READ/BUY/REDEEM references all use the same exact identity map.
        matches = [product for product in products if product.name.split("（")[0] in task.message]
    if requested_action is not None:
        # Use the source-validated typed quantity, not a display string where
        # an ASCII product key can run into its following numeral.
        requested_units=requested_action.slots.units
        amount_minor=requested_action.slots.amount_minor
    else:
        from .intelligence import wealth_units_in_text
        unit_evidence = wealth_units_in_text(task.message)
        requested_units = next(iter(unit_evidence)) if len(unit_evidence) == 1 else None
        amount_minor = _amount_minor(task.message)
    from .wealth_quantity_polarity import mask_denied_wealth_units
    positive_quantity_source=mask_denied_wealth_units(task.message)
    if requested_action is None and re.search(r"[\d零〇一二两三四五六七八九十百千万.]+\s*份", positive_quantity_source) and requested_units is None:
        task.result_json = {"message": "当前产品只支持准确的正整数份数，请明确一个整份数量。", "error": "WEALTH_UNITS_REQUIRED"}
        return task
    if re.search(r"美元|美金|\bUSD\b|\bEUR\b|\bHKD\b|欧元|港元|港币|[$€£]", task.message, re.I):
        task.result_json = {"message": "当前理财交易以人民币计价，暂不支持外币金额；不会自动换汇后交易。", "error": "WEALTH_CURRENCY_UNSUPPORTED"}
        return task
    if len(matches) != 1 or (amount_minor is None and requested_units is None):
        task.result_json = {"message": "请明确产品名称和金额或整份数量，例如：赎回稳健现金管理1份。"}
        return task
    product = matches[0]
    order_type = "REDEEM" if is_redeem else "BUY"
    if product.status != "AVAILABLE" or product.price_minor <= 0:
        task.result_json = {"message": "该产品当前不可交易。"}
        transition_task(task, "FAILED")
        return task
    if requested_units is not None:
        derived_amount = requested_units * product.price_minor
        if derived_amount > 10**13:
            task.result_json = {"message": "交易金额超出当前支持范围，请调整份数。", "error": "WEALTH_AMOUNT_UNSUPPORTED"}
            return task
        if amount_minor is not None and amount_minor != derived_amount:
            task.result_json = {"message": "份数与当前单价对应的交易金额不一致，请选择按金额或按份数办理。", "error": "WEALTH_QUANTITY_AMOUNT_CONFLICT"}
            return task
        amount_minor = derived_amount
    if amount_minor <= 0 or amount_minor % product.price_minor != 0:
        task.result_json = {"message": f"交易金额必须为 {product.price_minor / 100:.2f} 元的正整数倍。"}
        return task
    units = amount_minor // product.price_minor
    if order_type == "BUY" and (profile is None or as_utc(profile.expires_at) <= _risk_clock(session, task.sandbox_id, task.user_id)):
        task.result_json = {"message": "风险测评不存在或已过期，请先独立完成测评；Agent 不会代填。",
            "error": "RISK_ASSESSMENT_REQUIRED" if profile is None else "RISK_PROFILE_EXPIRED"}
        transition_task(task, "FAILED")
        return task
    if order_type == "BUY" and product.risk_level > profile.risk_level:
        task.result_json = {"message": "产品风险等级高于当前测评等级，已拦截申购。", "error": "PRODUCT_RISK_EXCEEDS_PROFILE"}
        transition_task(task, "FAILED")
        return task
    if order_type == "BUY":
        assessment = risk_profile_view(session, task.sandbox_id, task.user_id)
        if not assessment['valid']:
            task.result_json = {'message': '风险测评需要更新，请先由你本人重新完成测评。',
                'error': 'RISK_ASSESSMENT_REQUIRED', 'reason_codes': list(assessment['reason_codes'])}
            transition_task(task, 'FAILED')
            return task
    holding = holdings.get(product.id)
    budget = session.scalar(select(BudgetEnvelope).where(
        BudgetEnvelope.sandbox_id == task.sandbox_id,
        BudgetEnvelope.user_id == task.user_id,
        BudgetEnvelope.label == "应急金",
    ))
    reserved_minor = budget.reserved_minor if budget else 0
    from .services import Principal
    from .payment_instruments import cash_reserved_minor, resolve_source_instrument, validate_instrument_snapshot
    principal = Principal(task.user_id, task.sandbox_id)
    reference = (requested_action.slots.card_number or requested_action.slots.card_last4) if requested_action is not None else None
    try:
        source = resolve_source_instrument(session, principal, reference, 'CNY', purpose='WEALTH' if order_type == 'BUY' else 'READ')
        if source['instrument_type'] != 'DEBIT':
            raise DomainError('WEALTH_CASH_ACCOUNT_REQUIRED', '理财申赎须使用本人借记资金账户。', 403)
        account = session.get(Account, source['account_id'])
        if order_type == 'BUY':
            validate_instrument_snapshot(session, principal, source, purpose='WEALTH', amount_minor=amount_minor, lock=False)
    except DomainError as error:
        task.result_json = {'error': error.code, 'message': error.message}
        transition_task(task, 'FAILED')
        return task
    if order_type == "BUY" and account.balance_minor - cash_reserved_minor(session, principal, account.id) - amount_minor < reserved_minor:
        task.result_json = {"message": "申购后活期余额会低于已留存的应急金，已拦截。"}
        transition_task(task, "FAILED")
        return task
    if order_type == "REDEEM" and (holding is None or holding.units < units):
        task.result_json = {"message": "持仓份额不足，无法赎回。"}
        transition_task(task, "FAILED")
        return task

    fic.slots = {
        "product_id": {"value": product.id, "source": "AUTHORITATIVE", "name": product.name},
        "amount_minor": {"value": amount_minor, "source": "DERIVED_FROM_CURRENT_PRODUCT_PRICE" if requested_units is not None else "USER_TEXT"},
        "order_type": {"value": order_type, "source": "USER_TEXT"},
        "units": {"value": units, "source": "USER_TEXT" if requested_units is not None else "DERIVED_FROM_FIXED_SIMULATED_PRICE"},
    }
    fic.requested_capabilities = ["wealth.order.v1"]
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    plan = {
        "plan_version": 1, "policy_version": "wealth-order-v1",
        "task_id": task.id, "sandbox_id": task.sandbox_id,
        "nodes": [{
            "node_id": "post-investment-order", "tool_name": "wealth.order.v1",
            "read_only": False, "product_id": product.id, "product_name": product.name,
            "expected_product_version": product.version,
            "expected_account_version": account.version,
            "account_id": account.id, "payment_instrument": source,
            "expected_holding_version": holding.version if holding else None,
            "expected_profile_id": profile.id if profile else None,
            "order_type": order_type, "units": units,
            "amount_minor": amount_minor, "unit_price_minor": product.price_minor,
            "requires_confirmation": True,
        }],
    }
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id,
                  node_key="post-investment-order", decision="REQUIRE_CONFIRMATION",
                  reason_codes=["INVESTMENT_TRADE", "RISK_PROFILE_MATCHED" if order_type == "BUY" else "OWNED_REDEEMABLE_UNITS_VERIFIED"],
                  policy_version="wealth-order-v1")
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {
        "message": f"准备{('申购' if order_type == 'BUY' else '赎回')}{product.name} {amount_minor / 100:.2f} 元（{units} 份），确认后执行交易；不保证收益。",
        "simulated": True,
        "order_preview": {
            "product_id": product.id, "product_name": product.name, "product_version": product.version,
            "risk_level": product.risk_level, "order_type": order_type,
            "amount_minor": amount_minor, "units": units, "unit_price_minor": product.price_minor,
            "source_card_last4": source['display_last4'],
            "terms": simulation_product_terms(),
            # Carry the same audited assessment used by the eligibility gate,
            # not merely a level/expiry pair which the client cannot verify.
            "risk_profile": {**(assessment if order_type == "BUY" else
                                  risk_profile_view(session, task.sandbox_id, task.user_id)),
                             "required_for_this_order": order_type == "BUY"},
            "product_fact_source": {"kind": "SIMULATION_PRODUCT_RECORD", "record_id": product.id,
                                    "version": product.version},
        },
    }
    session.add(Confirmation(
        sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key="post-investment-order", action_type="wealth.order.v1",
        target_id=product.id, plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash": task.plan_hash, "product_name": product.name,
                                  "order_type": order_type, "amount_minor": amount_minor, "units": units}),
        expires_at=utcnow() + timedelta(minutes=5),
    ))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                 event_type="WEALTH_ORDER_PROPOSED",
                 payload={"product_id": product.id, "order_type": order_type,
                          "amount_minor": amount_minor, "plan_hash": task.plan_hash})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task
