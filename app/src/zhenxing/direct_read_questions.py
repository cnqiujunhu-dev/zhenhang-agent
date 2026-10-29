"""Recognize a single question about the speaker's own current resources.

This is a conversation QUERY-versus-FAQ classifier, not an action compiler.
It grants no authority and never rewrites the message or supplies a proposal.
The ordinary planner, grounding, permission and read-result checks still run.
"""
import re
import unicodedata

from .intelligence import _intent_text


_CARD_QUESTIONS = re.compile(
    r"(?:what\s+(?:are|is)\s+my\s+(?:current\s+)?(?:bank\s+)?cards?\s+"
    r"(?:current\s+)?(?:limits?|status)"
    r"|what\s+(?:are|is)\s+(?:the\s+)?(?:current\s+)?(?:limits?|status)\s+"
    r"(?:of|for)\s+my\s+(?:bank\s+)?cards?"
    r"|我的(?:当前|现有)?(?:银行卡|卡片)(?:的)?(?:限额是多少|状态是什么|状态如何))",
    re.I,
)
_SUBSCRIPTION_QUESTIONS = re.compile(
    r"(?:(?:what|which)\s+subscriptions\s+do\s+I\s+(?:currently\s+)?have"
    r"|what\s+are\s+my\s+(?:(?:current|active)\s+)?subscriptions"
    r"|我的(?:当前|现有)?(?:订阅(?:代扣)?|自动续费(?:订阅)?)(?:有哪些|有什么)"
    r"|我(?:目前|现在|当前)?有(?:哪些|什么)(?:订阅|自动续费))",
    re.I,
)
_MODEL_OWNED_BALANCE = re.compile(
    r"(?:(?:请|帮我|请帮我|麻烦|麻烦你|我想)\s*)?"
    r"(?:(?:查询|查看|查一下|查查|查下|查|看看|看一下|看下|显示)\s*"
    r"(?:(?:我的|本人的|我自己的|本人|本账户的|当前账户的)\s*)?(?:当前|现在|现有|可用)?余额"
    r"|(?:我的|本人的|我自己的|本账户的|当前账户的)(?:当前|现在|可用)?余额(?:是多少|有多少|还剩多少|多少))"
    r"[？?。.!！]?", re.I,
)


def model_owned_balance_evidence(message: str) -> bool:
    """Literal evidence for a MODEL-selected own bank-balance read only.

    It does not produce a proposal or change the rule fallback's empty-plan
    behavior. The model chooses CARD_STATUS; the authorized bank tool supplies
    fresh balances with currencies separated. No account/object is invented.
    """
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 120:
        return False
    text = unicodedata.normalize("NFKC", message).strip()
    if _intent_text(text) != text:
        return False
    from .financial_intent import foreign_financial_request
    from .intelligence import _private_read_veto, _UNMET_CONDITION
    from .redaction import redact_credentials
    if foreign_financial_request(text) or _private_read_veto(text) or _UNMET_CONDITION.search(text) or redact_credentials(text) != text:
        return False
    return bool(_MODEL_OWNED_BALANCE.fullmatch(text))


def direct_owned_read_kind(message: str) -> str | None:
    """Return only a routing hint for an anchored, explicit-owner question.

    Source/quoted text, additional clauses, conditions, denials, third-party
    owners, task progress and definition/how/why questions cannot match.
    Callers must also give explicit consultation and reviewed sources priority
    and prove the same single read with the existing rule proposal.
    """
    if not isinstance(message, str) or len(message) > 120:
        return None
    text = unicodedata.normalize("NFKC", message).strip()
    if _intent_text(text) != text:
        return None
    # Remove one sentence terminator, never clauses or quotation marks.
    if text.endswith(("?", ".", "。")):
        text = text[:-1].rstrip()
    if _CARD_QUESTIONS.fullmatch(text):
        return "CARD_STATUS"
    if _SUBSCRIPTION_QUESTIONS.fullmatch(text):
        return "SUBSCRIPTION_LIST"
    return None
