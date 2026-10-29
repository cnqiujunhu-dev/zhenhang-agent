"""Source-bound ambiguity about values, not ambiguity about bank objects.

No selected value, conversion, write, default or entity lookup occurs here.
The model must identify multiple literal alternatives before asking the user.
"""
from .contracts import DomainError
from .planning import canonical_hash


def checked_value_ambiguity(evidence, message):
    evidence = evidence.model_dump() if hasattr(evidence, 'model_dump') else evidence
    def deny():
        raise DomainError('SEMANTIC_CLARIFICATION_VALUES_INVALID',
            '这些信息还不能确定，请确认一个准确值；不会替你猜选或保存。', 409)
    if not isinstance(evidence, dict) or evidence.get('reason') != 'AMBIGUOUS_VALUE' or not isinstance(message, str):
        deny()
    from .intelligence import ActionSlots, _intent_text
    field = evidence.get('field')
    # Arrays, internal IDs and compound selections are not scalar choices.
    non_scalar = {'participant_names', 'scopes', 'schedule_occurrence_times', 'card_balance_currencies',
        'merchant_categories', 'life_plan_items'}
    if field not in ActionSlots.model_fields or field in non_scalar:
        deny()
    if evidence.get('candidate_refs'):
        deny()  # literal alternatives cannot turn into database entity IDs
    outer = evidence.get('source_span')
    spans = evidence.get('candidate_spans')
    if not isinstance(outer, dict) or not isinstance(spans, list) or not 2 <= len(spans) <= 8:
        deny()
    def valid(span, start, end):
        return (isinstance(span, dict) and set(span) == {'start', 'end'}
            and type(span['start']) is int and type(span['end']) is int
            and start <= span['start'] < span['end'] <= end)
    if not valid(outer, 0, len(message)):
        deny()
    literals = []; intervals = []
    for span in spans:
        if not valid(span, outer['start'], outer['end']): deny()
        literal = message[span['start']:span['end']]
        if len(literal) > 160 or not literal.strip() or _intent_text(literal) != literal:
            deny()
        if any(span['start'] < right and left < span['end'] for left, right in intervals):
            deny()
        intervals.append((span['start'], span['end']))
        literals.append(literal.strip().casefold())
    if len(set(literals)) != len(literals):
        deny()
    return {'version': 'source-value-ambiguity-v1', 'field': field,
        'candidate_spans': spans, 'candidate_literal_hashes': [canonical_hash(value) for value in literals],
        'selection_made': False, 'bank_state_changed': False}
