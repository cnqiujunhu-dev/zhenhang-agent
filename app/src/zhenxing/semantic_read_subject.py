"""Source-bound ownership for private READs, never a new bank authority.

The semantic model chooses the subject once. This codec proves source-bound
resource references and possessors composed only of complete current-self
resources. Only that owner phrase is projected for the existing foreign-owner
guard; unselected text and the business decision stay unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from .semantic_read_resources import (PRIVATE_READ_KINDS, LEGACY_PRIVATE_READ_KINDS,
    LEGACY_CN_RESOURCE, LEGACY_EN_RESOURCE, LEGACY_SELF_ANCHOR,
    RECORD_CN_RESOURCE, RECORD_EN_RESOURCE, CN_RESOURCE_MODIFIER, EN_RESOURCE_MODIFIER,
    compatible_resource_types, self_resource_matches)


VERSION = 'source-bound-private-read-subject-v1'
NORMALIZATION_VERSION = 'source-bound-private-read-subject-completion-v1'


class ReadSubjectSpan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class ReadSubjectLiteral(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    source_span: ReadSubjectSpan
    source_text: str = Field(min_length=1, max_length=100)


class SemanticReadSubject(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    action_ref: str = Field(min_length=1, max_length=100)
    subject: Literal['SELF', 'OTHER', 'MIXED', 'UNRESOLVED']
    source_span: ReadSubjectSpan
    self_anchor: ReadSubjectLiteral | None = Field(default=None, exclude_if=lambda value: value is None)
    owner_references: list[ReadSubjectLiteral] = Field(default_factory=list, max_length=8,
        exclude_if=lambda value: not value)


# These are field grammars, not sentences, actions, owner names, or a question
# classifier. Human references never prove ownership. The ambiguous English
# plural "their" is offered only with the separate same-clause bank-resource
# antecedent proof below, and still must be explicitly selected by the model.
_CN_RESOURCE = LEGACY_CN_RESOURCE
_EN_RESOURCE = LEGACY_EN_RESOURCE
_SELF_ANCHOR = LEGACY_SELF_ANCHOR
_RESOURCE_REFERENCE = re.compile(r'(?:它(?:们)?(?:各自|分别)?|(?:这|那)(?:些|两|几)(?:张|个)?(?:各自|分别)?|'
    r'(?:each|both|either|all)(?:\s+of)?\s+(?:these|those)|these|those|its|it|their)', re.I)
_TOKEN = re.compile(r'[A-Za-z0-9_-]')
_QUOTED = re.compile(r'''"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘(?:[^’\n]|(?<=\w)’(?=\w))*’(?!\w)|`[^`]*`|(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))*'(?!\w)''')
_CLAUSE = re.compile(r'[，,。；;!?！？\n]')
_NEGATED_OWNER = re.compile(r'(?:不是|并非|不属于|不是属于|并不是|非本人|非我|not|isn[’\x27]?t|aren[’\x27]?t|'
    r'don[’\x27]?t|doesn[’\x27]?t|no\s+longer)(?:\s|的|属于|owned\s+by|belong(?:s)?\s+to){0,4}$', re.I)
_NEGATED_HOLDING = re.compile(r'^\s*(?:并不|不|没有|没|未)(?:拥有|持有|有)|'
    r'^\s*(?:do\s+not|don[’\x27]?t|never)\s+(?:own|have|hold)\b', re.I)
_SELF_DENIAL = re.compile(r'(?:不属于|不是属于|并不属于|并不是|不是|并非)\s*(?:我(?:自己|本人)?|本人)(?:的)?|'
    r'\b(?:not|isn[’\x27]?t|aren[’\x27]?t|wasn[’\x27]?t|weren[’\x27]?t)\s+(?:mine|owned\s+by\s+me)\b|'
    r'\b(?:do|does|did)(?:\s+not|n[’\x27]t)\s+belong\s+to\s+me\b', re.I)
_CN_QUALIFIERS = (r'(?:(?:' + CN_RESOURCE_MODIFIER
    + r'|各自|分别|人民币|美元|CNY|USD|这|那|些|[一二两三四五六七八九十\d]+(?:张|个|项|笔)?)(?:的)?\s*){0,8}')
_PRIVATE_CN_RESOURCE = r'(?:' + _CN_RESOURCE + r'|余额|可用额度|授信额度|额度|欠款|债务)'
_CN_OWNED_RESOURCE = re.compile(r'(?P<owner>[^，,。；;!?！？\n]{1,160}?)的\s*' + _CN_QUALIFIERS
    + r'(?P<resource>' + _PRIVATE_CN_RESOURCE + ')', re.I)
_EN_QUALIFIERS = r'(?:(?:two|both|all|three|' + EN_RESOURCE_MODIFIER + r'|CNY|USD|[1-9]\d*)\s+){0,8}'
_EN_PRIVATE_RESOURCE = (r'(?:' + _EN_RESOURCE
    + r'|(?:(?:bank|account|wallet|credit\s+card)\s+)?balances?|credit\s+limit|debt)')
_EN_OWNED_RESOURCE = re.compile(r"(?P<owner>[A-Za-z][A-Za-z0-9_. -]{0,120}?)['’]s\s+" + _EN_QUALIFIERS
    + r'(?P<resource>' + _EN_PRIVATE_RESOURCE + ')', re.I)
_EN_FOREIGN_RESOURCE = re.compile(r'\b(?:his|her|their|someone\s+else[\x27’]s|another\s+user[\x27’]s)\s+'
    + _EN_QUALIFIERS + _EN_PRIVATE_RESOURCE + r'\b', re.I)
_RECORD_CN_OWNED_RESOURCE = re.compile(r'(?P<owner>[^，,。；;!?！？\n]{1,160}?)的\s*' + _CN_QUALIFIERS
    + r'(?P<resource>' + RECORD_CN_RESOURCE + ')', re.I)
_RECORD_EN_OWNED_RESOURCE = re.compile(r"(?P<owner>[A-Za-z][A-Za-z0-9_. -]{0,120}?)(?:['’]s|s['’])\s+"
    + _EN_QUALIFIERS + r'(?P<resource>' + RECORD_EN_RESOURCE + ')', re.I)
_RECORD_EN_FOREIGN_RESOURCE = re.compile(r'\b(?:his|her|their|someone\s+else[\x27’]s|another\s+user[\x27’]s)\s+'
    + _EN_QUALIFIERS + RECORD_EN_RESOURCE + r'\b', re.I)
_RECORD_CN_FOREIGN_RESOURCE = re.compile(r'(?:朋友|他人|别人|同事|房东|家人|家庭成员|女儿|儿子|孩子|他|她|他们|她们)'
    + r'(?:本人)?(?:的)?' + _CN_QUALIFIERS + RECORD_CN_RESOURCE, re.I)
_RECORD_CN_NAMED_STATUS_RESOURCE = re.compile(r'(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{0,60}(?:本人)?'
    + r'(?:(?:' + CN_RESOURCE_MODIFIER + r')(?:的)?){1,4}' + RECORD_CN_RESOURCE, re.I)
_CONNECTOR = r'(?:以及|还有|同时|并且|和|与|及|并|的|各自|分别|\band\b|\bof\b|\s)'
_ONLY_SELF_REFERENCES = re.compile(r'(?:' + _CONNECTOR + r')*(?:\x00(?:' + _CONNECTOR + r')*)+', re.I)
_ONLY_CONNECTORS = re.compile(r'(?:' + _CONNECTOR + r')*', re.I)
_LEADING_COOWNER = re.compile(r'(?P<left>.+?)\s*(?:以及|还有|和|与|及|\band\b)\s*$', re.I)


def _fail(code):
    raise ValueError(code)


def _literal(value, message):
    """Same unique exact-current-text rebinding as ordinary field changes."""
    span = value.source_span
    start, end = span.start, span.end
    if message[start:end] != value.source_text:
        start = message.find(value.source_text)
        if start < 0 or message.find(value.source_text, start + 1) >= 0:
            _fail('SEMANTIC_READ_SUBJECT_SOURCE_NOT_UNIQUE')
        end = start + len(value.source_text)
    if not 0 <= start < end <= len(message):
        _fail('SEMANTIC_READ_SUBJECT_SOURCE_INVALID')
    # Do not turn the "my" in an identifier or another possessive word into
    # first person.  Chinese source boundaries are checked by the field grammar.
    if value.source_text.isascii() and ((start and _TOKEN.fullmatch(message[start - 1]))
            or (end < len(message) and _TOKEN.fullmatch(message[end]))):
        _fail('SEMANTIC_READ_SUBJECT_SOURCE_INCOMPLETE')
    return value.model_copy(update={'source_span': ReadSubjectSpan(start=start, end=end)})


def _trusted_literal(value, message):
    value = _literal(value, message)
    start, end = value.source_span.start, value.source_span.end
    from .intelligence import _source_spans
    if any(start < right and end > left for left, right in _source_spans(message)) \
            or any(start < quoted.end() and end > quoted.start() for quoted in _QUOTED.finditer(message)):
        _fail('SEMANTIC_READ_SUBJECT_UNTRUSTED_SOURCE')
    before = _CLAUSE.split(message[:start])[-1]
    after = _CLAUSE.split(message[end:])[0]
    if _NEGATED_OWNER.search(before) or _NEGATED_HOLDING.search(after):
        _fail('SEMANTIC_READ_SUBJECT_NEGATED_OWNER')
    return value


def _action_map(value, prepared):
    if value.get('mode') == 'REQUEST':
        return {'new:' + str(index): kind for index, kind in enumerate(value.get('action_kinds', []))}
    if value.get('mode') in {'CONTINUE', 'REVISE'}:
        frame = next((row for row in prepared.get('frames', []) if row['frame_ref'] == value.get('focus_ref')), {})
        return {row['action_ref']: row['kind'] for row in frame.get('actions', [])}
    return {}


def offered_subject_literals(message, kind=None):
    """Finite literal candidates, not a subject/intent/action decision.

    This helps the model copy complete source spans without calculating Chinese
    offsets. Candidates do not mean the user selected them and cannot remove
    an unselected owner from the refusal guard.
    """
    def collect(grammar):
        rows = []
        for found in grammar.finditer(message):
            raw = ReadSubjectLiteral(source_span=ReadSubjectSpan(start=found.start(), end=found.end()),
                source_text=found[0])
            try: checked = _trusted_literal(raw, message)
            except ValueError: continue
            if checked.source_text.casefold() == 'their' and not _proved_plural_bank_reference(
                    message, checked.source_span.start, checked.source_span.end):
                continue
            rows.append(checked.model_dump())
            if len(rows) >= 8: break
        return rows
    anchors=[]
    for found in self_resource_matches(message, kind):
        raw=ReadSubjectLiteral(source_span=ReadSubjectSpan(start=found.start,end=found.end),source_text=found.text)
        try:checked=_trusted_literal(raw,message)
        except ValueError:continue
        anchors.append(checked.model_dump())
        if len(anchors)>=8:break
    return anchors, collect(_RESOURCE_REFERENCE)


def _trusted_owner_message(message):
    """Equal-length source mask; external text is not an owner's assertion."""
    from .intelligence import _source_spans
    ranges = [*_source_spans(message), *(found.span() for found in _QUOTED.finditer(message))]
    chars = list(message)
    for left, right in ranges:
        for index in range(left, right):
            # Preserve separators as well as all coordinates so two adjacent
            # customer clauses cannot merge across an external quotation.
            if chars[index] not in '\r\n，,。；;!?！？':
                chars[index] = ' '
    return ''.join(chars)


