"""Bind each multi-BILL narration to its own runtime-verified child result.

This display adapter does not prove ownership or authorize a tool. Runtime's
owned READ gate supplies completed_read_results; we preserve that exact binding
instead of treating the compatibility parent analysis as every child's result.
"""
from copy import deepcopy
from datetime import datetime
import json


VERSION = 'customer-multi-bill-child-narration-v1'
_FIELDS = {'schema_version','task_id','task_version','node_id','operation_id','child_task_id',
           'action','status','result','analysis','observed_at'}


def _same(left, right):
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False, separators=(',', ':')) == \
            json.dumps(right, sort_keys=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError):
        return False


def has_multiple_bills(trace):
    return sum(isinstance(node, dict) and node.get('action') == 'BILL_ANALYSIS'
               for node in trace.get('nodes', [])) > 1


def _checked_fact(trace, node):
    if trace.get('state') != 'SUCCEEDED' or trace.get('pending_confirmation') \
            or type(trace.get('version')) is not int or trace['version'] < 1:
        return None
    outcome = trace.get('outcome')
    if not isinstance(outcome, dict) or outcome.get('verified') is not True \
            or outcome.get('state') != 'SUCCEEDED' or not isinstance(outcome.get('completed_operation_ids'), list):
        return None
    if node.get('action') != 'BILL_ANALYSIS' or node.get('status') != 'SUCCEEDED' \
            or not isinstance(node.get('result'), dict) or node['result'].get('error'):
        return None
    if any(not isinstance(node.get(key), str) or not node[key] for key in ('node_id','operation_id','child_task_id')) \
            or outcome['completed_operation_ids'].count(node['operation_id']) != 1:
        return None
    if sum(isinstance(other, dict) and (other.get('node_id') == node['node_id']
            or other.get('operation_id') == node['operation_id']
            or other.get('child_task_id') == node['child_task_id']) for other in trace.get('nodes', [])) != 1:
        return None
    facts = trace.get('completed_read_results')
    if not isinstance(facts, list):
        return None
    matches = [fact for fact in facts if isinstance(fact, dict) and fact.get('operation_id') == node['operation_id']]
    if len(matches) != 1:
        return None
    fact = matches[0]
    if set(fact) != _FIELDS or fact.get('schema_version') != 'completed-read-result-v1' \
            or fact.get('task_id') != trace.get('task_id') or type(fact.get('task_version')) is not int \
            or fact['task_version'] != trace['version'] \
            or any(fact.get(key) != node.get(key) for key in ('node_id','operation_id','child_task_id','action','status')) \
            or not _same(fact.get('result'), node['result']):
        return None
    analysis = fact.get('analysis')
    if not isinstance(analysis, dict) or type(analysis.get('transaction_count')) is not int \
            or analysis['transaction_count'] < 0:
        return None
    try:
        observed = datetime.fromisoformat(fact['observed_at'])
        if observed.tzinfo is None:
            return None
    except (TypeError, ValueError):
        return None
    return fact


def bill_node_trace(trace, node):
    """Return one exact child analysis, or no usable multi-object narration.

    Single-BILL legacy callers retain their original compatibility projection;
    only a true multi-BILL surface requires the new per-child display proof.
    """
    if not has_multiple_bills(trace):
        return trace
    fact = _checked_fact(trace, node)
    if fact is None:
        return None
    scoped = {**trace, 'analysis': fact['analysis'], 'nodes': [node]}
    # Supplementary fallback is a separate, single-query result, not an
    # alternate analysis to apply to every selected card in a collection.
    scoped.pop('supplementary_bill', None)
    return scoped


def completed_bill_results(trace):
    """All-or-nothing customer display subset for a successful multi-BILL."""
    if not has_multiple_bills(trace):
        return []
    facts = [_checked_fact(trace, node) for node in trace.get('nodes', [])
             if isinstance(node, dict) and node.get('action') == 'BILL_ANALYSIS']
    return deepcopy(facts) if all(fact is not None for fact in facts) else []


def narration_fingerprint_projection(trace):
    """Pin multi-BILL child business facts; observation time is not a change."""
    if not has_multiple_bills(trace):
        return None
    selected = []
    for node in trace.get('nodes', []):
        if not isinstance(node, dict) or node.get('action') != 'BILL_ANALYSIS':
            continue
        fact = _checked_fact(trace, node)
        if fact is None:
            return {'version': VERSION, 'status': 'UNAVAILABLE_CHILD_BINDING'}
        selected.append({key: deepcopy(fact[key]) for key in _FIELDS if key != 'observed_at'})
    return {'version': VERSION, 'status': 'EXACT_CHILD_BINDINGS', 'results': selected}
