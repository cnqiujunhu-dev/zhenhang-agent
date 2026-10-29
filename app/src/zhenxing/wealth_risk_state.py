"""Finite display of an owned risk-profile READ, never scoring or suitability authority."""
from datetime import datetime
from zoneinfo import ZoneInfo


def suitability_rejection_reason(profile):
    """A missing independent form is not an expired, completed assessment."""
    valid, state = profile.get('valid'), profile.get('status')
    if type(valid) is not bool or state not in {'VALID', 'MISSING', 'EXPIRED', 'REASSESSMENT_REQUIRED'} \
            or valid is not (state == 'VALID'):
        raise ValueError('INVALID_WEALTH_RISK_STATE')
    if valid:
        return None
    return 'RISK_PROFILE_EXPIRED' if state == 'EXPIRED' else 'RISK_ASSESSMENT_REQUIRED'


def risk_status_summary(profile, language='zh-CN'):
    """Only current typed READ fields; no user wording, new reads or account changes.

    The caller already owns/authenticates the READ result. Structural validation
    here refuses incomplete or inconsistent display data rather than promoting
    a seeded R-level, a future expiry date, or a public questionnaire to validity.
    """
    try:
        if not isinstance(profile, dict):
            return None
        from .wealth_suitability_facts import PROFILE_FIELDS, _profile
        checked = _profile({key: profile.get(key) for key in PROFILE_FIELDS})
        suitability_rejection_reason(checked)
        observed = datetime.fromisoformat(profile['business_time'])
        if observed.tzinfo is None or observed.utcoffset() is None:
            return None
        state, source = checked['status'], checked['assessment_source']
        expiry = datetime.fromisoformat(checked['expires_at']) if checked['expires_at'] else None
        if state == 'VALID' and (expiry is None or observed >= expiry or source == 'SYNTHETIC_SEED') \
                or state == 'EXPIRED' and (expiry is None or observed < expiry):
            return None
        english = language == 'en'
        level = f"R{checked['risk_level']}" if checked['risk_level'] is not None else None
        date = expiry.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M') if expiry else None
        if state == 'MISSING':
            return ('No personal risk assessment is registered. Complete the eight-question form independently before a purchase; I cannot choose answers for you.' if english else
                '目前还没有你的风险测评记录。买入前需要你本人独立填写8题问卷，我不会替你选择答案。')
        if state == 'REASSESSMENT_REQUIRED':
            if source == 'SYNTHETIC_SEED':
                return (f'The initial profile records {level}, but you have not independently completed a valid risk assessment. The recorded future expiry date does not establish validity. Complete the eight-question form yourself before a purchase; I cannot choose answers.' if english else
                    f'初始档案记录为{level}，但还没有你本人独立完成的有效风险测评；档案上的到期日不代表测评已通过。买入前需要你本人独立填写8题问卷，我不会替你选答案。')
            return (f'The recorded level is {level}, but the assessment needs a new independent submission and is not currently valid. Complete the current eight-question form yourself before a purchase.' if english else
                f'档案记录为{level}，但当前测评需要重新由你本人独立提交，不能当作有效测评。买入前请本人完成当前8题问卷。')
        if state == 'EXPIRED':
            return (f'Your recorded risk assessment is {level} and expired at {date} (Shanghai time). Complete the eight-question form independently before a new purchase; I cannot choose answers.' if english else
                f'你的风险测评记录为{level}，已于{date}（北京时间）到期。买入前需要你本人重新独立填写8题问卷，我不会替你选答案。')
        if source == 'DEV_TEST_OVERRIDE':
            return (f'The explicit test-override profile records {level}, with a recorded expiry of {date} (Shanghai time). This is not an independently completed customer questionnaire; this query did not change the profile or submit a purchase.' if english else
                f'当前测试设定档案记录为{level}，登记有效至{date}（北京时间）；来源不是本人独立提交的问卷。本次仅查询，没有改写档案或申购。')
        return (f'Your current risk assessment is {level}, valid until {date} (Shanghai time). Product suitability, price and funds still require their own checks; no purchase was made by this query.' if english else
            f'你当前有效的风险测评为{level}，有效至{date}（北京时间）。具体产品、价格和资金仍需分别核验；本次查询没有申购。')
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
