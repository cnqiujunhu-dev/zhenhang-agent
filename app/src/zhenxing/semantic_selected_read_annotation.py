"""Remove only redundant SELF metadata on explicit current owned-record READs.

No intent, selector, subject, anchor, permission or banking fact is created.
The unmodified whole message still passes the ordinary foreign-owner and domain
owner gates. Literal shape is never evidence that a card exists or is owned.
"""
from copy import deepcopy
from types import MappingProxyType
import re

from .card_identifiers import CARD_NUMBER_PATTERN
from .semantic_read_resources import READ_RESOURCE_POLICIES, SELF_RECORDS, VERSION as RESOURCE_POLICY_VERSION
from .semantic_source_conditions import SOURCE_CONDITION as _CONDITIONAL

VERSION = 'current-actor-selected-read-annotation-v1'
POLICIES = MappingProxyType({'CARD_STATUS':'card.status.v1','BILL_ANALYSIS':'bill.analyze.v1',
    'CREDIT_VIEW':'credit.view.v1','CREDIT_STATEMENT':'credit.statement.v1'})
ROLES = frozenset({'CUSTOMER','DEV_TEST','DEMO_OWNER'})
_CARD = re.compile(r'(?<![A-Za-z0-9_])'+CARD_NUMBER_PATTERN+r'(?![A-Za-z0-9_])')
_IDENTITIES = frozenset({'card_number','card_reference','card_last4','source_card','destination_card_reference',
    'recipient_card_reference','funding_card_number'})
# Refusal-only source polarity for an already selected READ. These lexical
# operators never select a kind or manufacture an affirmative request.
_READ_OPERATOR = re.compile(r'查询|查看|读取|访问|显示|展示|披露|检查|分析|看|查|'
    r'\b(?:read(?:ing)?|access(?:ing|ed)?|quer(?:y|ying|ied)|show(?:ing|n|ed)?|display(?:ing|ed)?|'
    r'reveal(?:ing|ed)?|check(?:ing|ed)?|view(?:ing|ed)?|analy[sz](?:e|ed|ing))\b',re.I)
_READ_NEGATION = re.compile(r'不要|不需要|不想|不愿|不必|不允许|不许可|无须|无需|先别|别|勿|不许|不准|禁止|拒绝|停止|暂缓|'
    r'\b(?:not|never|cannot|(?:do|does|did|is|are|was|were|has|have|had|must|should|shall|could|would|need)n[’\x27]t|'
    r'can[’\x27]t|won[’\x27]t|without|no\s+need\s+to|refus(?:e|es|ed|ing)\s+to)\b',re.I)
_DIRECT_DENIED_READ = re.compile(r'不\s*(?:查询|查看|读取|访问|显示|展示|披露|检查|分析|看|查)')
_POLARITY_CLAUSE = re.compile(r'[，,。；;!?！？\n]|但是|不过|而是|但|\b(?:but|however|instead)\b',re.I)
_OWNERSHIP_ASSERTION = re.compile(r'属于|归属|\b(?:owned\s+by|belongs?\s+to)\b',re.I)


def _denied_read_source(message):
    """Refusal scope may contain objects/pronouns before its READ operator.

    The clause is bounded by the input contract and punctuation/contrast, not
    a fixed sentence template. A READ before a later 'do not repay' does not
    become denied: only a READ operator within the negation's following scope
    vetoes removal. Ambiguous scope stays on the original strict path.
    """
    for clause in _POLARITY_CLAUSE.split(message):
        if _DIRECT_DENIED_READ.search(clause):return True
        for negative in _READ_NEGATION.finditer(clause):
            if _READ_OPERATOR.search(clause,negative.end()):return True
    return False


