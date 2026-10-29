"""Credential minimization at untrusted text boundaries, not banking authority.

No raw secret is returned by diagnostics. Hashes and simulated card identifiers
are not secrets; do not damage their use for receipts and scope verification.
"""
import re


_BARE_KEY = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])", re.I)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}", re.I)
_EN_LABEL = re.compile(r"((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|verification[_ -]?code|passcode|credential|secret)\s*(?:[:：=]|\bis\b)\s*)[^\s，。；;]{4,256}", re.I)
_EN_CODE = re.compile(r"(\b(?:verification\s+code|passcode)\s+(?:(?:is|was)\s+)?)(?=[A-Za-z0-9_-]{0,63}[0-9])[A-Za-z0-9_-]{4,64}\b", re.I)
_ZH_LABEL = re.compile(r"((?:验证码|校验码|动态码|密码|密钥)\s*(?:是|为|[:：=])?\s*)[A-Za-z0-9_-]{4,256}", re.I)
_ZH_NON_ASCII = re.compile(r"((?:密码|密钥)\s*(?:是|为|[:：=])\s*)[^\s，。；;]{4,256}", re.I)
_VALUE_CONTEXT = re.compile(r"((?:验证码|校验码|动态码|密码|密钥|口令|token|api[_ -]?key)\s*"
    r"(?:是不是|会不会是|收到的是|填的是|需要填|要填|应该填|设置为|改成|改为|换成)\s*[：:=]?\s*)"
    r"[A-Za-z0-9_-]{4,256}", re.I)
_OTP_VALUE = re.compile(r"(\bOTP\s*(?:是|为|[:：=])?\s*)[0-9]{4,8}\b", re.I)


def redact_credentials(value: str) -> str:
    """Preserve noncredential text and explicit banking quantities."""
    text = _BARE_KEY.sub("[REDACTED]", value)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    def label_value(match):
        prefix = match.group(1)
        token = match.group(0)[len(prefix):].lower().strip(".,!?")
        # Natural verification requirements are not supplied codes. Explicit
        # colon/equal values and quoted strings still redact even these words.
        if re.search(r"(?:verification[_ -]?code|passcode)\s+is\s*$", prefix, re.I) and token in {
                "required", "needed", "missing", "incorrect", "expired", "invalid", "optional", "unavailable"}:
            return match.group(0)
        return prefix + "[REDACTED]"
    for pattern in (_EN_LABEL, _EN_CODE, _ZH_LABEL, _ZH_NON_ASCII, _VALUE_CONTEXT, _OTP_VALUE):
        text = pattern.sub(label_value, text)
    return text
