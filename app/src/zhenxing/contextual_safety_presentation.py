"""Controlled, concise explanation fallback for validated safety-case records.

This verifies the typed projection and its integrity, not database authority.
The caller must revalidate actor/source access through contextual_safety_dialogue
before publishing. A user report never becomes a bank receipt here. No raw JSON,
third-party instruction, source identifier, amount or secret is rendered.
"""
from __future__ import annotations

import json
import re

from .contextual_safety_dialogue import AUTHORITY, SOURCE_ID, VERSION
from .planning import canonical_hash

_STAGES = {
    "FRAUD": {"IDENTITY_VERIFICATION", "PAYMENT_DECISION", "QUESTION_PAY", "REPORTED_PAYMENT_RECOVERY", "RISK_EXPLANATION"},
    "FAMILY": {"BALANCE_ONLY_POLICY", "NO_TRANSFER_PERMISSION", "DELEGATION_POLICY"},
}
_FEATURES = {
    "SAFETY_ACCOUNT_INDUCEMENT": ("把钱转到所谓安全账户", "sending money to a so-called safe account"),
    "REFUND_ADVANCE_FEE_INDUCEMENT": ("先交费用才能退款", "paying a fee before receiving a refund"),
    "ADVANCE_FEE_INDUCEMENT": ("为领取奖励先付款", "paying in advance to receive a reward"),
    "REMOTE_ACCESS_INDUCEMENT": ("共享屏幕或远程操控账户", "screen sharing or remote control of an account"),
    "CREDENTIAL_COLLECTION_INDUCEMENT": ("向对方提供验证码或密码", "giving someone a verification code or password"),
    "AUTHORITY_IMPERSONATION": ("自称执法机关要求转钱", "someone claiming to be law enforcement and demanding a transfer"),
    "SECRECY_INDUCEMENT": ("要求瞒着家人或银行", "being told to hide the request from family or your bank"),
}


