"""Departure-related renewal review of recorded subscriptions; no cancellation."""
from datetime import date, datetime, timedelta, timezone
import re

CHINA_TIME = timezone(timedelta(hours=8))
MONTHS = {name: index for index, name in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}


def departure_date(message, created_at):
    from .intelligence import _intent_text
    text = _intent_text(message or "")
    text = re.sub(r"\"[^\"]*\"|(?<![A-Za-z0-9])'[^']*'(?![A-Za-z0-9])", "", text)
    match = re.search(r"(?:\b(?:I\s+(?:am\s+)?(?:leaving|departing|returning\s+home|leave\s+Shenzhen)|I['’]m\s+(?:leaving|departing|returning\s+home)|my\s+departure)\b|(?:我(?:将|准备|会|要)?|我的).{0,8}(?:离境|离开深圳|回国))[^.;\n。；]{0,100}", text, re.I)
    if not match:
        return None
    fragment = match.group(0)
    first_number = re.search(r"\d", fragment)
    declaration = text[max(0, match.start()-24):match.start()] + fragment[:first_number.start() if first_number else len(fragment)]
    if re.search(r"\b(?:if|maybe|possibly|not|never|don['’]?t|hypothetically|assuming)\b|如果|假如|也许|可能|不是|尚未确定", declaration, re.I):
        return None
    if re.search(r"\b(?:either|or)\b|或者|或是", fragment, re.I):
        return None
    try:
        reference = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if reference.tzinfo is None:
            return None
        reference = reference.astimezone(CHINA_TIME)
        if reference.year < 2000 or reference.year > 2100:
            return None
        if found := re.search(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)", fragment):
            return date(*map(int, found.groups())).isoformat()
        if found := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+(20\d{2}))?\b", fragment, re.I):
            return date(int(found[3]) if found[3] else reference.year, MONTHS[found[2].lower()], int(found[1])).isoformat()
        if found := re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b", fragment, re.I):
            return date(int(found[3]) if found[3] else reference.year, MONTHS[found[1].lower()], int(found[2])).isoformat()
        if found := re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日", fragment):
            return date(int(found[1]) if found[1] else reference.year, int(found[2]), int(found[3])).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def attach_renewal_review(view):
    from .intelligence import READ_KINDS
    nodes = view.get("nodes", [])
    if view.get("state") != "SUCCEEDED" or view.get("pending_confirmation") is not None \
            or (view.get("outcome") or {}).get("verified") is not True or not nodes \
            or any(not isinstance(node, dict) or node.get("action") not in READ_KINDS
                   or node.get("status") != "SUCCEEDED" or not isinstance(node.get("result"), dict)
                   or node["result"].get("error") for node in nodes):
        return
    requested = departure_date(view.get("message", ""), view.get("created_at"))
    if requested is None:
        return
    for node in view.get("nodes", []):
        if node.get("action") != "SUBSCRIPTION_LIST" or node.get("status") != "SUCCEEDED":
            continue
        result = node.get("result")
        rows = result.get("subscriptions") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            continue
        matching, uncertain, earlier = [], [], []
        for row in rows:
            if row.get("status") != "ACTIVE":
                continue
            merchant = row.get("merchant")
            if not isinstance(merchant, str):
                continue
            try:
                raw_due = row.get("next_due_at")
                due = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    raise ValueError()
                day = due.astimezone(CHINA_TIME).date().isoformat()
            except (ValueError, TypeError, AttributeError):
                uncertain.append(merchant)
                continue
            if row.get("due_status") != "UPCOMING":
                uncertain.append(merchant)
            elif day > requested:
                matching.append({"merchant": merchant, "recorded_due_at": raw_due})
            else:
                earlier.append(merchant)
        result["renewal_review"] = {"version": "visitor-renewal-review-v1", "departure_date": requested, "time_zone": "Asia/Shanghai",
            "recorded_after_departure": matching, "active_dates_need_verification": uncertain,
            "recorded_before_or_on_departure": earlier, "complete_future_schedule_known": False,
            "read_only": True, "bank_state_changed": False,
            "notice": "仅核对记录的日期，不能据此保证离境后没有其他续费；过往计划或缺失日期需向服务商核实，未取消任何订阅。",
            "notice_en": "Only recorded dates were reviewed. This is not proof of no later renewals. Past or missing plans need provider verification; no subscription was cancelled."}
