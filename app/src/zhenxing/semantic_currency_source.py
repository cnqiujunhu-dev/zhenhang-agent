"""Selected denomination evidence, not intent, amount or payment-role routing.

The model still selects the registered finite value and its source span.
This refusal-only grammar accepts a denomination inside a role phrase while
rejecting absent, contradictory, mixed or negated current-source evidence.
"""
import re

TOKEN=re.compile(r'人民币|美元|美金|美刀|(?<![A-Za-z0-9_])(?:US\$|USD|CNY|RMB|dollars?|yuan|renminbi)(?![A-Za-z0-9_])|元',re.I)
CURRENCY_FIELDS=frozenset({'currency','source_currency','target_currency'})
_BREAK=re.compile(r'[，,。；;！!？?\n]')
_DENIAL=r'(?:不能|不可|不得|不准|不许|不允许|不想|不愿意|不愿|不需要|不要|不用|别|禁止|不选|非|不是|不使用|不接受|不收|不付)'
_USE=r'(?:再|使用|选择|用|以|收取|接收|收|支付|付款|付|的|\s)*'
_EN_LEFT=r'(?<![A-Za-z0-9_])'
_EN_RIGHT=r'(?![A-Za-z0-9_])'
_NEGATED_PREFIX=re.compile(_DENIAL+_USE+r'$|'
    +_EN_LEFT+r"(?:not|no|never|do\s+not|don't|cannot|can\s+not|can't)"+_EN_RIGHT
    +r'\s*(?:(?:use|pay|receive|in|with)'+_EN_RIGHT+r'\s*)*$',re.I)
_NEGATED_SUFFIX=re.compile(r'\s*(?:不使用|不用|不收|不接受|不付)|\s*'+_DENIAL+r'(?:再)?(?:被)?(?:使用|用|接收|收取|接受|收|支付|付款|付)|'
    +r'\s*'+_EN_LEFT+r'(?:must\s+not|cannot|can\s+not)'+_EN_RIGHT
    +r'\s+be'+_EN_RIGHT+r'\s+(?:used|paid|received)'+_EN_RIGHT,re.I)
_NUMBER=r'(?:\d+(?:\.\d+)?\s*)?'
_LINK=r'(?:币种|货币)?\s*(?:为|是|用|以|[：:])?\s*'+_NUMBER
_CONVERT=r'(?:换成|换为|兑换成|兑换为|转换为|转换成|转成|转为|兑成|兑为|折成|折合为|换入)'
_SOURCE_BEFORE=re.compile(r'(?:付款|支付|扣款|付出|资金来源|来源|换出)'+_LINK+r'$|'
    r'\b(?:pay|paying|source|debit|funding|convert|exchange)(?:\s+currency)?\s*(?:in|with|from|is|:)?\s*'+_NUMBER+r'$',re.I)
_TARGET_BEFORE=re.compile(r'(?:收款|收款方收|收款人收|对方收|对方收到|接收|收到|收取|到账|入账|目标|'+_CONVERT+r')'+_LINK+r'$|'
    r'\b(?:(?:recipient\s+)?receives?|received|target|credit|into|to)(?:\s+currency)?\s*(?:in|as|is|:)?\s*'+_NUMBER+r'$',re.I)
_SOURCE_AFTER=re.compile(r'^\s*(?:付款|支付|扣款|付出|出资|作为资金来源|'+_CONVERT+r')|'
    r'^\s*(?:paid|debited|used\s+as\s+(?:funding|source))\b',re.I)
_TARGET_AFTER=re.compile(r'^\s*(?:收款|到账|入账|作为收款币种)|^\s*(?:received|credited)\b',re.I)
_EXCHANGE_BEFORE=re.compile(r'兑换\s*'+_NUMBER+r'$',re.I)
_EXCHANGE_AFTER=re.compile(r'^\s*兑换\s*'+_NUMBER+r'$',re.I)
_JOINT=r'(?:(?:付款|支付|扣款)\s*(?:和|与|及|、|/|以及)\s*(?:收款|到账|入账)|(?:收款|到账|入账)\s*(?:和|与|及|、|/|以及)\s*(?:付款|支付|扣款))'
_JOINT_BEFORE=re.compile(_JOINT+r'(?:币种|货币)?\s*(?:均|都)?\s*(?:为|是|用|以)?\s*$',re.I)
_JOINT_AFTER=re.compile(r'^\s*'+_JOINT,re.I)


def has_denomination_evidence(message):
    return isinstance(message,str) and TOKEN.search(message) is not None


