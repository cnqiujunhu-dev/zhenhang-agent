"""Prove a selected date's local role; never select an action or resolve its clock."""
from datetime import datetime, timezone
import re

from .planning import canonical_hash

ROLES = {('TRANSFER_SCHEDULE', 'scheduled_at'): 'EXECUTE_AT',
    ('TRANSFER_SCHEDULE', 'schedule_end_at'): 'RECURRENCE_END',
    ('AA_REQUEST', 'aa_expires_at'): 'EXPIRY',
    ('MERCHANT_PURCHASE', 'delivery_start'): 'FULFILMENT_START',
    ('MERCHANT_PURCHASE', 'delivery_end'): 'FULFILMENT_END',
    ('LIFE_PLAN_AUTHORIZE', 'delivery_start'): 'FULFILMENT_START',
    ('LIFE_PLAN_AUTHORIZE', 'delivery_end'): 'FULFILMENT_END'}
_CUE = r'(?:截止|截至|有效期|期限|到期|收款结束|\b(?:deadline|expires?|expiry)\b)'
_BEFORE = re.compile(r'(?P<cue>' + _CUE + r')(?:日期|时间)?\s*(?:是|为|在|到|至|于|is|at|on|by|until|through)?\s*$', re.I)
_AFTER = re.compile(r'^\s*(?:的)?(?P<cue>' + _CUE + r')', re.I)
_INSIDE = re.compile(_CUE, re.I)
_BREAK = re.compile(r'[，,。；;！？?\n]')


def expiry_role_evidence(message, span):
    """Return exact trusted qualifier evidence, or None when role is unproven.

    The constant clock checks only the existing closed date grammar. Actual
    interpretation still uses the field's original ChatTurn clock, and future
    validity still uses actual now. No resolved instant from here is consumed.
    """
    if not isinstance(message, str) or not isinstance(span, dict) or set(span) != {'start', 'end'}:
        return None
    start, end = span['start'], span['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(message):
        return None
    from .transfer_authorization_dates import authorization_deadline
    if authorization_deadline(message[start:end], datetime(2026, 1, 1, tzinfo=timezone.utc)) is None:
        return None  # A date field cannot mask a whole creation instruction.
    # "Create by tomorrow" / "wait until tomorrow then create" are creation
    # timing, not an AA's expiry. A bare connector is never expiry authority.
    left = max((match.end() for match in _BREAK.finditer(message, 0, start)), default=0)
    right = next((match.start() for match in _BREAK.finditer(message, end)), len(message))
    before = _BEFORE.search(message[left:start])
    after = _AFTER.match(message[end:right])
    inside = _INSIDE.search(message[start:end])
    if inside:
        qstart, qend = start + inside.start(), start + inside.end()
    elif before:
        qstart, qend = left + before.start('cue'), left + before.end('cue')
    elif after:
        qstart, qend = end + after.start('cue'), end + after.end('cue')
    else:
        return None
    tail = message[max(end, qend):right]
    if re.match(r'\s*(?:日期|时间)?\s*(?:之?[前后]|以后|之后|才|再|然后|\b(?:to|then|before|after)\b)', tail, re.I):
        return None  # A deadline used as a prerequisite is not this AA's expiry.
    from .delivery_window_fields import TIME_TOKEN, DATE_TOKEN
    if TIME_TOKEN.match(tail.lstrip()) or DATE_TOKEN.match(tail.lstrip()) or re.match(r'\s*T\d', tail):
        return None  # A clipped date cannot silently discard an adjacent time.
    from .semantic_proposal import _trusted_span
    qualifier_span = {'start': qstart, 'end': qend}
    qualifier = _trusted_span(message, qualifier_span, 'aa_expires_at')
    return {'role': 'EXPIRY', 'qualifier_span': qualifier_span, 'qualifier_text': qualifier,
        'qualifier_hash': canonical_hash({'text': qualifier})}
