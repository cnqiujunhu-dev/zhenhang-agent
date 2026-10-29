"""Whole-object prompt budgeting, preserving source offsets and opaque refs.

Only old terminal navigation frames and whole linguistic turns are optional.
Source validation failures are not repaired or made valid by this compactor.
"""
import json

VERSION = "whole-object-semantic-context-budget-v1"
MAX_CHARACTERS = 42000
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CLOSED"}


def budget_semantic_context(offered, history_groups, *, protected_frame_refs=()):
    from .semantic_router import prepare_semantic_input
    model = offered["model"]
    original_frames = len(model["frames"])
    original_entities = len(model["entities"])
    original_turns = len(history_groups)
    original_memory = len(model.get('memory',[]))
    original_subjects = len(model.get('retrieval_subjects',[]))
    protected = set(protected_frame_refs)
    groups = list(history_groups)
    blocked = False
    input_valid = True
    prepared_characters = None
    while True:
        try:
            prepared = prepare_semantic_input(**model)
            prepared_characters = len(json.dumps(prepared, ensure_ascii=False))
            break
        except ValueError as error:
            if str(error) != "SEMANTIC_INPUT_TOTAL_BOUND":
                # The actual semantic entry remains responsible for failing
                # closed. Never remove an invalid record to hide a violation.
                input_valid = False
                break
        removable = next((frame for frame in reversed(model["frames"])
            if frame["frame_ref"] not in protected and not frame.get("current")
            and not frame.get("continuation_allowed") and frame.get("state") in _TERMINAL_STATES), None)
        if removable is not None:
            ref = removable["frame_ref"]
            model["frames"] = [frame for frame in model["frames"] if frame["frame_ref"] != ref]
            removed_entities = {entity["entity_ref"] for entity in model["entities"] if entity.get("frame_ref") == ref}
            model["entities"] = [entity for entity in model["entities"] if entity["entity_ref"] not in removed_entities]
            offered["frame_bindings"].pop(ref, None)
            for entity_ref in removed_entities:
                offered["entity_bindings"].pop(entity_ref, None)
            continue
        if model.get('retrieval_subjects'):
            # This public directory only enables optional query rewriting.
            # Drop complete registry entries before consuming the established
            # conversation budget. No wording classifier chooses what to keep.
            model['retrieval_subjects'].pop()
            continue
        if groups:
            groups.pop(0)
            model["history"] = [item for group in groups for item in group]
            continue
        if model.get('memory'):
            # Preferences are optional language context, never a substitute
            # for required task/authority proof. Drop complete oldest entries
            # only after ordinary history, with matching private source refs.
            dropped=model['memory'].pop(0)
            offered.get('memory_bindings',{}).pop(dropped['memory_ref'],None)
            continue
        blocked = True
        break
    return {"context_budget_version": VERSION, "context_character_limit": MAX_CHARACTERS,
        "context_prepared_character_count": prepared_characters,
        "context_input_shape_valid": input_valid, "context_required_content_over_budget": blocked,
        "context_included_frame_count": len(model["frames"]),
        "context_dropped_terminal_frame_count": original_frames - len(model["frames"]),
        "context_included_entity_count": len(model["entities"]),
        "context_dropped_entity_count": original_entities - len(model["entities"]),
        "context_included_history_turn_count": len(groups),
        "context_dropped_history_turn_count": original_turns - len(groups),
        **({'context_included_memory_count':len(model['memory']),
            'context_dropped_memory_count':original_memory-len(model['memory'])} if 'memory' in model else {}),
        **({'context_included_retrieval_subject_count':len(model['retrieval_subjects']),
            'context_dropped_retrieval_subject_count':original_subjects-len(model['retrieval_subjects'])}
            if 'retrieval_subjects' in model else {})}