def apply_current_currency_schema(schema,prepared):
    """Mirror effective SET (including omitted default), not whole-task state.

    Literal SET candidates come only from the current source. CURRENT/history
    remain independent source mechanisms; no inherited currency is restated as
    a fabricated current change. CLEAR keeps its existing domain contract.
    """
    if has_denomination_evidence(prepared['message']):return schema
    fields={field for cap in prepared['capabilities'] for field in cap['allowed_fields']}
    fields.update(field for frame in prepared['frames'] for action in frame['actions'] for field in action['allowed_fields'])
    schema['$defs']['OfferedCurrentLiteralSetField']={'enum':sorted(fields-CURRENCY_FIELDS)}
    change=schema['$defs']['SemanticFieldChange']
    change.setdefault('allOf',[]).append({
        'if':{'properties':{'operation':{'not':{'const':'CLEAR'}},'entity_ref':{'type':'null'}}},
        'then':{'properties':{'field':{'$ref':'#/$defs/OfferedCurrentLiteralSetField'}}}})
    for field in sorted(CURRENCY_FIELDS):
        refs=[row['entity_ref'] for row in prepared['entities'] if row.get('field')==field
            and field in row.get('allowed_fields',[field])]
        change['allOf'].append({'if':{'properties':{'field':{'const':field},'operation':{'not':{'const':'CLEAR'}}},
                'required':['field']},
            'then':{'properties':{'entity_ref':{'enum':refs}},'required':['entity_ref']} if refs else False})
    schema['properties']['changes']['description']=(
        'No current currency: inherit CURRENT/HISTORY_USER; no new SET (omitted operation defaults SET).')
    return schema


def check_current_currency_available(value,prepared):
    """Fail before positional decoding when a current currency source is absent.

    This never edits or consumes a field. Unknown/non-null entity references
    retain their original identity/permission checks, not a new retry permit.
    """
    if not isinstance(value,dict) or value.get('mode') not in {'REQUEST','CONTINUE','REVISE'} \
            or has_denomination_evidence(prepared.get('message')):
        return
    changes=value.get('changes')
    if not isinstance(changes,list) or len(changes)>24:return
    for change in changes:
        if isinstance(change,dict) and isinstance(change.get('field'),str) and change['field'] in CURRENCY_FIELDS \
                and change.get('operation','SET')=='SET' and change.get('entity_ref') is None:
            raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED')


def _role_context(message,start,end):
    left=max((row.end() for row in _BREAK.finditer(message,0,start)),default=0)
    right=next((row.start() for row in _BREAK.finditer(message,end)),len(message))
    prior=list(TOKEN.finditer(message,left,start));following=TOKEN.search(message,end,right)
    before=message[prior[-1].end() if prior else left:start]
    after=message[end:following.start() if following else right]
    return before,after,bool(prior),following is not None


def selected_role_evidence(message,start,end):
    """Local field qualifiers only; never select a bank action or another value."""
    before,after,prior,following=_role_context(message,start,end)
    roles=set()
    if _SOURCE_BEFORE.search(before) or _SOURCE_AFTER.match(after):roles.add('SOURCE')
    if _TARGET_BEFORE.search(before) or _TARGET_AFTER.match(after):roles.add('TARGET')
    # Bare 兑换 is directional only between two explicit denominations, not
    # permission to guess a side for a standalone "兑换100美元" request.
    if following and _EXCHANGE_AFTER.match(after):roles.add('SOURCE')
    if prior and _EXCHANGE_BEFORE.search(before):roles.add('TARGET')
    if _JOINT_BEFORE.search(before) or _JOINT_AFTER.match(after):roles.update({'SOURCE','TARGET'})
    return roles


def _explicit_joint_roles(message,start,end):
    before,after,_prior,_following=_role_context(message,start,end)
    return bool(_JOINT_BEFORE.search(before) or _JOINT_AFTER.match(after))


def checked_currency_source(field,raw,kind,selected_options,*,message,span):
    from .semantic_proposal import _literal_field
    from .contracts import DomainError
    from pydantic import ValidationError
    if field not in {'currency','source_currency','target_currency'} or not isinstance(raw,str) \
            or not isinstance(message,str) or not isinstance(span,dict) or set(span)!={'start','end'} \
            or any(type(span[key]) is not int for key in ('start','end')) \
            or not 0<=span['start']<span['end']<=len(message) or message[span['start']:span['end']]!=raw:
        raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED')
    try:value=_literal_field(field,raw,kind,{},selected_options)
    except (DomainError,ValidationError):raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED') from None
    tokens=list(TOKEN.finditer(raw))
    declared={'CNY' if token[0].upper() in {'CNY','RMB','YUAN','RENMINBI'} or token[0] in {'人民币','元'} else 'USD' for token in tokens}
    if declared!={value}:raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED')
    for token in tokens:
        start,end=span['start']+token.start(),span['start']+token.end()
        before=_BREAK.split(message[:start])[-1];after=_BREAK.split(message[end:],maxsplit=1)[0]
        # Match the suffix at its original absolute position, so the ASCII
        # boundary sees the real preceding currency character, not a sliced
        # string's artificial start. Both checks stay inside this clause.
        if _NEGATED_PREFIX.search(before) or _NEGATED_SUFFIX.match(message,end,end+len(after)):
            raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED')
        # currency is the independent amount denomination (including FX's
        # requested_currency ledger), never an alias for target_currency.
        expected={'source_currency':'SOURCE','target_currency':'TARGET'}.get(field)
        roles=selected_role_evidence(message,start,end)
        if expected and roles and (expected not in roles or len(roles)>1 and not _explicit_joint_roles(message,start,end)):
            raise ValueError('SEMANTIC_CURRENCY_LITERAL_REQUIRED')
    return value
