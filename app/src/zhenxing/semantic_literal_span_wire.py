"""Exact current-message wire coordinates, never intent or permission recovery.

Only changes[], read_targets[].targets[], query_reference and the explicit
clarification_evidence.source_span and its registered candidate_spans are visited. Internal models continue to
accept start/end only; this encoder belongs exclusively to the HTTP boundary.
"""
from copy import deepcopy
import hashlib
import json


VERSION = 'semantic-exact-literal-span-wire-v1'
CONTEXT_VERSION = 'semantic-exact-literal-context-wire-v2'


class LiteralSpanWireError(ValueError):
    def __init__(self, code, index, reason, *, count=None, context=False, anchor_count=None, literal_count=None):
        super().__init__(code)
        self.version = CONTEXT_VERSION if context else VERSION
        self.diagnostic = {'location': ['changes', index, 'source_span'],
            'original_shape': 'EXACT_LITERAL_OCCURRENCE_OBJECT', 'canonical_shape': None,
            'reason': reason, 'occurrence_count': count}
        if context:
            self.diagnostic.update(version=CONTEXT_VERSION, original_shape='EXACT_LITERAL_CONTEXT_OBJECT',
                anchor_occurrence_count=anchor_count,literal_occurrence_count_in_anchor=literal_count)


def _positions(text,container):
    result=[];start=container.find(text)
    while start>=0:
        result.append(start);start=container.find(text,start+1)
    return result


def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _normalize_change_literal_span_wire(value, message):
    """Encode an explicitly selected exact substring occurrence, losslessly.

    Every character start is considered, including overlapping occurrences.
    Nothing is trimmed, folded, translated, inferred or looked up in history.
    """
    if not isinstance(value, dict) or not isinstance(value.get('changes'), list) or len(value['changes']) > 24:
        return value, []
    canonical = None
    events = []
    for index, change in enumerate(value['changes']):
        if not isinstance(change, dict):
            continue
        span = change.get('source_span')
        if not isinstance(span, dict) or not ({'text', 'occurrence', 'within'} & set(span)):
            continue  # Old standard/array and unsupported forms keep their old validator.
        if 'within' in span:
            if set(span)!={'text','within'} or not isinstance(message,str) \
                    or type(span['text']) is not str or not span['text'].strip() \
                    or type(span['within']) is not str or not span['within'].strip() \
                    or len(span['text'])>len(message) or len(span['within'])>len(message):
                raise LiteralSpanWireError('SEMANTIC_CURRENT_SOURCE_SPAN_INVALID',index,
                    'INVALID_CONTEXT_SELECTOR_SHAPE',context=True)
            text,anchor=span['text'],span['within']
            if 'source_text' in change and (type(change['source_text']) is not str or change['source_text']!=text):
                raise LiteralSpanWireError('SEMANTIC_SOURCE_LITERAL_NOT_UNIQUE',index,
                    'CONFLICTING_SOURCE_TEXT',context=True)
            anchors=_positions(anchor,message)
            if len(anchors)!=1:
                raise LiteralSpanWireError('SEMANTIC_CURRENT_SOURCE_SPAN_INVALID',index,
                    'CONTEXT_NOT_UNIQUE_IN_CURRENT_MESSAGE',context=True,anchor_count=len(anchors))
            literals=_positions(text,anchor)
            if len(literals)!=1:
                raise LiteralSpanWireError('SEMANTIC_CURRENT_SOURCE_SPAN_INVALID',index,
                    'LITERAL_NOT_UNIQUE_IN_CONTEXT',context=True,anchor_count=1,literal_count=len(literals))
            start=anchors[0]+literals[0];selected={'start':start,'end':start+len(text)}
            if canonical is None:canonical=deepcopy(value)
            canonical['changes'][index]['source_span']=selected
            events.append({'version':CONTEXT_VERSION,'location':['changes',index,'source_span'],
                'original_shape':'EXACT_LITERAL_CONTEXT_OBJECT','canonical_shape':'OBJECT_START_END',
                'canonical_span':dict(selected),'anchor_span':{'start':anchors[0],'end':anchors[0]+len(anchor)},
                'anchor_source':'CURRENT_MESSAGE','anchor_occurrence_count':1,'literal_occurrence_count_in_anchor':1,
                'anchor_hash':_hash({'within':anchor}),'literal_hash':_hash({'text':text}),
                'selection_hash':_hash({'text':text,'within':anchor})})
            continue
        if set(span) != {'text', 'occurrence'} or not isinstance(message, str) \
                or type(span['text']) is not str or not span['text'].strip() or len(span['text']) > len(message) \
                or type(span['occurrence']) is not int or not 1 <= span['occurrence'] <= len(message):
            raise LiteralSpanWireError('SEMANTIC_CURRENT_SOURCE_SPAN_INVALID', index, 'INVALID_EXACT_SELECTOR_SHAPE')
        text, occurrence = span['text'], span['occurrence']
        if 'source_text' in change and (type(change['source_text']) is not str or change['source_text'] != text):
            raise LiteralSpanWireError('SEMANTIC_SOURCE_LITERAL_NOT_UNIQUE', index, 'CONFLICTING_SOURCE_TEXT')
        positions = []
        start = message.find(text)
        while start >= 0:
            positions.append(start)
            start = message.find(text, start + 1)
        if occurrence > len(positions):
            raise LiteralSpanWireError('SEMANTIC_CURRENT_SOURCE_SPAN_INVALID', index, 'OCCURRENCE_NOT_IN_CURRENT_MESSAGE', count=len(positions))
        start = positions[occurrence - 1]
        if canonical is None:
            canonical = deepcopy(value)
        canonical['changes'][index]['source_span'] = {'start': start, 'end': start + len(text)}
        events.append({'location': ['changes', index, 'source_span'],
            'original_shape': 'EXACT_LITERAL_OCCURRENCE_OBJECT', 'canonical_shape': 'OBJECT_START_END',
            'canonical_span': {'start': start, 'end': start + len(text)},
            'occurrence': occurrence, 'occurrence_count': len(positions)})
    return (canonical if canonical is not None else value), events


