"""Keep test-only rule bans local, including lazily imported function aliases."""
import importlib

RULE_ALIAS_MODULES = (
    'planning_fact_projection', 'task_dialogue_context', 'dialogue_brain',
    'turn_interpretation', 'card_lock_reference', 'dialogue_slot_updates',
    'dialogue_state', 'model_routing', 'model_gateway', 'owned_user_dialogue',
    'conversation_service', 'dialogue_reference_integration',
)


def forbid_intent_rules(monkeypatch, forbidden):
    # Import before changing the definition. Otherwise a late import can keep
    # a reference to the temporary forbidden function after fixture teardown.
    modules = [importlib.import_module('zhenxing.' + name) for name in RULE_ALIAS_MODULES]
    for module in modules:
        monkeypatch.setattr(module, 'rule_proposal', forbidden)
    monkeypatch.setattr('zhenxing.intelligence.rule_proposal', forbidden)
    monkeypatch.setattr('zhenxing.model_gateway.RuleIntentModel.propose', forbidden)