def _proved_plural_bank_reference(message, start, end):
    """Prove a grammatical resource possessor, not an actor or bank access.

    Only a complete current-self plural bank noun, followed by connectors and
    the selected pronoun plus a bank property in the same trusted clause, is
    eligible. Human names, a different sentence, quotes, negation, singular
    resources and saved family records cannot supply this antecedent.
    """
    if message[start:end].casefold() != 'their':
        return False
    trusted = _trusted_owner_message(message)
    if trusted[start:end].casefold() != 'their' or not re.match(
            r'\s+(?:balances?|credit\s+limits?|debts?)\b', trusted[end:], re.I):
        return False
    candidates = [row for row in self_resource_matches(trusted)
        if 'BANK_INSTRUMENT' in row.resource_types and row.end <= start
        and re.search(r'\b(?:cards|accounts|wallets)$', row.text, re.I)
        and _ONLY_CONNECTORS.fullmatch(trusted[row.end:start])]
    if len(candidates) != 1:
        return False
    antecedent = candidates[0]
    try:
        _trusted_literal(ReadSubjectLiteral(source_span=ReadSubjectSpan(
            start=antecedent.start, end=antecedent.end), source_text=antecedent.text), message)
    except ValueError:
        return False
    return True


def _resource_owner_audit(message, anchor, references):
    """All explicit owner/resource relations must remain current-self.

    Looking only beside "balance" is insufficient: "my cards and John's
    cards; their balances" already changed the resource antecedent.  Inspect
    possessive resource fields throughout the complete current source without
    classifying a question or extracting a new bank action.
    """
    trusted = _trusted_owner_message(message)
    if _SELF_DENIAL.search(trusted):
        _fail('SEMANTIC_READ_SUBJECT_NEGATED_OWNER')
    # Other complete, explicit self-resource noun phrases are the same current
    # actor, not new permissions.  Each is source/quote/polarity checked anew.
    own_ranges = []; typed_bank_ranges=[]
    for found in self_resource_matches(trusted):
        candidate = ReadSubjectLiteral(source_span=ReadSubjectSpan(start=found.start, end=found.end),
            source_text=found.text)
        try: candidate = _trusted_literal(candidate, message)
        except ValueError: continue
        own_ranges.append((candidate.source_span.start, candidate.source_span.end))
        if 'BANK_INSTRUMENT' in found.resource_types:
            typed_bank_ranges.append((candidate.source_span.start,candidate.source_span.end))
    legacy_ranges=[]
    for found in _SELF_ANCHOR.finditer(trusted):
        candidate=ReadSubjectLiteral(source_span=ReadSubjectSpan(start=found.start(),end=found.end()),source_text=found[0])
        try:candidate=_trusted_literal(candidate,message)
        except ValueError:continue
        legacy_ranges.append((candidate.source_span.start,candidate.source_span.end))
    legacy_ranges.extend(typed_bank_ranges)
    if (anchor.source_span.start, anchor.source_span.end) not in own_ranges:
        _fail('SEMANTIC_READ_SUBJECT_SELF_ANCHOR_INVALID')
    for left, _right in own_ranges:
        prefix = _CLAUSE.split(trusted[:left])[-1]
        if _ONLY_CONNECTORS.fullmatch(prefix):
            continue
        coordination = _LEADING_COOWNER.fullmatch(prefix)
        if coordination:
            # Coordination of two separately explicit self resources is fine;
            # an unproved possessor before "and my cards" is not.
            previous_end = left - len(prefix) + len(coordination['left'].rstrip())
            if not any(end == previous_end for _start, end in own_ranges):
                _fail('SEMANTIC_READ_SUBJECT_UNPROVEN_RESOURCE_OWNER')
    for grammar in (_EN_FOREIGN_RESOURCE, _RECORD_EN_FOREIGN_RESOURCE):
        for found in grammar.finditer(trusted):
            selected = any(row.source_text.casefold() == 'their'
                and row.source_span.start == found.start()
                and _proved_plural_bank_reference(message, row.source_span.start, row.source_span.end)
                for row in references)
            if not selected:
                _fail('SEMANTIC_READ_SUBJECT_UNPROVEN_RESOURCE_OWNER')
    for grammar in (_RECORD_CN_FOREIGN_RESOURCE,_RECORD_CN_NAMED_STATUS_RESOURCE):
        for found in grammar.finditer(trusted):
            # "my family birthday records" may contain a family noun. Only
            # its fully proved own record phrase covers that noun; a nearby
            # own card/subscription never covers another person's records.
            if not any(left<=found.start()<found.end()<=right for left,right in own_ranges):
                _fail('SEMANTIC_READ_SUBJECT_UNPROVEN_RESOURCE_OWNER')
    projections = []
    for grammar in (_CN_OWNED_RESOURCE, _EN_OWNED_RESOURCE, _RECORD_CN_OWNED_RESOURCE, _RECORD_EN_OWNED_RESOURCE):
        for found in grammar.finditer(trusted):
            start, end = found.start('resource'), found.end('resource')
            covered = next(((left, right) for left, right in own_ranges if left <= start < end <= right), None)
            if covered:
                continue
            owner_start, owner_end = found.start('owner'), found.end('owner')
            if grammar in (_CN_OWNED_RESOURCE, _RECORD_CN_OWNED_RESOURCE):
                # A prior possessive can end an earlier regex match, e.g.
                # "my [cards] each's balance". Prove its complete same-clause
                # resource composition, not just the leftover "each" suffix.
                boundaries = list(_CLAUSE.finditer(trusted, 0, owner_end))
                owner_start = boundaries[-1].end() if boundaries else 0
            owner = trusted[owner_start:owner_end]
            # A saved family/person profile is an owned record, NOT proof the
            # person's bank resources belong to this actor. Only original bank
            # resource antecedents may possess a financial resource here.
            financial = grammar in (_CN_OWNED_RESOURCE, _EN_OWNED_RESOURCE) or re.fullmatch(
                r'(?:' + _PRIVATE_CN_RESOURCE + '|' + _EN_PRIVATE_RESOURCE + ')', found['resource'], re.I)
            owner_ranges = legacy_ranges if financial else own_ranges
            explicit = [(left, right) for left, right in owner_ranges if owner_start <= left < right <= owner_end]
            components = []
            ranges = [*explicit, *((row.source_span.start, row.source_span.end) for row in references)]
            for left, right in sorted(set(ranges), key=lambda pair: (pair[0], -pair[1])):
                if owner_start <= left < right <= owner_end and not any(a <= left < right <= b for a, b in components):
                    components.append((left, right))
            for left, right in reversed(components):
                # NUL marks only a separately source/quote/polarity-checked
                # self-resource or selected non-person reference. Unknown
                # owner names and every remaining modifier must still match.
                owner = owner[:left - owner_start] + '\x00' + owner[right - owner_start:]
            from .financial_intent import _requested_period_owner
            owner = _requested_period_owner(owner)
            if not _ONLY_SELF_REFERENCES.fullmatch(owner):
                _fail('SEMANTIC_READ_SUBJECT_UNPROVEN_RESOURCE_OWNER')
            if grammar is _CN_OWNED_RESOURCE and explicit:
                # e.g. [my two cards] + each + possessive + balance. This is
                # only the proven resource possessor, not its request verb,
                # field value, another owner's clause, or a business selector.
                projections.append((min(left for left, _right in explicit), owner_end))
    return projections


