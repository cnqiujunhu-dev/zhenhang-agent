"""Complete, actor-scoped linguistic turns for the unified semantic entry.

This is not an authorization, fact cache, classifier, or replacement for fresh
runtime sources. Oversized/old turns are omitted whole, never cut mid-message.
"""
from sqlalchemy import select

from .conversation_models import ChatTurn
from .redaction import redact_credentials

MAX_TURNS = 6
MAX_MESSAGES = 12
MAX_MESSAGE_CHARACTERS = 2000
MAX_HISTORY_CHARACTERS = 8000
MAX_CANDIDATE_TURNS = 100
VERSION = "complete-semantic-history-v1"


def safe_model_history(history):
    """Transport guard for complete language turns, never a second truncator.

    The database reader owns authorization and the six-turn budget. Reject a
    malformed/over-budget projection whole rather than cutting a negation or
    leaving an assistant answer separated from its user question.
    """
    if not isinstance(history, list) or len(history) > MAX_MESSAGES:
        return []
    result = []
    for row in history:
        if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}:
            return []
        content = row.get('content')
        if not isinstance(content, str) or not content or len(content) > MAX_MESSAGE_CHARACTERS:
            return []
        result.append({'role': row['role'], 'content': redact_credentials(content)})
    if sum(len(row['content']) for row in result) > MAX_HISTORY_CHARACTERS:
        return []
    return result


def semantic_history(session, principal, thread, turn, *, role, actor_scope_hash, with_scope_bindings=False):
    from .product_knowledge import sources_still_authorized
    from .knowledge_runtime import knowledge_sources_current
    from .planning import canonical_hash
    from .semantic_context_history import bounded_context_turns
    rows = bounded_context_turns(session,principal,thread.id,select(ChatTurn).where(
        ChatTurn.thread_id == thread.id, ChatTurn.user_id == principal.user_id,
        ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.sequence < turn.sequence,
        ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()).limit(MAX_CANDIDATE_TURNS))
    groups = []
    source_hashes = []
    scope_bindings=[]
    characters = 0
    skipped_authority = 0
    skipped_oversized = 0
    unavailable_sources = 0
    budget_dropped = 0
    for index, row in enumerate(rows):
        diagnostics = row.diagnostics_json or {}
        unavailable=bool(getattr(row,'_context_source_unavailable',False))
        if diagnostics.get("input_redacted") or diagnostics.get("credential_only") \
                or diagnostics.get("dialogue_actor_scope_hash") not in (None, actor_scope_hash) \
                or diagnostics.get("actor_role_at_submission") not in (None, role) \
                or not unavailable and (not sources_still_authorized(session, principal, row.sources_json) \
                or not knowledge_sources_current(row.sources_json, role)):
            skipped_authority += 1
            continue
        # Bank acknowledgements/status never become historical assistant facts.
        group = [{"role": "user", "content": row.message}]
        if unavailable:unavailable_sources+=1
        if not unavailable and row.task_id is None and row.answer:
            group.append({"role": "assistant", "content": row.answer})
        if any(not isinstance(item["content"], str) or len(item["content"]) > MAX_MESSAGE_CHARACTERS
                for item in group):
            skipped_oversized += 1
            continue
        size = sum(len(item["content"]) for item in group)
        if len(groups) >= MAX_TURNS or characters + size > MAX_HISTORY_CHARACTERS:
            budget_dropped = len(rows) - index
            break
        groups.append(group)
        if with_scope_bindings:
            from .authentication import as_utc
            scope_bindings.append({'version':'explicit-history-user-read-scope-v1','turn_id':row.id,
                'sequence':row.sequence,'thread_id':row.thread_id,'user_id':row.user_id,'sandbox_id':row.sandbox_id,
                'created_at':as_utc(row.created_at).isoformat(),'message_hash':canonical_hash({'message':row.message}),
                'group_hash':canonical_hash(group),'role':role,'actor_scope_hash':actor_scope_hash})
        source_hashes.append(canonical_hash({'turn_id': row.id, 'sequence': row.sequence,
            'thread_id': row.thread_id, 'user_id': row.user_id, 'sandbox_id': row.sandbox_id,
            'task_id': row.task_id, 'history': group, 'sources': row.sources_json,
            'actor_role': diagnostics.get('actor_role_at_submission'),
            'actor_scope_hash': diagnostics.get('dialogue_actor_scope_hash'),
            **({'source_status':'SOURCE_UNAVAILABLE'} if unavailable else {})}))
        characters += size
    groups.reverse()
    source_hashes.reverse()
    scope_bindings.reverse()
    history = [item for group in groups for item in group]
    return history, groups, {"history_version": VERSION, "history_candidate_turn_count": len(rows),
        "history_included_turn_count": len(groups), "history_included_message_count": len(history),
        "history_included_character_count": characters, "history_dropped_budget_turn_count": budget_dropped,
        "history_source_hashes": source_hashes,
        "history_skipped_authority_turn_count": skipped_authority,
        "history_skipped_oversized_turn_count": skipped_oversized,
        **({'history_unavailable_source_count':unavailable_sources} if unavailable_sources else {}),
        **({'_history_read_scope_bindings':scope_bindings} if with_scope_bindings else {})}
