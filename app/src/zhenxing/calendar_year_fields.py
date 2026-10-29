"""Year projected from an exact relative year/day at the source user turn."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

OFFSETS = {'今年':0,'本年':0,'明年':1,'后年':2,'去年':-1,
    'this year':0,'next year':1,'the year after next':2,'last year':-1}
DAY_OFFSETS = {'今天':0,'今日':0,'明天':1,'明日':1,'后天':2,'昨天':-1,'昨日':-1,
    'today':0,'tomorrow':1,'yesterday':-1,'the day after tomorrow':2}


def is_relative_year(value):
    return isinstance(value,str) and value.strip().casefold() in (OFFSETS.keys() | DAY_OFFSETS.keys())


def relative_year(value,source_created_at):
    if not is_relative_year(value):
        return None
    if not isinstance(source_created_at,datetime) or source_created_at.tzinfo is None or source_created_at.utcoffset() is None:
        raise ValueError('RELATIVE_YEAR_SOURCE_CLOCK_REQUIRED')
    source=source_created_at.astimezone(ZoneInfo('Asia/Shanghai'))
    literal=value.strip().casefold()
    if literal in DAY_OFFSETS:
        return (source+timedelta(days=DAY_OFFSETS[literal])).year
    return source.year + OFFSETS[literal]
