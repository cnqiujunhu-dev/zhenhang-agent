"""Project finite inert annotations, never a payment or ownership requirement."""
from copy import deepcopy
import re
from .semantic_inert_write_policies import (POLICIES, matching_policy, affirmative_projection_source,
    annotation_owner_audit_view, VERSION as POLICY_VERSION)

VERSION = 'finite-inert-read-annotation-v3'
ACTION = 'OWN_ACCOUNT_TRANSFER'
TOOL = 'own.account.transfer.v1'


def normalize_inert_read_annotations(raw, prepared):
    """Strict allowlist: a trusted, complete SELF bank-resource annotation.

    Missing proof, another subject, an unknown/duplicate reference or an action
    outside POLICIES is not repaired. No parameter, source or context is inferred.
    """
    if not isinstance(raw,dict) or raw.get('mode') != 'REQUEST' or not raw.get('read_subjects') \
            or prepared.get('role') not in {'CUSTOMER','DEV_TEST'}:
        return raw, []
    from .semantic_router import SemanticDecision
    parsed = SemanticDecision.model_validate(raw)
    kinds = parsed.action_kinds
    if not any(kind in POLICIES for kind in kinds): return raw, []
    offered = {row['kind']: row for row in prepared.get('capabilities', [])}
    for kind in set(kinds).intersection(POLICIES):
        policy = POLICIES[kind]
        if policy.allowed and matching_policy(kind, offered.get(kind, {}), prepared.get('role')) is None:
            return raw, []
    refs = [row.action_ref for row in parsed.read_subjects]
    if len(set(refs)) != len(refs): return raw, []
    indices = []
    for reference in refs:
        match = re.fullmatch(r'new:([0-7])', reference)
        if match is None or int(match[1]) >= len(kinds): return raw, []
        indices.append(int(match[1]))
    message = prepared['message']
    from .semantic_read_subject import _trusted_literal, _resource_owner_audit, _RESOURCE_REFERENCE
    from .semantic_read_resources import self_resource_matches
    from .semantic_proposal import _request_boundary
    from .contracts import DomainError
    removed = []
    for position, (subject, ordinal) in enumerate(zip(parsed.read_subjects, indices)):
        action = kinds[ordinal]
        policy = matching_policy(action, offered.get(action, {}), prepared.get('role'))
        if policy is None: continue
        if subject.subject != 'SELF' or subject.self_anchor is None \
                or subject.source_span.model_dump() != {'start':0,'end':len(message)}:
            return raw, []
        anchor = subject.self_anchor
        if message[anchor.source_span.start:anchor.source_span.end] != anchor.source_text:
            return raw, []
        matching = [row for row in self_resource_matches(message) if set(policy.resource_types).intersection(row.resource_types)
            and (row.start,row.end,row.text) == (anchor.source_span.start,anchor.source_span.end,anchor.source_text)]
        if len(matching) != 1: return raw, []
        try:
            anchor = _trusted_literal(anchor,message)
            references = []; ranges = [(anchor.source_span.start,anchor.source_span.end)]
            for reference in subject.owner_references:
                left,right = reference.source_span.start,reference.source_span.end
                if message[left:right] != reference.source_text or not _RESOURCE_REFERENCE.fullmatch(reference.source_text) \
                        or left < anchor.source_span.end or any(left < end and right > start for start,end in ranges):
                    return raw, []
                references.append(_trusted_literal(reference,message)); ranges.append((left,right))
            _resource_owner_audit(annotation_owner_audit_view(message,policy),anchor,references)
            changes = [row for row in parsed.changes if row.action_ref == subject.action_ref]
            if any(row.operation != 'SET' or not 0 <= row.source_span.start < row.source_span.end <= len(message)
                    or row.source_text is not None and message[row.source_span.start:row.source_span.end] != row.source_text for row in changes):
                return raw, []
            if not affirmative_projection_source(message, policy, anchor, changes, action):
                return raw, []
            _request_boundary(message,action,[row.source_span.model_dump() for row in changes])
        except (ValueError, DomainError):
            return raw, []
        removed.append(position)
    if not removed: return raw, []
    result = deepcopy(raw)
    result['read_subjects'] = [deepcopy(row) for index,row in enumerate(raw['read_subjects']) if index not in removed]
    before = {key:value for key,value in raw.items() if key != 'read_subjects'}
    after = {key:value for key,value in result.items() if key != 'read_subjects'}
    from .planning import canonical_hash
    if canonical_hash(before) != canonical_hash(after): raise ValueError('SEMANTIC_INERT_ANNOTATION_PROJECTION_CHANGED')
    events = [{'event_kind':'INERT_ACTION_READ_ANNOTATION_REMOVED','version':VERSION,
        'policy_version':POLICY_VERSION, 'policy_group':POLICIES[kinds[indices[index]]].group,
        'action_ref':refs[index],'action_kind':kinds[indices[index]],'bank_write_authorized':False} for index in removed]
    return result, events
