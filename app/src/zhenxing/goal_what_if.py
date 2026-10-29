"""Read-only savings scenarios. User assumptions are not bank instructions."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import DomainError


WHAT_IF_VERSION = "goal-monthly-saving-what-if-v1"
_QUESTION = re.compile(r"提前(?:多久|多少|几个月|几月|完成|实现)|多久(?:完成|实现|能)|(?:能|可以).{0,8}(?:提前|早)|how\s+(?:much\s+)?(?:sooner|earlier)", re.I)
_DELTA = re.compile(r"(?:每月|每个月).{0,8}(?:少花|少支出|少消费|减少支出|减少消费|多存|多攒|多储蓄)\s*(?P<money>[^，,。；;\n]{1,32}?(?:元|块钱?|人民币|CNY|RMB))", re.I)
_BASE = re.compile(r"(?:原本|原来|目前|现在|本来)?\s*(?:每月|每个月)\s*(?:原本|原来|目前|现在|本来)?\s*(?:能|可以|可)?\s*(?:存|攒|储蓄|储存)\s*(?P<money>[^，,。；;\n]{1,32}?(?:元|块钱?|人民币|CNY|RMB))", re.I)
_MONTHLY_EN = r"(?:(?:every|each|per)\s+month|monthly)"
_DELTA_EN = re.compile(r"\b(?:save|put\s+away|set\s+aside)\s+(?:an?\s+)?(?:extra|additional)\s+(?P<money>[^,;!?\n]{1,65}?)\s+" + _MONTHLY_EN, re.I)
_DELTA_LESS_EN = re.compile(r"\bspend\s+(?P<money>[^,;!?\n]{1,65}?)\s+less\s+" + _MONTHLY_EN, re.I)
_BASE_EN = re.compile(r"\b(?:I\s+)?(?:(?:currently|normally|usually|originally|already)\s+)?save\s+(?!(?:an?\s+)?(?:extra|additional|more)\b)(?P<money>[^,;!?\n]{1,65}?)\s+" + _MONTHLY_EN, re.I)


@dataclass(frozen=True)
class GoalAssumptionUpdate:
    """A user-stated monthly assumption, never an instruction to move money."""
    kind: str
    amount_minor: int

    def public(self):
        return {"kind": self.kind, "amount_minor": self.amount_minor, "currency": "CNY", "period": "MONTH"}


def goal_assumption_update(text: str) -> GoalAssumptionUpdate | None:
    """Source-closed monthly frames distinguish total, original and extra.

    A bare amount is intentionally ambiguous here. Missing original-baseline
    replies keep their older dedicated path; this parser never assigns a goal.
    """
    from .intelligence import _intent_text
    from .conversation_service import _continuation_money
    if not isinstance(text, str) or len(text) > 160 or _intent_text(text) != text or re.search(r"[；;\n\r“”\"'「」]", text):
        return None
    frames = (
        ("NEW_TOTAL", r"(?:如果\s*)?(?:改为|改成)\s*(?:每月|每个月)\s*(?:总共|一共|合计)?\s*(?:存|攒|储蓄)\s*(?P<money>.+?)(?:呢|的话|吧)?[？?]?$"),
        ("NEW_TOTAL", r"(?:那)?(?:如果)?(?:我)?\s*(?:每月|每个月)\s*(?:总共|一共|合计)?\s*(?:存|攒|储蓄)\s*(?P<money>.+?)(?:呢|的话|吧)?[？?]?$"),
        ("NEW_TOTAL", r"How\s+(?:much\s+)?(?:sooner|earlier)\s+would\s+(?:that|this)\s+be\s+if\s+I\s+save\s+(?P<money>.+?)\s+" + _MONTHLY_EN + r"\s+instead[?]?$"),
        ("NEW_TOTAL", r"What\s+if\s+I\s+(?:save|put\s+away|set\s+aside)\s+(?P<money>.+?)\s+" + _MONTHLY_EN + r"\s+instead[?]?$"),
        ("NEW_TOTAL", r"Make\s+it\s+(?P<money>.+?)\s+(?:a|per)\s+month[.?]?$"),
        ("BASELINE", r"(?:原本|原来|本来)\s*(?:每月|每个月)\s*(?:存|攒|储蓄)\s*(?P<money>.+?)(?:呢)?[？?]?$"),
        ("BASELINE", r"I\s+(?:originally|normally|currently)\s+save\s+(?P<money>.+?)\s+" + _MONTHLY_EN + r"[.?]?$"),
        ("DELTA", r"(?:如果\s*)?(?:(?:改为|改成)\s*)?(?:每月|每个月)\s*(?:额外|再)?\s*(?:多存|多攒|多储蓄|少花)\s*(?P<money>.+?)(?:呢|的话|吧)?[？?]?$"),
        ("DELTA", r"(?:What\s+if\s+)?I\s+save\s+(?:an?\s+)?(?:extra|additional)\s+(?P<money>.+?)\s+" + _MONTHLY_EN + r"(?:\s+instead)?[.?]?$"),
    )
    for kind, frame in frames:
        match = re.fullmatch(frame, text.strip(), re.I)
        if not match:
            continue
        body = match.group("money").strip()
        # English requires an explicit CNY denomination; neither '$' nor a
        # conversational amount can silently change the source currency.
        if re.search(r"[A-Za-z]", text) and not re.fullmatch(r"(?:CNY|RMB)\s*[0-9.,]+|[0-9.,]+\s*(?:yuan|renminbi)", body, re.I):
            return None
        money = _continuation_money(body, "CNY")
        if money and money[1] == "CNY" and money[0] <= 10**13:
            return GoalAssumptionUpdate(kind, money[0])
        if kind in {"BASELINE", "NEW_TOTAL"} and re.fullmatch(r"(?:CNY\s*)?0(?:\.00)?(?:\s*元)?", body, re.I):
            return GoalAssumptionUpdate(kind, 0)
        return None
    return None


def revise_goal_assumptions(original: dict, update: GoalAssumptionUpdate) -> dict | None:
    """Project exact user assumptions; preserve the comparison on total edits."""
    baseline, delta = original.get("monthly_saving_minor"), original.get("monthly_saving_delta_minor")
    if update.kind == "NEW_TOTAL":
        return {"goal_name": original["goal_name"], "monthly_saving_total_minor": update.amount_minor,
            **({"monthly_saving_minor": baseline} if type(baseline) is int and baseline >= 0 else {})}
    elif update.kind == "BASELINE":
        baseline = update.amount_minor
        if original.get("monthly_saving_total_minor") is not None:
            return {"goal_name": original["goal_name"], "monthly_saving_minor": baseline,
                "monthly_saving_total_minor": original["monthly_saving_total_minor"]}
    elif update.kind == "DELTA":
        delta = update.amount_minor
    else:
        return None
    # Explicit additional savings stay positive. A new total is a separate
    # field, so zero/negative change is derived by trusted comparison math.
    if type(baseline) is not int or baseline < 0 or type(delta) is not int or delta <= 0:
        return None
    return {"goal_name": original["goal_name"], "monthly_saving_minor": baseline,
        "monthly_saving_delta_minor": delta}


def goal_what_if_requested(text: str) -> bool:
    return bool(_QUESTION.search(text) and (re.search(r"(?:每月|每个月).{0,8}(?:少花|少支出|少消费|减少支出|减少消费|多存|多攒|多储蓄)", text)
                or _DELTA_EN.search(text) or _DELTA_LESS_EN.search(text)))


def goal_what_if_slots(text: str) -> dict:
    """Extract exact CNY assumptions; no dates/rates/cash amounts are inferred."""
    from .intelligence import amounts_in_text

    values = {}
    for field, pattern in (("monthly_saving_delta_minor", _DELTA), ("monthly_saving_minor", _BASE)):
        matches = list(pattern.finditer(text))
        amounts = []
        for match in matches:
            prefix = text[max(0, match.start() - 8):match.start()]
            if re.search(r"不要|不能|不是|并非|不想|无需|别", prefix):
                continue
            if re.search(r"不要|不能|不是|并非|不想|无需|别|最多|至少|不超过|大概|大约|约|负|[-－]|\d\s*(?:到|至|~|～)\s*\d", match.group("money")):
                continue
            evidence = amounts_in_text(match.group("money"))
            if field == "monthly_saving_minor" and re.fullmatch(r"(?:人民币\s*)?0(?:\.00)?\s*(?:元|块钱?|人民币|CNY|RMB)", match.group("money"), re.I):
                evidence = {0}
            if len(evidence) == 1:
                amounts.append(next(iter(evidence)))
        if len(set(amounts)) == 1:
            values[field] = amounts[0]
    from .language import english_money_evidence
    for field, patterns in (("monthly_saving_delta_minor", (_DELTA_EN, _DELTA_LESS_EN)), ("monthly_saving_minor", (_BASE_EN,))):
        amounts = []
        for pattern in patterns:
            for match in pattern.finditer(text):
                if re.search(r"\b(?:not|never|don['’]?t|do\s+not)\b", text[max(0,match.start()-25):match.start()], re.I):
                    continue
                money = match.group("money")
                if re.search(r"\b(?:USD|dollars?|EUR|HKD|JPY|not|about|around|approximately|at\s+most|at\s+least|up\s+to|between)\b|[$€£]|\d\s*(?:to|through|[-–~])\s*\d", money, re.I):
                    continue
                evidence = {item[2] for item in english_money_evidence(money,"CNY")}
                if field == "monthly_saving_minor" and re.fullmatch(r"(?:CNY|RMB)\s*0(?:\.00)?|0(?:\.00)?\s*(?:yuan|renminbi)", money.strip(), re.I):
                    evidence = {0}
                if len(evidence) == 1:
                    amounts.append(next(iter(evidence)))
        if amounts:
            candidates = set(amounts) | ({values[field]} if field in values else set())
            if len(candidates) == 1:
                values[field] = next(iter(candidates))
            else:
                values.pop(field,None)
    # Named targets remain source identifiers; the owner-scoped resolver alone
    # decides whether one exists. Generic query wording is never a goal name.
    if goal_what_if_requested(text):
        named = re.search(r"(?:元|块钱?|人民币|CNY|RMB)\s*[，,]?\s*(?:让|使|能让|可以让)?\s*[“「\"]?([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_ -]{0,55}?)[”」\"]?\s*(?:目标)?\s*(?:能|可以|会)?\s*提前", text, re.I)
        if named:
            name = named.group(1).strip().removesuffix("目标").removesuffix("储蓄")
            if name and name not in {"多久", "多少", "储蓄", "完成", "实现"}:
                values["goal_name"] = name
        english_named = re.search(r"\bhow\s+(?:much\s+)?(?:sooner|earlier)\s+(?:will|would|can|could)\s+(?:my\s+)?(?:[\"“「](?P<quoted>[^\"”」\n]{1,60})[\"”」]|(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_ -]{0,59}?))\s+(?:be\s+(?:reached|achieved|completed)|(?:goal\s+)?(?:be\s+)?(?:complete|finish))", text, re.I)
        if english_named:
            name = (english_named.group("quoted") or english_named.group("name")).strip()
            if name.lower() not in {"i", "it", "this", "that", "my goal", "the goal", "my savings goal"}:
                if "goal_name" not in values or values["goal_name"] == name:
                    values["goal_name"] = name
                else:
                    values.pop("goal_name",None)
    update = goal_assumption_update(text)
    if update and update.kind == "NEW_TOTAL":
        values.pop("monthly_saving_minor", None)
        values.pop("monthly_saving_delta_minor", None)
        values["monthly_saving_total_minor"] = update.amount_minor
    return values


def calculate_goal_what_if(goal: dict, *, monthly_saving_minor: int, monthly_saving_delta_minor: int | None = None,
        monthly_saving_total_minor: int | None = None) -> dict:
    """Integer minor-unit, end-of-month comparison; no invented calendar date."""
    amounts = ((monthly_saving_minor, 0), (monthly_saving_total_minor, 0)) if monthly_saving_total_minor is not None else ((monthly_saving_minor, 0), (monthly_saving_delta_minor, 1))
    for value, minimum in amounts:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 10**13:
            raise DomainError("GOAL_WHAT_IF_INPUT_REQUIRED", "请提供准确的每月储蓄金额和每月增加的储蓄金额（人民币）。", 422)
    if monthly_saving_total_minor is not None and monthly_saving_delta_minor is not None and monthly_saving_total_minor != monthly_saving_minor + monthly_saving_delta_minor:
        raise DomainError("GOAL_WHAT_IF_ASSUMPTION_CONFLICT", "每月总额和额外增加金额不一致，请明确采用哪一个储蓄方案。", 422)
    remaining = goal.get("remaining_minor")
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
        raise DomainError("GOAL_FACTS_INVALID", "目标金额需要重新核对。", 409)
    improved = monthly_saving_total_minor if monthly_saving_total_minor is not None else monthly_saving_minor + monthly_saving_delta_minor
    delta = improved - monthly_saving_minor
    baseline_months = (remaining + monthly_saving_minor - 1) // monthly_saving_minor if monthly_saving_minor else (0 if remaining == 0 else None)
    improved_months = (remaining + improved - 1) // improved if improved else (0 if remaining == 0 else None)
    return {
        "calculation_version": WHAT_IF_VERSION, "goal_id": goal["goal_id"], "goal_name": goal["goal_name"],
        "goal_version": goal["version"], "remaining_minor": remaining,
        "monthly_saving_minor": monthly_saving_minor, "monthly_saving_delta_minor": delta,
        "monthly_saving_total_minor": monthly_saving_total_minor,
        "improved_monthly_saving_minor": improved, "baseline_months": baseline_months,
        "improved_months": improved_months, "months_saved": baseline_months - improved_months if baseline_months is not None and improved_months is not None else None,
        "proposed_plan_reachable": improved_months is not None,
        "baseline_reachable": baseline_months is not None,
        "currency": "CNY", "basis": "USER_STATED_MONTHLY_SAVING", "rounding": "CEILING_TO_WHOLE_MONTH",
        "read_only": True, "changes_applied": False, "guaranteed": False,
        "assumptions": ["每月节省金额全部用于该目标，收入、支出和目标金额不再变化。", "按月末累计储蓄向上取整，不预测日期，也未调整账户或目标预留。", "已有预留从目标金额中扣除；未预留的现金和其他目标资金不自动计入。"],
    }