def _registered_capabilities(kinds,prepared):
    from .capability_catalog import catalog
    from .dialogue_state import ACTION_ALLOWED_FIELDS
    registered={row['kind']:row for row in catalog(prepared['role'])['capabilities']}
    declared=prepared.get('capabilities')
    if not isinstance(declared,list):return None
    result={}
    for kind in kinds:
        policy=READ_RESOURCE_POLICIES.get(kind)
        actual=registered.get(kind)
        offered=[row for row in declared if isinstance(row,dict) and row.get('kind')==kind]
        if kind not in POLICIES or policy is None or policy.subject_policy!=SELF_RECORDS or actual is None or len(offered)!=1:
            return None
        row=offered[0];fields=row.get('allowed_fields')
        if actual['tool']!=POLICIES[kind] or actual['read_only'] is not True \
                or row.get('tool')!=actual['tool'] or row.get('read_only') is not True \
                or not isinstance(fields,list) or any(not isinstance(field,str) for field in fields) \
                or len(fields)!=len(set(fields)) or not set(fields)<=set(ACTION_ALLOWED_FIELDS.get(kind,())):
            return None
        result[kind]=set(fields)
    return result


def _closed_owner_nominals(message):
    """Whole possessive resource fields, without fabricating a self anchor.

    The existing financial-topic guard does not inspect every possessive card
    noun (for example John's card followed by an unpossessed 'statement').
    Reuse the reviewed resource/owner grammars, accepting only exact first
    person or a closed neutral card description, never a suffix after a name.
    """
    from .semantic_read_subject import (_CN_OWNED_RESOURCE, _RECORD_CN_OWNED_RESOURCE,
        _EN_OWNED_RESOURCE, _RECORD_EN_OWNED_RESOURCE, _EN_FOREIGN_RESOURCE,
        _RECORD_EN_FOREIGN_RESOURCE, _RECORD_CN_FOREIGN_RESOURCE)
    from .financial_intent import _requested_period_owner, _RESOURCE_DESCRIPTION, _EN_RESOURCE_DESCRIPTION
    if any(grammar.search(message) for grammar in (_EN_FOREIGN_RESOURCE,_RECORD_EN_FOREIGN_RESOURCE,_RECORD_CN_FOREIGN_RESOURCE)):
        return False
    for grammar in (_CN_OWNED_RESOURCE,_RECORD_CN_OWNED_RESOURCE):
        for found in grammar.finditer(message):
            owner=_requested_period_owner(found['owner'].strip())
            if re.fullmatch(r'(?:我(?:自己|本人)?|本人)(?:现在|当前|目前)?',owner) is None \
                    and _RESOURCE_DESCRIPTION.fullmatch(owner) is None:
                return False
    for grammar in (_EN_OWNED_RESOURCE,_RECORD_EN_OWNED_RESOURCE):
        for found in grammar.finditer(message):
            if _EN_RESOURCE_DESCRIPTION.fullmatch(found['owner'].strip()) is None:return False
    return True


def _closed_selector_nominal(message,left,right):
    """The literal cannot hide a name just before its card noun.

    This is a source-field boundary check, not a sentence/action classifier:
    only the selected identifier's nominal prefix is checked, using the
    existing neutral card description grammar. Bare exact IDs also work.
    """
    from .financial_intent import _requested_period_owner, _RESOURCE_DESCRIPTION, _EN_RESOURCE_DESCRIPTION
    start=max(message.rfind(mark,0,left) for mark in ('，',',','。','；',';','\n'))+1
    nominal=message[start:right].strip()
    cn=_requested_period_owner(nominal)
    if _RESOURCE_DESCRIPTION.fullmatch(cn) or _EN_RESOURCE_DESCRIPTION.fullmatch(nominal):return True
    prefix=message[start:left].strip()
    if _requested_period_owner(prefix)=='' or prefix=='':return True
    return re.fullmatch(r'(?:(?:only|just|please)\s+){0,2}(?:show|check|view|read|query)\s*',prefix,re.I) is not None


