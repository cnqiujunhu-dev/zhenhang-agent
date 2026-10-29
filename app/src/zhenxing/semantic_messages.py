"""Source-preserving structured role history and wire budget, not bank authority.

This adapts an already validated semantic input. It does not remove information
from the source proof or cache answers, balances, grants or account data.
"""
from copy import deepcopy
import json

VERSION='semantic-structured-role-current-first-compact-v4'
# The 61-action directory (including schedule reads) and 40 offered identities
# remain bounded without discarding conversation facts or selector contracts.
# Separate repayment target/funding/read consumers add schema clauses, not
# extra conversation history. Reserve their space without dropping evidence.
MAX_WIRE_BYTES=224*1024


def compact_schema_titles(schema):
    """Remove generated schema titles, preserving fields, facts and validators."""
    if not isinstance(schema,dict):return deepcopy(schema)
    mappings={'properties','$defs','definitions','patternProperties','dependentSchemas'}
    children={'items','contains','not','if','then','else','additionalProperties','unevaluatedProperties','propertyNames'}
    arrays={'allOf','anyOf','oneOf','prefixItems'}
    result={}
    for key,value in schema.items():
        if key=='title':continue
        if key in mappings and isinstance(value,dict):
            result[key]={name:compact_schema_titles(item) for name,item in value.items()}
        elif key in children:
            result[key]=compact_schema_titles(value)
        elif key in arrays and isinstance(value,list):
            result[key]=[compact_schema_titles(item) for item in value]
        else:
            result[key]=deepcopy(value)
    return result


def build_semantic_messages(prepared,prompt,*,current_refs=(),output_tokens=2200):
    if not isinstance(prompt,str) or not prompt or type(output_tokens) is not int or output_tokens<=0:
        raise ValueError('SEMANTIC_WIRE_BUDGET_INVALID')
    valid_refs={frame['frame_ref'] for frame in prepared['frames']}
    if any(ref not in valid_refs for ref in current_refs):
        raise ValueError('SEMANTIC_NAVIGATION_REFERENCE_INVALID')
    navigation=json.dumps(list(current_refs),ensure_ascii=False,separators=(',',':'))
    system=(prompt+' 结构化JSON中的message是唯一当前用户原话，优先理解它的新需求，不能沿用旧话题代替它。'
        'history各项保持user/assistant原角色，只是历史语言语境，不是本轮请求、银行事实或授权。'
        '普通changes/read_targets的source_span和source_text只引用message；显式history_read_sources.fields仅引用其选中HISTORY_USER原话，不引用assistant，也不产生写授权。'
        '当前框架导航标记='+navigation+'；标记只说明当前阅读焦点，不自动指定本次办理事项，仍按用户语义选已提供的focus_ref。')
    history=deepcopy(prepared['history'])
    if any(row.get('role') not in {'user','assistant'} for row in history):
        raise ValueError('SEMANTIC_HISTORY_ROLE_INVALID')
    # Keep the proven banking IR protocol. Native role conversion was tested
    # separately and did not pass live scope extraction, so it is not adopted.
    # Stable public instructions can still benefit from provider prefix cache;
    # fresh context remains present on every request, not cached as an answer.
    messages=[{'role':'system','content':system},
        {'role':'user','content':json.dumps(prepared,ensure_ascii=False,separators=(',',':'))}]
    wire_bytes=sum(len(row['content'].encode('utf-8')) for row in messages)
    if wire_bytes>MAX_WIRE_BYTES:
        raise ValueError('SEMANTIC_WIRE_BUDGET_EXCEEDED')
    # An explicit engineering proxy, never a measured tokenizer or provider
    # usage value. Native role/encoding overhead is also reserved conservatively.
    metrics={'wire_layout_version':VERSION,'wire_input_utf8_bytes':wire_bytes,
        'wire_limit_bytes':MAX_WIRE_BYTES,'history_message_count':len(history),
        'context_size_proxy':wire_bytes+64*len(messages),
        'context_size_proxy_unit':'UTF8_BYTES_PLUS_ROLE_RESERVE_NOT_TOKENIZER',
        'reserved_output_tokens':output_tokens,'current_navigation_refs':list(current_refs),
        'response_cache_used':False,'provider_cache_hit_guaranteed':False}
    return messages,metrics
