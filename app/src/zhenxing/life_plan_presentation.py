"""Pure shape checks and bounded wording. These never replace DB integrity."""
from datetime import datetime

from .life_plan_facts import DOMAINS, READ_ACTIONS, READ_VERSION, SCHEMAS, _body
from .planning import canonical_hash


ACTION_PRESENTATIONS = {"OCCASION_VIEW": ("OCCASIONS", "家庭生日"), "OCCASION_SET": ("OCCASIONS", "保存家庭生日"),
    "OCCASION_MEMBER_VIEW": ("OCCASIONS", "家庭成员资料"), "OCCASION_MEMBER_SET": ("OCCASIONS", "保存家庭成员资料"),
    "LIFE_PLAN_VIEW": ("LIFE_PLANS", "生日生活计划"), "LIFE_PLAN_AUTHORIZE": ("LIFE_PLANS", "本次有界计划授权"),
    "LIFE_PLAN_CANCEL": ("LIFE_PLANS", "取消未执行计划"), "MERCHANT_CATALOG": ("MERCHANTS", "商户商品"),
    "MERCHANT_ORDERS": ("MERCHANTS", "商户订单"), "MERCHANT_PURCHASE": ("MERCHANTS", "单笔商户付款")}
STATES = {"DRAFT": "待本人授权", "ACTIVE": "已授权，等待约定步骤", "PAUSED_RECONFIRM": "已暂停，需重新核对",
    "PARTIAL": "部分完成", "CANCELLING": "正在取消", "COMPENSATING": "退款处理中", "COMPENSATED": "原路退款已核验",
    "CANCELLED": "未执行部分已取消", "SUCCEEDED": "本次计划完成", "PAID": "已付款，尚未履约",
    "FULFILLED": "商户已记录履约", "REFUND_PENDING": "退款处理中，尚未到账",
    "REFUNDED": "已原路退款", "SOURCE_UNVERIFIED": "来源暂未确认", "FULFILMENT_UNVERIFIED": "履约情况暂未确认"}
PLAN_STATES = {"DRAFT", "ACTIVE", "PAUSED_RECONFIRM", "PARTIAL", "CANCELLING", "COMPENSATING", "COMPENSATED", "CANCELLED", "SUCCEEDED"}
ORDER_STATES = {"PAID", "FULFILLED", "REFUND_PENDING", "REFUNDED", "SOURCE_UNVERIFIED", "FULFILMENT_UNVERIFIED"}


def _int(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53 - 1


def _text(value):
    return isinstance(value, str) and bool(value)


def _rows(value):
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)


def _plans(result):
    return _rows(result.get("plans")) and all(_text(row.get("plan_id")) and row.get("status") in PLAN_STATES
        and all(_int(row.get(field)) for field in ("budget_minor", "reserved_minor", "spent_minor", "refunded_minor"))
        and _int(row["budget_minor"], 1) and row["reserved_minor"] <= row["budget_minor"]
        and row["spent_minor"] <= row["budget_minor"] and row["refunded_minor"] <= row["spent_minor"]
        and row.get("net_cash_debit_minor") == row["spent_minor"] - row["refunded_minor"] for row in result["plans"])


def _orders(result):
    return _rows(result.get("orders")) and all(_text(row.get("order_id")) and row.get("status") in ORDER_STATES
        and _int(row.get("amount_minor"), 1) and row.get("currency") in {"CNY", "USD"}
        and row.get("simulated") is True and row.get("external_fulfilment_verified") is False for row in result["orders"])


def _members(result):
    return _rows(result.get("members")) and result.get("account_access_granted") is False and all(
        _text(row.get("family_member_id")) and _text(row.get("family_member_name")) and _int(row.get("version"))
        and row.get("account_access_granted") is False for row in result["members"])