def normalize_selected_read_annotations(raw,prepared):
    """All-or-nothing, literal-only v1; every unsupported shape stays strict."""
    if not isinstance(raw,dict) or not isinstance(prepared,dict) or raw.get('mode')!='REQUEST' \
            or not raw.get('read_subjects') or prepared.get('role') not in ROLES:
        return raw,[]
    from .semantic_router import SemanticDecision
    parsed=SemanticDecision.model_validate(raw)
    kinds=parsed.action_kinds;message=prepared.get('message')
    if not kinds or any(kind not in POLICIES for kind in kinds) or not isinstance(message,str) \
            or parsed.focus_ref is not None or parsed.query_reference is not None or parsed.query_base=='CURRENT' \
            or parsed.entity_refs or parsed.read_targets or any(change.entity_ref is not None for change in parsed.changes):
        return raw,[]
    capabilities=_registered_capabilities(kinds,prepared)
    if capabilities is None:return raw,[]
    subjects=parsed.read_subjects;seen=set()
    for subject in subjects:
        found=re.fullmatch(r'new:([0-7])',subject.action_ref)
        if found is None or int(found[1])>=len(kinds) or subject.action_ref in seen \
                or subject.subject!='SELF' or subject.self_anchor is not None or subject.owner_references \
                or subject.source_span.model_dump()!={'start':0,'end':len(message)}:
            return raw,[]
        seen.add(subject.action_ref)
    # All selected actions, not just an annotated subset, retain one explicit
    # current selector. No legacy default, target set or entity hint enters v1.
    selectors={};pairs=set()
    for change in parsed.changes:
        found=re.fullmatch(r'new:([0-7])',change.action_ref)
        pair=(change.action_ref,change.field)
        if found is None or int(found[1])>=len(kinds) or pair in pairs or change.operation!='SET' \
                or change.field not in capabilities[kinds[int(found[1])]]:
            return raw,[]
        pairs.add(pair)
        if change.field not in _IDENTITIES:continue
        if change.field not in {'card_number','card_reference'} or change.selected_options or change.action_ref in selectors:
            return raw,[]
        left,right=change.source_span.start,change.source_span.end
        if not 0<=left<right<=len(message) or right-left!=16:return raw,[]
        text=message[left:right]
        if re.fullmatch(CARD_NUMBER_PATTERN,text) is None or change.source_text is not None and change.source_text!=text:
            return raw,[]
        selectors[change.action_ref]=(left,right,text,change.field)
    if set(selectors)!={f'new:{index}' for index in range(len(kinds))}:return raw,[]
    tokens=[(row.start(),row.end(),row[0]) for row in _CARD.finditer(message)]
    selected=[value[:3] for value in selectors.values()]
    # Each action has one selector. Several already selected READ actions may
    # deliberately share that same current literal without selecting a new
    # card or changing their independent source/permission checks.
    if len({token[2].casefold() for token in tokens})!=len(tokens) or set(tokens)!=set(selected):return raw,[]
    # Never turn external text, another possessor or a denied READ into a
    # default-current-actor query by deleting its optional metadata.
    from .intelligence import _source_spans, _private_read_veto
    from .semantic_read_subject import _QUOTED, _SELF_DENIAL, _trusted_literal, ReadSubjectLiteral
    if _source_spans(message) or _QUOTED.search(message) or _SELF_DENIAL.search(message) \
            or _private_read_veto(message) or _denied_read_source(message) or _CONDITIONAL.search(message) \
            or _OWNERSHIP_ASSERTION.search(message) or not _closed_owner_nominals(message) \
            or any(not _closed_selector_nominal(message,left,right) for left,right,_text,_field in selectors.values()):
        return raw,[]
    from .semantic_proposal import _request_boundary
    from .contracts import DomainError
    try:
        for reference,(left,right,text,_field) in selectors.items():
            _trusted_literal(ReadSubjectLiteral(source_span={'start':left,'end':right},source_text=text),message)
            _request_boundary(message,kinds[int(reference[4:])],[{'start':left,'end':right}])
    except (ValueError,DomainError):
        return raw,[]
    result=deepcopy(raw);result.pop('read_subjects')
    from .planning import canonical_hash
    if canonical_hash({key:value for key,value in raw.items() if key!='read_subjects'})!=canonical_hash(result):
        raise ValueError('SEMANTIC_INERT_ANNOTATION_PROJECTION_CHANGED')
    return result,[{'event_kind':'REDUNDANT_SELF_READ_ANNOTATION_REMOVED','version':VERSION,
        'policy_version':RESOURCE_POLICY_VERSION,'subject_policy':SELF_RECORDS,'action_ref':subject.action_ref,
        'action_kind':kinds[int(subject.action_ref[4:])],'selector_field':selectors[subject.action_ref][3],
        'source_span':{'start':selectors[subject.action_ref][0],'end':selectors[subject.action_ref][1]},
        'non_annotation_fields_equal':True,'bank_write_authorized':False} for subject in subjects]
