"""Public daily reference rate only; never a model-supplied execution price."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from threading import Lock

import httpx

from .contracts import DomainError
from .models import utcnow
from .simulation import as_utc


RATE_URL = "https://api.frankfurter.dev/v2/providers/ecb/rate/usd/cny"
CACHE_TTL = timedelta(minutes=30)
QUOTE_TTL = timedelta(seconds=120)
MAX_RATE_AGE_DAYS = 7
_cache = None
_cache_lock = Lock()


def decimal_text(value):
    return format(Decimal(value).normalize(), "f")


def validate_market_rate(data, *, now=None):
    now = as_utc(now or utcnow())
    if not isinstance(data, dict) or data.get("provider") not in {"FRANKFURTER_ECB", "TEST_STUB"}:
        raise DomainError("FX_RATE_UNTRUSTED", "必须使用服务器受控参考汇率", 409)
    try:
        raw = data["rate"]
        if isinstance(raw, (bool, float)):
            raise ValueError()
        rate = Decimal(raw)
        market_date = date.fromisoformat(data["market_date"])
        fetched = as_utc(datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00")))
        if not rate.is_finite() or not Decimal("1") <= rate <= Decimal("20") or rate.as_tuple().exponent < -8:
            raise ValueError()
        if fetched > now + timedelta(seconds=5) or now - fetched > CACHE_TTL:
            raise DomainError("FX_RATE_CACHE_EXPIRED", "参考汇率缓存已过期，请重新获取", 409)
        if not 0 <= (now.date() - market_date).days <= MAX_RATE_AGE_DAYS:
            raise DomainError("FX_RATE_STALE", "参考汇率日期过旧或来自未来，无法生成资金方案", 409)
        source = data["source_url"]
        if data["provider"] == "FRANKFURTER_ECB" and source != RATE_URL:
            raise ValueError()
        if data["provider"] == "TEST_STUB" and source != "test://fx-rate":
            raise ValueError()
    except DomainError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise DomainError("FX_RATE_INVALID", "参考汇率内容、日期或合法范围不正确", 409) from None
    return {"rate": decimal_text(rate), "provider": data["provider"], "market_date": market_date.isoformat(),
            "fetched_at": fetched.isoformat(), "source_url": source, "reference_only": True}


def _fetch_public_rate():
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(RATE_URL, headers={"Accept": "application/json"})
        response.raise_for_status()
        return json.loads(response.text, parse_float=Decimal)


def get_current_market_rate(*, fetcher=None, now=None, force_refresh=False):
    """Call outside any DB transaction. Fake injection is explicit TEST_STUB."""
    global _cache
    now = as_utc(now or utcnow())
    if fetcher is None and not force_refresh:
        with _cache_lock:
            if _cache is not None:
                try:
                    return validate_market_rate(deepcopy(_cache), now=now)
                except DomainError:
                    pass
    try:
        raw = (fetcher or _fetch_public_rate)()
        if isinstance(raw, dict) and "market_date" in raw:
            if fetcher is None:
                raise ValueError()
            data = {**raw, "provider": "TEST_STUB", "source_url": "test://fx-rate", "fetched_at": now.isoformat()}
        else:
            if not isinstance(raw, dict) or raw.get("base", "").upper() != "USD" or raw.get("quote", "").upper() != "CNY":
                raise ValueError()
            data = {"rate": raw["rate"], "market_date": raw["date"], "fetched_at": now.isoformat(),
                    "provider": "TEST_STUB" if fetcher else "FRANKFURTER_ECB", "source_url": "test://fx-rate" if fetcher else RATE_URL}
        checked = validate_market_rate(data, now=now)
    except DomainError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError, InvalidOperation):
        raise DomainError("FX_RATE_UNAVAILABLE", "公开参考汇率暂不可用；未使用模型估计或伪造价格", 503) from None
    if fetcher is None:
        with _cache_lock:
            _cache = deepcopy(checked)
    return checked


def public_rate(data, *, now=None):
    data = validate_market_rate(data, now=now)
    fetched = datetime.fromisoformat(data["fetched_at"])
    return {**data, "base_currency": "USD", "quote_currency": "CNY", "expires_at": (fetched + CACHE_TTL).isoformat(),
            "max_age_days": MAX_RATE_AGE_DAYS, "simulated_execution_price": True,
            "fee_policy": {"fee_minor": 0, "source": "SIM_POLICY"},
            "notice": "ECB日频中间参考汇率，不是银行实时成交价；本平台按参考汇率与零手续费政策记账。"}
