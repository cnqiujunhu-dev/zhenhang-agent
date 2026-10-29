"""Bounded civil delivery fields; no session, persistence or model access."""
from datetime import date,datetime,timedelta,timezone as datetime_timezone
import re
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from .contracts import DomainError


UTC=datetime_timezone.utc
CHINA=datetime_timezone(timedelta(hours=8))
DATE_TOKEN=re.compile(r'(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|(?:(?<!\d)\d{4}年)?\d{1,2}月\d{1,2}日|'
    r'后天|明天|今天|下周[一二三四五六日天]?|\bnext\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|\btomorrow\b|\btoday\b',re.I)
NUMBER=r'(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})'
TIME_TOKEN=re.compile(r'(?<![\d:])\d{1,2}:\d{2}(?![\d:])(?:\s*(?:AM|PM)\b)?|'
    rf'(?<!\d){NUMBER}[点时](?:(?:{NUMBER})分|半|一刻|三刻)?|'
    r'(?<![\d.])\d{1,2}\s*(?:AM|PM)\b',re.I)
MERIDIEM=re.compile(r'上午|早上|清晨|凌晨|下午|中午|晚上|晚间|\bmorning\b|\bafternoon\b|\bevening\b|\bAM\b|\bPM\b',re.I)


def _deny(code,message):
    raise DomainError('DELIVERY_WINDOW_'+code,message,422)


def _zone(value):
    try:
        if not isinstance(value,str) or not value or len(value)>80:raise ValueError()
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError,TypeError):
        _deny('TIMEZONE_INVALID','请明确配送使用的城市时区；未指定时按北京时间。')


def _civil_utc(value,zone):
    """Round-trip both folds: never guess a DST gap or repeated local time."""
    candidates=set()
    for fold in (0,1):
        aware=value.replace(tzinfo=zone,fold=fold)
        result=aware.astimezone(UTC)
        if result.astimezone(zone).replace(tzinfo=None)==value:candidates.add(result)
    if len(candidates)!=1:
        _deny('TIMEZONE_AMBIGUOUS','这个当地时间处于夏令时切换，请明确带时差的具体时间。')
    return candidates.pop()


def _anchor(value,zone):
    if value is None:return None
    try:
        if isinstance(value,datetime):
            if value.tzinfo is None or value.utcoffset() is None:raise ValueError()
            return value.astimezone(zone).date()
        if isinstance(value,date):return value
        if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return date.fromisoformat(value)
        raise ValueError()
    except (ValueError,TypeError):
        _deny('DATE_REQUIRED','请先确认配送对应的完整日期。')