def normalize_literal_span_wire(value, message):
    """Use one exact-source codec for scalar and explicit collection members.

    The small wrapper does not classify fields or supply missing selectors.
    Unknown envelopes and WRITE targets still meet the original strict schema
    and read-only compiler after this lossless positional encoding.
    """
    result,events=_normalize_change_literal_span_wire(value,message)
    if not isinstance(result,dict):
        return result,events
    canonical=None
    selections=result.get('read_targets',[])
    for selection_index,selection in enumerate(selections if isinstance(selections,list) and len(selections)<=8 else []):
        if not isinstance(selection,dict) or not isinstance(selection.get('targets'),list) \
                or len(selection['targets'])>8:continue
        for target_index,target in enumerate(selection['targets']):
            if not isinstance(target,dict):continue
            location=['read_targets',selection_index,'targets',target_index,'source_span']
            try:encoded,observations=_normalize_change_literal_span_wire({'changes':[target]},message)
            except LiteralSpanWireError as error:
                error.diagnostic['location']=location
                raise
            if not observations:continue
            if canonical is None:canonical=deepcopy(result)
            canonical['read_targets'][selection_index]['targets'][target_index]=encoded['changes'][0]
            events.extend({**row,'location':location} for row in observations)
    reference=(canonical if canonical is not None else result).get('query_reference')
    if isinstance(reference,dict) and 'source_span' in reference:
        location=['query_reference','source_span']
        try:encoded,observations=_normalize_change_literal_span_wire({'changes':[{'source_span':reference['source_span']}]},message)
        except LiteralSpanWireError as error:
            error.diagnostic['location']=location
            raise
        if observations:
            if canonical is None:canonical=deepcopy(result)
            canonical['query_reference']['source_span']=encoded['changes'][0]['source_span']
            events.extend({**row,'location':location} for row in observations)
    evidence=(canonical if canonical is not None else result).get('clarification_evidence')
    if isinstance(evidence,dict) and 'source_span' in evidence:
        location=['clarification_evidence','source_span']
        # This is the same exact-current-substring codec, not a recursive
        # repair of unknown fields or a classifier for a missing purpose.
        # Preserve reason, field, candidates and every unknown envelope key
        # for their existing strict schema and source/meaning validators.
        try:encoded,observations=_normalize_change_literal_span_wire({'changes':[{'source_span':evidence['source_span']}]},message)
        except LiteralSpanWireError as error:
            error.diagnostic['location']=location
            raise
        if observations:
            if canonical is None:canonical=deepcopy(result)
            canonical['clarification_evidence']['source_span']=encoded['changes'][0]['source_span']
            events.extend({**row,'location':location} for row in observations)
    candidates=evidence.get('candidate_spans') if isinstance(evidence,dict) else None
    if isinstance(candidates,list) and len(candidates)<=8:
        for index,candidate in enumerate(candidates):
            location=['clarification_evidence','candidate_spans',index]
            try:encoded,observations=_normalize_change_literal_span_wire({'changes':[{'source_span':candidate}]},message)
            except LiteralSpanWireError as error:
                error.diagnostic['location']=location
                raise
            if observations:
                if canonical is None:canonical=deepcopy(result)
                canonical['clarification_evidence']['candidate_spans'][index]=encoded['changes'][0]['source_span']
                events.extend({**row,'location':location} for row in observations)
    return (canonical if canonical is not None else result),events
