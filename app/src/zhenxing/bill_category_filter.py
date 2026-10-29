"""Explicit category narrowing, never a new period or a bank authorization."""
import re


FILTER_VERSION = "bill-category-filter-v1"


def category_filter_in_text(text: str) -> str | None:
    source = re.sub(r"[。！？!?]+$", "", text.strip())
    english = re.fullmatch(r"(?:please\s+)?(?:only\s+(?:show|list|check)|show\s+only|filter\s+(?:to|by))\s+(?P<category>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z _-]{0,31}?)(?:\s+(?:spending|expenses?|transactions?|bills?))?[.!?\s]*", source, re.I)
    if english:
        category = english.group("category").strip()
        if not re.search(r"\b(?:only|my|this|last|current|month|year|his|her|their|friend|all|balance|card|account|not|if)\b", category, re.I):
            return category
    match = re.fullmatch(r"(?:请|帮我)?(?:只看|仅看|只查|只查询|只统计|筛选|过滤)(?P<category>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z _-]{0,31}?)(?:的)?(?:账单|帐单|支出|消费|记录|明细)?", source)
    if not match:
        # Named category before an explicit bill subject also narrows a normal
        # query. Dates are kept outside this parser and handled by reports.
        match = re.search(r"(?:只看|仅看|只查|只查询|只统计|筛选|过滤)\s*(?P<category>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z _-]{0,31}?)(?:的)?(?:账单|帐单|支出|消费|记录|明细)(?=[，,。；;]|$)", source)
    if not match:
        return None
    category = match.group("category").strip()
    if not category or re.search(r"本月|这个月|当月|上月|上个月|今年|去年|银行卡|卡片|账户|余额|目标|订阅|授权|转账|不要|不看|如果|假如|示例|内容|说|[“”「」]", category):
        return None
    return category
