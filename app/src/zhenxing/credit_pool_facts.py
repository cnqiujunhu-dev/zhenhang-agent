"""Non-monetary, source-bound relationship of a single credit facility.

The domain emits this contract from its actual CreditAccount. Consumers never
infer a common pool from equal balances, equal limits or supported currencies.
Publishing callers must revalidate the original authenticated read source.
"""
from copy import deepcopy
import re

from .planning import canonical_hash

VERSION = "credit-shared-pool-v1"
AUTHORITY = "REGISTERED_SINGLE_CREDIT_FACILITY_POLICY"
# Explicitly reviewed CREDIT_POLICY version, not automatic future opt-in.
REGISTERED_POLICY_VERSION = "sim-credit-v1"
KEYS = frozenset({"schema_version", "authority", "credit_account_id", "facility_version", "base_currency",
    "supported_currencies", "shared_credit_limit", "policy_version", "policy_hash"})


def current_credit_pool_contract(facility):
    policy = facility.policy_json
    if not isinstance(policy, dict) or facility.base_currency != "CNY" or policy.get("base_currency") != "CNY" \
            or policy.get("supported_currencies") != ["CNY", "USD"] \
            or policy.get("policy_version") != REGISTERED_POLICY_VERSION \
            or policy.get("shared_credit_limit", True) is not True:
        return None
    value = {"schema_version": VERSION, "authority": AUTHORITY, "credit_account_id": facility.id,
        "facility_version": facility.version, "base_currency": facility.base_currency,
        "supported_currencies": deepcopy(policy["supported_currencies"]), "shared_credit_limit": True,
        "policy_version": policy.get("policy_version"), "policy_hash": canonical_hash(policy)}
    return value if valid_credit_pool_contract(value) else None


def valid_credit_pool_contract(value, result=None):
    if not isinstance(value, dict) or set(value) != KEYS or value.get("schema_version") != VERSION \
            or value.get("authority") != AUTHORITY or value.get("base_currency") != "CNY" \
            or value.get("supported_currencies") != ["CNY", "USD"] or value.get("shared_credit_limit") is not True \
            or not isinstance(value.get("credit_account_id"), str) or not value["credit_account_id"] \
            or type(value.get("facility_version")) is not int or value["facility_version"] < 1 \
            or value.get("policy_version") != REGISTERED_POLICY_VERSION \
            or not isinstance(value.get("policy_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", value["policy_hash"]):
        return False
    if result is None:
        return True
    policy = result.get("policy")
    return isinstance(policy, dict) and value["credit_account_id"] == result.get("credit_account_id") \
        and value["facility_version"] == result.get("facility_version") \
        and value["policy_version"] == policy.get("policy_version") and value["policy_hash"] == canonical_hash(policy) \
        and policy.get("base_currency") == value["base_currency"] \
        and policy.get("supported_currencies") == value["supported_currencies"] \
        and policy.get("shared_credit_limit", True) is True


def selected_credit_pool_relation(selected):
    """Consume registered selected statement contents, never arbitrary prose."""
    if not selected or len(selected) > 3:
        return None
    contracts = []; currencies = [];facilities=0
    for row, content in selected:
        binding = row.get("source_binding") or {}
        if content.get("domain") != "CREDIT" or "ACCOUNT_RELATIONSHIP" not in binding.get("question_aspects", []):
            return None
        fields = {fact["field"]: fact["value"] for fact in content["facts"]}
        contract = fields.get("credit_pool_contract")
        if fields.get("facts_authority") != "OWNED_CREDIT_BOOK" or fields.get("credit_is_cash") is not False \
                or not valid_credit_pool_contract(contract):
            return None
        if fields.get('credit_account_id',contract['credit_account_id'])!=contract['credit_account_id']:return None
        currency=fields.get('currency')
        if currency is None:
            if fields.get('base_currency')!='CNY' or fields.get('supported_currencies')!=['CNY','USD'] or fields.get('shared_credit_limit') is not True:return None
            facilities+=1
            if facilities>1:return None
        elif currency in {'CNY','USD'}:currencies.append(currency)
        else:return None
        contracts.append(contract)
    if len(set(currencies)) != len(currencies) or any(value != contracts[0] for value in contracts[1:]):
        return None
    return {"base_currency": contracts[0]["base_currency"], "supported_currencies": deepcopy(contracts[0]["supported_currencies"]),
        "shared_credit_limit": True, "debts_separate_by_currency": True, "credit_is_cash": False}
