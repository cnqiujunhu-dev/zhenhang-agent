"""Typed read-only goal explanation, not model math or banking authority."""
import json
import re
from decimal import Decimal
from uuid import UUID

VERSION='owned-goal-calculation-v1'
SOURCE_ID='facts:goal-calculation'
FACT_ID=SOURCE_ID+'.explanation'
AUTHORITY='VERIFIED_READ_ONLY_GOAL_CALCULATION_NOT_AUTHORIZATION'


def goal_explanation_question(message):
    from .intelligence import _intent_text,rule_proposal,READ_KINDS
    from .financial_intent import foreign_financial_request
    from .intelligence import _private_read_veto
    if not isinstance(message,str) or len(message)>240 or _intent_text(message)!=message or re.search(r'[；;\n\r]|新话题|另外|另一个|new\s+topic|ignore',message,re.I): return False
    if _private_read_veto(message) or foreign_financial_request(message): return False
    if any(action.kind not in READ_KINDS for action in rule_proposal(message).actions): return False
    return bool(re.search(r'(?:为什么|为啥|怎么|如何|解释).{0,55}(?:这样|这么|计算|算|测算|个月|月份|储蓄|目标|提前)|'
        r'\b(?:why|how|explain)\b.{0,70}\b(?:calculat\w*|estimate\w*|take|months?|saving\w*|goals?)\b',message,re.I))


def goal_explanation_requested(message,context):
    proof=checked_goal_calculation((context or {}).get('goal_calculation'))
    if not proof or not goal_explanation_question(message): return False
    name=proof['goal']['goal_name']
    if any(row.get('field')=='goal_name' and isinstance(row.get('value'),str) and row['value']!=name and row['value'] in message for row in context.get('entities',[])): return False
    for match in re.finditer(r'([\u4e00-\u9fffA-Za-z0-9_-]{1,60})目标',message):
        target=re.sub(r'^(?:为什么|为啥|解释|当前|我的|本人|请)+','',match.group(1))
        if target and target not in {name,'储蓄','这个','这次','当前','我的'}: return False
    match=re.search(r'\b(?:my|the)\s+([\u4e00-\u9fffA-Za-z0-9_-]{1,60})\s+goal\b',message,re.I)
    return not match or match.group(1) in {name,'savings','saving','current','travel' if name=='旅行' else name}


def valid_goal_calculation(value):
    """Reject bool/float and forged derived fields before rendering any number."""
    if not isinstance(value,dict) or value.get('version')!=VERSION or value.get('authority')!=AUTHORITY \
        or value.get('state')!='SUCCEEDED' or value.get('actor_role') not in {'CUSTOMER','DEV_TEST','DEMO_OWNER'} \
        or type(value.get('task_version')) is not int or not 0<value['task_version']<=2**53-1: return None
    for key in ('task_id','operation_id'):
        try:
            if not isinstance(value.get(key),str) or str(UUID(value[key]))!=value[key].lower(): return None
        except (ValueError,TypeError): return None
    if any(not isinstance(value.get(key),str) or not re.fullmatch(r'[0-9a-f]{64}',value[key]) for key in ('source_request_hash','source_proposal_hash')): return None
    if set(value)!={'version','authority','task_id','task_version','operation_id','state','actor_role','goal','arguments','scenario','source_request_hash','source_proposal_hash'}: return None
    goal=value.get('goal');args=value.get('arguments');scenario=value.get('scenario')
    if not isinstance(goal,dict) or not isinstance(args,dict) or not isinstance(scenario,dict): return None
    from .redaction import redact_credentials
    from .intelligence import _intent_text
    name=goal.get('goal_name')
    if not isinstance(name,str) or not 0<len(name)<=60 or redact_credentials(name)!=name or _intent_text(name)!=name or goal.get('status')!='ACTIVE': return None
    for key,minimum in (('target_minor',1),('reserved_minor',0),('remaining_minor',0),('version',1)):
        if type(goal.get(key)) is not int or not minimum<=goal[key]<=2**53-1: return None
    if goal['reserved_minor']>goal['target_minor'] or goal['remaining_minor']!=goal['target_minor']-goal['reserved_minor']: return None
    try:
        if not isinstance(goal.get('goal_id'),str) or str(UUID(goal['goal_id']))!=goal['goal_id'].lower(): return None
        from .goal_what_if import calculate_goal_what_if
        expected=calculate_goal_what_if(goal,monthly_saving_minor=args.get('monthly_saving_minor'),
            monthly_saving_delta_minor=args.get('monthly_saving_delta_minor'),monthly_saving_total_minor=args.get('monthly_saving_total_minor'))
    except (ValueError,TypeError,KeyError,ArithmeticError): return None
    if any(key not in scenario or type(scenario[key]) is not type(expected_value) or scenario[key]!=expected_value for key,expected_value in expected.items()): return None
    if set(scenario)!=set(expected) or set(args)-{'monthly_saving_minor','monthly_saving_delta_minor','monthly_saving_total_minor'}: return None
    return value