def validate_read_subjects(subjects, value, prepared):
    """Validate optional proof without changing older decision serialization."""
    if not subjects:
        return subjects
    actions = _action_map(value, prepared)
    offered = {row['kind']: row for row in prepared.get('capabilities', [])}
    if prepared.get('role') in {'FAMILY_HELPER', 'ADMIN'} or not actions:
        _fail('SEMANTIC_READ_SUBJECT_PRIVATE_READ_ONLY')
    message = prepared['message']
    seen = set()
    result = []
    for raw in subjects:
        subject = raw if isinstance(raw, SemanticReadSubject) else SemanticReadSubject.model_validate(raw)
        if subject.action_ref in seen or actions.get(subject.action_ref) not in PRIVATE_READ_KINDS \
                or offered.get(actions.get(subject.action_ref), {}).get('read_only') is not True:
            _fail('SEMANTIC_READ_SUBJECT_ACTION_INVALID')
        seen.add(subject.action_ref)
        # Full current scope prevents the model from selecting a convenient
        # self-only clause and dropping the rest of a mixed-owner request.
        if (subject.source_span.start, subject.source_span.end) != (0, len(message)):
            _fail('SEMANTIC_READ_SUBJECT_SCOPE_INCOMPLETE')
        if subject.subject != 'SELF':
            if subject.self_anchor is not None or subject.owner_references:
                _fail('SEMANTIC_READ_SUBJECT_CONFLICT')
            result.append(subject)
            continue
        if subject.self_anchor is None:
            _fail('SEMANTIC_READ_SUBJECT_SELF_ANCHOR_REQUIRED')
        anchor = _trusted_literal(subject.self_anchor, message)
        # An isolated "I/my/我" is only the speaker, not proof that a later
        # pronoun is their resource. Require the complete explicit owner-
        # resource noun phrase before any reference may be canonicalized.
        kind=actions[subject.action_ref]
        legacy_complete=kind in LEGACY_PRIVATE_READ_KINDS and _SELF_ANCHOR.fullmatch(anchor.source_text)
        if not legacy_complete and not compatible_resource_types(kind,anchor.source_text):
            _fail('SEMANTIC_READ_SUBJECT_SELF_ANCHOR_INVALID')
        references = []
        ranges = [(anchor.source_span.start, anchor.source_span.end)]
        for raw_reference in subject.owner_references:
            reference = _trusted_literal(raw_reference, message)
            start, end = reference.source_span.start, reference.source_span.end
            if not _RESOURCE_REFERENCE.fullmatch(reference.source_text) or start < anchor.source_span.end \
                    or reference.source_text.casefold() == 'their' and not _proved_plural_bank_reference(message, start, end):
                _fail('SEMANTIC_READ_SUBJECT_RESOURCE_REFERENCE_INVALID')
            if any(start < right and end > left for left, right in ranges):
                _fail('SEMANTIC_READ_SUBJECT_SOURCE_OVERLAP')
            ranges.append((start, end))
            references.append(reference)
        _resource_owner_audit(message, anchor, references)
        result.append(subject.model_copy(update={'self_anchor': anchor, 'owner_references': references}))
    return result