def _checked(records):
    if not isinstance(records, list) or len(records) != 2 or not all(isinstance(row, dict) for row in records):
        return None
    context = next((row for row in records if row.get("source_id") == SOURCE_ID), None)
    policy = next((row for row in records if row.get("kind") == "PRODUCT_KNOWLEDGE"), None)
    if context is None or policy is None or context.get("kind") != "SCOPED_BUSINESS_FACTS" or context.get("version") != VERSION:
        return None
    try:
        facts = json.loads(context["content"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(facts, dict) or facts.get("authority") != AUTHORITY \
            or facts.get("bank_write_authorized") is not False or facts.get("bank_operation_executed") is not False \
            or facts.get("reported_payment_is_verified_bank_receipt") is not False \
            or facts.get("establish_or_change_delegation_requested") is not False:
        return None
    topic, stage = facts.get("topic"), facts.get("stage")
    if not isinstance(topic, str) or not isinstance(stage, str) or stage not in _STAGES.get(topic, set()) \
            or facts.get("user_reports_prior_payment") is not (stage == "REPORTED_PAYMENT_RECOVERY"):
        return None
    expected_policy = "kb:fraud.case-continuity-v1" if topic == "FRAUD" else "kb:family.scope-v1"
    if policy.get("source_id") != expected_policy or not isinstance(policy.get("content"), str):
        return None
    proof = context.get("source_binding")
    if not isinstance(proof, dict) or proof.get("version") != VERSION or proof.get("authority") != AUTHORITY \
            or proof.get("topic") != topic or proof.get("stage") != stage \
            or proof.get("bank_write_authorized") is not False or proof.get("bank_operation_executed") is not False:
        return None
    digest = proof.get("binding_hash")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) \
            or digest != canonical_hash({key: value for key, value in proof.items() if key != "binding_hash"}) \
            or proof.get("safe_facts_hash") != canonical_hash(facts.get("safe_case_facts")) \
            or proof.get("policy_hash") != canonical_hash([policy]):
        return None
    safe = facts.get("safe_case_facts")
    if not isinstance(safe, dict):
        return None
    if topic == "FRAUD":
        for risk in [safe, safe.get("current_risk")]:
            if not isinstance(risk, dict) or risk.get("risk_level") not in {"LOW", "HIGH", "UNKNOWN"} \
                    or risk.get("decision") not in {"ALLOW", "BLOCK", "HOLD_FOR_REVIEW"} \
                    or risk.get("confirmation_can_override") is not False \
                    or not isinstance(risk.get("reason_codes"), list) or not all(isinstance(code, str)
                        and re.fullmatch(r"[A-Z0-9_]{1,80}", code) for code in risk["reason_codes"]):
                return None
    else:
        permissions = safe.get("permissions")
        if not isinstance(permissions, dict) or not all(type(permissions.get(key)) is bool for key in
                ("can_grant", "can_propose_transfer", "can_propose_card_lock", "can_read_owner_balance")) \
                or not isinstance(safe.get("delegations"), list):
            return None
    return topic, stage, safe


def render_contextual_safety_fallback(records, reply_language="zh-CN"):
    """An explanation-only fallback, or None for a foreign/malformed source set."""
    try:
        checked = _checked(records)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if checked is None:
        return None
    topic, stage, safe = checked
    english = reply_language == "en"
    if topic == "FRAUD":
        if stage == "REPORTED_PAYMENT_RECOVERY":
            return ("Since you say you have already sent money, stop any further payments. Keep the chat, call and transfer evidence, report to the police promptly, and check the original transaction through your bank's official channel. Your report alone does not verify settlement, and recovery cannot be guaranteed. Do not pay anyone promising to recover it."
                if english else "你说已经转了，先别继续付款。请保留聊天、通话和转账凭证，尽快向公安机关反映，并通过银行官方渠道核查原交易。仅凭这条消息还不能核实实际到账，也不能保证追回；别再付费找人“追回”。")
        if stage == "IDENTITY_VERIFICATION":
            return ("A caller's claim about their role does not verify their identity. Pause any payment, screen sharing or disclosure of codes. Independently find the institution's official contact and verify the request; do not use the number or link supplied by the caller."
                if english else "仅凭对方自报身份，并不能证明是官方人员。先别转钱、共享屏幕或提供验证码；自己查找机构的官方客服电话核实，不用对方发来的号码或链接。")
        codes = list(dict.fromkeys(safe.get("reason_codes", []) + safe.get("current_risk", {}).get("reason_codes", [])))
        features = [_FEATURES[code][1 if english else 0] for code in codes if code in _FEATURES][:2]
        if stage in {"PAYMENT_DECISION", "QUESTION_PAY"}:
            if features:
                return ("Do not send the money for now. " + " and ".join(features).capitalize() + " are warning signs. Verify through an official channel first; an extra payment or confirmation does not establish safety."
                    if english else "先不要转。刚才提到的“" + "、".join(features) + "”是风险线索，先通过官方渠道核实；追加付款或回复确认不能证明安全。")
            return ("Do not continue the payment until the recipient's identity and purpose are verified through an official channel. A missing risk flag is not proof that a caller or account is safe."
                if english else "对方身份和收款用途还需要通过官方渠道核实，先别继续付款。没有匹配到风险标签，也不等于这个来电或账户已经证实安全。")
        return (("The request includes warning signs: " + "; ".join(features) + ". " if features else "The caller's identity and purpose still need verification. ")
                + "Pause the payment and verify through a contact you independently obtain from the institution. Do not share passwords, codes or remote access."
            if english else ("这类要求需要警惕：“" + "、".join(features) + "”。" if features else "对方身份和用途仍需核实。")
                + "先暂停付款，自己通过机构官方渠道核实；不要交出密码、验证码或远程控制权限。")
    owner = safe["permissions"]["can_grant"]
    if stage == "BALANCE_ONLY_POLICY":
        return ("Yes, balance viewing and transfer suggestions are separate permissions. Grant balance viewing only, without transfer-suggestion access; the child still cannot make payments for you. " + ("If you want to set this up, the proposed scope must be reviewed and confirmed first." if owner else "The account owner must review and confirm the actual access settings.")
            if english else "可以把权限分开：只开放查看余额，不开放转账建议，孩子也不能直接替你付款。" + ("如果要设置，我再帮你核对具体范围，由你确认后生效。" if owner else "实际设置需要由账户本人核对并确认。"))
    if stage == "NO_TRANSFER_PERMISSION":
        return ("Understood: you want viewing access without transfer access. Balance viewing can remain separate from transfer suggestions, and a helper cannot make payments directly even when suggestions are allowed. This discussion has not changed the existing access settings."
            if english else "明白，你希望能查余额，但不能转钱。查看余额可以和转账建议权限分开；即使允许提建议，家人也不能直接付款。这次讨论没有改变现有授权。")
    return ("A family helper cannot directly transfer money or lock your card. Within valid access, they can view balances or submit the permitted suggestions; actual banking actions still need the owner's review and confirmation. Balance-only access can be selected separately."
        if english else "家人不能直接替你转账或锁卡。在有效授权内，可以查看余额或提出允许的建议，实际办理仍由本人审阅确认；如果只想让孩子查余额，就只开放查看权限。")
