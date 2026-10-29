"""Small auditable read-only compiler, distinct from both Laya and fallback.

Exact Chinese reads and anchored English card-status requests only.
No quotes, images, task references, money, writes
or multiple intentions. Existing domain permissions/data checks still execute.
"""
import re
import unicodedata

from .intelligence import IntentProposal, ProposedAction, _intent_text
from .language import polite_operational_body


READ_COMPILER_VERSION = "standard-read-bilingual-v2"
PATTERNS = (
    ("CARD_STATUS", re.compile(r"^(?:请|帮我)?(?:查询|查看|查)(?:一下|下)?(?:我的|我)?(?:银行卡|卡片)(?:的)?(?:状态(?:和限额)?|状态与限额|限额)$")),
    ("SUBSCRIPTION_LIST", re.compile(r"^(?:请|帮我)?(?:查询|查看|查)(?:一下|下)?(?:我的|我)?(?:订阅(?:代扣)?清单|自动续费(?:订阅)?清单|自动续费|订阅和自动续费)$")),
    ("BILL_ANALYSIS", re.compile(r"^(?:请|帮我)?(?:分析|查询|查看)(?:一下|下)?(?:我的|我)?(?:本月|上月|今年|年度)?(?:账单|账单报告)$")),
)
EN_CARD_PATTERN = re.compile(
    r"^(?:please\s+)?(?:show|check|list|view)(?:\s+me)?\s+"
    r"(?:(?:the\s+)?(?:status(?:\s+and\s+limits?)?|limits?)\s+of\s+(?:my\s+)?(?:bank\s+)?cards?"
    r"|(?:my\s+)?(?:bank\s+)?cards?(?:\s+(?:status(?:\s+and\s+limits?)?|limits?))?)$", re.I)


def compile_standard_read(message, allowed_actions):
    if not isinstance(message,str) or len(message)>64: return None
    text=unicodedata.normalize("NFKC",message).strip().rstrip("。？！?!")
    for kind,pattern in PATTERNS:
        if kind in allowed_actions and pattern.fullmatch(text):
            return IntentProposal(summary=message,actions=[ProposedAction(kind=kind)],
                model_label=READ_COMPILER_VERSION,fallback=False)
    trusted = _intent_text(text)
    if trusted != text:
        return None  # A compiler hit never comes from quoted/source material.
    english = polite_operational_body(trusted) or trusted.rstrip(".")
    if "CARD_STATUS" in allowed_actions and EN_CARD_PATTERN.fullmatch(english):
        return IntentProposal(summary=message, actions=[ProposedAction(kind="CARD_STATUS")],
            model_label=READ_COMPILER_VERSION, fallback=False)
    return None