def validate_executable_read_subjects(subjects,value,prepared):
    """Reject at the semantic boundary what the private READ consumer cannot use.

    Keep the older metadata decoder and runtime refusal intact. In particular,
    no nonself value is removed, completed, or reclassified as SELF here.
    """
    result=validate_read_subjects(subjects,value,prepared)
    if any(row.subject!='SELF' for row in result):_fail('SEMANTIC_READ_SUBJECT_NOT_SELF')
    return result


def normalize_read_subject_wire(raw, prepared):
    """Complete only an explicitly SELF, full-scope proof's unique anchor.

    No optional proof is invented and no action, subject, selector, reference,
    money or authority is inferred. Existing complete proofs use their exact
    old serialization. All original owner/source/polarity audits must pass
    before the canonical value or its finite diagnostic event is returned.
    """
    if not isinstance(raw,dict) or raw.get('mode')!='REQUEST' or not isinstance(raw.get('read_subjects'),list) \
            or not 1<=len(raw['read_subjects'])<=8 \
            or prepared.get('role') not in {'CUSTOMER','DEV_TEST','DEMO_OWNER'}:
        return raw,[]
    actions=_action_map(raw,prepared)
    offered={row['kind']:row for row in prepared.get('capabilities',[])}
    message=prepared['message']
    result=None;events=[]
    for index,item in enumerate(raw['read_subjects']):
        subject=SemanticReadSubject.model_validate(item)
        kind=actions.get(subject.action_ref)
        if subject.subject!='SELF' or subject.self_anchor is not None or subject.owner_references \
                or kind not in PRIVATE_READ_KINDS or offered.get(kind,{}).get('read_only') is not True \
                or (subject.source_span.start,subject.source_span.end)!=(0,len(message)):
            continue
        candidates=[]
        for literal in offered_subject_literals(message,kind)[0]:
            anchor=ReadSubjectLiteral.model_validate(literal)
            complete=subject.model_copy(update={'self_anchor':anchor})
            try:checked=validate_read_subjects([complete],raw,prepared)[0]
            except ValueError:continue
            candidates.append(checked)
        if len(candidates)!=1:continue
        complete=candidates[0]
        if result is None:result=deepcopy(raw)
        result['read_subjects'][index]={**result['read_subjects'][index], 'self_anchor':complete.self_anchor.model_dump()}
        events.append({'code':'UNIQUE_COMPATIBLE_SELF_RESOURCE_ANCHOR','version':NORMALIZATION_VERSION,
            'action_ref':subject.action_ref,'read_kind':kind,'resource_kind':compatible_resource_types(kind,complete.self_anchor.source_text)[0],
            'source_span':complete.self_anchor.source_span.model_dump(),'candidate_count':1,'bank_write_authorized':False})
    if result is None:return raw,[]
    validate_read_subjects(result['read_subjects'],result,prepared)
    return result,events


