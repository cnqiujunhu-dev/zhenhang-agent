"""Explain actual pending-plan dependencies; never quote or execute money.

Only an owned server trace's unprepared operations are considered. The text
does not infer a funding currency, recipient eligibility, rate or settlement.
"""

MAX_NODES = 32
LABELS = {
    "TRANSFER": ("转账", "transfer"),
    "FX_TRANSFER": ("跨币种转账", "cross-currency transfer"),
    "FX_EXCHANGE": ("换汇", "currency exchange"),
    "WEALTH_REDEEM": ("理财赎回", "investment redemption"),
    "WEALTH_BUY": ("理财申购", "investment purchase"),
    "SUBSCRIPTION_CANCEL": ("取消代扣", "recurring-payment cancellation"),
    "CARD_LOCK": ("临时锁卡", "temporary card lock"),
}


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 200


def _unprepared(node):
    return (node.get("status") == "BLOCKED" and "child_task_id" in node
            and node["child_task_id"] is None and node.get("result") == {}
            and node.get("error") is None)


def deferred_preparation_notice(trace, *, english=False):
    if not isinstance(trace, dict) or trace.get("state") != "AWAITING_CONFIRMATION":
        return None
    pending, nodes = trace.get("pending_confirmation"), trace.get("nodes")
    if (not isinstance(pending, dict) or not _identifier(pending.get("task_id"))
            or not _identifier(pending.get("confirmation_id"))
            or not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES):
        return None
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict) or not _identifier(node.get("node_id")) or node["node_id"] in by_id:
            return None
        by_id[node["node_id"]] = node
    waiting = [node for node in nodes if node.get("status") == "WAITING_CONFIRMATION"
               and node.get("child_task_id") == pending["task_id"]]
    if len(waiting) != 1:
        return None
    current_id = waiting[0]["node_id"]

    def depends_on_confirmation(node_id, visiting):
        if node_id == current_id:
            return True
        if node_id in visiting or node_id not in by_id:
            return None
        node = by_id[node_id]
        if node.get("status") == "SUCCEEDED" and _identifier(node.get("child_task_id")):
            return False  # Already-completed siblings are not pending blockers.
        dependencies = node.get("depends_on")
        if (not _unprepared(node) or not isinstance(dependencies, list) or not dependencies
                or len(dependencies) > MAX_NODES or not all(_identifier(item) for item in dependencies)):
            return None
        results = [depends_on_confirmation(item, visiting | {node_id}) for item in dependencies]
        return None if any(result is None for result in results) else any(results)

    labels = []
    for node in nodes:
        if node.get("action") not in LABELS or not _unprepared(node):
            continue
        if depends_on_confirmation(node["node_id"], set()) is True:
            label = LABELS[node["action"]][1 if english else 0]
            if label not in labels:
                labels.append(label)
    if not labels:
        return None
    if english:
        return ("The subsequent " + ", ".join(labels)
                + " is waiting for this step's review and confirmation and has not been prepared. "
                  "Its details must be checked separately; this does not mean a quote or settlement is complete.")
    return ("后面的" + "、".join(labels)
            + "还在等待这一步核对确认，尚未开始准备。后续办理内容仍需单独核对；这不表示已经报价或到账。")
