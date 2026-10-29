"""Read the source-preserving semantic wire protocol in isolated fake HTTP.

No model interpretation, keyword routing, or relaxed business expectations.
"""
import json


def supplied_input(payload):
    messages=payload['messages']
    if len(messages)>=3:
        try:context=json.loads(messages[-2]['content'])
        except (ValueError,TypeError,KeyError):context=None
        if isinstance(context,dict) and context.get('schema_version')=='unified-cloud-semantics-v1':
            assert messages[-2]['role']=='user' and messages[-1]['role']=='user'
            assert messages[-1]['content']==context['message'],'Final raw user input must be exact current source'
            return context
    return json.loads(messages[-1]['content'])
