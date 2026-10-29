from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent


def append_audit(
    session: Session,
    *,
    sandbox_id: str,
    task_id: str,
    event_type: str,
    payload: dict,
) -> AuditEvent:
    previous = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.task_id == task_id, AuditEvent.sandbox_id == sandbox_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
        .with_for_update()
    )
    seq = 1 if previous is None else previous.seq + 1
    previous_hash = "0" * 64 if previous is None else previous.event_hash
    canonical = json.dumps(
        {"task_id": task_id, "seq": seq, "type": event_type, "payload": payload, "previous": previous_hash},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    event = AuditEvent(
        sandbox_id=sandbox_id,
        task_id=task_id,
        seq=seq,
        event_type=event_type,
        payload_json=payload,
        previous_hash=previous_hash,
        event_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    session.add(event)
    session.flush()
    return event


def verify_audit_chain(events: list[AuditEvent]) -> bool:
    previous_hash = "0" * 64
    for seq, event in enumerate(events, start=1):
        if event.seq != seq or event.previous_hash != previous_hash:
            return False
        canonical = json.dumps(
            {
                "task_id": event.task_id,
                "seq": event.seq,
                "type": event.event_type,
                "payload": event.payload_json,
                "previous": event.previous_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if event.event_hash != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
            return False
        previous_hash = event.event_hash
    return True