def checked_goal_calculation(value):
    from .contracts import DomainError
    try: return valid_goal_calculation(value)
    except DomainError: return None


def goal_calculation_source(context):
    proof=checked_goal_calculation((context or {}).get('goal_calculation'))
    if not proof or proof['task_id']!=context.get('task_id') or proof['task_version']!=context.get('task_version'): return None
    return {'source_id':SOURCE_ID,'title':'本次目标储蓄测算的核验依据','title_en':'Verified basis for this savings calculation',
        'kind':'SCOPED_BUSINESS_FACTS','reference':'/api/v3/customer/tasks/'+proof['task_id'],
        'content':json.dumps(proof,ensure_ascii=False,sort_keys=True)}


def goal_source_content(source):
    if not isinstance(source,dict) or source.get('source_id')!=SOURCE_ID or source.get('kind')!='SCOPED_BUSINESS_FACTS': return None
    try: value=json.loads(source.get('content',''))
    except (ValueError,TypeError): return None
    proof=checked_goal_calculation(value)
    return proof if proof and source.get('reference')=='/api/v3/customer/tasks/'+proof['task_id'] else None


def render_goal_explanation(value,english=False):
    proof=checked_goal_calculation(value)
    if not proof: return ''
    row=proof['scenario'];name=proof['goal']['goal_name']
    money=lambda minor:f'{Decimal(minor)/100:.2f}'
    gap,base,new,delta=(row[key] for key in ('remaining_minor','monthly_saving_minor','improved_monthly_saving_minor','monthly_saving_delta_minor'))
    before,after,saved=(row[key] for key in ('baseline_months','improved_months','months_saved'))
    if english:
        lines=[f'For {name}, the recorded remaining gap is CNY {money(gap)}.']
        if before is None: lines.append('The original plan saves nothing each month, so no original completion month or time saving can be compared.')
        elif gap==0: lines.append('The recorded gap is already zero, so the original estimate is zero months; this is not proof that funds were spent.')
        else: lines.append(f'The original CNY {money(base)} per month gives ceiling({money(gap)} / {money(base)}) = {before} months.')
        if row['monthly_saving_total_minor'] is None: lines.append(f'The new monthly plan is CNY {money(base)} + CNY {money(delta)} = CNY {money(new)}.')
        else: lines.append(f'The new total is CNY {money(new)} per month; the original comparison remains CNY {money(base)} per month.')
        if after is None: lines.append('The new plan saves nothing each month, so a completion month cannot currently be estimated.')
        elif gap==0: lines.append('The new estimate is also zero months because the recorded gap is zero.')
        else: lines.append(f'The new estimate is ceiling({money(gap)} / {money(new)}) = {after} months. A part-month is rounded up, not rounded down.')
        if saved is not None: lines.append(f'This is {saved} '+('month' if saved==1 else 'months')+' sooner.' if saved>0 else f'This is {-saved} '+('month' if saved==-1 else 'months')+' later.' if saved<0 else 'The estimated completion time is unchanged.')
        lines.append('This assumes the stated monthly saving all goes to this goal, with other conditions unchanged. Unreserved cash and other goals are not added. It is an estimate, not a guaranteed date; no goal reserve, transfer or other funds were changed.')
    else:
        lines=[f'{name}当前记录的缺口是{money(gap)}人民币元。']
        if before is None: lines.append('原计划每月没有储蓄，无法比较原完成月份或能提前多久。')
        elif gap==0: lines.append('当前记录的缺口已为零，所以原计划估算为0个月；不代表这些钱已经花掉。')
        else: lines.append(f'原计划每月{money(base)}元，{money(gap)}÷{money(base)}向上取整，约需{before}个月。')
        if row['monthly_saving_total_minor'] is None: lines.append(f'新计划每月为{money(base)}+{money(delta)}={money(new)}元。')
        else: lines.append(f'新的每月总储蓄是{money(new)}元，仍与原来每月{money(base)}元的计划比较。')
        if after is None: lines.append('新计划每月不再储蓄，暂时无法预计完成月份。')
        elif gap==0: lines.append('因为记录缺口为零，新计划估算也为0个月。')
        else: lines.append(f'{money(gap)}÷{money(new)}向上取整，约需{after}个月；不足整月也按一个月算，不向下舍入。')
        if saved is not None: lines.append(f'因此可提前{saved}个月。' if saved>0 else f'因此约晚{-saved}个月。' if saved<0 else '预计完成月份与原计划相同。')
        lines.append('假设所述每月储蓄全部用于这个目标，其他条件不变；未预留现金和其他目标资金不计入。这只是估算，不保证日期，没有调整目标预留、转账或其他资金。')
    return '\n'.join(lines)