def _field(value,clock,zone,anchor,label):
    if isinstance(value,datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _deny('TIMEZONE_INVALID',f'请明确{label}时间的时区。')
        return value.astimezone(UTC)
    if not isinstance(value,str) or not value.strip() or len(value)>160:
        _deny('FIELD_INVALID',f'请明确一个配送{label}日期和时间。')
    value=value.strip()
    # Existing aware ISO receipts retain their exact instant. Offset-bearing
    # values do not require any locale or DST inference.
    try:
        iso=datetime.fromisoformat(value)
    except ValueError:iso=None
    if iso is not None and iso.tzinfo is not None and iso.utcoffset() is not None:return iso.astimezone(UTC)
    if re.search(r'不要|不是|不在|别|取消|改为|改成|或者|或是|还是|\bor\b|\bnot\b|\binstead\b|[?？]',value,re.I):
        _deny('AMBIGUOUS',f'请只给出最终采用的一个{label}时间。')
    cleaned=re.sub(r'北京时间|中国时间|Asia/Shanghai|(?<![A-Za-z])(?:UTC|GMT)\s*\+0?8(?::?00)?(?![A-Za-z0-9])','',value,flags=re.I)
    if cleaned!=value and zone.key!='Asia/Shanghai':
        _deny('TIMEZONE_AMBIGUOUS','配送文字时区与已选城市不一致，请明确使用哪一个。')
    if re.search(r'纽约|伦敦|美国|当地时间|本地时间|(?<![A-Za-z])(?:PST|PDT|EST|EDT|CST|MST|GMT|UTC|timezone)(?![A-Za-z])|'
            r'\b(?:New\s+York|London|Los\s+Angeles|local\s+time)\b|[+-]\d{2}:?\d{2}|\bZ\b',cleaned,re.I):
        _deny('TIMEZONE_AMBIGUOUS','请明确配送使用的城市时区或带时差时间，不推测外区时区。')
    # The semantic span may include the range boundary ("到12点" / "to 12 PM").
    # Accept one boundary only in its matching field. Do not remove arbitrary
    # interior wording, alternatives, inequalities or a second clock/date.
    boundary = r'^(?:从|from\b)\s*' if label == '开始' else r'^(?:到|至|to\b)\s*'
    cleaned=re.sub(boundary,'',cleaned.strip(),count=1,flags=re.I)
    dates=list(DATE_TOKEN.finditer(cleaned));times=list(TIME_TOKEN.finditer(cleaned));markers=MERIDIEM.findall(cleaned)
    if len(dates)>1 or len(times)>1 or len(markers)>1:
        _deny('AMBIGUOUS',f'请只给出一个{label}日期和一个时间。')
    if not times:_deny('TIME_REQUIRED',f'请补充配送{label}的具体时分。')
    if not dates and anchor is None:_deny('DATE_REQUIRED',f'请补充配送{label}日期，例如明天；不能只凭时分猜日期。')
    # Do not silently discard unsupported qualifiers, a second partial date,
    # seconds, recurring wording or an alternative instruction.
    remaining=cleaned
    for match in sorted([*dates,*times],key=lambda item:item.start(),reverse=True):
        remaining=remaining[:match.start()]+remaining[match.end():]
    remaining=MERIDIEM.sub('',remaining)
    remaining=re.sub(r'配送|送达|开始|结束|时间|当天|\b(?:at|on|from|until|delivery|start|end)\b|[\s,，。:：T]','',remaining,flags=re.I)
    if remaining:_deny('AMBIGUOUS',f'请用明确的一个{label}日期和时分，不附加其他时间条件。')
    literal=times[0].group();normalized=cleaned.replace('〇','零').replace('两','二')
    normalized=re.sub(r'(?<=[点时])半','30分',normalized)
    normalized=re.sub(r'(?<=[点时])一刻','15分',normalized)
    normalized=re.sub(r'(?<=[点时])三刻','45分',normalized).replace('时','点')
    for old,new in (('morning','上午'),('afternoon','下午'),('evening','晚上')):
        normalized=re.sub(r'\b'+old+r'\b',new,normalized,flags=re.I)
    # Twelve in a morning/evening expression is not a safe noon/midnight
    # decision; a caller can give 00:00 / 12:00 or an explicit ISO offset.
    if re.search(r'(?:12|十二)[点时:]|\b12\s*(?:AM|PM)\b',literal,re.I) \
            and any(marker in normalized for marker in ('上午','早上','清晨','晚上','晚间','凌晨')):
        _deny('AMBIGUOUS','请用00:00或12:00明确区分午夜和中午。')
    if '中午' in normalized and not re.match(r'(?:12|十二)(?:[点时:]|\s*(?:AM|PM)\b)',literal,re.I):
        _deny('AMBIGUOUS','请用明确的24小时制时分说明中午的配送时间。')
    if not dates:normalized=anchor.isoformat()+' '+normalized
    from .banking_extensions import parse_schedule_date
    local_clock=clock.astimezone(zone)
    proxy=datetime(local_clock.year,local_clock.month,local_clock.day,local_clock.hour,local_clock.minute,
        local_clock.second,local_clock.microsecond,tzinfo=CHINA)
    parsed=parse_schedule_date(normalized,proxy)
    if parsed is None:_deny('FIELD_INVALID',f'请核对配送{label}日期和时分是否有效。')
    civil=parsed.astimezone(CHINA).replace(tzinfo=None)
    return _civil_utc(civil,zone)


def parse_delivery_window(start,end,clock,*,timezone='Asia/Shanghai',anchor_date=None,start_clock=None,end_clock=None):
    """Return aware UTC (start,end), bounded to one unambiguous <=24h window.

    anchor_date is an already-verified full birthday date supplied by the
    domain, not a user permission or a guessed year. Time-only end always
    inherits start's local date; it never means the next day implicitly.
    start_clock/end_clock bind relative words to each verified field origin.
    They never replace the caller's current-time/future-execution checks.
    """
    if any(not isinstance(basis,datetime) or basis.tzinfo is None or basis.utcoffset() is None
            for basis in (clock,start_clock if start_clock is not None else clock,end_clock if end_clock is not None else clock)):
        _deny('CLOCK_INVALID','暂时无法核对当前日历，请稍后重试。')
    zone=_zone(timezone)
    first=_field(start,start_clock if start_clock is not None else clock,zone,_anchor(anchor_date,zone),'开始')
    last=_field(end,end_clock if end_clock is not None else clock,zone,first.astimezone(zone).date(),'结束')
    if last<=first:_deny('ORDER_INVALID','结束时间须晚于开始时间；跨日配送请明确写出结束日期。')
    if last-first>timedelta(hours=24):_deny('TOO_LONG','配送时间范围不能超过24小时，请缩小范围。')
    return first,last
