"""Card number and tail are one READ identity axis, not competing owners."""

READ_CARD_KINDS=frozenset({'BILL_ANALYSIS','CARD_STATUS','CREDIT_VIEW','CREDIT_STATEMENT'})


def replace_inherited_card_alias(action, field, value, explicit_fields, *, inherits_scope):
    """Discard only a conflicting inherited alias after a source-checked SET.

    Equal aliases preserve the original proposal shape for historical replay.
    Explicit current constraints, WRITE slots and all nonidentity filters are
    untouched. Ambiguous tails still fail the ordinary owned-card resolver.
    """
    if not inherits_scope or action.get('kind') not in READ_CARD_KINDS or field not in {'card_number','card_last4'}:
        return
    other='card_last4' if field=='card_number' else 'card_number'
    old=action['slots'].get(other)
    if other in explicit_fields or not isinstance(old,str) or not isinstance(value,str):
        return
    full,tail=(value,old) if field=='card_number' else (old,value)
    if not full.casefold().endswith(tail.casefold()):
        action['slots'].pop(other,None)
