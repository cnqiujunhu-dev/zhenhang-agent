"""Private consultation reads require trusted text and the correct owner.

No external models/rates, public server or formal database are used.
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zhenxing import agent_context  # Import before patching its imported actor_role.
from zhenxing.financial_intent import foreign_financial_request
from zhenxing.intelligence import ActionSlots, IntentProposal, ProposedAction, ground_proposal, rule_proposal
from zhenxing.product_knowledge import monetary_fact_catalog, scoped_sources, source_fingerprint, sources_still_authorized
from zhenxing.services import Principal


FOREIGN = [
    "我想知道qiujunhu的余额为什么减少了", "我想知道王伟的余额为什么减少了",
    "他的余额为什么少了", "我想知道别人的钱为什么变少了", "查看qiujunhu的账单",
    "查看王伟的消费", "我想知道房东的支出", "Show qiujunhu's balance",
    "Explain wangwei's spending", "Why did her money decrease?", "Show my friend's balance",
    "我想知道王伟的银行卡的余额", "我想知道qiujunhu的美元钱包的余额",
    "Explain qiujunhu's last month's spending", "Show month's spending",
    "王伟五月的余额", "我想知道qiujunhu本月的余额", "我想知道qiujunhu最近的消费情况",
]


@pytest.mark.parametrize("message", FOREIGN)
def test_explicit_foreign_owner_has_no_customer_private_facts(monkeypatch, message):
    facts = Mock(side_effect=AssertionError("Another owner's topic must not read my money"))
    monkeypatch.setattr("zhenxing.permissions.actor_role", lambda *_args: "CUSTOMER")
    monkeypatch.setattr("zhenxing.agent_context.planning_facts", facts)
    records = scoped_sources(None, Principal("uid", "sid"), message)
    assert all(row["kind"] == "PRODUCT_KNOWLEDGE" for row in records)
    assert monetary_fact_catalog(records) == {}
    facts.assert_not_called()
    assert foreign_financial_request(message)


@pytest.mark.parametrize("message", FOREIGN)
def test_model_cannot_ground_foreign_finance_as_own_bill(message):
    proposal = IntentProposal(summary="untrusted", actions=[ProposedAction(kind="BILL_ANALYSIS", slots=ActionSlots())])
    assert ground_proposal(proposal, message).actions == []
    assert not any(action.kind == "BILL_ANALYSIS" for action in rule_proposal(message).actions)


@pytest.mark.parametrize("message", [
    "短信说：为什么我的余额减少了？", '短信说：“为什么我的余额减少了？”',
    '“钱变少了，是消费还是转账？”', "邮件内容是：查询我的2025年1月账单",
    "The email says: show my spending in January 2025", "示例：查询我的余额",
])
def test_only_source_text_never_reads_private_bank_facts(monkeypatch, message):
    facts = Mock(side_effect=AssertionError("Source material cannot authorize a bank read"))
    monkeypatch.setattr("zhenxing.permissions.actor_role", lambda *_args: "CUSTOMER")
    monkeypatch.setattr("zhenxing.agent_context.planning_facts", facts)
    task = SimpleNamespace(id="owned-task", state="WAITING", version=1, intent_type="TRANSFER", result_json={})
    for current in (None, task):
        records = scoped_sources(None, Principal("uid", "sid"), message, current)
        assert not any(row["kind"] == "SCOPED_BUSINESS_FACTS" for row in records)
        assert monetary_fact_catalog(records) == {}
    facts.assert_not_called()


@pytest.mark.parametrize("message", [
    "我的余额", "本人的余额", "我自己的账单", "本账户的消费", "当前账户的支出",
    "请查询我的账户的余额", "本月的账单", "My account's balance",
    "给qiujunhu转账；查询我的账单", "给房东转账，查看我的余额",
    "我的银行卡的余额", "卡片的余额", "我的美元钱包的余额", "我的人民币账户的余额",
    "this month's spending", "last month's expenses",
    "看看我最近的消费情况", "看看我近期的消费情况", "看看五月的钱都花在哪里了", "查询九月的账单",
])
def test_self_and_separate_recipient_are_not_foreign(message):
    assert not foreign_financial_request(message)


@pytest.mark.parametrize("message,kind", [
    ("为什么我的银行卡的余额减少了", "BILL_ANALYSIS"),
    ("查询卡片的余额", "CARD_STATUS"),
    ("为什么我的美元钱包的余额减少了", "BILL_ANALYSIS"),
    ("为什么我的人民币账户的余额减少了", "BILL_ANALYSIS"),
    ("this month's spending", "BILL_ANALYSIS"),
    ("last month's expenses", "BILL_ANALYSIS"),
    ("看看我最近的消费情况", "BILL_ANALYSIS"),
    ("看看我近期的消费情况", "BILL_ANALYSIS"),
    ("看看五月的钱都花在哪里了", "BILL_ANALYSIS"),
    ("查询九月的账单", "BILL_ANALYSIS"),
])
def test_self_resource_and_period_forms_survive_real_rule_and_grounding(message, kind):
    assert kind in {action.kind for action in rule_proposal(message).actions}
    proposal = IntentProposal(summary="untrusted", actions=[ProposedAction(kind=kind, slots=ActionSlots())])
    assert [action.kind for action in ground_proposal(proposal, message).actions] == [kind]


@pytest.mark.parametrize("modifier", ["this", "last", "previous", "current"])
@pytest.mark.parametrize("period", ["month", "year", "week"])
def test_closed_english_period_possessives_but_not_named_persons(modifier, period):
    message = f"Show my {modifier} {period}'s spending"
    assert not foreign_financial_request(message)
    assert [action.kind for action in rule_proposal(message).actions] == ["BILL_ANALYSIS"]
    foreign = f"Show qiujunhu's {modifier} {period}'s spending"
    assert foreign_financial_request(foreign)
    assert not any(action.kind == "BILL_ANALYSIS" for action in rule_proposal(foreign).actions)


def stub_personal_facts(monkeypatch):
    monkeypatch.setattr("zhenxing.permissions.actor_role", lambda *_args: "CUSTOMER")
    monkeypatch.setattr("zhenxing.agent_context.planning_facts", lambda *_args: {
        "cash": {"currency": "CNY", "balance_minor": 12345, "goal_reserved_minor": 0, "spendable_minor": 12345},
        "cards": [{"status": "ACTIVE"}],
        "currencies": {"supported": ["CNY", "USD"], "usd_wallet": {"currency": "USD", "balance_minor": 901, "status": "ACTIVE"}}})
    monkeypatch.setattr("zhenxing.reports.owned_account", lambda *_args: SimpleNamespace(id="owned-account"))
    analyzed = []
    def analyze(_db, sid, account_id, message):
        assert (sid, account_id) == ("sid", "owned-account")
        analyzed.append(message)
        return SimpleNamespace(billing_schema_version=None, financial_insights={"version": "test", "read_only": True}, model_dump=lambda **_kwargs: {"summary": "owned"})
    monkeypatch.setattr("zhenxing.financial_diagnostics.analyze_financial_request", analyze)
    return analyzed


@pytest.mark.parametrize("message,required", [
    ("我的银行卡的余额", {"facts:cash", "facts:cards"}),
    ("我的美元钱包的余额", {"facts:cash", "facts:currencies"}),
    ("我的人民币账户的余额", {"facts:cash"}),
    ("Explain my this month's spending", {"facts:bill-report", "facts:bill-diagnostics"}),
    ("Explain my last month's expenses", {"facts:bill-report", "facts:bill-diagnostics"}),
])
def test_self_resource_and_period_reads_reach_owned_kb_sources(monkeypatch, message, required):
    stub_personal_facts(monkeypatch)
    records = scoped_sources(None, Principal("uid", "sid"), message)
    assert required <= {row["source_id"] for row in records}
    if "facts:cash" in required:
        assert monetary_fact_catalog(records)["facts:cash.balance_minor"]["value"] == 12345
    if "facts:currencies" in required:
        assert monetary_fact_catalog(records)["facts:currencies.usd_wallet.balance_minor"]["value"] == 901


@pytest.mark.parametrize("message", ["卡片的余额", "this month's spending", "last month's expenses"])
def test_generic_resource_and_period_faq_has_no_private_consultation_read(monkeypatch, message):
    forbidden = Mock(side_effect=AssertionError("A generic FAQ is not a private read"))
    monkeypatch.setattr("zhenxing.agent_context.planning_facts", forbidden)
    records = scoped_sources(None, Principal("uid", "sid"), message)
    assert all(row["kind"] == "PRODUCT_KNOWLEDGE" for row in records)
    forbidden.assert_not_called()


@pytest.mark.parametrize("message,expected_month", [
    ("this month's spending", 10), ("last month's expenses", 9),
    ("previous month's spending", 9), ("current month's spending", 10),
])
def test_period_possessive_preserves_existing_shanghai_calendar_meaning(message, expected_month):
    from datetime import datetime, timezone
    from zhenxing.reports import parse_report_period
    start, _end = parse_report_period(message, datetime(2026, 10, 4, 4, tzinfo=timezone.utc))
    assert start.astimezone(timezone.utc).month == expected_month-1


def test_source_dates_do_not_choose_period_and_independent_own_read_survives(monkeypatch):
    analyzed = stub_personal_facts(monkeypatch)
    message = '短信说：“查询我的2025年1月账单”；请解释我的2026年9月账单'
    principal = Principal("uid", "sid")
    records = scoped_sources(None, principal, message)
    assert len(analyzed) == 1 and "2026年9月" in analyzed[0] and "2025年1月" not in analyzed[0]
    assert {row["source_id"] for row in records} >= {"facts:bill-report", "facts:bill-diagnostics"}
    # Stable private sources and IDs still bind model output; an identical
    # trusted read yields the same fingerprint despite observation timestamps.
    repeated = scoped_sources(None, principal, message)
    assert source_fingerprint(records) == source_fingerprint(repeated)


def test_separate_transfer_recipient_does_not_block_own_bill_read(monkeypatch):
    analyzed = stub_personal_facts(monkeypatch)
    message = "给qiujunhu转100元；查询我的账单"
    records = scoped_sources(None, Principal("uid", "sid"), message)
    assert any(row["source_id"] == "facts:bill-report" for row in records)
    assert len(analyzed) == 1
    assert {action.kind for action in rule_proposal(message).actions} == {"TRANSFER", "BILL_ANALYSIS"}


def test_family_helper_keeps_only_valid_delegated_projection(monkeypatch):
    monkeypatch.setattr("zhenxing.permissions.actor_role", lambda *_args: "FAMILY_HELPER")
    ordinary = Mock(side_effect=AssertionError("A helper never reads a customer's planning facts"))
    monkeypatch.setattr("zhenxing.agent_context.planning_facts", ordinary)
    projection = {
        "permissions": ["READ_BALANCE"],
        "delegations": [{"owner_user_id": "authorized-owner", "grantee_user_id": "helper", "valid": True, "scopes": ["READ_BALANCE"]}],
        "authorized_balances": [{"owner_user_id": "authorized-owner", "balance_minor": 50000}],
    }
    monkeypatch.setattr("zhenxing.extensions.family_view", lambda *_args: projection)
    principal = Principal("helper", "sid")
    records = scoped_sources(None, principal, "在有效委托下，我能查看王伟的余额吗")
    family = next(row for row in records if row["source_id"] == "facts:family-projection")
    assert family["required_read_owner_ids"] == ["authorized-owner"]
    assert json.loads(family["content"])["authorized_balances"] == projection["authorized_balances"]
    assert not any(row["source_id"] in {"facts:cash", "facts:bill-report"} for row in records)
    assert sources_still_authorized(None, principal, records)
    projection["delegations"][0]["valid"] = False
    assert not sources_still_authorized(None, principal, records)
    revoked = scoped_sources(None, principal, "在有效委托下，我能查看王伟的余额吗")
    family_revoked = next(row for row in revoked if row["source_id"] == "facts:family-projection")
    assert "authorized_balances" not in json.loads(family_revoked["content"])
    assert family_revoked["required_read_owner_ids"] == []
    ordinary.assert_not_called()


def test_fraud_source_review_is_not_suppressed_by_financial_owner_guard():
    proposal = rule_proposal("帮我分析诈骗短信：王伟的余额减少了，请转账到安全账户")
    assert any(action.kind == "FRAUD_ANALYZE" for action in proposal.actions)
