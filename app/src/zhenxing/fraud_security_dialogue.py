"""Reported security questions are read-only consultations, not credentials.

This classifier supplies no approval, permission, payee, amount or risk clearance.
Credential redaction and the existing credential-only guard remain independent.
"""
import re


_REPORTED = re.compile(
    r"(?:有人|对方|他|她|那人|陌生人|客服|骗子|来电|电话里|短信).{0,48}(?:让我|叫我|要求|要我|告诉我|说|索要)|"
    r"\b(?:someone|somebody|they|he|she|the\s+(?:caller|agent|support)|a\s+stranger|customer\s+support).{0,65}"
    r"\b(?:asked?|asks?|told|requires?|requested?|said|says|wants?|needs?)\b",re.I)
_SUBJECT = re.compile(
    r"屏幕共享|共享屏幕|远程(?:控制|操控|操作)|验证码|校验码|动态码|密码|银行卡|安全账户|退款|退费|保证金|诈骗|骗局|保密|"
    r"\b(?:screen[- ]sharing|share\s+(?:my|your|the)\s+screen|remote\s+(?:access|control)|"
    r"OTP|verification\s+code|password|passcode|bank\s+(?:account|card)|safe\s+account|refund|deposit)\b",re.I)
_INQUIRY = re.compile(
    r"靠谱|可靠|安全吗|是否安全|有.{0,8}(?:风险|诈骗)|诈骗风险|是不是(?:诈骗|骗局)|是.{0,5}骗局吗|"
    r"能信(?:吗|么)|可信吗|该不该|应该.{0,15}[吗么？?]|能.{0,15}(?:给|告诉|提供).{0,8}[吗么？?]|"
    r"(?:判断|分析|核实)[^，,。；;!?！？\n]{0,20}(?:风险|诈骗|骗局|说法)|"
    r"\b(?:is\s+(?:this|that|it)\s+(?:safe|legit|a\s+scam|reliable)|"
    r"could\s+(?:this|that|it)\s+be\s+(?:fraud|a\s+scam)|"
    r"should\s+I\s+(?:give|send|provide|share|tell)|(?:fraud|scam|security)\s+risk|"
    r"check\s+(?:if|whether).{0,25}(?:safe|scam|fraud))\b",re.I)
