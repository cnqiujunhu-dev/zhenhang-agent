"""Read-only lifecycle intent candidates, never a banking operation parser.

The grammar identifies question structure, aspect and task reference. A model
can classify STATUS, but the server alone chooses an owned task and reads its
fresh trace. Unclear/quoted/mixed requests do not fall through to a new write.
"""
import re

from .intelligence import _intent_text
from .redaction import redact_credentials

REFERENCE = re.compile(r"(?:这|那)(?:(?:一)?(?:笔|项|个)|些|几(?:笔|项|个))(?:业务|任务|请求|操作|交易|转账)?|刚才|当前(?:任务|请求|业务)|原(?:任务|请求)|\b(?:it|this|that|these|those|current|previous|original)\s*(?:requests?|tasks?|transfers?|payments?|operations?|transactions?)?\b", re.I)
PREDICATE = re.compile(r"转(?:出(?:去)?|走)|汇出|付款|支付|到账|收到|扣款|退款|执行|办(?:好|完)|处理(?:好|完)|完成|成功|取消|锁(?:住|卡)|解锁|赎回|申购|\b(?:sent|paid|transferred|processed|executed|completed|finished|done|received|charged|refunded|cancelled|locked)\b", re.I)
ASPECT = re.compile(r"还|已经|已|尚|没|未|了|是不是|有没有|是否|\b(?:not|yet|already|did|has|have|was|is|still)\b", re.I)
QUESTION = re.compile(r"[？?]|是不是|有没有|是否|(?:吗|么|吧|没)\s*[。!！]?\s*$|^\s*(?:has|have|did|is|was|isn't|hasn't|didn't)\b", re.I)
FOREIGN = re.compile(r"(?:他的|她的|他们的|她们的|别人的|他人的|朋友的|同事的).{0,12}(?:这|那|转账|付款|任务|交易)|\b(?:his|her|their|someone\s+else's|another\s+user's)\b", re.I)
COMPOUND = re.compile(r"(?:没|未|不).{0,15}(?:就|再).{0,12}(?:转|汇|付款|支付|执行|办理|处理|做)|(?:再|重新|另外).{0,6}(?:转|汇|付款|支付|执行|办理|处理|做)|(?:先|请|帮我|立即|马上).{0,8}(?:取消|撤销|撤回)|(?:取消|撤销|撤回)(?:掉|后|并|再)|(?:确认|同意|验证码|密码)|\b(?:then|again|resend|retry|confirm|proceed|password|otp)\b", re.I)


def lifecycle_candidate(message):
    if not isinstance(message, str) or not 1 <= len(message) <= 600:
        return None
    if not QUESTION.search(message) or not REFERENCE.search(message) or not PREDICATE.search(message) or not ASPECT.search(message):
        return None
    if redact_credentials(message) != message:
        return None  # The credential-minimization channel retains priority.
    if _intent_text(message) != message:
        return {"version": "lifecycle-intent-v1", "allow_current": False, "reason": "REFERENCE_ONLY"}
    if FOREIGN.search(message):
        return {"version": "lifecycle-intent-v1", "allow_current": False, "reason": "OTHER_OWNER"}
    if COMPOUND.search(message):
        return {"version": "lifecycle-intent-v1", "allow_current": False, "reason": "MIXED_CONTROL_OR_REPEAT"}
    return {"version": "lifecycle-intent-v1", "allow_current": True, "reason": "OWNED_STATUS_QUESTION"}


def compatible_lifecycle_context(message, context):
    if not context:
        return False
    # A transfer status question cannot silently display an unrelated card or
    # goal query's success. Broad "completed" questions can use any owned task.
    kinds = set(context.get("action_kinds", []))
    scopes = (
        (r"转(?:账|出|走)|汇款|\b(?:transferred|transfer)\b", {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER", "AA_REQUEST"}),
        (r"赎回|申购|理财(?:交易|操作)|\b(?:redemption|redeem|investment\s+(?:purchase|order))\b", {"WEALTH_BUY", "WEALTH_REDEEM"}),
        (r"锁卡|锁住|解锁|挂失|\b(?:lock|locked|unlock|unlocked)\b", {"CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS"}),
        (r"(?:订阅|续费|会员|代扣).{0,12}(?:取消|停|关)|(?:取消|停|关).{0,12}(?:订阅|续费|会员|代扣)|\b(?:cancelled|canceled|cancel).{0,20}(?:subscription|mandate)\b", {"SUBSCRIPTION_CANCEL"}),
        (r"(?:领取|申领).{0,8}积分|积分.{0,8}(?:领取|申领)|\b(?:claimed|claim).{0,16}points\b", {"CARBON_CLAIM"}),
        (r"兑换.{0,8}(?:权益|积分)|(?:权益|积分).{0,8}兑换|\b(?:redeemed|redeem).{0,16}(?:points|benefits|rewards)\b", {"CARBON_REDEEM"}),
    )
    if any(re.search(pattern, message, re.I) and not kinds.intersection(allowed) for pattern, allowed in scopes):
        return False
    # A name qualifying "this transaction" is not permission to display a
    # different current recipient/product. These subjects come from the owned
    # stored proposal; a browser/model cannot supply them as state authority.
    qualifier = re.search(r"^(?:请问|帮我查(?:一下)?|查询|核对)?\s*(?:给|向|转给)?\s*"
        r"(?P<name>[A-Za-z][A-Za-z0-9_]{2,31}|[\u4e00-\u9fff]{1,20}?)\s*"
        r"(?:的)?(?:这|那)(?:一)?(?:笔|项)", message, re.I)
    if qualifier and not any(row.get('value') == qualifier.group('name') for row in context.get('status_subjects', [])):
        return False
    return True


def explicitly_current_lifecycle_reference(message):
    """A singular deictic reference, not a request to choose among old tasks.

    Used only after the latest owned answered turn is proved to refer to the
    current task. Plurals and historical references still require selection
    when more than one request is unfinished. This supplies no write authority.
    """
    if not isinstance(message, str) or _intent_text(message) != message:
        return False
    if re.search(r"原(?:来|任务|请求)|之前|以前|上一|那些|这些|那(?:一)?笔|\b(?:previous|prior|original|earlier|last|those|these)\b", message, re.I):
        return False
    return bool(re.search(r"这(?:一)?(?:笔|项)(?:业务|任务|请求|操作|交易|转账)?|当前(?:任务|请求|业务)|\bthis\s+(?:request|task|transfer|payment|operation|transaction)\b", message, re.I))
