"""A current no-charge instruction is a veto on paid subscription preparation.

It never chooses a merchant, action, card or future debit authorization.
"""
import re
def forbids_current_subscription_charge(message):
    if not isinstance(message,str):return False
    from .intelligence import _source_spans
    tainted=_source_spans(message)
    pattern=r'(?:不要|不许|禁止|不能|不得|不)(?:[^，。；;\n]{0,16})(?:本次|现在|立即|马上)(?:[^，。；;\n]{0,5})(?:扣款|扣费|收费)|(?:本次|现在|立即|马上)(?:不要|不许|禁止|不能|不得|不)(?:扣款|扣费|收费)|\b(?:do not|don[’\x27]t|no)\s+charg(?:e|ing)\s+(?:me\s+)?(?:now|today|this time)\b'
    return any(not any(m.start()<end and m.end()>start for start,end in tainted) for m in re.finditer(pattern,message,re.I))
