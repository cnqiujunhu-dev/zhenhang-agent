"""Read-time birthday dates, retained with their original observation clock."""
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .authentication import as_utc
from .contracts import DomainError

VERSION = 'occasion-next-birthdays-v1'


def upcoming_birthdays(rows, as_of):
    from .occasion_service import normalize_occasion_values, occurrence_date
    if not isinstance(as_of,datetime) or as_of.tzinfo is None:
        raise ValueError('Birthday observation requires an aware timestamp')
    when=as_utc(as_of);resolved=[];unresolved=[]
    for row in rows:
        identity={key:row[key] for key in ('occasion_id','family_member_name')}
        try:
            values=normalize_occasion_values(**{key:row.get(key) for key in
                ('month','day','calendar','timezone','leap_day_policy','lunar_leap_month','confirmed_solar_date')},
                source_reference='registered-read-date')
            today=when.astimezone(ZoneInfo(values['timezone'])).date();occasion=SimpleNamespace(**values)
            if values['calendar']=='LUNAR':
                confirmed=date.fromisoformat(values['confirmed_solar_date']) if values['confirmed_solar_date'] else None
                if confirmed is None or confirmed<today:
                    raise DomainError('OCCASION_SOLAR_DATE_REQUIRED','需要确认下次生日的阳历日期。',422)
                next_date=occurrence_date(occasion,confirmed.year)
            else:
                next_date=None
                for year in range(today.year,2201):
                    try:candidate=occurrence_date(occasion,year)
                    except DomainError as error:
                        if error.code=='OCCASION_NO_DATE_THIS_YEAR':continue
                        raise
                    if candidate>=today:
                        next_date=candidate;break
                if next_date is None:raise DomainError('OCCASION_YEAR_INVALID','下次年份超出支持范围。',422)
            resolved.append({**identity,'date':next_date.isoformat(),'timezone':values['timezone'],
                'days_until':(next_date-today).days})
        except (DomainError,ValueError,TypeError,KeyError) as error:
            unresolved.append({**identity,'reason_code':error.code if isinstance(error,DomainError) else 'OCCASION_DATE_INVALID'})
    nearest=min((row['days_until'] for row in resolved),default=None)
    return {'version':VERSION,'as_of':when.isoformat(),'basis':'LOCAL_CALENDAR_DAY_DISTANCE',
        'status':'PARTIAL' if resolved and unresolved else 'AVAILABLE' if resolved else 'UNRESOLVED' if unresolved else 'EMPTY',
        'next':[row for row in resolved if row['days_until']==nearest],'unresolved':unresolved}


def upcoming_result_valid(result):
    """Optional additive read field; old retained results keep their contract."""
    if 'next_birthdays' not in result:return True
    value=result['next_birthdays']
    try:
        if not isinstance(value,dict) or value.get('version')!=VERSION:return False
        if 'observed_at' in result and result['observed_at']!=value.get('as_of'):return False
        return upcoming_birthdays(result['occasions'],datetime.fromisoformat(value['as_of']))==value
    except (ValueError,TypeError,KeyError,AttributeError):return False


def upcoming_summary(result, *, english=False):
    if 'next_birthdays' not in result or not upcoming_result_valid(result):return None
    value=result['next_birthdays'];rows=value['next']
    if rows:
        descriptions=[]
        for row in rows:
            day=date.fromisoformat(row['date'])
            descriptions.append(f"{row['family_member_name']}, {day.strftime('%B')} {day.day}" if english
                else f"{row['family_member_name']}，{day.month}月{day.day}日")
        if english:
            intro=('Among confirmed dates, next up: ' if value['unresolved'] else 'Next birthday: ')+'; '.join(descriptions)+'.'
            detail='Some lunar birthday dates still need confirmation.' if value['unresolved'] else ''
        else:
            intro=('已确认日期中最近是' if value['unresolved'] else '最近是')+'；'.join(descriptions)+'。'
            detail='另有生日的下次阳历日期待确认。' if value['unresolved'] else ''
        return intro,detail
    if value['status']=='EMPTY':
        return ('No confirmed family birthdays are saved.','') if english else ('目前还没有已确认的家庭生日。','')
    return (('The next birthday date needs confirmation.','I will not guess an unconfirmed lunar date.') if english
        else ('还需要确认下次生日的阳历日期。','不会猜测未确认的农历换算。'))
