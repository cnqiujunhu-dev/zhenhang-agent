"""Finite deadlines resolved against an existing trusted China-time clock.

This parses dates only; it cannot choose a payment, amount, recurrence or actor.
Date-only deadlines mean the end of that China calendar day and are displayed
as the resulting exact instant before the user authorizes anything.
"""
import re
from datetime import datetime, timezone


def authorization_deadline(value, clock):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # Calendar aliases only. A date-only expiry still means local end of day;
    # this does not infer an execution time or choose a banking action.
    text = re.sub(r'今晚', '今天晚上', text)
    text = re.sub(r'明晚', '明天晚上', text)
    text = re.sub(r'\btonight\b', 'today', text, flags=re.I)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", text):
        try:
            exact = datetime.fromisoformat(text)
            return exact.astimezone(timezone.utc) if exact.tzinfo is not None else None
        except ValueError:
            return None
    # A deadline must select one calendar date, not silently choose the first
    # date from a range, alternatives, or another independent date.
    dates = re.findall(r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|"
        r"(?:\d{4}年)?\d{1,2}月\d{1,2}日|后天|明天|今天|下周[一二三四五六日天]?|"
        r"\btomorrow\b|\btoday\b|\bnext\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
        text, re.I)
    if len(dates) != 1 or re.search(r"不要|先别|撤销|取消|或|或者|\b(?:or|not|cancel)\b", text, re.I):
        return None
    from .delivery_window_fields import TIME_TOKEN,DATE_TOKEN,MERIDIEM
    if len(TIME_TOKEN.findall(text))>1:return None
    remaining=text
    for token in sorted([*DATE_TOKEN.finditer(text),*TIME_TOKEN.finditer(text)],key=lambda item:item.start(),reverse=True):
        remaining=remaining[:token.start()]+remaining[token.end():]
    remaining=MERIDIEM.sub('',remaining)
    remaining=re.sub(r'北京时间|中国时间|Asia/Shanghai|(?:UTC|GMT)\s*\+0?8(?::?00)?|截止|截至|有效期|有效|结束|日期|时间|当天|之前|为|于|到|前|的|'
        r'\b(?:until|by|valid|through|expires|expiry|end|date|time|at|on)\b|[\s,，。:：]','',remaining,flags=re.I)
    if remaining:return None
    from .banking_extensions import LOCAL_TZ, parse_schedule_date
    result = parse_schedule_date(text, clock)
    if result is None:
        return None
    if not re.search(r"\d{1,2}:\d{2}|(?:\d{1,2}|[零一二三四五六七八九十]{1,3})点|\d{1,2}\s*(?:AM|PM)\b", text, re.I):
        result = result.astimezone(LOCAL_TZ).replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(timezone.utc)
    return result


def scheduled_instant(value,clock):
    """Same unambiguous one-date grammar, retaining the schedule's 09:00 default."""
    checked=authorization_deadline(value,clock)
    if checked is None:return None
    if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T.+',value.strip()):return checked
    from .banking_extensions import parse_schedule_date
    return parse_schedule_date(value,clock)
