"""Civil-time normalization after the model selected a life-plan field.

This module does not route intents or grant authority. Relative order dates
use an owned birthday; immediate reservation is a proposal for confirmation.
"""
from datetime import datetime,time,timedelta
import re

from .contracts import DomainError
from .delivery_window_fields import TIME_TOKEN,MERIDIEM,_field

ON_CONFIRMATION='ON_CONFIRMATION'


def split_delivery_range(start,end):
    if end is not None or not isinstance(start,str):return start,end
    tokens=list(TIME_TOKEN.finditer(start))
    if len(tokens)!=2:return start,end
    first,last=tokens
    between=start[first.end():last.start()]
    # Only an explicit two-boundary range, never alternatives or extra clauses.
    if not re.fullmatch(r'\s*(?:到|至|[-~～—]|to)\s*(?:中午|上午|下午|晚上|凌晨)?\s*',between,re.I):return start,end
    if start[last.end():].strip():return start,end
    left=start[:first.end()].strip()
    right=re.sub(r'^\s*(?:到|至|[-~～—]|to)\s*','',between,flags=re.I)+last.group()
    marker=MERIDIEM.search(left)
    if marker and not MERIDIEM.search(right):
        inherited=marker.group()
        # A morning range ending at twelve denotes noon. Nighttime twelve
        # stays ambiguous and is rejected by the existing civil-time parser.
        if inherited in {'上午','早上','清晨'} and re.match(r'(?:12|十二)(?:点|时|:)',right):inherited='中午'
        right=inherited+right
    return left,right


def _integer(value):
    if value.isascii() and value.isdigit():return int(value)
    values={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if value in values:return values[value]
    if value.count('十')==1:
        a,b=value.split('十')
        if (not a or a in values) and (not b or b in values):return (values[a] if a else 1)*10+(values[b] if b else 0)
    return None


def parse_life_schedule_field(value,*,kind,clock,zone,birthday):
    """Normalize a verified source span, preserving ambiguous input rejection."""
    if isinstance(value,str):
        raw=value.strip()
        if kind=='reserve' and re.fullmatch(
                r'(?:请)?(?:把)?(?:这笔钱|这笔资金|钱|资金|预算)?(?:现在|立即|马上|确认后|先)(?:就)?(?:帮我)?'
                r'(?:预留|留出来|留出|留着|保留|预留出来)?(?:吧|就行)?',raw):
            return ON_CONFIRMATION
        if kind=='order':
            match=re.fullmatch(r'(?:生日)?提前([零〇一二两三四五六七八九十\d]{1,3})天(?:下单|购买)?',raw)
            if match:
                days=_integer(match[1])
                if days is None or not 1<=days<=30:
                    raise DomainError('LIFE_PLAN_ORDER_OFFSET_INVALID','请明确生日之前1至30天中的哪一天办理。',422)
                return datetime.combine(birthday-timedelta(days=days),time(9),zone).isoformat()
    return _field(value,clock,zone,None,'开始').isoformat()
