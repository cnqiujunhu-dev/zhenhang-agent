"""Registered settlement contract used by the actual activity redemption path."""
from copy import deepcopy

VERSION='activity-reward-points-only-v1'
_CONTRACT={'version':VERSION,'unit':'POINTS','cash_debit_minor':0,'bank_card_debit':False,
    'inventory_decrement':1,'requires_confirmation':True,
    'authority':'REGISTERED_ACTIVITY_REDEMPTION_RULE_NOT_EXECUTION_RECEIPT'}


def reward_settlement_contract():
    return deepcopy(_CONTRACT)


def valid_reward_settlement(value):
    return isinstance(value,dict) and value==_CONTRACT