def owner_guard_message(message, subject):
    """Project only proved owners for the heuristic; keep the decision intact."""
    if subject.subject != 'SELF':
        _fail('SEMANTIC_READ_SUBJECT_NOT_SELF')
    projections = _resource_owner_audit(message, subject.self_anchor, subject.owner_references)
    replacements = [(left, right, '我') for left, right in projections]
    replacements += [(row.source_span.start, row.source_span.end, 'my' if row.source_text.isascii() else '我')
        for row in subject.owner_references]
    maximal = []
    for start, end, substitute in sorted(replacements, key=lambda row: (row[0], -row[1])):
        if any(left <= start < end <= right for left, right, _value in maximal):
            continue
        if any(start < right and end > left for left, right, _value in maximal):
            _fail('SEMANTIC_READ_SUBJECT_SOURCE_OVERLAP')
        maximal.append((start, end, substitute))
    result = message
    for start, end, substitute in reversed(maximal):
        # Other owners and every unselected clause remain available to the
        # original guard. No replacement enters slots, prompts or receipts.
        result = result[:start] + substitute + result[end:]
    return result


def checked_guard_messages(decision, prepared):
    subjects = validate_read_subjects(decision.get('read_subjects', []), decision, prepared)
    return {row.action_ref: owner_guard_message(prepared['message'], row) for row in subjects}
