"""Shared refusal-only source conditions; never affirmative bank authority.

The pattern and flags are the reviewed selected-READ annotation v1 grammar.
Extracting it does not retrofit older inert-WRITE proof interpretation.
"""
import re


SOURCE_CONDITION = re.compile(r'如果|假如|假设|仅当|只有|一旦|若|倘|万一|'
    r'\b(?:if|unless|suppose|hypothetical|provided(?:\s+that)?|only\s+(?:when|after)|on\s+condition|subject\s+to)\b|'
    r'(?:待|等|在)(?:我|本人|你)[^，,。；;!?！？\n]{0,40}(?:允许|同意|授权|确认)[^，,。；;!?！？\n]{0,20}(?:后|才)|'
    r'\b(?:when|after)\s+(?:I|you|we)\s+(?:(?:explicitly|again|later|first)\s+){0,3}'
    r'(?:agree|consent|confirm|permit|allow|authori[sz]e)\b|'
    r'\bafter\s+(?:my|your)\s+(?:(?:explicit|further|new|renewed|fresh)\s+){0,3}'
    r'(?:approval|consent|permission|confirmation)\b',re.I)


def has_source_condition(message):
    return SOURCE_CONDITION.search(message) is not None
