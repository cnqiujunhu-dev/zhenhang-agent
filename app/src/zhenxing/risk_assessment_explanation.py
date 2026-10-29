"""Public questionnaire definitions, never personal answers or risk scoring.

The semantic layer selects SUBJECT. The caller revalidates this complete
record before publication; merely finding its ID does not establish authority.
"""
import json

SUBJECT = 'policy:risk-assessment'
SOURCE_ID = 'kb:wealth.assessment-guide-v1'
GUIDE_VERSION = 'risk-questionnaire-guide-v1'
_EXPLANATIONS = {
    'investment_horizon': ('投资期限是这笔钱距离真正要使用的时间，不是希望多久盈利；以实际用钱安排回答。',
        'Investment horizon means when you will actually need this money, not when you hope to make a profit.'),
    'liquidity': ('流动性是遇到日常生活或应急需要时，这笔钱要多快取用；先保留基本生活和应急资金。',
        'Liquidity means how soon you may need this money for living costs or emergencies. Keep essential and emergency money separate.'),
    'experience': ('投资经验指本人实际经历过、理解过的产品和波动，不以家人经验或他人推荐代替。',
        'Investment experience is your own experience and understanding, not a relative\'s experience or someone else\'s recommendation.'),
    'loss_tolerance': ('损失承受能力同时考虑能否接受本金下降、生活能否承担该损失；希望获得高收益不等于能够承受高风险。',
        'Loss tolerance considers both your willingness and financial ability to withstand a loss. Wanting higher returns does not establish higher tolerance.'),
    'income_stability': ('收入稳定性关注可用于投资的收入能否持续，以及收入中断时是否还有备用资金，不是只比较收入高低。',
        'Income stability concerns continuity of investable income and reserves if that income stops, not income size alone.'),
    'asset_buffer': ('资产缓冲是扣除负债和这次拟投资金额后，可随时动用的应急资产能维持多久基本生活；不是账户总余额，也不把借款算作应急金。',
        'Asset buffer is how long readily available emergency assets can cover essential living costs after liabilities and the planned investment. It is not total account balance, and borrowed money is not an emergency reserve.'),
    'liability_burden': ('负债比例比较全部尚未偿还的负债与全部资产，关注整体债务负担；只看某张卡当前欠款不能代表全部负债。无法确认时按问卷的对应选项如实回答。',
        'Liability burden compares all outstanding liabilities with all assets. One card\'s current debt is not your entire liability position. If uncertain, use the questionnaire\'s stated uncertainty option honestly.'),
    'repayment_pressure': ('偿债压力比较每月固定还款与稳定可支配收入，关注月度现金流是否吃紧；它和负债总额占资产比例是两个问题，有资产也可能有较大还款压力。',
        'Repayment pressure compares monthly fixed repayments with stable disposable income. It concerns monthly cash flow, unlike total liabilities divided by assets. Owning assets does not rule out repayment pressure.'),
}


def registered_questionnaire_source():
    """Exact public record derived from the current registered questionnaire."""
    from .wealth import risk_assessment_questions
    questionnaire = risk_assessment_questions()
    questions = questionnaire['questions']
    if {row['question_id'] for row in questions} != set(_EXPLANATIONS):
        raise ValueError('RISK_QUESTIONNAIRE_GUIDE_CHANGED')
    data = {'questionnaire_version': questionnaire['questionnaire_version'],
        'independent_submission_required': True, 'personal_answers_supplied': False,
        'answer_or_score_mutation_allowed': False,
        'questions': [{**row, 'explanation': _EXPLANATIONS[row['question_id']][0],
            'explanation_en': _EXPLANATIONS[row['question_id']][1]} for row in questions],
        'boundary': '可以解释题意和选项，但不能代选、代填、以现有余额猜答案、推荐高分答案或通过聊天修改风险等级。请按本人实际情况独立填写；不确定的资料先核实。',
        'boundary_en': 'Explain questions and options only. Never choose or submit answers, infer them from account balances, recommend high-score answers or change a risk level in chat. Complete the assessment independently using your actual circumstances; verify uncertain information.'}
    return {'source_id': SOURCE_ID, 'kind': 'PRODUCT_KNOWLEDGE',
        'reference': 'product_knowledge:wealth.assessment-guide-v1',
        'version': questionnaire['questionnaire_version'] + ':' + GUIDE_VERSION,
        'title': '风险问卷题意与独立填写说明',
        'title_en': 'Risk questionnaire definitions and independent completion',
        'content': json.dumps(data, ensure_ascii=False, sort_keys=True)}


def verified_questionnaire_source(records):
    current = registered_questionnaire_source()
    rows = [row for row in records if row.get('source_id') == SOURCE_ID]
    return current if rows == [current] else None


def questionnaire_fallback(records, language, *, subject_ref, question_aspects=None, rule_aspects=None):
    """Finite public explanation, not a classifier of user messages."""
    if subject_ref != SUBJECT:
        return None
    if (not set(question_aspects or []) <= {'RULES', 'SUMMARY'}
            or not set(rule_aspects or []) <= {'GENERAL', 'RISK', 'PROCESS', 'LIMITS', 'ELIGIBILITY'}):
        return None
    source = verified_questionnaire_source(records)
    if source is None:
        return None
    content = json.loads(source['content']); english = language == 'en'
    key = 'explanation_en' if english else 'explanation'
    lines = [row[key] for row in content['questions']]
    lines.append(content['boundary_en' if english else 'boundary'])
    return '\n'.join(lines), {'fallback_scope': 'REGISTERED_QUESTIONNAIRE_GUIDE',
        'fallback_policy_source_ids': [SOURCE_ID], 'question_coverage': {'complete': True,
            'public_definitions_only': True, 'personal_assessment_completed': False}}
