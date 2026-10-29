from __future__ import annotations

import hashlib
import json

from .contracts import BillAnalysis


def canonical_hash(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_lock_plan(
    *,
    task_id: str,
    sandbox_id: str,
    card_id: str,
    card_last4: str,
    card_version: int,
    analysis: BillAnalysis,
) -> tuple[dict, str]:
    evidence_ids = sorted(item["transaction_id"] for item in analysis.anomalies)
    plan = {
        "plan_version": 1,
        "policy_version": "card-lock-v1",
        "task_id": task_id,
        "sandbox_id": sandbox_id,
        "nodes": [
            {
                "node_id": "analyze-bill",
                "tool_name": "bill.analyze.v1",
                "read_only": True,
                "status": "SUCCEEDED",
                "evidence_ids": evidence_ids,
            },
            {
                "node_id": "lock-card",
                "tool_name": "card.lock.v1",
                "read_only": False,
                "depends_on": ["analyze-bill"],
                "card_id": card_id,
                "card_last4": card_last4,
                "expected_card_version": card_version,
                "requires_confirmation": True,
            },
        ],
    }
    return plan, canonical_hash(plan)