def life_read_recorded(action, result):
    try:
        if (action not in READ_ACTIONS or not isinstance(result, dict) or result.get("schema_version") != SCHEMAS[action]
                or result.get("action") != action or result.get("read_schema_version") != READ_VERSION
                or result.get("bank_state_changed") is not False or not _text(result.get("owner_scope_hash"))
                or not datetime.fromisoformat(result["observed_at"]).tzinfo
                or result.get("read_fingerprint") != canonical_hash(_body(result))):
            return False
        if action == "OCCASION_MEMBER_VIEW": return _members(result)
        if action == "LIFE_PLAN_VIEW": return _plans(result)
        if action == "MERCHANT_ORDERS": return _orders(result)
        if action == "MERCHANT_CATALOG":
            from .merchant_catalog_selection import category_scope_valid
            return category_scope_valid(result) and _rows(result.get("products")) and result.get("external_merchant_connected") is False and all(
                _text(row.get("product_id")) and _int(row.get("unit_price_minor"), 1)
                and row.get("currency") in {"CNY", "USD"} and _int(row.get("stock")) for row in result["products"])
        from .occasion_upcoming import upcoming_result_valid
        return upcoming_result_valid(result) and _rows(result.get("occasions")) and all(_text(row.get("occasion_id"))
            and _int(row.get("month"), 1) and row["month"] <= 12 and _int(row.get("day"), 1) and row["day"] <= 31
            and row.get("calendar") in {"GREGORIAN", "LUNAR"} for row in result["occasions"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def life_display_read_recorded(action, result):
    """Finite public DTO shape only; not source evidence or write permission.

    If any private proof field is present, require the complete strict raw
    proof shape. A sanitized DTO must not reintroduce private hashes.
    """
    if not isinstance(result, dict):
        return False
    if any(key in result for key in ("owner_scope_hash", "read_fingerprint")):
        return life_read_recorded(action, result)
    try:
        if (action not in READ_ACTIONS or result.get("schema_version") != SCHEMAS[action]
                or result.get("action") != action or result.get("read_schema_version") != READ_VERSION
                or result.get("bank_state_changed") is not False):
            return False
        if action == "OCCASION_MEMBER_VIEW": return _members(result)
        if action == "LIFE_PLAN_VIEW": return _plans(result)
        if action == "MERCHANT_ORDERS": return _orders(result)
        if action == "MERCHANT_CATALOG":
            from .merchant_catalog_selection import category_scope_valid
            return category_scope_valid(result) and _rows(result.get("products")) and result.get("external_merchant_connected") is False and all(
                _text(row.get("product_id")) and _text(row.get("merchant_name")) and _text(row.get("product_name"))
                and _int(row.get("unit_price_minor"), 1) and row.get("currency") in {"CNY", "USD"}
                and _int(row.get("stock")) for row in result["products"])
        from .occasion_upcoming import upcoming_result_valid
        return upcoming_result_valid(result) and _rows(result.get("occasions")) and all(_text(row.get("occasion_id"))
            and _text(row.get("family_member_name")) and _int(row.get("month"), 1) and row["month"] <= 12
            and _int(row.get("day"), 1) and row["day"] <= 31 and row.get("calendar") in {"GREGORIAN", "LUNAR"}
            and _text(row.get("timezone")) for row in result["occasions"])
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def life_write_recorded(action, result):
    try:
        if not isinstance(result, dict) or result.get("error"): return False
        if action == "OCCASION_MEMBER_SET":
            return (result.get("schema_version") == "family-profile-v1" and _members(result) and len(result["members"]) == 1
                and result.get("family_profile_state_changed") is True and result.get("bank_state_changed") is False)
        if action == "OCCASION_SET":
            return (result.get("schema_version") == "family-occasion-v1" and result.get("occasion_state_changed") is True
                and result.get("bank_state_changed") is False and _rows(result.get("occasions")) and len(result["occasions"]) == 1)
        if action == "LIFE_PLAN_AUTHORIZE":
            receipt = result.get("authorization_receipt") or {}
            if "reservation_adjustment" in result:
                from .life_reservation_replacement import public_adjustment_valid
                if (not public_adjustment_valid(result["reservation_adjustment"]) or not _plans(result)
                        or len(result["plans"]) != 1
                        or result["reservation_adjustment"]["new_reserved_minor"] != result["plans"][0]["budget_minor"]):
                    return False
            return (result.get("schema_version") == "life-plans-v1" and _plans(result) and len(result["plans"]) == 1
                and result.get("life_plan_state_changed") is True and result.get("cash_debit_minor") == 0
                and receipt.get("maximum_occurrences") == 1 and _text(receipt.get("mandate_id"))
                and receipt.get("plan_id") == result["plans"][0]["plan_id"])
        if action == "LIFE_PLAN_CANCEL":
            return result.get("schema_version") == "life-plans-v1" and _plans(result) and len(result["plans"]) == 1 \
                and result["plans"][0]["status"] in {"CANCELLED", "COMPENSATING", "COMPENSATED", "PARTIAL"}
        if action == "MERCHANT_PURCHASE":
            receipt = result.get("payment") or {}
            return (result.get("schema_version") == "virtual-merchant-purchase-v1" and _orders(result) and len(result["orders"]) == 1
                and result["orders"][0]["status"] == "PAID" and receipt.get("schema_version") == "payment-receipt-v1"
                and receipt.get("status") == "POSTED" and _text(receipt.get("payment_id")) and receipt.get("ledger_balanced") is True
                and receipt.get("amount_minor") == result["orders"][0]["amount_minor"]
                and receipt.get("currency") == result["orders"][0]["currency"])
        return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _money(value, currency="CNY"):
    return f"{value / 100:.2f} {currency}" if _int(value) else "暂未提供"


def _pack(lines, limit=2200, *, english=False, footer=None):
    budget = limit - (len(footer) + 1 if footer else 0)
    kept = []
    for line in lines:
        if len("\n".join([*kept, line])) + 60 > budget: break
        kept.append(line)
    if len(kept) < len(lines):
        kept.append(f"See the cards for {len(lines) - len(kept)} further records." if english else f"其余{len(lines) - len(kept)}项请查看结果卡。")
    if footer:
        kept.append(footer)
    return "\n".join(kept)


def life_action_summary(action, result, *, english=False):
    """Finite complete facts; no truncating a number or claiming external fulfilment."""
    if english:
        return _english_summary(action, result)
    if action in READ_ACTIONS:
        if not life_display_read_recorded(action, result): return ("查询结果尚未核验。", "请重新查询；未知资料不作为零值。")
        if action == "OCCASION_MEMBER_VIEW":
            lines = [row["family_member_name"] + (" · " + row["family_relationship"] if row.get("family_relationship") else "") for row in result["members"]]
            return "已查到本人家庭成员资料。", (_pack(lines) or "目前没有登记的家庭成员，可以先告诉我显示名和关系。") + "\n这些资料不授予家人账户或付款权限。"
        if action == "OCCASION_VIEW":
            from .occasion_upcoming import upcoming_summary
            summary=upcoming_summary(result)
            if summary:return summary
            lines = [f"{row.get('family_member_name', '家庭成员')}：{'阳历' if row['calendar'] == 'GREGORIAN' else '农历'}{row['month']}月{row['day']}日，时区{row['timezone']}。"
                for row in result["occasions"]]
            return "已查到本次家庭生日资料。", _pack(lines) or "本次范围没有已确认的家庭生日。"
        if action == "LIFE_PLAN_VIEW":
            lines = [f"{row['name']}：{STATES[row['status']]}；预算{_money(row['budget_minor'])}，当前预留{_money(row['reserved_minor'])}，已付{_money(row['spent_minor'])}，已核验退款{_money(row['refunded_minor'])}。"
                for row in result["plans"]]
            lines.append("预留不是锁卡，授权不是已付款或已履约；仅本次有界范围有效。")
            return "已查到本人生活计划。", _pack(lines, footer=lines.pop())
        if action == "MERCHANT_CATALOG":
            lines = [f"{row['merchant_name']} · {row['product_name']}：{_money(row['unit_price_minor'], row['currency'])}，库存{row['stock']}。"
                for row in result["products"]]
            if len(result['products']) == 1:
                from .merchant_service_content import content_notice
                notice = content_notice(result['products'][0])
                if notice: lines.append(notice)
            return ("可以先看看这些商品。", _pack(lines)) if lines else ("暂时没有上架商品。", "可以稍后再看看。")
        lines = [f"{row['merchant_name']} · {row['product_name']}：{STATES[row['status']]}，原付款{_money(row['amount_minor'], row['currency'])}。"
            for row in result["orders"]]
        return "这是你的订单记录。", _pack(lines) or "目前还没有订单。"
    if not life_write_recorded(action, result): return "本次办理结果尚未核验。", "不会据此宣称付款、退款或履约成功。"
    if action == "OCCASION_MEMBER_SET": return "家庭成员资料已按本次确认保存。", "显示名和关系标签已保存；没有开通登录、账户或协助权限。生日还需单独确认。"
    if action == "OCCASION_SET": return "生日资料已按本次确认保存。", "这不会给家庭成员开通账户、授信或购物权限。"
    if action == "LIFE_PLAN_AUTHORIZE":
        row = result["plans"][0]
        intro = "新安排已替换原安排，本次有界授权已生效。" if result.get("replaced_plan") else "本次有界计划授权已生效。"
        if isinstance(result.get("reservation_adjustment"), dict):
            change = result["reservation_adjustment"]
            amount = change.get("additional_reserved_minor") or change.get("released_difference_minor")
            effect = "追加预留" if change.get("additional_reserved_minor") else "释放差额"
            return intro, (f"原预留{_money(change['previous_reserved_minor'])}已调整为{_money(change['new_reserved_minor'])}，"
                f"{effect}{_money(amount)}；原下单和配送排期保留。不是付款，也不是沿用旧确认。")
        return intro, ("已预留" + _money(row["reserved_minor"]) if row["reserved_minor"] else "尚未到约定预留时间，当前未占用这笔现金") + "；不是锁卡，也不是订单已付款或已履约的回执。"
    if action == "LIFE_PLAN_CANCEL":
        if result.get("no_effect") is True:
            return "这份计划此前已结束。", "本次没有再次撤销、退款或改动现金。"
        return "未执行的计划授权已撤销。", "剩余预留已按记录处理；已支付部分以各订单退款记录为准，不把退款中说成已到账。"
    row = result["orders"][0]
    return "这笔订单已付款。", f"付款{_money(row['amount_minor'], row['currency'])}，订单尚待商户履约。"


def _english_summary(action, result):
    states = {"DRAFT": "awaiting your authorization", "ACTIVE": "authorized; waiting for scheduled steps",
        "PAUSED_RECONFIRM": "paused for renewed review", "PARTIAL": "partly completed", "CANCELLING": "cancelling",
        "COMPENSATING": "refunds in progress", "COMPENSATED": "original-source refunds verified",
        "CANCELLED": "unexecuted steps cancelled", "SUCCEEDED": "this occurrence completed",
        "PAID": "paid, not yet fulfilled", "FULFILLED": "merchant fulfilment recorded",
        "REFUND_PENDING": "refund in progress, not yet credited", "REFUNDED": "refunded to the original source",
        "SOURCE_UNVERIFIED": "source unverified", "FULFILMENT_UNVERIFIED": "fulfilment unverified"}
    if action in READ_ACTIONS:
        if not life_display_read_recorded(action, result):
            return "The query result is not verified.", "Unavailable facts are not treated as zero."
        if action == "OCCASION_MEMBER_VIEW":
            lines = [row["family_member_name"] + (" · " + row["family_relationship"] if row.get("family_relationship") else "") for row in result["members"]]
            return "Your family directory is available.", (_pack(lines, english=True) or "No family members have been saved yet.") + "\nDirectory details grant no account or payment permission."
        if action == "OCCASION_VIEW":
            from .occasion_upcoming import upcoming_summary
            summary=upcoming_summary(result,english=True)
            if summary:return summary
            lines = [f"{row.get('family_member_name', 'Family member')}: {row['month']}/{row['day']} "
                f"({'Gregorian' if row['calendar'] == 'GREGORIAN' else 'lunar'}), {row['timezone']}." for row in result["occasions"]]
            return "The selected family birthday records are available.", _pack(lines, english=True) or "No confirmed birthday is in this scope."
        if action == "LIFE_PLAN_VIEW":
            lines = [f"{row['name']}: {states[row['status']]}; budget {_money(row['budget_minor'])}, "
                f"reserved {_money(row['reserved_minor'])}, paid {_money(row['spent_minor'])}, verified refunds {_money(row['refunded_minor'])}."
                for row in result["plans"]]
            lines.append("A reservation is not a card lock. Authorization is not payment or fulfilment; it covers this bounded occurrence only.")
            return "Your life-plan records are available.", _pack(lines, english=True, footer=lines.pop())
        if action == "MERCHANT_CATALOG":
            lines = [f"{row['merchant_name']} · {row['product_name']}: {_money(row['unit_price_minor'], row['currency'])}, stock {row['stock']}."
                for row in result["products"]]
            if len(result['products']) == 1:
                from .merchant_service_content import content_notice
                notice = content_notice(result['products'][0], 'en')
                if notice: lines.append(notice)
            return ("You can browse these products.", _pack(lines, english=True)) if lines else ("There are no listed products yet.", "Please check again later.")
        lines = [f"{row['merchant_name']} · {row['product_name']}: {states[row['status']]}; original payment {_money(row['amount_minor'], row['currency'])}."
            for row in result["orders"]]
        return "Here are your order records.", _pack(lines, english=True) or "There are no orders yet."
    if not life_write_recorded(action, result):
        return "The outcome is not yet verified.", "No payment, refund or fulfilment is claimed."
    if action == "OCCASION_MEMBER_SET":
        return "The confirmed family details were saved.", "No login, account or banking permission was created. Birthday details need separate confirmation."
    if action == "OCCASION_SET":
        return "The confirmed birthday record was saved.", "This grants the family member no account, credit or payment permission."
    if action == "LIFE_PLAN_AUTHORIZE":
        row = result["plans"][0]
        detail = f"Reserved {_money(row['reserved_minor'])}." if row["reserved_minor"] else "The reservation date has not arrived; no cash is reserved yet."
        intro = "The new authorized arrangement replaced the original." if result.get("replaced_plan") else "This bounded plan is authorized."
        if isinstance(result.get("reservation_adjustment"), dict):
            change = result["reservation_adjustment"]
            amount = change.get("additional_reserved_minor") or change.get("released_difference_minor")
            effect = "Additional cash reserved" if change.get("additional_reserved_minor") else "Difference released"
            return intro, (f"The previous {_money(change['previous_reserved_minor'])} reservation is now {_money(change['new_reserved_minor'])}. "
                f"{effect}: {_money(amount)}. The original ordering and delivery schedule is preserved; this is not payment or reuse of an old confirmation.")
        return intro, detail + " This is not a card lock, payment or fulfilment receipt."
    if action == "LIFE_PLAN_CANCEL":
        if result.get("no_effect") is True:
            return "This plan had already ended.", "No further revocation, refund or cash change was made."
        return "Unexecuted plan authority was revoked.", "Remaining reservations and original-source refunds are separate; a pending refund is not a verified credit."
    row = result["orders"][0]
    return "This order has been paid.", f"Paid {_money(row['amount_minor'], row['currency'])}; awaiting merchant fulfilment."
