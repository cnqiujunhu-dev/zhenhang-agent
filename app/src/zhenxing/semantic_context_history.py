"""Bounded legacy CONSULT candidates, never a substitute for source audits."""
from types import SimpleNamespace

from sqlalchemy import JSON, and_, case, select

from .conversation_models import ChatTurn


def bounded_context_turns(session, principal, conversation_id, query, *, descending=True):
    from .conversation_history_projection import input_source_bytes, MAX_TURN_BYTES
    table=ChatTurn.__table__
    eligible=and_(ChatTurn.status=='ANSWERED',ChatTurn.mode=='CONSULT',ChatTurn.task_id.is_(None))
    candidates=query.with_only_columns(ChatTurn.id,ChatTurn.sequence,
        case((eligible,input_source_bytes(session)>MAX_TURN_BYTES),else_=False).label('unavailable'),
        maintain_column_froms=True).where(ChatTurn.thread_id==conversation_id,ChatTurn.user_id==principal.user_id,
        ChatTurn.sandbox_id==principal.sandbox_id).cte('bounded_semantic_candidates').prefix_with('MATERIALIZED')
    unavailable=candidates.c.unavailable
    expressions=[]
    for column in table.c:
        if isinstance(column.type,JSON) or column.name=='answer':
            expressions.append(case((unavailable,None),else_=column).label(column.name))
        else:expressions.append(column)
    expressions.append(unavailable.label('_context_source_unavailable'))
    lite=('actor_role_at_submission','dialogue_actor_scope_hash','input_redacted','credential_only')
    expressions.extend(ChatTurn.diagnostics_json[name].label('_lite_'+name) for name in lite)
    projected=select(*expressions).join(candidates,ChatTurn.id==candidates.c.id).order_by(
        candidates.c.sequence.desc() if descending else candidates.c.sequence.asc())
    rows=[]
    for record in session.execute(projected).mappings():
        value=dict(record)
        values={name:value.pop('_lite_'+name) for name in lite}
        if value['_context_source_unavailable']:
            value.update(diagnostics_json=values,sources_json=[],reviewed_evidence_refs_json=[])
        rows.append(SimpleNamespace(**value))
    return rows
