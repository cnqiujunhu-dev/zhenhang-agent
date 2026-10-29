"""Frozen synthetic policy-label evaluation, not real fraud detection accuracy."""
from datetime import datetime, timedelta, timezone

import pytest

from zhenxing.billing_evidence import ANOMALY_RULE_VERSION, detect_billing_anomalies
from zhenxing.contracts import DomainError


START = datetime(2026, 9, 2, 4, tzinfo=timezone.utc)  # Shanghai noon.


def row(index, *, seconds=0, amount=-100, **changes):
    return {"evidence_id": f"fixture:{index}", "amount_minor": amount, "cash_amount_minor": amount,
        "currency": "CNY", "economic_kind": "CONSUMPTION", "occurred_at": (START + timedelta(seconds=seconds)).isoformat(),
        "merchant": f"shop-{index}", "category": "餐饮", "account_id": "account-a", "card_id": "card-a",
        "instrument_id": "debit:card-a", "card_identity_verified": True, **changes}


def burst(count=5, *, step=120, **changes):
    return [row(index, seconds=index * step, amount=-100-index, **changes) for index in range(count)]


def baseline(count=5):
    return [row(index, seconds=index*86400, amount=-10000) for index in range(count)]


# Declared cases and labels are fixed before execution. Metrics below refer to
# presence of a review clue, never to confirmed fraud or production customers.
FROZEN_CASES = (
    ("normal_sparse", burst(5, step=3600), set()),
    ("four_below_frequency", burst(4), set()),
    ("five_different_orders", burst(), {"HIGH_FREQUENCY_SPENDING"}),
    ("frequency_exact_600_seconds", burst(step=150), {"HIGH_FREQUENCY_SPENDING"}),
    ("frequency_outside_600_seconds", burst(step=151), set()),
    ("frequency_burst_suppresses_repeats", burst(9, step=30), {"HIGH_FREQUENCY_SPENDING"}),
    ("split_currency", [row(i, seconds=i*30, amount=-100-i, currency="CNY" if i<3 else "USD") for i in range(5)], set()),
    ("split_source", [row(i, seconds=i*30, amount=-100-i, instrument_id="debit:a" if i<3 else "debit:b") for i in range(5)], set()),
    ("unknown_source", burst(instrument_id=None, account_id=None), set()),
    ("night_start_inclusive", [row(0, occurred_at="2026-09-01T16:00:00+00:00")], {"UNUSUAL_HOUR_SPENDING"}),
    ("night_end_minus_tick", [row(0, occurred_at="2026-09-01T21:59:59.999999+00:00")], {"UNUSUAL_HOUR_SPENDING"}),
    ("night_end_exclusive", [row(0, occurred_at="2026-09-01T22:00:00+00:00")], set()),
    ("night_previous_tick", [row(0, occurred_at="2026-09-01T15:59:59.999999+00:00")], set()),
    ("refund_excluded", [row(0, amount=100, economic_kind="REFUND", flag="ANOMALY", occurred_at="2026-09-01T16:00:00+00:00")], set()),
    ("transfer_excluded", burst(economic_kind="MOVEMENT", flag="ANOMALY"), set()),
    ("principal_excluded", burst(economic_kind="RECLASSIFICATION"), set()),
    ("fee_not_consumption_frequency", burst(economic_kind="FEE"), set()),
    ("duplicate_exact_120_seconds", [row(0, merchant="same"), row(1, seconds=120, merchant="same")], {"POSSIBLE_DUPLICATE_CHARGE"}),
    ("duplicate_after_120_seconds", [row(0, merchant="same"), row(1, seconds=121, merchant="same")], set()),
    ("duplicate_other_source", [row(0, merchant="same"), row(1, merchant="same", instrument_id="debit:other")], set()),
    ("large_exact_absolute_and_relative", baseline()+[row(5, seconds=5*86400, amount=-50000)], {"LARGE_RELATIVE_TO_PRIOR_SPENDING"}),
    ("large_below_threshold", baseline()+[row(5, seconds=5*86400, amount=-49999)], set()),
    ("large_insufficient_history", baseline(4)+[row(5, seconds=5*86400, amount=-50000)], set()),
    ("large_other_currency_not_baseline", baseline()+[row(5, seconds=5*86400, amount=-50000, currency="USD")], set()),
    ("bank_marked_consumption", [row(0, flag="ANOMALY")], {"BANK_MARKED_FOR_VERIFICATION"}),
    ("bank_marked_fee", [row(0, economic_kind="FEE", flag="ANOMALY")], {"BANK_MARKED_FOR_VERIFICATION"}),
)


@pytest.mark.parametrize("name,rows,expected", FROZEN_CASES, ids=[case[0] for case in FROZEN_CASES])
def test_frozen_rule_labels(name, rows, expected):
    result = detect_billing_anomalies(rows)
    assert {item["rule"] for item in result} == expected
    for item in result:
        assert item["rule_version"] == ANOMALY_RULE_VERSION == "billing-anomaly-v2"
        assert item["policy_hash"] and item["evidence_ids"] and item["reason"] and item["basis"]
        assert item["fraud_confirmed"] is False and item["automatic_action_authorized"] is False
        assert item["severity"] == "NEEDS_USER_VERIFICATION"
    if name == "frequency_burst_suppresses_repeats":
        assert len(result) == 1


def test_frozen_development_policy_metrics(capsys):
    counts = dict(TP=0, FP=0, TN=0, FN=0)
    for _, rows, expected in FROZEN_CASES:
        actual = bool(detect_billing_anomalies(rows))
        counts["TP" if actual and expected else "FP" if actual else "FN" if expected else "TN"] += 1
    precision = counts["TP"] / (counts["TP"] + counts["FP"])
    recall = counts["TP"] / (counts["TP"] + counts["FN"])
    normal_false_positive_rate = counts["FP"] / (counts["FP"] + counts["TN"])
    assert counts == {"TP":9, "FP":0, "TN":17, "FN":0}
    assert precision == recall == 1 and normal_false_positive_rate == 0
    with capsys.disabled():
        print({"frozen_policy_fixture_count":len(FROZEN_CASES), **counts, "precision":precision, "recall":recall,
            "normal_false_positive_rate":normal_false_positive_rate, "scope":"SYNTHETIC_DEVELOPMENT_REVIEW_CLUES_NOT_FRAUD"})


@pytest.mark.parametrize("changes", [{"frequency_minimum":True}, {"frequency_minimum":2},
    {"frequency_window_seconds":0}, {"unusual_start_hour":6,"unusual_end_hour":6}, {"unusual_end_hour":25}])
def test_invalid_rule_configuration_fails_closed(changes):
    with pytest.raises(DomainError):
        detect_billing_anomalies([], **changes)
