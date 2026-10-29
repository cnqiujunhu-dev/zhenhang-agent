"""Closed calendar scalar fields selected by the model, never intent routing."""
import re
from datetime import datetime
import calendar


def start_month(value,source_created_at=None):
    if isinstance(value,str):
        relative=re.fullmatch(r'(?:从\s*)?(本月|这个月|下月|下个月|今年\d{1,2}月|明年\d{1,2}月)(?:开始|起)?',value.strip())
        if relative:
            from .authentication import as_utc
            from .banking_extensions import LOCAL_TZ
            if not isinstance(source_created_at,datetime):raise ValueError('SCHEDULE_MONTH_SOURCE_CLOCK_REQUIRED')
            origin=as_utc(source_created_at).astimezone(LOCAL_TZ)
            token=relative[1]
            if token in {'本月','这个月','下月','下个月'}:
                offset=1 if token in {'下月','下个月'} else 0
                year,month=divmod(origin.year*12+origin.month-1+offset,12);month+=1
            else:year=origin.year+(1 if token.startswith('明年') else 0);month=int(token[2:-1])
            if not 1<=year<=9999 or not 1<=month<=12:raise ValueError('SCHEDULE_START_MONTH_INVALID')
            return f'{year:04d}-{month:02d}'
    match=re.fullmatch(r'(?:从\s*)?(\d{4})(?:年\s*|[-/])(\d{1,2})(?:月)?(?:\s*(?:开始|起))?',value.strip()) if isinstance(value,str) else None
    if not match or not 1<=int(match[1])<=9999 or not 1<=int(match[2])<=12: raise ValueError('SCHEDULE_START_MONTH_INVALID')
    return f'{int(match[1]):04d}-{int(match[2]):02d}'


def day_of_month(value):
    from .intelligence import _chinese_integer
    match=re.fullmatch(r'(?:每月\s*)?(\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:日|号)?',value.strip()) if isinstance(value,str) else None
    if not match:raise ValueError('SCHEDULE_MONTH_DAY_INVALID')
    number=int(match[1]) if match[1].isdigit() else _chinese_integer(match[1])
    if not 1<=number<=31:raise ValueError('SCHEDULE_MONTH_DAY_INVALID')
    return number


def monthly_first(month,day):
    from .banking_extensions import LOCAL_TZ
    if type(day)is not int or not 1<=day<=31:raise ValueError('SCHEDULE_MONTH_DAY_INVALID')
    year,month=map(int,start_month(month).split('-'))
    return datetime(year,month,min(day,calendar.monthrange(year,month)[1]),9,0,tzinfo=LOCAL_TZ)


def schedule_deadline(value,clock):
    """A stated ending month includes that month's scheduled occurrence.

    This calendar rule is used only by bounded recurring transfers. It does
    not broaden AA deadlines, infer a recurrence, or create a payment.
    """
    from .transfer_authorization_dates import authorization_deadline
    precise=authorization_deadline(value,clock)
    if precise is not None:return precise
    if not isinstance(value,str):return None
    raw=re.sub(r'^(?:先到|截至|截止到|截止|到|until\s+|through\s+)','',value.strip(),flags=re.I)
    try:month=start_month(raw,clock)
    except (ValueError,TypeError):return None
    from .banking_extensions import LOCAL_TZ
    from datetime import timezone
    year,number=map(int,month.split('-'))
    return datetime(year,number,calendar.monthrange(year,number)[1],23,59,59,999999,tzinfo=LOCAL_TZ).astimezone(timezone.utc)
