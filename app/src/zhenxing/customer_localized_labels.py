"""Read-only display labels, never replace business identifiers or signed scope."""
STATUS = {
    "QUEUED": "received", "UNDERSTANDING": "reviewing your request", "PLANNING": "preparing a proposal",
    "NEED_CLARIFICATION": "more information needed", "AWAITING_CONFIRMATION": "waiting for your review and confirmation",
    "EXECUTING": "processing", "UNKNOWN": "outcome unknown; check the original request and do not resend payment",
    "PARTIAL": "partly completed; review each result", "WAITING": "waiting for the agreed condition",
    "SUCCEEDED": "completed according to verified business records", "FAILED": "not completed; review the recorded cause",
    "CLOSED": "closed; completed business remains intact",
}
ACTION = {
    "BILL_ANALYSIS": "Bill analysis", "CARD_STATUS": "Card status", "SUBSCRIPTION_LIST": "Subscriptions",
    "WEALTH_COMPARE": "Investment comparison", "FRAUD_ANALYZE": "Transfer risk review", "FAMILY_VIEW": "Family permissions",
    "GOAL_VIEW": "Goals and cash flow", "CARBON_VIEW": "Activity points and benefits", "FX_QUOTE": "Reference exchange rate",
    "TRANSFER": "Transfer", "TRANSFER_SCHEDULE": "Scheduled transfer", "FX_TRANSFER": "Currency transfer",
    "FX_EXCHANGE": "Currency exchange", "CARD_LOCK": "Temporary card lock", "CARD_UNLOCK": "Card unlock",
    "CARD_REPORT_LOSS": "Report card loss", "CARD_REPLACEMENT": "Replacement request", "CARD_SET_LIMIT": "Card limit change",
    "SUBSCRIPTION_CANCEL": "Subscription cancellation", "WEALTH_BUY": "Investment purchase", "WEALTH_REDEEM": "Redemption",
    "FAMILY_GRANT": "Grant family assistance", "FAMILY_REVOKE": "Revoke family assistance", "FAMILY_PROPOSE": "Assistance proposal",
    "GOAL_CREATE": "Create savings goal", "GOAL_RESERVE": "Reserve goal funds", "GOAL_RELEASE": "Release goal funds",
    "CARBON_CLAIM": "Claim activity points", "CARBON_REDEEM": "Exchange activity benefit", "AA_REQUEST": "Split-payment request",
    "CONTACT_BIND": "Personal recipient label",
}
ERROR = {
    "CARD_VERSION_CHANGED": "The card changed. Review a new proposal before proceeding.",
    "CARD_ACTION_NOT_ALLOWED": "The card's current status does not allow this action.",
    "FX_CARD_CHANGED": "The selected card changed. The old quote cannot be used.",
    "FX_CARD_LIMIT": "The reference amount exceeds the selected card's limit. No limit is raised automatically.",
    "FX_INSUFFICIENT_FUNDS": "There are not enough funds in the explicitly selected currency.",
    "INSUFFICIENT_FUNDS": "There are not enough available funds for this request.",
    "SUBSCRIPTION_CHANGED": "The subscription changed. Review its current status and a new proposal.",
    "CONFIRMATION_EXPIRED": "This confirmation expired or was already used. Review the latest proposal.",
    "PLAN_CHANGED": "The proposal changed. The previous confirmation cannot be used.",
    "TASK_DEADLINE_EXCEEDED": "This request expired. Check its original result before preparing a new payment.",
    "FX_QUOTE_EXPIRED": "The quote expired. Request and review a new quote.",
}


def english_status(value):
    return STATUS.get(value, "status needs verification")


def localize_customer_task_labels(view):
    """Mutate only the newly-created projection; persisted trace is untouched."""
    view["display_status_message"] = view["assistant_response"]["intro"]
    for node in view.get("nodes", []):
        node["display_label"] = ACTION.get(node.get("action"), "Business step")
        error = node.get("error")
        code = error.get("code") if isinstance(error, dict) else error
        if isinstance(code, str):
            node["display_error_message"] = ERROR.get(code, "The step could not be verified or completed. Review its current details; do not resend a payment automatically.")
        if node.get("wait_reason"):
            node["display_wait_reason"] = "This step is waiting for its required condition or your review. It has not completed yet."
    pending = view.get("pending_confirmation")
    if isinstance(pending, dict):
        node = next((item for item in view.get("nodes", []) if item.get("child_task_id") == pending.get("task_id")), {})
        pending["display_label"] = ACTION.get(node.get("action"), "Review this banking step")
        pending["display_summary"] = "Check the actual recipient, card, amount, currencies and date below. This step has not been executed; confirmation and any required verification are separate."
