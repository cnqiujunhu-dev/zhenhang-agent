"""One optional CONSULT metadata relation; no intent repair or bank authority.

The validator calls the gate only after all other checks. Its private receipt
attests a real rejection, rather than treating an error-shaped JSON as proof.
"""
from copy import deepcopy
import hashlib
import hmac
import secrets

from .planning import canonical_hash

VERSION = 'consult-optional-rule-grammar-v1'
CODE = 'SEMANTIC_RULE_ASPECT_MODE_INVALID'
_AUTHORITY = object()
_KEY = secrets.token_bytes(32)


def consult_rule_schema_relation():
    return {'if': {'properties': {'mode': {'const': 'CONSULT'},
        'question_aspects': {'not': {'contains': {'const': 'RULES'}}}}, 'required': ['mode']},
        'then': {'properties': {'rule_aspects': {'maxItems': 0}}}}


def _sign(data):
    return hmac.new(_KEY, canonical_hash(data).encode('ascii'), hashlib.sha256).hexdigest()


class ConsultRuleModeError(ValueError):
    def __init__(self, data=None, *, authority=None, seal=None):
        super().__init__(CODE)
        self.data = deepcopy(data)
        self.authority = authority
        self.seal = seal
        self.diagnostic = {'location': ['rule_aspects']}


def enforce_consult_rule_relation(parsed, prepared, validator_input):
    """Reject unchanged semantics, after ordinary references/source validation."""
    if parsed.mode != 'CONSULT' or not parsed.rule_aspects or 'RULES' in parsed.question_aspects:
        return
    # Memory, operation controls or executable annotations are outside this
    # finite grammar permit, even if another consumer would reject them later.
    pure = not any((parsed.action_kinds, parsed.changes, parsed.dependencies,
        parsed.memory_updates, parsed.read_targets, parsed.read_subjects,
        parsed.history_read_sources, parsed.scope_frame_refs, parsed.operation_scope,
        parsed.presentation_focus, parsed.clarification_evidence, parsed.preference_rebase_ref))
    if not pure:
        raise ValueError(CODE)
    data = {'version': VERSION, 'classification': 'WIRE_GRAMMAR',
        'phase': 'CONSULT_OPTIONAL_RULE_METADATA', 'validation_code': CODE,
        'reason': 'RULE_METADATA_WITHOUT_RULES', 'location': ['rule_aspects'],
        'input_hash': canonical_hash(prepared),
        'source_message_hash': canonical_hash({'message': prepared['message']}),
        'decoder_input_hash': canonical_hash(validator_input),
        'validated_decision_hash': canonical_hash(parsed.model_dump()),
        'mode': 'CONSULT', 'question_aspects': list(parsed.question_aspects),
        'rule_aspects': list(parsed.rule_aspects), 'all_other_validation_passed': True,
        'schema_relation_hash': canonical_hash(consult_rule_schema_relation()),
        'bank_write_authorized': False}
    raise ConsultRuleModeError(data, authority=_AUTHORITY, seal=_sign(data))


def consult_rule_grammar_witness(error, prepared, canonical, schema):
    """Require the actual gate and the exact currently offered allOf relation."""
    if type(error) is not ConsultRuleModeError or error.authority is not _AUTHORITY \
            or not isinstance(error.data, dict) or not isinstance(error.seal, str) \
            or not hmac.compare_digest(error.seal, _sign(error.data)):
        return None
    data = error.data
    relation = consult_rule_schema_relation()
    if not isinstance(schema, dict) or relation not in schema.get('allOf', []) \
            or data.get('input_hash') != canonical_hash(prepared) \
            or data.get('source_message_hash') != canonical_hash({'message': prepared['message']}) \
            or data.get('decoder_input_hash') != canonical_hash(canonical) \
            or data.get('schema_relation_hash') != canonical_hash(relation):
        return None
    # Re-run the unchanged real validator, including all reference/source
    # checks. Calling this module's small gate alone cannot attest those checks.
    from .semantic_router import validate_semantic_decision
    try:
        validate_semantic_decision(canonical, prepared)
    except ConsultRuleModeError as checked:
        if type(checked) is not ConsultRuleModeError or checked.authority is not _AUTHORITY \
                or checked.data != data or not isinstance(checked.seal, str) \
                or not hmac.compare_digest(checked.seal, _sign(checked.data)):
            return None
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None
    else:
        return None
    # The full offered schema is observed, not modified or relaxed.
    from .semantic_messages import compact_schema_titles
    return {**deepcopy(data), 'outgoing_schema_hash': canonical_hash(compact_schema_titles(schema))}
