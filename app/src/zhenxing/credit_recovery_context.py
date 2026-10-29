"""Offer one verified READ-only card after a partial credit repayment workflow."""
from .contracts import DomainError


def append_partial_repayment_reads(session, principal, thread, parent, before_sequence,
        model, offered, frame_ref, capacity):
    from .semantic_credit_identity import repayment_read_recovery_identities
    from .semantic_offered_contracts import completed_identity_contract
    try:
        identities = repayment_read_recovery_identities(session, principal, thread, parent, before_sequence)
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return
    for identity in identities:
        if len(model['entities']) >= capacity:
            break
        entity_ref = 'e'+str(len(model['entities']))
        model['entities'].append({'entity_ref': entity_ref, 'frame_ref': frame_ref, 'domain': 'CREDIT',
            'kind': identity['source_kind'], 'label': identity['label'], 'ordinal': None,
            'field': 'card_reference', 'read_kind': 'CREDIT_VIEW', 'allowed_fields': ['card_reference'],
            'available_aspects': ['BALANCE'], **completed_identity_contract(identity['source_kind'])})
        offered['entity_bindings'][entity_ref] = identity
