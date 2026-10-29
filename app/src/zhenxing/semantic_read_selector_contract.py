"""Opt-in named SELF resource / READ selector consistency, not intent routing.

Only an already selected registered resource noun can require a READ field.
The model must supply that field/source; this module never supplies a value.
"""
import re

VERSION='explicit-named-read-selector-v1'


def _named_anchors(prepared,kind):
    if kind!='SUBSCRIPTION_LIST':return []
    from .subscription_catalog_data import CATALOG_DEFAULTS,MERCHANT_ALIASES
    from .semantic_read_subject import offered_subject_literals
    names=[row[0] for row in CATALOG_DEFAULTS]+list(MERCHANT_ALIASES)
    result=[]
    for anchor in offered_subject_literals(prepared['message'],kind)[0]:
        text=anchor['source_text']
        if any(re.search((r'(?<![A-Za-z0-9_])' if name.isascii() else '')+re.escape(name)+r'$',text,re.I) for name in names):
            result.append(text)
    return sorted(set(result))


def check_selector_contract(value,prepared):
    if prepared.get('read_selector_contract')!=VERSION or value.get('mode')!='REQUEST':return
    kinds=value.get('action_kinds',[])
    for subject in value.get('read_subjects',[]):
        match=re.fullmatch(r'new:([0-7])',subject.get('action_ref',''))
        index=int(match[1]) if match else -1
        if not 0<=index<len(kinds) or subject.get('subject')!='SELF':continue
        kind=kinds[index];anchor=(subject.get('self_anchor') or {}).get('source_text')
        if anchor not in _named_anchors(prepared,kind):continue
        if not any(row.get('action_ref')==subject['action_ref'] and row.get('field')=='merchant'
                and row.get('operation','SET')=='SET' for row in value.get('changes',[])):
            raise ValueError('SEMANTIC_READ_SELECTOR_REQUIRED')


def apply_selector_schema(schema,prepared):
    if prepared.get('read_selector_contract')!=VERSION:return schema
    anchors=_named_anchors(prepared,'SUBSCRIPTION_LIST')
    if not anchors:return schema
    for index in range(8):
        ref='new:'+str(index)
        condition={'properties':{'mode':{'const':'REQUEST'},'action_kinds':{'minItems':index+1,
            'prefixItems':[{}]*index+[{'const':'SUBSCRIPTION_LIST'}]},'read_subjects':{'contains':{
                'properties':{'action_ref':{'const':ref},'subject':{'const':'SELF'},'self_anchor':{
                    'type':'object','properties':{'source_text':{'enum':anchors}},'required':['source_text']}},
                'required':['action_ref','subject','self_anchor']}}},'required':['mode','action_kinds','read_subjects']}
        schema['allOf'].append({'if':condition,'then':{'anyOf':[
            {'properties':{'changes':{'contains':{'properties':{'action_ref':{'const':ref},'field':{'const':'merchant'}},
                'required':['action_ref','field']}}},'required':['changes']},
            {'properties':{'read_targets':{'contains':{'properties':{'action_ref':{'const':ref},'field':{'const':'merchant'}},
                'required':['action_ref','field']}}},'required':['read_targets']},
            {'properties':{'query_base':{'const':'CURRENT'},'query_reference':{'type':'object',
                'properties':{'selection':{'const':'SUBSET'},'member_refs':{'minItems':1}},'required':['selection','member_refs']}},
                'required':['query_base','query_reference']} ]}})
    return schema
