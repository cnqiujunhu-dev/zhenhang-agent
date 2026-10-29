"""Decode one source-bound card identifier, never classify a request.

The model may select a complete noun phrase ("尾号abcd借记卡") instead of
bare digits. Only a closed, unambiguous identifier with harmless type/label
decorators is accepted. Ownership and card controls remain domain checks.
"""
import re


_TOKEN = r'(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[0-9a-fA-F]{4}'
_DECORATOR = r'(?:我的|本人|自己的|的|这张|那张|尾号|末四位|后四位|卡号|号码|银行卡|借记卡|储蓄卡|信用卡|卡|my|own|debit|credit|bank|card|ending|in|number|last|four|digits)'
# First-person ownership may directly precede a closed identifier/type label
# ("我尾号abcd的借记卡"). Keep it at the noun's beginning, never strip an
# arbitrary pronoun, clause, negation, amount, or second card from the source.
_FIRST_PERSON = r'(?:我(?=\s*(?:尾号|末四位|后四位|卡号|号码|银行卡|借记卡|储蓄卡|信用卡|卡)))?'
_CLOSED = re.compile(r'^'+_FIRST_PERSON+r'(?:\s|[：:#“”「」"\x27]|'+_DECORATOR+r')*(?P<identifier>'+_TOKEN+r')(?:\s|[“”「」"\x27]|'+_DECORATOR+r')*$', re.I)


def card_reference_literal(raw):
    if not isinstance(raw, str) or not 4 <= len(raw) <= 120:
        raise ValueError('CARD_REFERENCE_LITERAL_INVALID')
    match = _CLOSED.fullmatch(raw.strip())
    if match is None:
        raise ValueError('CARD_REFERENCE_LITERAL_INVALID')
    identifier = match['identifier']
    # Preserve the exact hexadecimal spelling for source provenance. Lookup
    # performs its own case-insensitive identity check; only the fixed issuer
    # prefix has a canonical spelling in the public card-number schema.
    return identifier[:8].lower()+identifier[8:] if len(identifier)==16 else identifier