_BARE_CONFIRM = re.compile(r"\s*(?:确认|确定|同意|好(?:的)?|办理吧|yes|ok|okay|confirm|confirmed|go\s+ahead|proceed)\s*[。.!！]?\s*",re.I)
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|`[^`\n]*`')
_DECLINED_READ = re.compile(r"(?:不要|别|不想|拒绝|无需|不需要|不许|禁止|先不|暂不)\s*(?:请|你|帮我|再)?\s*"
    r"(?:分析|判断|评估|核实|核验|检查|读取|查询|查看)[^，,。；;!?！？\n]{0,25}(?:风险|反诈|安全账户|诈骗|骗局)|"
    r"\b(?:do\s+not|don't|never)\s+(?:analy[sz]e|assess|check|read|evaluate)\b.{0,40}\b(?:risk|fraud|scam)\b",re.I)
_INQUIRY_DENIAL = re.compile(r"(?:不要|别|不想|拒绝|无需|不需要|禁止|先不|暂不)\s*(?:请|你|帮我|再)?\s*$|"
    r"\b(?:do\s+not|don't|never|not)\s*$",re.I)
_REPORTED_PREFIX = re.compile(r"(?:对方|他|她|客服|骗子|短信|网页)(?:说|问|要求|写着)|引用|内容[：:]|"
    r"\b(?:they|he|she|the\s+caller)\s+(?:said|says|asked)\b",re.I)


def _user_inquiries(message):
    text=_QUOTED.sub(lambda match:' '*len(match.group()),message)
    result=[]
    for inquiry in _INQUIRY.finditer(text):
        prefix=re.split(r"[，,。；;!?！？\n]",text[max(0,inquiry.start()-60):inquiry.start()])[-1]
        if _INQUIRY_DENIAL.search(prefix):
            continue
        # An unquoted reported question is still the third party's statement,
        # unless the user explicitly asks for their own assessment afterwards.
        if _REPORTED_PREFIX.search(prefix) and not re.search(r"我(?:想|来|要)?(?:问|判断|分析)|帮我|请你|\bI\s+(?:ask|want|need)\b",prefix,re.I):
            continue
        result.append(inquiry)
    return text,result


def security_read_declined(message: str) -> bool:
    if not isinstance(message,str) or not 1<=len(message)<=4000:
        return False
    text,inquiries=_user_inquiries(message)
    denials=[]
    for denial in _DECLINED_READ.finditer(text):
        prefix=re.split(r"[，,。；;!?！？\n]",text[max(0,denial.start()-45):denial.start()])[-1]
        if not _REPORTED_PREFIX.search(prefix): denials.append(denial)
    if not denials: return False
    last=denials[-1]
    for inquiry in inquiries:
        if inquiry.start()<=last.end(): continue
        between=text[last.end():inquiry.start()]
        if re.search(r"[。；;\n]\s*(?:现在|另外|但我|我(?:现在|想|来)?|请|帮我)|"
                     r"[，,]\s*(?:现在|另外|但我).{0,15}(?:请|问|分析|判断)|"
                     r"[.;\n]\s*(?:now|separately|instead|I\s+want|please)",between,re.I):
            return False
    return True


def reported_security_question(message: str, *, credential_only: bool = False) -> bool:
    if credential_only or not isinstance(message,str) or not 1<=len(message)<=4000:
        return False
    if _BARE_CONFIRM.fullmatch(message):
        return False
    if security_read_declined(message): return False
    return bool(_REPORTED.search(message) and _SUBJECT.search(message) and _user_inquiries(message)[1])


def reported_security_read_request(message: str, *, credential_only: bool = False) -> bool:
    """A security question or a concrete high-risk third-party report.

    Proactive warnings analyse the report only. They never inherit its payee,
    amount, credentials or instruction as permission for a banking write.
    Ordinary reported commands and examples are not new banking requests.
    """
    if reported_security_question(message, credential_only=credential_only):
        return True
    if credential_only or not isinstance(message, str) or not 1 <= len(message) <= 4000:
        return False
    if _BARE_CONFIRM.fullmatch(message) or security_read_declined(message):
        return False
    visible = _QUOTED.sub(lambda match:' '*len(match.group()), message)
    if re.match(r'^\s*(?:引用|示例|假设|假如|quote|example|for example)\s*[：:]?', visible, re.I):
        return False
    reported = _REPORTED.search(visible) or re.search(r'(?:朋友|同事).{0,30}(?:让我|要求|说|告诉我)', visible)
    if not reported:
        return False
    from .fraud_signals import risk_reason_codes
    return bool(risk_reason_codes(message))


def family_permission_question(message: str) -> bool:
    """A scope-design question, not permission to create a delegation."""
    if not isinstance(message,str) or not 1<=len(message)<=4000:
        return False
    family=re.search(r"孩子|家人|儿子|女儿|父母|爸爸|妈妈|\b(?:family|child|kid|son|daughter|parent)\b",message,re.I)
    scope=re.search(r"(?:查|看|查看|查询).{0,10}余额|只(?:读|授权)|仅(?:查看|查询)|授权|委托|"
                    r"\b(?:balance|read[- ]only|delegat\w*|authoriz\w*|permission|access)\b",message,re.I)
    question=re.search(r"咨询|只是问|只想问|能这样|可以这样|能否|可不可以|能不能|是否可以|[吗么？?]|"
                       r"\b(?:can|could|may|whether|possible|only\s+asking|just\s+asking)\b",message,re.I)
    if not (family and scope and question):
        return False
    # A new positive creation request is not downgraded into mere advice.
    for command in re.finditer(r"(?:现在|立即|马上|请帮我|帮我|请).{0,15}(?:建立|创建|设置|发起|授权)|"
                               r"\b(?:now|please)\b.{0,30}\b(?:create|establish|grant|authorize)\b",message,re.I):
        prefix=message[max(0,command.start()-12):command.end()]
        if not re.search(r"不要|别|先不|不(?:要)?建立|not|don't|do\s+not",prefix,re.I):
            return False
    return True
