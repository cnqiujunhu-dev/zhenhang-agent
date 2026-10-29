"""Private source proof for one validated cloud semantic decision.

No rule proposal/action-keyword classifier, model/network calls, bank writes or
confirmations live here. The model selects registered kinds and offered refs;
the server reparses literal fields and rechecks DB actor/source/lifecycle facts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread,ChatTurn
from .dialogue_state import ACTION_ALLOWED_FIELDS,dialogue_actor_scope
from .intelligence import ActionSlots,IntentProposal,ProposedAction,READ_KINDS
from .models import Task,Confirmation
from .planning import canonical_hash
from .redaction import redact_credentials
from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
from .semantic_source_bundle import entity_catalog as _entity_catalog

VERSION='verified-cloud-semantic-intent-v1'
METADATA_KEY='cloud_semantic_intent'
PRIVATE_KEY='_verified_semantic_intent'
RECEIPT_KEY='semantic_receipt'
_AUTHORITY=object()
_SEAL_KEY=secrets.token_bytes(32)
_MUTABLE={'QUEUED','UNDERSTANDING','PLANNING','NEED_CLARIFICATION','AWAITING_CONFIRMATION'}
_IDENTITIES={'payee_name','merchant','product_name','card_number','card_last4','owner_name','grantee_name','goal_name','behavior_name','reward_name','recipient_alias','benefit_name','preference_value','preference_kind'}
_MONEY={'amount_minor','maximum_amount_minor','target_minor','monthly_saving_minor','monthly_saving_delta_minor','monthly_saving_total_minor'}
_IDENTITIES.update({'recipient_card_reference', 'destination_card_reference', 'card_reference', 'source_card', 'aa_obligation_ref',
    'payment_id', 'installment_id', 'application_id', 'family_member_name', 'family_member_id', 'occasion_id',
    'life_plan_id', 'life_plan_name', 'merchant_product_id', 'recipient_name', 'recipient_phone', 'recipient_address'})
_IDENTITIES.update({'family_member_new_name', 'family_relationship', 'aa_note','aa_collection_ref'})
_IDENTITIES.add('schedule_reference')
_MONEY.update({'budget_minor', 'requested_limit_minor'})


def _deny(code='SEMANTIC_SOURCE_CHANGED'):
    messages={
        'SEMANTIC_MONEY_NOT_EXACT':'这段金额还没准确识别，请补充一个明确金额和币种；已有收款人信息会保留。没有执行业务。',
        'SEMANTIC_MONEY_SOURCE_NOT_CLOSED':'这段金额含有约数、区间、负号或未读完整的数字，请给出一个确定金额；已有需求会保留，没有执行业务。',
        'SEMANTIC_CURRENCY_NOT_EXACT':'这段币种还没核对清楚，请明确人民币或美元；已有信息会保留。没有执行业务。',
        'SEMANTIC_CURRENCY_CONFLICT':'这次金额和币种存在冲突，请核对希望使用的币种。没有执行业务。',
        'SEMANTIC_FIELD_NOT_SUPPORTED':'这项服务的该信息暂未支持，之前的需求会保留；没有准备新的执行方案。',
        'SEMANTIC_FIELD_NOT_EXACT':'这项信息还没有准确核对好，之前的需求会保留；没有执行业务。',
        'SEMANTIC_ENTITY_FIELD_MISMATCH':'这次引用的对象信息暂未对齐，之前的查询结果会保留；没有准备新的执行方案。',
        'SEMANTIC_CATEGORY_NOT_EXACT':'这项账单分类还没核对清楚，可以指定一个类别或查看全部；不会修改账户或执行付款。',
        'SEMANTIC_IDENTITY_SPAN_INCOMPLETE':'收款人、服务或卡片名称没有读取完整，请补充完整名称；已有需求会保留，没有执行业务。',
        'SEMANTIC_CLEAR_NOT_EXPLICIT':'还不能确定你要去掉哪项信息，请明确要清除的内容；已有方案会保留，没有执行业务。',
        'SEMANTIC_MATERIAL_ACTION_NOT_ALLOWED':'这段历史话术只能用于风险分析，不能用作收款人、金额或其他办理信息；没有执行业务。',
        'SEMANTIC_QUOTED_FIELD_SOURCE':'这项办理信息来自引用话术，不能直接作为你的操作指令；请直接说明自己的需求。没有执行业务。',
        'SEMANTIC_UNTRUSTED_LITERAL':'这项信息来自转述或外部材料，需要你直接说明自己的办理需求；没有执行业务。',
        'SEMANTIC_SCOPE_NOT_EXACT':'家庭协助权限还没核对清楚，请说明允许查看余额、提出转账建议或提出锁卡建议中的哪些项；没有建立授权。',
        'SEMANTIC_SCOPE_DENIED':'这段权限说明同时含有限制或否定，尚不能按它建立授权；请明确希望允许的权限。',
        'SEMANTIC_OPTION_NOT_ALLOWED':'这项设置不在服务支持的选项内，或同时选择了冲突选项；请重新说明希望的设置。没有执行业务。',
        'SEMANTIC_ENTITY_NOT_OFFERED':'这次引用的对象不在本会话可用的已核验资料中；请说明具体对象或重新查询。没有执行业务。',
        'SEMANTIC_FIELD_NOT_ALLOWED':'这项信息不能用于当前服务；已有需求会保留，没有准备新的执行方案。',
        'SEMANTIC_READ_CLONE_SOURCE_CHANGED':'之前的只读查询来源或状态已变化，请重新查询；没有修改原记录或执行银行写操作。',
        'SEMANTIC_READ_CLONE_BINDING_INVALID':'本次只读跟进与原查询的关联未通过核验，请重新查询；原记录没有被修改。',
        'SEMANTIC_TEMPORAL_ROLE_REQUIRED':'请明确这项日期是否为AA收款截止时间；尚未发起收款，也不会把未来发起时间当作截止时间。',
    }
    raise DomainError(code,messages.get(code,'这次理解的来源、对象或权限已变化，请重新核对；不会据此执行。'),409)


def _owned(row,principal):
    return row is not None and (row.user_id,row.sandbox_id)==(principal.user_id,principal.sandbox_id)


def _plain(value):
    return value.model_dump(exclude_none=True) if hasattr(value,'model_dump') else deepcopy(value)


def _validated_result(result,offered,message):
    from .semantic_router import is_verified_semantic_result,serialize_semantic_receipt,semantic_input_hash,prepare_semantic_input,validate_semantic_decision
    model=offered.get('model') if isinstance(offered,dict) else None
    if not isinstance(model,dict):_deny('SEMANTIC_CONTEXT_INVALID')
    params={**model,'message':message}
    if model.get('message',message)!=message:_deny()
    hashed=semantic_input_hash(**params)
    if not is_verified_semantic_result(result,input_hash=hashed) or result.status!='READY':_deny('SEMANTIC_CLOUD_RECEIPT_REQUIRED')
    receipt=serialize_semantic_receipt(result)
    validate_semantic_decision(deepcopy(receipt['decision']),prepare_semantic_input(**params))
    if receipt['source_message_hash']!=canonical_hash({'message':message}):_deny()
    return receipt


def _valid_turn(session,principal,thread,turn):
    if isinstance(thread,str):thread=session.get(ChatThread,thread)
    if not _owned(thread,principal) or thread.status!='ACTIVE' or not _owned(turn,principal) or turn.thread_id!=thread.id \
            or turn.error_code or turn.diagnostics_json.get('input_redacted') or redact_credentials(turn.message)!=turn.message:_deny()
    scope,epoch=dialogue_actor_scope(session,principal)
    diagnostics=turn.diagnostics_json or {}
    if diagnostics.get('actor_role_at_submission')!=scope['role'] or diagnostics.get('dialogue_actor_scope_hash')!=epoch:_deny()
    from .dialogue_state_models import ConversationDialogueState
    state=session.get(ConversationDialogueState,thread.id)
    if state and (state.actor_scope_hash!=epoch or turn.sequence<=(state.checkpoint_json or {}).get('epoch_start_sequence',0)):_deny()
    return thread,scope,epoch


def _span(message,span):
    if not isinstance(span,dict) or set(span)!={'start','end'} or type(span['start']) is not int or type(span['end']) is not int \
            or not 0<=span['start']<span['end']<=len(message):_deny('SEMANTIC_LITERAL_SPAN_INVALID')
    return message[span['start']:span['end']]


def _aa_selected_quantity_total(message,span,slots):
    """Exact per-person arithmetic for a model-selected AA amount and roster.

    No participant or amount is discovered here. The current selected literal
    and the already verified roster are the only operands. Recipient identity
    resolution later rejects aliases that resolve to the same participant.
    """
    raw=_span(message,span)
    before=message[:span['start']]
    each=bool(re.search(r'(?:每人|每位|每个人|各自|各)\s*$',before)
        or re.match(r'\s*(?:每人|每位|每个人)\s*',raw)
        or re.match(r'\s*(?:per\s+person|each)\b',message[span['end']:],re.I))
    if not each:return
    names=slots.get('participant_names')
    if not isinstance(names,list) or not 2<=len(names)<=20 or len(set(names))!=len(names):
        _deny('SEMANTIC_AA_PARTICIPANTS_REQUIRED')
    amount=slots.get('amount_minor')
    if type(amount) is not int or amount<=0:_deny('SEMANTIC_MONEY_NOT_EXACT')
    slots['amount_minor']=amount*len(names)


def _trusted_span(message,span,field=None,*,prior=None,allow_payment_purpose=False):
    from .intelligence import _source_spans
    raw=_span(message,span)
    # A customer's own AA note is an opaque argument, like a transfer memo.
    # Its comma-delimited end must not taint a sibling deadline. This exception
    # never releases names, money or other instructions from note/source text.
    own_aa_note=False
    own_aa_deadline=False
    if field in {'aa_note','aa_expires_at'}:
        from .intelligence import _transfer_memo_data
        note,issue,note_span=_transfer_memo_data(message)
        sources=_source_spans(message)
        if note and not issue and note_span:
            outer=[(start,end) for start,end in sources if start<=note_span[0]<end]
            direct=all(start==note_span[0] and message[start:].startswith('备注') for start,end in outer)
            if direct:
                pairs={'“':'”','「':'」','"':'"','‘':'’',"'":"'"}
                own_aa_note=field=='aa_note' and note_span[0]<=span['start']<span['end']<=note_span[1] and (
                    raw==note or len(note)>1 and pairs.get(note[0])==note[-1] and raw==note[1:-1])
                own_aa_deadline=field=='aa_expires_at' and note_span[1]<=span['start'] and bool(
                    re.fullmatch(r'[,，][^，,。;；\n“”"‘’]*',message[note_span[1]:span['start']])) and all(
                    start==note_span[0] for start,end in sources if span['start']<end and span['end']>start)
    if field in _MONEY:
        # Validate the numeric field's original boundaries, not user intent.
        # A model must not wash "about 120", "120 or 150", "-120" or a
        # decimal into an exact sum by selecting only a convenient substring.
        # Distinct amounts in separate action clauses remain independent.
        start,end=span['start'],span['end']
        number_chars='0123456789零〇一二两三四五六七八九十百千万亿.点'
        if (start and raw[0] in number_chars and message[start-1] in number_chars) \
                or (end<len(message) and raw[-1] in number_chars and message[end] in number_chars):
            _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
        before=re.split(r'[，,。；;!?！？\n]',message[:start])[-1][-60:]
        after=re.split(r'[，,。；;!?！？\n]',message[end:])[0][:60]
        # A denomination outside the selected amount must not hide its nearby
        # qualifier (e.g. "约人民币1元" or "1元人民币左右"). Keep the original
        # before/after values for currency-conflict checks below.
        qualifier_before=re.sub(r'(?:人民币|美元|美金|CNY|RMB|USD)\s*$','',before,flags=re.I)
        qualifier_after=re.sub(r'^\s*(?:人民币|美元|美金|CNY|RMB|USD)\s*','',after,flags=re.I)
        major_unit=r'(?:元|块钱|块)'
        digit=r'[\d零〇一二两三四五六七八九]'
        if re.search(digit+r'\s*'+major_unit+r'\s*$',before) \
                or re.search(major_unit+r'\s*$',raw) and re.match(r'\s*'+digit,after) \
                or raw[-1] in number_chars and re.match(r'\s*'+major_unit+r'\s*'+digit,after) \
                or re.search(major_unit+r'\s*'+digit+r'$',raw) and re.match(r'\s*(?:角|毛|分)',after):
            _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
        if re.search(r'(?:[-+−–—/÷⁄]|负|约|大约|大概|差不多|around|about|approximately|roughly|negative|minus)\s*$',qualifier_before,re.I) \
                or re.match(r'\s*(?:左右|上下|出头|多一点|approximately\b|roughly\b)',qualifier_after,re.I):
            _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
        if field!='maximum_amount_minor' and (
                re.search(r'(?:至少|最多|不超过|不低于|at\s+(?:least|most)|up\s+to)(?:为|是)?\s*$',qualifier_before,re.I)
                or re.match(r'\s*(?:以上|以下|以内|or\s+(?:more|less)\b)',qualifier_after,re.I)):
            _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
        # These operators join numeric alternatives/ranges, not two independent
        # operations. No guessed first/last/mean value is ever supplied.
        numeric=r'[\d零〇一二两三四五六七八九十百千万亿]'
        unit=r'(?:元|块钱|块|人民币|美元|美金|CNY|RMB|USD)?'
        join=r'(?:或者?|到|至|[~～−–—/]|\bor\b|\bto\b)'
        if re.search(numeric+r'[\d零〇一二两三四五六七八九十百千万亿.点]*\s*'+unit+r'\s*(?:[，,]\s*)?'+join+r'\s*$',message[:start][-120:],re.I) \
                or re.match(r'\s*(?:[，,]\s*)?'+join+r'\s*(?:(?:人民币|美元|美金|CNY|RMB|USD)\s*)?'+numeric,message[end:][:120],re.I) \
                or re.match(r'\s*'+numeric+r'\s*(?:角|毛|分)',after) \
                or raw[-1] in number_chars and re.match(r'\s*w\b',after,re.I):
            _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
        # Repeated denomination is valid, conflicting adjacent denomination is
        # not. Read the complete original quantity to check equality only; do
        # not supply an unselected currency, enlarge a span or rewrite a slot.
        denomination=r'(?:人民币|美元|美金|CNY|RMB|USD)'
        left=re.search(r'('+denomination+r')\s*$',before,re.I)
        right=re.match(r'\s*('+denomination+r')',after,re.I)
        if left or right:
            from .conversation_service import _continuation_money
            inherited=(prior or {}).get('currency') or 'CNY'
            original=(left[1] if left else '')+raw+(right[1] if right else '')
            complete=_continuation_money(original,inherited)
            selected=_continuation_money(raw,inherited)
            if complete is None or selected is None or complete[:2]!=selected[:2]:
                _deny('SEMANTIC_MONEY_SOURCE_NOT_CLOSED')
    if field in _IDENTITIES or field=='participant_names':
        # Literal identity spans must not clip an ASCII identifier. This checks
        # only structural token boundaries: never append guessed characters,
        # resolve a different account or ask the model a second time.
        start,end=span['start'],span['end']
        token_char=lambda character: bool(character) and (
            character.isascii() and character.isalnum() or character in {'_','-'})
        if (start>0 and token_char(message[start-1]) and token_char(message[start])) \
                or (end<len(message) and token_char(message[end-1]) and token_char(message[end])):
            _deny('SEMANTIC_IDENTITY_SPAN_INCOMPLETE')
    # Taint is structural, not a list of quoted action verbs. Quoting a whole
    # instruction in colloquial words is still not permission. Only a whole
    # opaque identity argument may use quote delimiters; a substring of a
    # quoted sentence cannot become a recipient, merchant or money field.
    if field not in {'memo','context_message'} and not own_aa_note:
        for quoted in re.finditer(r'“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’|\x27[^\x27\n]*\x27|`[^`]*`',message):
            if span['start']<quoted.end() and span['end']>quoted.start():
                whole_identity=field in _IDENTITIES and ((span['start'],span['end'])==(quoted.start()+1,quoted.end()-1)
                    or (span['start'],span['end'])==(quoted.start(),quoted.end()))
                # AA_SCOPE is a read-only enum, not a payment field. A full
                # direct question may contain an opaque quoted collection
                # name without tainting the user's outside-the-quotes query.
                read_scope = field in {'aa_scope','merchant_order_scope'} and (span['start'],span['end'])==(0,len(message)) and bool(
                    re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’|\x27[^\x27\n]*\x27|`[^`]*`','',message).strip(' ，,。；;!?！？'))
                if not whole_identity and not read_scope:_deny('SEMANTIC_QUOTED_FIELD_SOURCE')
    if field not in {'memo','context_message'} and not (own_aa_note or own_aa_deadline) and any(span['start']<end and span['end']>start for start,end in _source_spans(message)):_deny('SEMANTIC_UNTRUSTED_LITERAL')
    if field=='memo':
        from .intelligence import _transfer_memo
        value,issue=_transfer_memo(message)
        # For a NEW transfer the model may select an exact, directly stated
        # payment purpose as its visible memo. Do not require a magic "memo:"
        # marker, synthesize text, or let a purpose override an explicit memo.
        # Continuations and structured recipient clicks retain the stricter
        # explicit-memo contract so selecting an account cannot rewrite it.
        if allow_payment_purpose and value is None and issue is None:
            quoted=list(re.finditer(r'“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’|\x27[^\x27\n]*\x27|`[^`]*`',message))
            tainted=any(span['start']<end and span['end']>start for start,end in _source_spans(message))
            in_quote=any(span['start']<part.end() and span['end']>part.start() for part in quoted)
            no_memo=re.search(r'(?:无需|不要|不加|不写|不用)\s*备注|\b(?:no|without)\s+(?:memo|note)\b',message,re.I)
            if not raw.strip() or len(raw)>80 or tainted or in_quote or no_memo:
                _deny('SEMANTIC_MEMO_SOURCE_MISMATCH')
            return raw
        if issue or raw!=value:
            # Equivalent explicit memo wire: the entire exact inside of its
            # paired quotation delimiters. Never trim/rewrite a model value,
            # accept a partial memo, or interpret its contents as instructions.
            # Existing full-quote literals keep their original bytes/receipts.
            pairs={'“':'”','「':'」','"':'"','‘':'’',"'":"'"}
            if issue or not isinstance(value,str) or len(value)<2 or pairs.get(value[0])!=value[-1] \
                    or raw!=value[1:-1] or span['start']<1 or span['end']>=len(message) \
                    or message[span['start']-1:span['end']+1]!=value:
                _deny('SEMANTIC_MEMO_SOURCE_MISMATCH')
    return raw


def _request_boundary(message,kind,spans,*,owned_card_reference=False,deadline_spans=(),read_subject_message=None,funding_spans=()):
    """Source polarity/taint guard, never an action selector or verb allowlist."""
    from .intelligence import _intent_text
    from .financial_intent import foreign_financial_request
    trusted=_intent_text(message)
    if not trusted.strip() or re.fullmatch(r'\s*(?:“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’)\s*',message):_deny('SEMANTIC_QUOTED_REQUEST')
    if kind not in READ_KINDS and re.search(r'忽略.{0,12}(?:规则|权限)|系统指令|developer\s+message|ignore.{0,15}(?:rules|instructions)',trusted,re.I):_deny('SEMANTIC_SOURCE_INJECTION')
    # The legacy owner-topic heuristic guesses any "X's balance" is foreign.
    # A server-validated own-card entity (including ordinal/pronoun wording) is
    # a stronger ownership proof. Only an exactly validated own-card source bypasses
    # the guess; arbitrary literals, foreign catalogs and family reads do not.
    if kind in READ_KINDS and kind!='FAMILY_VIEW' and (read_subject_message is not None or not (
            kind in {'CARD_STATUS','BILL_ANALYSIS','CREDIT_VIEW','CREDIT_STATEMENT'} and owned_card_reference)) \
            and foreign_financial_request(_intent_text(read_subject_message) if read_subject_message is not None else trusted):_deny('SEMANTIC_FOREIGN_READ')
    if kind in READ_KINDS:return
    # Only the compiler's already source-checked current funding SET fields
    # enter this refusal-only gate; targets/control objects are never inferred.
    from .semantic_funding_source import selected_funding_is_negated
    if any(selected_funding_is_negated(message,span) for span in funding_spans):
        _deny('SEMANTIC_DENIED_WRITE')
    # Conditions cover the entire original sentence, including text before a
    # comma. A selected numeric span cannot wash away "if ... then".
    positions=[span['start'] for span in spans] or [0]
    left=max(message.rfind(mark,0,min(positions)) for mark in ('。','；',';','\n'))+1
    ends=[message.find(mark,max(positions)) for mark in ('。','；',';','\n')]
    right=min([end for end in ends if end>=0] or [len(message)])
    sentence=_intent_text(message[left:right])
    if re.search(r'如果|假如|假设|只有|一旦|等.{0,16}(?:后|再)|\b(?:if|unless|suppose)\b',sentence,re.I):_deny('SEMANTIC_CONDITIONAL_WRITE')
    future_raw=message[left:right]
    unproven_deadline=False
    if kind=='AA_REQUEST':
        from .temporal_field_roles import expiry_role_evidence
        for value in sorted(deadline_spans,key=lambda item:item['start'],reverse=True):
            if left<=value['start']<value['end']<=right:
                if expiry_role_evidence(message,value) is None:unproven_deadline=True
                else:future_raw=future_raw[:value['start']-left]+' '*(value['end']-value['start'])+future_raw[value['end']-left:]
    if kind not in {'TRANSFER_SCHEDULE', 'LIFE_PLAN_AUTHORIZE', 'OCCASION_SET', 'MERCHANT_PURCHASE'} and re.search(r'明天|后天|下周|下个月|\b(?:tomorrow|next\s+(?:week|month))\b',_intent_text(future_raw),re.I):_deny('SEMANTIC_UNSUPPORTED_FUTURE_WRITE')
    if unproven_deadline:_deny('SEMANTIC_TEMPORAL_ROLE_REQUIRED')
    if spans:
        low=min(span['start'] for span in spans);high=max(span['end'] for span in spans)
        before=max(message.rfind(mark,0,low) for mark in ('，',',','。','；',';','\n'))+1
        ends=[message.find(mark,high) for mark in ('，',',','。','；',';','\n')]
        after=min([end for end in ends if end>=0] or [len(message)])
        clause=_intent_text(message[before:after])
    else:clause=trusted
    # Local field restriction "不自动续费" is not a no-purchase instruction.
    clause=re.sub(r'(?:不(?:要|用|想|需要)?|关闭|取消|停止|禁用)\s*自动续(?:费|订)',' ',clause)
    neg=r'(?:不要|先别|别|不想|不需要|无需|暂不|禁止|不能|不(?:允许|准)|不是(?:要|想)|并非(?:要|想)|不(?=订|买|转|汇|付|开通|恢复|续订|续期|执行|办理))'
    operation=r'(?:执行|办理|处理|操作|支付|付款|转|汇|买|购买|开通|恢复|订阅|订|续订|续期|锁|冻结|授权|兑换)'
    if re.search(neg+r'(?:再|现在|帮我)?\s*'+operation+r'|'+neg+r'\s*(?:给|向|为|替|帮)[^，,。；;\n]{0,80}?'+operation+r'|不给[^，,。；;\n]{0,80}?(?:转|汇|付款|授权)|\b(?:do\s+not|don[’\x27]t|never)\s+(?:execute|pay|transfer|send|buy|subscribe|renew|lock|grant)',clause,re.I):_deny('SEMANTIC_DENIED_WRITE')


def _literal_field_value(field,raw,kind,prior,selected_options=None,*,source_created_at=None):
    """Only field grammar/unit conversion; never rule_proposal/_allowed_evidence."""
    value=raw.strip()
    if field in {'schedule_start_month','schedule_day_of_month'}:
        if kind!='TRANSFER_SCHEDULE':_deny('SEMANTIC_FIELD_NOT_EXACT')
        from .schedule_calendar_fields import start_month,day_of_month
        try:return start_month(value,source_created_at) if field=='schedule_start_month' else day_of_month(value)
        except ValueError:_deny('SEMANTIC_FIELD_NOT_EXACT')
    if field == 'schedule_reference':
        if kind != 'TRANSFER_SCHEDULE' or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value, re.I):
            _deny('SEMANTIC_FIELD_NOT_EXACT')
        return value.lower()
    if field=='merchant_product_id' and kind=='MERCHANT_CATALOG':
        _deny('SEMANTIC_MERCHANT_ID_REQUIRES_VERIFIED_ENTITY')
    if field=='occasion_id' and kind=='OCCASION_SET':
        _deny('SEMANTIC_OCCASION_ID_REQUIRES_VERIFIED_ENTITY')
    if field=='family_member_id' and kind in {'OCCASION_MEMBER_VIEW','OCCASION_MEMBER_SET','OCCASION_SET','LIFE_PLAN_AUTHORIZE'}:
        _deny('SEMANTIC_ENTITY_NOT_OFFERED')
    if field=='recipient_card_reference':
        from .recipient_card_selection import KINDS,REGISTERED_DEFAULT,is_registered_default_literal
        if is_registered_default_literal(value):
            if kind not in KINDS or selected_options:
                _deny('SEMANTIC_OPTION_NOT_ALLOWED')
            return REGISTERED_DEFAULT
    if selected_options:
        choices={'currency':{'CNY','USD'},'source_currency':{'CNY','USD'},'target_currency':{'CNY','USD'},
            'subscription_auto_renew':{'ENABLED','DISABLED'},'replace_alias':{'ENABLED','DISABLED'},
            'scopes':{'READ_BALANCE','PROPOSE_TRANSFER','PROPOSE_CARD_LOCK'},
            'limit_period':{'PER_PROPOSAL','MONTH'},'proposal_kind':{'TRANSFER','CARD_LOCK'}}
        from .semantic_router import FIELD_OPTIONS, MULTI_OPTION_FIELD_LIMITS
        choices['category_filter']=set(FIELD_OPTIONS['category_filter'])
        choices['asset_class']=set(FIELD_OPTIONS['asset_class'])
        choices['card_type']=set(FIELD_OPTIONS['card_type'])
        choices['card_balance_currencies']=set(FIELD_OPTIONS['card_balance_currencies'])
        choices['merchant_categories']=set(FIELD_OPTIONS['merchant_categories'])
        choices['preference_kind']=set(FIELD_OPTIONS['preference_kind'])
        from .upgraded_capabilities import FIELD_OPTIONS as NEW_OPTIONS
        choices.update({name: set(options) for name, options in NEW_OPTIONS.items()})
        if field not in choices or not isinstance(selected_options,list) or not selected_options \
                or len(set(selected_options))!=len(selected_options) or not set(selected_options)<=choices[field] \
                or len(selected_options)>MULTI_OPTION_FIELD_LIMITS.get(field,1):_deny('SEMANTIC_OPTION_NOT_ALLOWED')
        if field == 'card_balance_currencies':
            if kind != 'CARD_STATUS': _deny('SEMANTIC_FIELD_NOT_ALLOWED')
            from .card_balance_scope import currency_literal
            try: return currency_literal(value, selected_options)
            except ValueError as error: _deny(str(error))
        if field == 'merchant_categories':
            if kind != 'MERCHANT_CATALOG': _deny('SEMANTIC_FIELD_NOT_ALLOWED')
            from .semantic_merchant_categories import category_literal
            try: return category_literal(value, selected_options)
            except ValueError as error: _deny(str(error))
        selected=selected_options if field=='scopes' else selected_options[0]
        if field=='category_filter' and selected=='ALL':return None
        if field in {'subscription_auto_renew','replace_alias', 'lunar_leap_month'}:selected=selected=='ENABLED'
        return ActionSlots.model_validate({field:selected}).model_dump(exclude_none=True)[field]
    if field=='category_filter':
        # A literal category is a read filter, not bank data or authority. The
        # domain still validates it against actual registered statement rows.
        # Natural paraphrases are selected by the cloud from finite options,
        # never translated through a growing phrase/action rule here.
        if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z _-]{0,31}',value):
            _deny('SEMANTIC_CATEGORY_NOT_EXACT')
        return ActionSlots.model_validate({field:value}).category_filter
    if field in _IDENTITIES:
        if len(value)>=2 and (value[0],value[-1]) in {('“','”'),('「','」'),('"','"'),('‘','’')}:value=value[1:-1]
        if field in {'card_number','card_last4','card_reference','source_card','recipient_card_reference','destination_card_reference'}:
            from .card_reference_literal import card_reference_literal
            try:
                value = card_reference_literal(value)
            except ValueError:
                _deny('SEMANTIC_FIELD_NOT_EXACT')
        return ActionSlots.model_validate({field:value}).model_dump(exclude_none=True)[field]
    if field in _MONEY:
        from .conversation_service import _continuation_money
        if kind=='AA_REQUEST' and field=='amount_minor':
            # A model-selected total assignment may include its finite role
            # label. Strip only that label, then use the same exact amount
            # grammar; never calculate each-price × names or choose an amount.
            value=re.sub(r'^(?:合计总额|总金额|合计|总计|总额|total(?:\s+amount)?)\s*(?:为|是|:|：)?\s*','',value,flags=re.I)
            value=re.sub(r'^(?:每人|每位|每个人)\s*','',value)
        money=_continuation_money(value,prior.get('currency') or 'CNY')
        if money is None:_deny('SEMANTIC_MONEY_NOT_EXACT')
        minor,currency,_=money
        if kind not in {'FX_TRANSFER','FX_EXCHANGE','FX_QUOTE','CREDIT_REPAY'} and currency!='CNY':_deny('SEMANTIC_CURRENCY_CONFLICT')
        if prior.get('currency') and prior['currency']!=currency:_deny('SEMANTIC_CURRENCY_CONFLICT')
        return ActionSlots.model_validate({field:minor}).model_dump(exclude_none=True)[field]
    if field in {'currency','source_currency','target_currency'}:
        if re.fullmatch(r'人民币|CNY|RMB|元',value,re.I):return 'CNY'
        if re.fullmatch(r'美元|美金|USD|US\$',value,re.I):return 'USD'
        _deny('SEMANTIC_CURRENCY_NOT_EXACT')
    if field == 'card_balance_currencies': _deny('SEMANTIC_OPTION_NOT_ALLOWED')
    if field == 'merchant_categories': _deny('SEMANTIC_OPTION_NOT_ALLOWED')
    if field in {'subscription_months','subscription_auto_renew'}:
        from .subscription_intent import (subscription_field_spans, subscription_duration_tokens,
            duration_range_prefix, _UNPARSED_DURATION_UNIT)
        if field=='subscription_months':
            # Share registered tokens and their exact source receipt, while
            # retaining the original assignment-wrapper checks below. A to
            # assignment is not a quantity range such as one to three months.
            tokens=subscription_duration_tokens(value)
            if len(tokens)!=1:_deny('SEMANTIC_FIELD_NOT_EXACT')
            token=tokens[0]
            rows=[(name,scalar,start+token.start(),end+token.start())
                for name,scalar,start,end in subscription_field_spans(token.group()) or []]
        else:rows=subscription_field_spans(value)
        selected=[row for row in rows or [] if row[0]==field]
        if len(selected)!=1:_deny('SEMANTIC_FIELD_NOT_EXACT')
        if field=='subscription_auto_renew':
            if selected[0][2]!=0 or selected[0][3]!=len(value):_deny('SEMANTIC_FIELD_NOT_EXACT')
        else:
            # A quantity may be embedded in a model-selected assignment span.
            # Read the sole existing scalar, never infer a duration from the
            # wrapping phrase or borrow it from an earlier user message.
            _,months,start,end=selected[0]
            prefix,suffix=value[:start],value[end:]
            residual=prefix+' '+suffix
            if type(months) is not int or not 1<=months<=12 or len(re.findall(r'半年',value))>1 \
                    or re.search(r'\d',residual) \
                    or duration_range_prefix(prefix) or _UNPARSED_DURATION_UNIT.search(residual) \
                    or re.search(r'[-+−–—/÷⁄]',prefix[-2:]) \
                    or re.search(r'(?:负|negative|minus)\s*$',prefix,re.I) \
                    or re.search(r'(?:\d+|[零〇一二两三四五六七八九十]+)\s*(?:个)?\s*(?:或|和|与|到|至|[~～–—−/-])\s*(?:\d+|[零〇一二两三四五六七八九十]+)',value) \
                    or re.search(r'(?:\d+|[零〇一二两三四五六七八九十]+)\s*(?:个)?(?:年|季度|周|天|years?|weeks?|days?)',residual,re.I) \
                    or re.search(r'(?:\d+|[零〇一二两三四五六七八九十]+)\s*(?:个|次|份|倍)\s*$',prefix) \
                    or re.search(r'(?:约|大约|大概|差不多|至少|最多|不超过|around|about|approximately|roughly|at\s+(?:least|most)|up\s+to)(?:为|是)?\s*$',prefix,re.I) \
                    or re.match(r'\s*(?:左右|上下|以上|以下|以内|多|少|半|and\s+(?:a\s+)?half\b|approximately|roughly|or\s+(?:more|less))',suffix,re.I) \
                    or re.search(r'(?:不(?:要|用|想|需要)?|别|不是|并非|(?<![A-Za-z_])(?:not|no|never|don[’\x27]t|do\s+not)(?![A-Za-z_]))[^，,。；;!\n]{0,24}$',prefix,re.I):
                _deny('SEMANTIC_FIELD_NOT_EXACT')
        return selected[0][1]
    if field=='participant_names':
        names=[name.strip() for name in re.split(r'、|,|，|和|与|\band\b',value,flags=re.I)]
        if not names or len(set(names))!=len(names) or any(not name for name in names):_deny('SEMANTIC_FIELD_NOT_EXACT')
        return ActionSlots.model_validate({field:names}).participant_names
    if field=='scopes':
        if re.search(r'不要|禁止|不能|不得|\b(?:not|never)\b',value,re.I):_deny('SEMANTIC_SCOPE_DENIED')
        scopes=[]
        for token,pattern in [('READ_BALANCE',r'READ_BALANCE|(?:查看|查询|看|查).{0,6}余额|view.{0,8}balance|read.?balance'),
                ('PROPOSE_TRANSFER',r'PROPOSE_TRANSFER|(?:建议|提议|提案|提出|协助).{0,8}转账|转账.{0,8}(?:建议|提案)|propose.{0,8}transfer'),
                ('PROPOSE_CARD_LOCK',r'PROPOSE_CARD_LOCK|(?:建议|提议|提案|提出|协助).{0,8}锁卡|锁卡.{0,8}(?:建议|提案)|propose.{0,8}(?:lock|freeze)')]:
            if re.search(pattern,value,re.I):scopes.append(token)
        if not scopes:_deny('SEMANTIC_SCOPE_NOT_EXACT')
        return ActionSlots.model_validate({field:scopes}).scopes
    if field=='plan_year':
        # A calendar-date span is an exact source of its year. The LLM selects
        # the date; this projection never guesses a date or rewrites intent.
        calendar=re.fullmatch(r'(\d{4})(?:年(\d{1,2})月(\d{1,2})日?|-(\d{2})-(\d{2}))',value)
        if calendar:
            from datetime import date
            try:year=date(int(calendar[1]),int(calendar[2] or calendar[4]),int(calendar[3] or calendar[5])).year
            except ValueError:_deny('SEMANTIC_FIELD_NOT_EXACT')
            return ActionSlots.model_validate({'plan_year':year}).plan_year
        from .calendar_year_fields import is_relative_year,relative_year
        if is_relative_year(value):
            try:year=relative_year(value,source_created_at)
            except ValueError:_deny('SEMANTIC_FIELD_NOT_EXACT')
            return ActionSlots.model_validate({'plan_year':year}).plan_year
    if field=='schedule_occurrence_count':
        # A source-bound total-count assignment, not a schedule/intent parser.
        # Wrong units, ranges, negation and a second quantity remain outside
        # this whole-field grammar. The domain still verifies explicit dates.
        from .intelligence import _chinese_integer
        token=re.fullmatch(r'(?:(?:总共|一共|共|合计)(?:为|是)?\s*|(?:a\s+)?total\s+of\s+)?'
            r'(\d+|[零〇一二两三四五六七八九十百千万]+)(?:\s*(?:次|times?))?',value,re.I)
        if not token:_deny('SEMANTIC_FIELD_NOT_EXACT')
        try:count=int(token[1]) if token[1].isdigit() else _chinese_integer(token[1])
        except (ValueError,KeyError):_deny('SEMANTIC_FIELD_NOT_EXACT')
        return ActionSlots.model_validate({field:count}).model_dump(exclude_none=True)[field]
    if field in {'units','participant_count','expires_days','periods','birthday_month','birthday_day','plan_year','quantity'}:
        from .intelligence import _chinese_integer
        if field=='expires_days':
            # A model may copy the exact assignment ("有效7天") rather than
            # the bare scalar. This finite quantity grammar never selects an
            # intent and accepts no range, estimate, negation, or second value.
            token=re.fullmatch(r'(?:(?:有效(?:期)?|期限)(?:为|是|设为)?\s*|valid\s+for\s+|for\s+)?'
                r'(\d+|[零〇一二两三四五六七八九十百千万]+)(?:\s*(?:天|日|days?))?(?:\s*有效)?',value,re.I)
        else:
            token=re.fullmatch(r'(\d+|[零〇一二两三四五六七八九十百千万]+)(?:\s*(?:份|人|个人|天|期|月|日|号|年|次|件|units?|people|days?|periods?|times?))?',value,re.I)
        if not token:_deny('SEMANTIC_FIELD_NOT_EXACT')
        count=int(token[1]) if token[1].isdigit() else _chinese_integer(token[1])
        return ActionSlots.model_validate({field:count}).model_dump(exclude_none=True)[field]
    if field in {'replace_alias'}:
        if value not in {'true','false'}:_deny('SEMANTIC_FIELD_NOT_EXACT')
        return value=='true'
    # Raw dates, memo and context are preserved, not converted into actions.
    if field in {'scheduled_at','due_date','memo','context_message','limit_period','proposal_kind',
            'schedule_end_at','schedule_occurrence_times','aa_expires_at','confirmed_solar_date','birthday_timezone','delivery_start','delivery_end',
            'life_plan_items','life_plan_reserve_at','life_plan_order_at'}:
        return ActionSlots.model_validate({field:value}).model_dump(exclude_none=True)[field]
    _deny('SEMANTIC_FIELD_NOT_SUPPORTED')


def _literal_field(field,raw,kind,prior,selected_options=None,*,source_created_at=None):
    from pydantic import ValidationError
    try:
        return _literal_field_value(field,raw,kind,prior,selected_options,source_created_at=source_created_at)
    except ValidationError:
        # Invalid source-bound scalars are rejected before any task exists;
        # they must not be narrated as an unknown previous financial effect.
        _deny('SEMANTIC_FIELD_NOT_EXACT')


def _entity(session,principal,thread,offered,ref,field,*,action_kind=None,audit_only=False,payment_parent_task_id=None):
    from .dialogue_references import validate_result_reference_catalog
    entry=(offered.get('entity_bindings') or {}).get(ref)
    if not isinstance(entry,dict):_deny('SEMANTIC_ENTITY_NOT_OFFERED')
    if entry.get('source_kind') == 'COMPLETED_TRANSFER_RECEIPT_FACTS':
        _deny('SEMANTIC_TRANSFER_RECEIPT_READ_ONLY')
    if entry.get('source_kind') == 'OWNED_RECIPIENT_CHOICE':
        from .recipient_choices import checked_identity
        return checked_identity(session, principal, thread, entry, field, action_kind, audit_only=audit_only)
    if entry.get('source_kind')=='COMPLETED_TRANSFER_RECEIPT_IDENTITY':
        from .semantic_transfer_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_ASSET_RECEIPT_IDENTITY':
        from .semantic_asset_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_WEALTH_CARD_IDENTITY':
        from .semantic_asset_identity import checked_cash_identity
        return checked_cash_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_FX_CARD_IDENTITY':
        from .semantic_fx_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_MERCHANT_PAYMENT_IDENTITY':
        from .semantic_merchant_payment_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_CREDIT_INSTALLMENT_IDENTITY':
        from .semantic_credit_identity import checked_installment_identity
        return checked_installment_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind') in {'COMPLETED_OCCASION_RECEIPT_IDENTITY','COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY'}:
        from .semantic_occasion_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_CREDIT_RECEIPT_IDENTITY':
        from .semantic_credit_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='HISTORICAL_AA_READ_IDENTITY':
        from .semantic_aa_read_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_SUBSCRIPTION_IDENTITY':
        from .semantic_subscription_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='COMPLETED_LIFE_PLAN_IDENTITY':
        from .semantic_life_plan_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind') in {'COMPLETED_REPAYMENT_TARGET_IDENTITY','COMPLETED_REPAYMENT_FUNDING_IDENTITY','COMPLETED_REPAYMENT_FUNDING_READ_IDENTITY'}:
        from .semantic_repayment_source_identity import checked_identity
        return checked_identity(session,principal,thread,entry,field,action_kind)
    if entry.get('source_kind')=='USER_ANALYSIS_MATERIAL':
        if action_kind!='FRAUD_ANALYZE' or field!='context_message':_deny('SEMANTIC_MATERIAL_ACTION_NOT_ALLOWED')
        from .semantic_materials import validate_analysis_material_entity
        return validate_analysis_material_entity(session,principal,thread,entry,field,action_kind,audit_only=audit_only)
    catalog=_entity_catalog(offered,entry)
    from .dialogue_extended_read_sources import has_identity_route, immutable_identity_value
    candidate=entry.get('actual_entity') or entry.get('entity')
    from .bill_query_context import IDENTITY_KIND as BILL_QUERY_IDENTITY_KIND, has_identity_route as bill_identity_route
    if isinstance(candidate, dict) and candidate.get('kind') == BILL_QUERY_IDENTITY_KIND \
            and not bill_identity_route(candidate, field, action_kind):
        _deny('BILL_QUERY_IDENTITY_READ_ONLY')
    if has_identity_route(candidate,field,action_kind):
        if action_kind in {'BILL_ANALYSIS','CARD_STATUS'}:
            # Selecting an owned card/statement scope is still a fresh READ,
            # not the immutable-identity exception used by confirmed writes.
            validate_result_reference_catalog(session,principal,thread,catalog)
        else:_audit_catalog_source(session,principal,thread,catalog)
        return immutable_identity_value(session,principal,{**entry,'catalog':catalog},field,action_kind,
            payment_parent_task_id=payment_parent_task_id,audit_only=audit_only)
    if audit_only:_audit_catalog_source(session,principal,thread,catalog)
    else:validate_result_reference_catalog(session,principal,thread,catalog)
    actual=entry.get('actual_entity') or entry.get('entity')
    if not isinstance(actual,dict) or actual not in catalog.get('entities',[]) or actual.get('field')!=field \
            or field not in _IDENTITIES and not (field=='category_filter' and action_kind=='BILL_ANALYSIS') \
            or entry.get('value',actual['value'])!=actual['value']:_deny('SEMANTIC_ENTITY_FIELD_MISMATCH')
    return deepcopy(actual['value'])


def _frame(session,principal,thread,offered,focus,depth=0,*,audit_only=False):
    entry=(offered.get('frame_bindings') or {}).get(focus)
    if not isinstance(entry,dict):_deny('SEMANTIC_FRAME_NOT_OFFERED')
    model=next((row for row in offered['model'].get('frames',[]) if row.get('frame_ref')==focus),None)
    if not model or canonical_hash(model)!=entry.get('frame_hash'):_deny('SEMANTIC_FRAME_MAP_CHANGED')
    task=session.get(Task,entry.get('task_id'));snapshot=session.get(PlanSnapshot,entry['snapshot_id']) if entry.get('snapshot_id') else None
    if not _owned(task,principal):_deny()
    if snapshot is None:
        prior=entry.get('prior_semantic')
        if not isinstance(prior,dict):_deny()
        actions=_validate_prior_semantic(session,principal,thread,task,prior,depth+1,audit_only=audit_only)
        if prior.get('expected_proposal')!=entry.get('proposal_json'):_deny()
    else:
        if snapshot.task_id!=task.id or snapshot.version!=entry.get('plan_version') or snapshot.plan_hash!=entry.get('plan_hash') \
                or canonical_hash(snapshot.plan_json)!=snapshot.plan_hash or snapshot.proposal_json!=entry.get('proposal_json'):_deny()
        actions=_plain(IntentProposal.model_validate(deepcopy(snapshot.proposal_json)))['actions']
    pending=entry.get('pending_semantic')
    if pending:
        actions=_validate_prior_semantic(session,principal,thread,task,pending,depth+1,audit_only=audit_only)
    linked=session.scalar(select(ChatTurn.id).where(ChatTurn.thread_id==thread.id,ChatTurn.task_id==task.id,ChatTurn.user_id==principal.user_id))
    if not linked:_deny('SEMANTIC_FOREIGN_FRAME')
    public_actions=model.get('actions',[])
    if len(public_actions)!=len(actions):_deny('SEMANTIC_FRAME_MAP_CHANGED')
    for visible,actual in zip(public_actions,actions):
        if visible.get('kind')!=actual['kind'] or visible.get('depends_on',[])!=actual['depends_on'] \
                or any(actual['slots'].get(field)!=value for field,value in visible.get('slots',{}).items()):_deny('SEMANTIC_FRAME_MAP_CHANGED')
    return task,entry,actions


def _validate_prior_semantic(session,principal,thread,parent,binding,depth=0,*,audit_only=False):
    if depth>24:_deny('SEMANTIC_SOURCE_DEPTH_EXCEEDED')
    source=session.get(ChatTurn,binding.get('source_turn_id'))
    _thread,scope,epoch=_valid_turn(session,principal,thread,source)
    payload=deepcopy(binding);digest=payload.pop('binding_hash',None)
    receipt=(source.diagnostics_json or {}).get(RECEIPT_KEY)
    if digest!=canonical_hash(payload) or binding.get('task_id')!=parent.id or binding.get('version')!=VERSION \
            or source.task_id!=parent.id or source.status!='ANSWERED' or epoch!=binding.get('actor_scope_hash') \
            or canonical_hash({'message':source.message})!=binding.get('source_message_hash') \
            or canonical_hash(receipt)!=binding.get('source_receipt_hash') \
            or binding.get('bank_write_authorized') is not False:_deny()
    # This is the immutable prior genesis, not authority to act on old text.
    # Full later revisions still need their current raw receipt and new grant.
    from .semantic_router import validate_semantic_decision
    from .read_projection_request import prepare_read_input_once
    offered=binding['offered_context'];params={**offered['model'],'message':source.message}
    if canonical_hash(offered)!=binding['offered_hash']:_deny()
    prepared=prepare_read_input_once(session,principal,params,
        source_key=(parent.id,parent.version,source.id,epoch,digest))
    if canonical_hash(prepared)!=binding['input_hash']:_deny()
    if not isinstance(receipt,dict) or receipt.get('status')!='READY' or receipt.get('decision')!=binding['decision']:_deny()
    validate_semantic_decision(receipt['decision'],prepared)
    # Each chain hop has an offered immutable snapshot or earlier binding;
    # duplicate/self cycles are bounded before recursing.
    for entry in offered.get('frame_bindings',{}).values():
        prior=entry.get('prior_semantic')
        if prior and prior.get('source_sequence',0)>=binding['source_sequence']:_deny()
    credit_ledger=[]
    proposal,_prior=_rebuild_actions(session,principal,thread,source,receipt,offered,depth=depth+1,audit_only=audit_only,project_fx_unit=_fx_money_unit_projection(binding),project_read_entity_scope=_read_entity_scope_projection(binding),project_credit_literal=_credit_literal_projection(binding),credit_literal_ledger=credit_ledger,guard_funding_source=_funding_source_polarity(binding,live=not audit_only))
    _validate_credit_literal_ledger(binding,credit_ledger)
    _validate_fx_currency_source_ledger(binding,receipt,offered,proposal,source.message)
    _validate_read_entity_scope_ledger(binding,receipt,offered,proposal)
    _validate_read_clone_binding(session,principal,thread,parent,source,receipt,offered,proposal,_prior,binding)
    clean=lambda p:[{'kind':row.kind,'slots':row.slots.model_dump(exclude_none=True),'depends_on':row.depends_on} for row in p.actions]
    expected=IntentProposal.model_validate(deepcopy(binding['expected_proposal']))
    if clean(proposal)!=clean(expected):_deny()
    return deepcopy(binding['expected_proposal']['actions'])


READ_ENTITY_SCOPE_PROJECTION_VERSION='selected-read-object-scope-v1'
FAMILY_READ_SCOPE_PROJECTION_VERSION='selected-read-object-scope-v2'
from .family_read_contract import SELECTOR_FIELDS as FAMILY_READ_FIELDS
_READ_ENTITY_SCOPE_TARGETS={'CARD_STATUS':('CARDS',{'card_number','card_last4'}),
    'SUBSCRIPTION_LIST':('SUBSCRIPTIONS',{'merchant'}),
    'WEALTH_COMPARE':('WEALTH',{'product_name'}),
    'ASSET_POSITIONS_READ':('ASSETS',{'product_name'}),'BENEFITS_VIEW':('BENEFITS',{'benefit_name'}),
    'PREFERENCE_VIEW':('PREFERENCES',{'preference_kind'}),
    'CARBON_VIEW':('CARBON',{'reward_name','behavior_name'}),
    'FAMILY_VIEW':('FAMILY',FAMILY_READ_FIELDS)}


def _read_entity_scope_projection(binding):
    value=binding.get('read_entity_scope_projection')
    if value is None:return False
    if value not in {READ_ENTITY_SCOPE_PROJECTION_VERSION,FAMILY_READ_SCOPE_PROJECTION_VERSION}:_deny('SEMANTIC_READ_SELECTION_BINDING_INVALID')
    return value


def _read_entity_scope_selection(receipt,offered,actions,*,project_family_scope=False):
    """An explicit single object on a single completed READ, not any fact ref.

    Only consumers with an existing exact identity filter are registered.
    CONSULT refs and objects on other actions never become query filters.
    """
    from .semantic_router import is_completed_read_followup
    decision=receipt['decision'];selected=decision.get('entity_refs',[])
    if not selected or decision['mode'] not in {'REQUEST','CONTINUE','REVISE'}:return None
    if decision['mode']!='REQUEST' and not is_completed_read_followup(decision,offered['model']):return None
    if len(actions)!=1 or actions[0]['kind'] not in _READ_ENTITY_SCOPE_TARGETS:return None
    if actions[0]['kind']=='FAMILY_VIEW' and not project_family_scope:return None
    kind=actions[0]['kind'];domain,fields=_READ_ENTITY_SCOPE_TARGETS[kind]
    selected=[ref for ref in selected if isinstance((offered.get('entity_bindings') or {}).get(ref),dict)
        and ((offered['entity_bindings'][ref].get('actual_entity') or offered['entity_bindings'][ref].get('entity') or {}).get('field') in fields)
        and ((offered['entity_bindings'][ref].get('actual_entity') or offered['entity_bindings'][ref].get('entity') or {}).get('domain')==domain)
        and ((offered['entity_bindings'][ref].get('actual_entity') or offered['entity_bindings'][ref].get('entity') or {}).get('read_kind')==kind)]
    if not selected:return None  # Context facts/summaries never assign an identity filter.
    if len(selected)!=1:
        explicit_refs={row.get('entity_ref') for row in decision.get('changes',[])
            if row.get('operation')=='SET' and row.get('field') in _READ_ENTITY_SCOPE_TARGETS[actions[0]['kind']][1]}
        if set(selected)<=explicit_refs:return None  # All fields already individually source-checked; no automatic selection.
        _deny('SEMANTIC_READ_SELECTION_AMBIGUOUS')
    ref=selected[0]
    entry=(offered.get('entity_bindings') or {}).get(ref)
    visible=next((row for row in offered['model'].get('entities',[]) if row.get('entity_ref')==ref),None)
    focus=(entry or {}).get('frame_ref') if decision['mode']=='REQUEST' else decision['focus_ref']
    frame=(offered.get('frame_bindings') or {}).get(focus)
    public_frame=next((row for row in offered['model'].get('frames',[]) if row.get('frame_ref')==focus),None)
    if not isinstance(entry,dict) or not isinstance(visible,dict) or not isinstance(frame,dict):
        _deny('SEMANTIC_ENTITY_NOT_OFFERED')
    if decision.get('focus_ref') not in {None,focus} or not public_frame \
            or kind not in [a.get('kind') for a in public_frame.get('actions',[])]:
        _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')
    actual=entry.get('actual_entity') or entry.get('entity')
    if not isinstance(actual,dict) or entry.get('source_kind')=='USER_ANALYSIS_MATERIAL' \
            or entry.get('frame_ref')!=focus or visible.get('frame_ref')!=focus \
            or actual.get('read_kind')!=kind or visible.get('read_kind')!=kind \
            or actual.get('domain')!=domain or visible.get('domain')!=domain \
            or actual.get('field') not in fields or visible.get('field')!=actual.get('field') \
            or actual.get('field') not in visible.get('allowed_fields',[]) \
            or (_entity_catalog(offered,entry) or {}).get('selected_task_id')!=frame.get('task_id'):
        _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')
    # Existing explicit field changes already have their own current-span
    # proof. Selection never silently overwrites a conflicting explicit slot.
    target_fields=fields if kind=='CARD_STATUS' else {actual['field']}
    action_refs=frame.get('action_refs') or frame.get('actions') or {}
    target_changes=[row for row in decision.get('changes',[]) if row.get('field') in target_fields
        and (action_refs.get(row.get('action_ref'))==0 or
            isinstance(action_refs.get(row.get('action_ref')),dict) and action_refs[row['action_ref']].get('ordinal')==0)]
    # REQUEST uses new action refs; existing frame refs describe only source.
    if decision['mode']=='REQUEST':
        target_changes=[row for row in decision.get('changes',[]) if row.get('field') in target_fields and row.get('action_ref')=='new:0']
    if target_changes:
        for change in target_changes:
            if change.get('operation')=='CLEAR':continue
            expected=actual['value'][-4:] if change['field']=='card_last4' and actual['field']=='card_number' else actual['value']
            value=actions[0]['slots'].get(change['field'])
            same=value.lower()==expected.lower() if kind=='CARD_STATUS' and isinstance(value,str) and isinstance(expected,str) else value==expected
            if change.get('operation')!='SET' or not same:
                _deny('SEMANTIC_READ_SELECTION_CONFLICT')
        # A proved literal tail may match more than one owned card. The model's
        # explicit selected full identity is a stronger read constraint, not
        # permission to overwrite the literal tail or to act on either card.
        if kind=='CARD_STATUS' and actual['field']=='card_number' \
                and any(row.get('field')=='card_last4' and row.get('operation')=='SET' for row in target_changes) \
                and not any(row.get('field')=='card_number' for row in target_changes):
            _read_entity_scope_offered_origin(public_frame)
            return 0,ref,actual['field'],'CANONICAL_CARD_INTERSECTION'
        return 0,ref,actual['field'],False
    _read_entity_scope_offered_origin(public_frame)
    return 0,ref,actual['field'],True


def _read_entity_scope_offered_origin(frame):
    """A pending WRITE may coexist with a successful READ, not authorize it."""
    if frame.get('state') not in {'SUCCEEDED','AWAITING_CONFIRMATION'} \
            or frame.get('write_effect_possible') is not False or frame.get('paused') is True:
        _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')


def _read_entity_scope_live_origin(session,principal,thread,offered,selection,*,audit_only=False):
    # Historical replay checks the immutable offered state and READ source,
    # not a false claim that the old parent is still waiting today. Fresh
    # inference/execution must additionally reject current authorization or
    # possible effects, including a transition during the model HTTP call.
    if audit_only:return
    _ordinal,ref,_field,apply_scope=selection
    if not apply_scope:return  # Explicit fields keep the original _entity contract.
    entry=offered['entity_bindings'][ref];binding=offered['frame_bindings'][entry['frame_ref']]
    task=session.get(Task,binding['task_id'])
    from .conversation_service import current_transfer_pause
    if not _owned(task,principal) or task.state not in {'SUCCEEDED','AWAITING_CONFIRMATION'} \
            or task.version!=binding.get('task_version') or current_transfer_pause(session,principal,task):
        _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')
    for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id)):
        child=session.get(Task,op.child_task_id) if op.child_task_id else None
        if op.action_kind not in READ_KINDS and (op.status in {'AUTHORIZED','RUNNING','SUCCEEDED','UNKNOWN'} \
                or child and child.state in {'AUTHORIZED','EXECUTING','SUCCEEDED','UNKNOWN'}):
            _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')


def _reject_mixed_read_only_replanning(receipt,offered,actions):
    """No new-v1 query selection silently recompiles a pending WRITE DAG."""
    decision=receipt['decision']
    if decision['mode'] not in {'CONTINUE','REVISE'} or not decision.get('entity_refs') \
            or not any(a['kind'] in READ_KINDS for a in actions) or not any(a['kind'] not in READ_KINDS for a in actions):return
    frame=offered['frame_bindings'][decision['focus_ref']]
    refs=frame.get('action_refs') or frame.get('actions') or {}
    write_changes=False
    for row in decision.get('changes',[]):
        ordinal=refs.get(row.get('action_ref'))
        ordinal=ordinal.get('ordinal') if isinstance(ordinal,dict) else ordinal
        if type(ordinal) is int and 0<=ordinal<len(actions) and actions[ordinal]['kind'] not in READ_KINDS:
            write_changes=True
    if write_changes:return
    read_kinds={a['kind'] for a in actions if a['kind'] in READ_KINDS}
    for ref in decision['entity_refs']:
        entry=(offered.get('entity_bindings') or {}).get(ref) or {}
        entity=entry.get('actual_entity') or entry.get('entity') or {}
        if entity.get('read_kind') in read_kinds:
            _deny('SEMANTIC_READ_SELECTION_REQUIRES_INDEPENDENT_REQUEST')


def _validate_read_entity_scope_sources(session,principal,thread,receipt,offered,actions,*,audit_only=False,project_family_scope=False):
    """Selected context objects keep their exact source proof, even without a filter."""
    if receipt['decision']['mode'] not in {'REQUEST','CONTINUE','REVISE'} \
            or len(actions)!=1 or actions[0]['kind'] not in _READ_ENTITY_SCOPE_TARGETS:return
    if actions[0]['kind']=='FAMILY_VIEW' and not project_family_scope:return
    from .dialogue_references import validate_result_reference_catalog
    for ref in receipt['decision'].get('entity_refs',[]):
        entry=(offered.get('entity_bindings') or {}).get(ref)
        visible=next((row for row in offered['model'].get('entities',[]) if row.get('entity_ref')==ref),None)
        if not isinstance(entry,dict) or not isinstance(visible,dict) or entry.get('source_kind')=='USER_ANALYSIS_MATERIAL':
            _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')
        if entry.get('source_kind')=='COMPLETED_ASSET_RECEIPT_IDENTITY':
            from .semantic_asset_identity import checked_identity
            checked_identity(session,principal,thread,entry,'product_name',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_TRANSFER_RECEIPT_IDENTITY':
            from .semantic_transfer_identity import checked_identity
            checked_identity(session,principal,thread,entry,'card_number',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_WEALTH_CARD_IDENTITY':
            from .semantic_asset_identity import checked_cash_identity
            checked_cash_identity(session,principal,thread,entry,'card_number',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_FX_CARD_IDENTITY':
            from .semantic_fx_identity import checked_identity
            checked_identity(session,principal,thread,entry,'card_number',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='HISTORICAL_AA_READ_IDENTITY':
            from .semantic_aa_read_identity import checked_identity
            checked_identity(session,principal,thread,entry,entry['field'],actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_SUBSCRIPTION_IDENTITY':
            from .semantic_subscription_identity import checked_identity
            checked_identity(session,principal,thread,entry,'merchant',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_LIFE_PLAN_IDENTITY':
            from .semantic_life_plan_identity import checked_identity
            checked_identity(session,principal,thread,entry,'life_plan_id',actions[0]['kind'])
            continue
        if entry.get('source_kind')=='COMPLETED_REPAYMENT_FUNDING_READ_IDENTITY':
            from .semantic_repayment_source_identity import checked_identity
            checked_identity(session,principal,thread,entry,'card_number',actions[0]['kind'])
            continue
        catalog=_entity_catalog(offered,entry)
        if audit_only:_audit_catalog_source(session,principal,thread,catalog)
        else:validate_result_reference_catalog(session,principal,thread,catalog)
        actual=entry.get('actual_entity') or entry.get('entity')
        if not isinstance(actual,dict) or actual not in catalog.get('entities',[]) \
                or entry.get('value',actual.get('value'))!=actual.get('value') \
                or entry.get('frame_ref')!=visible.get('frame_ref') \
                or any(visible.get(key)!=actual.get(key) for key in ('field','domain','read_kind')):
            _deny('SEMANTIC_READ_SELECTION_SOURCE_MISMATCH')


def _read_entity_scope_ledger(receipt,offered,proposal,*,project_family_scope=False):
    actions=_plain(proposal)['actions'];selection=_read_entity_scope_selection(receipt,offered,actions,project_family_scope=project_family_scope)
    if selection is None or not selection[3]:return []
    ordinal,ref,field,_apply=selection;entry=offered['entity_bindings'][ref]
    actual=entry.get('actual_entity') or entry['entity'];focus=entry['frame_ref']
    frame=offered['frame_bindings'][focus];catalog=_entity_catalog(offered,entry)
    if actions[ordinal]['slots'].get(field)!=actual['value']:_deny('SEMANTIC_READ_SELECTION_SOURCE_CHANGED')
    evidence={'action_ordinal':ordinal,'action_kind':actions[ordinal]['kind'],'field':field,
        'entity_ref':ref,'source_frame_ref':focus,'source_task_id':frame['task_id'],
        'source_plan_hash':frame.get('plan_hash'),'source_snapshot_id':frame.get('snapshot_id'),
        'source_catalog_hash':catalog.get('catalog_hash'),'source_entity_ref':actual.get('entity_ref'),
        'source_result_ref':actual.get('result_ref'),'value':deepcopy(actual['value'])}
    if _apply=='CANONICAL_CARD_INTERSECTION':
        evidence.update(projection='CANONICAL_CARD_INTERSECTION',retained_literal_field='card_last4')
    return [evidence]


def _validate_read_entity_scope_ledger(binding,receipt,offered,proposal):
    if _read_entity_scope_projection(binding) and binding.get('read_entity_scope_ledger')!=_read_entity_scope_ledger(receipt,offered,proposal,
            project_family_scope=_read_entity_scope_projection(binding)==FAMILY_READ_SCOPE_PROJECTION_VERSION):
        _deny('SEMANTIC_READ_SELECTION_SOURCE_CHANGED')


FX_MONEY_UNIT_PROJECTION_VERSION='fx-current-money-unit-v1'


def _fx_money_unit_projection(binding):
    value=binding.get('fx_money_unit_projection')
    if value is None:return False
    if value!=FX_MONEY_UNIT_PROJECTION_VERSION:_deny('SEMANTIC_MONEY_UNIT_PROJECTION_INVALID')
    return True


def _fx_currency_source_ledger(receipt,offered,proposal,message):
    """Current literal or verified prior canonical currency, never bank funds."""
    from .conversation_service import _continuation_money
    decision=receipt['decision'];focus=decision.get('focus_ref')
    entry=(offered.get('frame_bindings') or {}).get(focus,{})
    refs=entry.get('action_refs') or entry.get('actions') or {}
    prior=(entry.get('proposal_json') or {}).get('actions',[])
    output=[]
    for index,action in enumerate(proposal.actions):
        if action.kind not in {'FX_QUOTE','FX_EXCHANGE','FX_TRANSFER'} or action.slots.currency is None:continue
        action_ref='new:'+str(index) if decision['mode']=='REQUEST' else next((key for key,value in refs.items()
            if (value.get('ordinal') if isinstance(value,dict) else value)==index),None)
        rows=[row for row in decision.get('changes',[]) if row.get('action_ref')==action_ref and row.get('operation')=='SET']
        declared=next((row for row in rows if row.get('field')=='currency'),None)
        amount=next((row for row in rows if row.get('field')=='amount_minor'),None)
        evidence={'action_ordinal':index,'field':'currency','currency':action.slots.currency}
        if declared:
            evidence.update(source='CURRENT_CURRENCY_FIELD',source_field='currency',source_span=declared['source_span'],
                source_message_hash=canonical_hash({'message':message}))
        elif amount:
            raw=_trusted_span(message,amount['source_span'],'amount_minor',prior=action.slots.model_dump(exclude_none=True))
            money=_continuation_money(raw,action.slots.currency)
            if money is not None and money[2] is not None:
                if money[0]!=action.slots.amount_minor or money[1]!=action.slots.currency:_deny('SEMANTIC_CURRENCY_CONFLICT')
                evidence.update(source='CURRENT_VERIFIED_AMOUNT_LITERAL',source_field='amount_minor',source_span=amount['source_span'],
                    source_message_hash=canonical_hash({'message':message}))
            else:amount=None
        if not declared and not amount:
            if not focus or index>=len(prior) or prior[index].get('slots',{}).get('currency')!=action.slots.currency:
                _deny('SEMANTIC_MONEY_UNIT_SOURCE_CHANGED')
            evidence.update(source='VERIFIED_PRIOR_CURRENCY_SLOT',source_field='currency',source_frame_ref=focus,
                source_plan_hash=entry.get('plan_hash'),source_snapshot_id=entry.get('snapshot_id'))
        output.append(evidence)
    return output


def _validate_fx_currency_source_ledger(binding,receipt,offered,proposal,message):
    if _fx_money_unit_projection(binding) and binding.get('fx_currency_source_ledger')!=_fx_currency_source_ledger(receipt,offered,proposal,message):
        _deny('SEMANTIC_MONEY_UNIT_SOURCE_CHANGED')


def _credit_literal_projection(binding):
    from .semantic_credit_identity import LITERAL_SELECTOR_VERSION
    marker=binding.get('credit_literal_selector_projection')
    if marker is None and 'credit_literal_selector_projection' not in binding:
        if 'credit_literal_selector_ledger' in binding:_deny('SEMANTIC_CREDIT_IDENTITY_SOURCE_CHANGED')
        return False
    if type(marker) is not str or marker!=LITERAL_SELECTOR_VERSION \
            or not isinstance(binding.get('credit_literal_selector_ledger'),list) \
            or not binding['credit_literal_selector_ledger']:_deny('SEMANTIC_CREDIT_IDENTITY_SOURCE_CHANGED')
    return True


def _validate_credit_literal_ledger(binding,ledger):
    if _credit_literal_projection(binding) and (not ledger or binding['credit_literal_selector_ledger']!=ledger):
        _deny('SEMANTIC_CREDIT_IDENTITY_SOURCE_CHANGED')


def _funding_source_polarity(binding,*,live=False):
    from .semantic_funding_source import VERSION as funding_version
    # Historical NAVIGATION_ONLY/audit replays keep old evidence byte-for-byte.
    # Missing old markers cannot exempt a live pending proof or new revision.
    if 'funding_source_polarity' not in binding:return bool(live)
    if type(binding['funding_source_polarity']) is not str or binding['funding_source_polarity']!=funding_version:_deny()
    return True


def _rebuild_actions(session,principal,thread,turn,receipt,offered,depth=0,*,audit_only=False,project_fx_unit=False,project_read_entity_scope=False,project_credit_literal=False,credit_literal_ledger=None,guard_funding_source=False):
    if depth>24:_deny('SEMANTIC_SOURCE_DEPTH_EXCEEDED')
    decision=receipt['decision'];mode=decision['mode']
    actions=[];frame=None;current_history_base=None
    if mode=='REQUEST':
        kinds=decision.get('action_kinds',[]);dependencies=decision.get('dependencies',[])
        if not kinds or len(kinds)!=len(dependencies):_deny('SEMANTIC_ACTION_DAG_REQUIRED')
        actions=[{'kind':kind,'slots':{},'depends_on':list(dependencies[i])} for i,kind in enumerate(kinds)]
        refs={'new:'+str(i):i for i in range(len(actions))}
        if decision.get('preference_rebase_ref'):
            from .semantic_completed_preference import completed_preference_kind
            actions[0]['slots']['preference_kind']=completed_preference_kind(session,principal,thread,turn,
                decision,offered,depth,audit_only=audit_only)
        if decision.get('query_base')=='CURRENT':
            from .intelligence import READ_KINDS
            frame,entry,prior=_frame(session,principal,thread,offered,decision.get('focus_ref'),depth,audit_only=audit_only)
            current_history_base=(frame,entry)
            if any(action['kind'] not in READ_KINDS for action in actions+prior):_deny('SEMANTIC_QUERY_REFERENCE_READ_ONLY')
            for action in actions:
                overridden={row['field'] for row in decision.get('changes',[]) if row.get('action_ref') in refs
                    and actions[refs[row['action_ref']]]['kind']==action['kind']}
                bases=[{key:value for key,value in old['slots'].items() if key not in overridden}
                    for old in prior if old['kind']==action['kind']]
                if bases and any(base!=bases[0] for base in bases):
                    # A precisely selected current READ collection may narrow
                    # multiple card-tail queries to one canonical full card.
                    # Full number/tail are one identity dimension, not two
                    # competing filters. Only a formerly ambiguous base uses
                    # this lane; historical successful proofs are unchanged.
                    from .semantic_read_collection import current_read_collection
                    selected=current_read_collection(decision,offered['model'])
                    if selected and selected['read_kind']==action['kind'] \
                            and selected['field']=='card_number' and 'card_number' in overridden \
                            and action['kind'] in {'BILL_ANALYSIS','CARD_STATUS','CREDIT_VIEW','CREDIT_STATEMENT'}:
                        bases=[{key:value for key,value in base.items() if key not in {'card_number','card_last4'}} for base in bases]
                if not bases or any(base!=bases[0] for base in bases):_deny('SEMANTIC_QUERY_BASE_FILTER_AMBIGUOUS')
                action['slots']=deepcopy(bases[0])
    elif mode in {'CONTINUE','REVISE','RESUME'}:
        frame,entry,actions=_frame(session,principal,thread,offered,decision.get('focus_ref'),depth,audit_only=audit_only)
        refs=entry.get('actions') or entry.get('action_refs')
        if not isinstance(refs,dict):_deny('SEMANTIC_ACTION_REFS_REQUIRED')
        refs={key:(value.get('ordinal') if isinstance(value,dict) else value) for key,value in refs.items()}
        if decision.get('action_kinds') and decision['action_kinds']!=[row['kind'] for row in actions]:_deny('SEMANTIC_KIND_REPLACEMENT')
    else:_deny('SEMANTIC_MODE_NOT_TASK')
    if decision.get('history_read_sources'):
        from .semantic_read_history_scope import seed_read_slots
        seed_read_slots(session,principal,thread,turn,decision,offered,actions,
            current_base=current_history_base,audit_only=audit_only)
    allowed={row.get('kind') for row in offered['model'].get('capabilities',[]) if isinstance(row,dict)}
    if any(row['kind'] not in allowed for row in actions):_deny('SEMANTIC_CAPABILITY_NOT_OFFERED')
    from .semantic_read_subject import checked_guard_messages
    # The existing receipt and _valid_turn bind current user/sandbox/actor epoch.
    # Optional subject proof changes only the old owner refusal input, never
    # action slots, account selection, family authority or a WRITE permission.
    try:read_subject_messages=checked_guard_messages(decision,offered['model'])
    except ValueError as error:_deny(str(error))
    changed=set();spans={i:[] for i in range(len(actions))};deadline_spans={i:[] for i in range(len(actions))};owned_card_references=set()
    owned_credit_status_references=set()
    checked_asset_classes={}
    funding_spans={i:[] for i in range(len(actions))}
    from .semantic_asset_identity import project_selected_identity_changes
    changes=project_selected_identity_changes(decision,offered,actions,turn.message)
    transfer_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_TRANSFER_RECEIPT_IDENTITY'}
    if transfer_refs and (mode!='REQUEST' or len(actions)!=1 or actions[0]['kind']!='CARD_STATUS'
            or not transfer_refs<={row.get('entity_ref') for row in changes
                if row.get('operation')=='SET' and row.get('action_ref')=='new:0' and row.get('field')=='card_number'}):
        _deny('SEMANTIC_TRANSFER_IDENTITY_REQUIRES_EXPLICIT_READ')
    identity_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_ASSET_RECEIPT_IDENTITY'}
    if identity_refs and (mode!='REQUEST' or not identity_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='product_name'}):
        _deny('SEMANTIC_ASSET_IDENTITY_REQUIRES_NEW_READ')
    credit_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_CREDIT_RECEIPT_IDENTITY'}
    subscription_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_SUBSCRIPTION_IDENTITY'}
    life_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_LIFE_PLAN_IDENTITY'}
    if life_refs and (mode!='REQUEST' or not life_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='life_plan_id'
            and type(refs.get(row.get('action_ref'))) is int
            and actions[refs[row['action_ref']]]['kind'] in {'LIFE_PLAN_VIEW','LIFE_PLAN_CANCEL'}}):
        _deny('SEMANTIC_LIFE_PLAN_IDENTITY_REQUIRES_NEW_ACTION')
    if subscription_refs and (mode!='REQUEST' or not subscription_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='merchant'
            and type(refs.get(row.get('action_ref'))) is int
            and actions[refs[row['action_ref']]]['kind']=='SUBSCRIPTION_LIST'}):
        _deny('SEMANTIC_SUBSCRIPTION_IDENTITY_REQUIRES_FRESH_READ')
    from .semantic_repayment_source_identity import PAY_KIND, READ_KIND
    target_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_REPAYMENT_TARGET_IDENTITY'}
    if target_refs and (mode!='REQUEST' or not target_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='card_reference'
            and type(refs.get(row.get('action_ref'))) is int
            and actions[refs[row['action_ref']]]['kind'] in {'CREDIT_REPAY','CREDIT_VIEW','CREDIT_STATEMENT'}}):
        _deny('SEMANTIC_REPAYMENT_IDENTITY_REQUIRES_CURRENT_REQUEST')
    for row in changes:
        ordinal=refs.get(row.get('action_ref'))
        if row.get('entity_ref') not in target_refs or type(ordinal) is not int or actions[ordinal]['kind']!='CREDIT_REPAY':continue
        # A remembered target cannot turn a debt question into a new payment.
        # The new amount or repayment mode must come from this user's turn.
        if not any(value.get('action_ref')==row.get('action_ref') and value.get('operation')=='SET'
                and value.get('field') in {'amount_minor','repayment_mode'} and value.get('source_span')
                and not value.get('entity_ref') for value in changes):
            _deny('SEMANTIC_REPAYMENT_REQUIRES_CURRENT_AMOUNT_OR_MODE')
    for identity_kind, field, consumer in ((PAY_KIND,'source_card','CREDIT_REPAY'),(READ_KIND,'card_number','CARD_STATUS')):
        funding_refs={ref for ref in decision.get('entity_refs',[]) if
            (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')==identity_kind}
        if funding_refs and (mode!='REQUEST' or not funding_refs<={row.get('entity_ref') for row in changes
                if row.get('operation')=='SET' and row.get('field')==field
                and type(refs.get(row.get('action_ref'))) is int
                and actions[refs[row['action_ref']]]['kind']==consumer}):
            _deny('SEMANTIC_REPAYMENT_IDENTITY_REQUIRES_CURRENT_REQUEST')
    literal_refs=set()
    if project_credit_literal and credit_refs:
        from .semantic_credit_identity import project_current_literal_selection
        changes,ledger=project_current_literal_selection(session,principal,thread,turn.message,decision,offered,changes,actions)
        literal_refs={row['entity_ref'] for row in ledger}
        if credit_literal_ledger is not None:credit_literal_ledger.extend(ledger)
    if credit_refs and (mode!='REQUEST' or not credit_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='card_reference'
            and type(refs.get(row.get('action_ref'))) is int
            and actions[refs[row['action_ref']]]['kind'] in {'CREDIT_VIEW','CREDIT_STATEMENT'}}|literal_refs):
        _deny('SEMANTIC_CREDIT_IDENTITY_REQUIRES_NEW_READ')
    from .bill_query_context import IDENTITY_KIND,has_identity_route
    bill_refs={ref for ref in decision.get('entity_refs',[]) if
        ((offered.get('entity_bindings',{}).get(ref) or {}).get('actual_entity') or {}).get('kind')==IDENTITY_KIND}
    consumed_bill_refs={row.get('entity_ref') for row in changes if row.get('operation')=='SET'
        and type(refs.get(row.get('action_ref'))) is int
        and has_identity_route(((offered.get('entity_bindings',{}).get(row.get('entity_ref')) or {}).get('actual_entity') or {}),
            row.get('field'),actions[refs[row['action_ref']]]['kind'])}
    from .semantic_router import is_completed_read_followup
    bill_fresh_read = mode=='REQUEST' or is_completed_read_followup(decision,offered['model'])
    if bill_refs and (not bill_fresh_read or not bill_refs<=consumed_bill_refs):
        _deny('SEMANTIC_BILL_IDENTITY_REQUIRES_EXPLICIT_READ')
    occasion_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_OCCASION_RECEIPT_IDENTITY'}
    if occasion_refs and (mode!='REQUEST' or not occasion_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='occasion_id'}):
        _deny('SEMANTIC_OCCASION_IDENTITY_REQUIRES_EXPLICIT_REFERENCE')
    member_refs={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')=='COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY'}
    if member_refs and (mode!='REQUEST' or not member_refs<={row.get('entity_ref') for row in changes
            if row.get('operation')=='SET' and row.get('field')=='family_member_id'}):
        _deny('SEMANTIC_OCCASION_IDENTITY_REQUIRES_EXPLICIT_REFERENCE')
    for change in changes:
        ordinal=refs.get(change.get('action_ref'))
        if change.get('field')!='occasion_id' or type(ordinal) is not int or not 0<=ordinal<len(actions) \
                or actions[ordinal]['kind']!='OCCASION_SET':continue
        # A completed save or an earlier READ identifies the family member;
        # neither can supply a new birthday or reopen its old confirmation.
        if mode!='REQUEST' or change.get('operation')!='SET' or not change.get('entity_ref'):
            _deny('SEMANTIC_OCCASION_UPDATE_REQUIRES_CURRENT_DATE')
        for field in ('birthday_month','birthday_day'):
            values=[row for row in changes if row.get('action_ref')==change['action_ref'] and row.get('field')==field]
            if len(values)!=1 or values[0].get('operation')!='SET' or values[0].get('entity_ref'):
                _deny('SEMANTIC_OCCASION_UPDATE_REQUIRES_CURRENT_DATE')
    if mode=='RESUME' and changes:_deny('SEMANTIC_RESUME_NEW_FIELDS')
    # Currency first; deterministic money parsing must never default a stated
    # USD amount into an old CNY operation because fields arrived reordered.
    changes=sorted(changes,key=lambda row:row.get('field') not in {'currency','source_currency','target_currency'})
    for change in changes:
        ordinal=refs.get(change.get('action_ref'));field=change.get('field')
        if type(ordinal) is not int or not 0<=ordinal<len(actions) or (ordinal,field) in changed:_deny('SEMANTIC_CHANGE_AMBIGUOUS')
        action=actions[ordinal]
        if field not in ACTION_ALLOWED_FIELDS.get(action['kind'],set()):_deny('SEMANTIC_FIELD_NOT_ALLOWED')
        changed.add((ordinal,field));span=change.get('source_span');entity_ref=change.get('entity_ref')
        raw=_trusted_span(turn.message,span,field,prior=action['slots'],
            allow_payment_purpose=mode=='REQUEST' and action['kind'] in {'TRANSFER','TRANSFER_SCHEDULE'}
                and not (turn.diagnostics_json or {}).get('recipient_choice_selection_v1'))
        spans[ordinal].append(span)
        if guard_funding_source and change.get('operation')=='SET':
            from .semantic_funding_source import is_funding_field
            if is_funding_field(action['kind'],field):funding_spans[ordinal].append(span)
        if action['kind']=='AA_REQUEST' and field=='aa_expires_at' and change.get('operation')=='SET':deadline_spans[ordinal].append(span)
        if entity_ref:
            if change.get('operation')!='SET' or entity_ref not in decision.get('entity_refs',[]):_deny()
            value=_entity(session,principal,thread,offered,entity_ref,field,action_kind=action['kind'],audit_only=audit_only,
                payment_parent_task_id=turn.task_id)
            if change.get('selected_options') and change['selected_options']!=[value]:
                _deny('SEMANTIC_OPTION_ENTITY_CONFLICT')
            entry=(offered.get('entity_bindings') or {}).get(entity_ref,{})
            actual=entry.get('actual_entity') or entry.get('entity') or {}
            if action['kind']=='ASSET_POSITIONS_READ' and field=='product_name' \
                    and entry.get('source_kind')=='COMPLETED_ASSET_RECEIPT_IDENTITY':
                # _entity has just independently verified the owner, posted
                # InvestmentOrder and InvestmentProduct source. That closed
                # table is WEALTH; no old amount/holding value is inherited.
                checked_asset_classes.setdefault(ordinal,set()).add('WEALTH')
            if action['kind']=='CARD_STATUS' and field in {'card_number','card_last4'} \
                    and ((actual.get('domain'),actual.get('read_kind')) in {('CARDS','CARD_STATUS'),('CREDIT','CREDIT_VIEW')}
                        or field=='card_number' and entry.get('source_kind') in {'COMPLETED_TRANSFER_RECEIPT_IDENTITY','COMPLETED_WEALTH_CARD_IDENTITY','COMPLETED_FX_CARD_IDENTITY','COMPLETED_REPAYMENT_FUNDING_READ_IDENTITY'}):
                owned_card_references.add(ordinal)
                if (actual.get('domain'),actual.get('read_kind'),actual.get('field'))==('CREDIT','CREDIT_VIEW','credit_card_status'):
                    owned_credit_status_references.add(ordinal)
            elif action['kind']=='BILL_ANALYSIS' and field in {'card_number','card_last4'} \
                    and (actual.get('domain'),actual.get('read_kind')) in {
                        ('CARDS','CARD_STATUS'),('CREDIT','CREDIT_VIEW'),('CREDIT','CREDIT_STATEMENT')}:
                # _entity has already rebuilt the entire fresh owned READ.
                # This authorizes only the selected statement scope, no cash.
                owned_card_references.add(ordinal)
            elif action['kind'] in {'CREDIT_VIEW','CREDIT_STATEMENT'} and field in {'card_reference','card_number','card_last4'} \
                    and (actual.get('domain')=='CREDIT' and actual.get('field') in {'credit_card_status','credit_card_read_identity'}
                        or entry.get('source_kind') in {'COMPLETED_CREDIT_RECEIPT_IDENTITY','COMPLETED_REPAYMENT_TARGET_IDENTITY'}):
                # The selected owned card has already passed its exact source
                # consumer above. An unrelated clause about the funding card
                # must not be mistaken for a different credit-card owner.
                owned_card_references.add(ordinal)
        else:
            if change.get('operation')=='CLEAR':
                old=action['slots'].get(field)
                direct=re.search(r'清除|删除|移除|去掉|不要.{0,8}(?:备注|字段)|\b(?:clear|remove|delete)\b',raw,re.I)
                denial=field in _IDENTITIES and isinstance(old,str) and old in raw and re.search(r'不是|并非|不要|不用|不再|\b(?:not|no\s+longer)\b',raw,re.I)
                read_filter=(action['kind']=='BILL_ANALYSIS' and field in {'category_filter','card_number','card_last4','currency'}) or (
                    project_read_entity_scope==FAMILY_READ_SCOPE_PROJECTION_VERSION
                    and action['kind']=='FAMILY_VIEW' and field in FAMILY_READ_FIELDS)
                if mode=='REQUEST' or not (read_filter or direct or denial):_deny('SEMANTIC_CLEAR_NOT_EXPLICIT')
                action['slots'].pop(field,None);continue
            if change.get('operation')!='SET':_deny()
            from .authentication import as_utc
            value=_literal_field(field,raw,action['kind'],action['slots'],change.get('selected_options'),
                source_created_at=as_utc(turn.created_at))
            if project_fx_unit and field=='amount_minor' and action['kind'] in {'FX_QUOTE','FX_EXCHANGE','FX_TRANSFER'}:
                from .conversation_service import _continuation_money
                money=_continuation_money(raw,action['slots'].get('currency') or 'CNY')
                if money is not None and money[2] is not None:
                    if money[0]!=value or action['slots'].get('currency') not in {None,money[1]}:
                        _deny('SEMANTIC_CURRENCY_CONFLICT')
                    action['slots']['currency']=money[1]
                    _normalize_currency_contract(action,allowed)
        if field=='category_filter' and value is None:
            action['slots'].pop(field,None)
        else:
            from .semantic_read_card_alias import replace_inherited_card_alias
            explicit={row.get('field') for row in changes if row.get('operation')=='SET'
                and refs.get(row.get('action_ref'))==ordinal}
            replace_inherited_card_alias(action,field,value,explicit,
                inherits_scope=decision.get('query_base')=='CURRENT' or is_completed_read_followup(decision,offered['model']))
            action['slots'][field]=value
        if field=='currency':
            _normalize_currency_contract(action,allowed)
    for change in changes:
        ordinal=refs.get(change.get('action_ref'))
        if type(ordinal) is int and actions[ordinal]['kind']=='AA_REQUEST' and change.get('field')=='amount_minor' \
                and change.get('operation')=='SET' and not change.get('entity_ref'):
            _aa_selected_quantity_total(turn.message,change['source_span'],actions[ordinal]['slots'])
    for ordinal,classes in checked_asset_classes.items():
        action=actions[ordinal]
        if classes!={'WEALTH'} or action['slots'].get('asset_class') not in {None,'WEALTH'}:
            _deny('SEMANTIC_ASSET_CLASS_SOURCE_MISMATCH')
        action['slots']['asset_class']='WEALTH'
    selected_scopes=set()
    for ordinal in owned_credit_status_references:
        action=actions[ordinal]
        # The exact entity above already passed full READ catalog/owner/card
        # proof. Align only this READ with its registered credit DTO/tool;
        # never classify a bare literal or turn a control into an inquiry.
        if action['kind']!='CARD_STATUS' or 'CREDIT_VIEW' not in allowed \
                or {key for key,value in action['slots'].items() if value is not None}-ACTION_ALLOWED_FIELDS['CREDIT_VIEW']:
            _deny('SEMANTIC_CREDIT_READ_MAPPING_INVALID')
        action['kind']='CREDIT_VIEW'
    from .card_balance_scope import canonical_currencies, check_scalar_currency
    for action in actions:
        requested = action['slots'].get('card_balance_currencies')
        if requested is not None:
            if action['kind'] != 'CARD_STATUS': _deny('SEMANTIC_FIELD_NOT_ALLOWED')
            try: check_scalar_currency(canonical_currencies(requested), action['slots'].get('currency'))
            except ValueError as error: _deny(str(error))
    if project_read_entity_scope:
        _reject_mixed_read_only_replanning(receipt,offered,actions)
        project_family_scope=project_read_entity_scope==FAMILY_READ_SCOPE_PROJECTION_VERSION
        _validate_read_entity_scope_sources(session,principal,thread,receipt,offered,actions,audit_only=audit_only,project_family_scope=project_family_scope)
        selection=_read_entity_scope_selection(receipt,offered,actions,project_family_scope=project_family_scope)
        if selection is not None:
            ordinal,entity_ref,field,apply_scope=selection;action=actions[ordinal]
            if mode=='REQUEST':
                _source_frame,_source_entry,_source_actions=_frame(session,principal,thread,offered,
                    offered['entity_bindings'][entity_ref]['frame_ref'],depth,audit_only=audit_only)
            value=_entity(session,principal,thread,offered,entity_ref,field,
                action_kind=action['kind'],audit_only=audit_only)
            _read_entity_scope_live_origin(session,principal,thread,offered,selection,audit_only=audit_only)
            if action['kind']=='CARD_STATUS':owned_card_references.add(ordinal)
            if apply_scope:
                if action['kind']=='CARD_STATUS' and apply_scope!='CANONICAL_CARD_INTERSECTION':
                    for alias in {'card_number','card_last4'}-{field}:action['slots'].pop(alias,None)
                action['slots'][field]=value;selected_scopes.add(ordinal)
    for i,action in enumerate(actions):
        # An untouched prior action is retained, not newly instructed by this
        # partial reply. Current polarity is checked for the affected actions.
        if mode=='REQUEST' or spans[i] or i in selected_scopes:_request_boundary(turn.message,action['kind'],spans[i],
            owned_card_reference=i in owned_card_references,deadline_spans=deadline_spans[i],
            read_subject_message=read_subject_messages.get(next((ref for ref,ordinal in refs.items() if ordinal==i),'')),funding_spans=funding_spans[i])
    # A CONTINUE with no fresh changes may explicitly resume the same raw
    # semantic goal. It cannot create a new confirmation or new action here.
    from .recipient_choices import validate_compiled_choices
    validate_compiled_choices(decision, offered, actions, refs)
    proposal=IntentProposal.model_validate({'summary':turn.message[:500],'actions':actions})
    return proposal,frame


def _normalize_currency_contract(action,allowed):
    """Registered denomination mapping, not phrase/action classification."""
    kind=action['kind'];currency=action['slots'].get('currency')
    if kind=='TRANSFER' and currency=='USD':
        if 'FX_TRANSFER' not in allowed:_deny('SEMANTIC_CURRENCY_CAPABILITY_NOT_OFFERED')
        if {field for field,value in action['slots'].items() if value is not None}-ACTION_ALLOWED_FIELDS['FX_TRANSFER']:_deny('SEMANTIC_FX_INHERITED_FIELD_UNSUPPORTED')
        action['kind']='FX_TRANSFER'
    elif currency=='USD' and kind in {'TRANSFER_SCHEDULE','AA_REQUEST','WEALTH_BUY','WEALTH_REDEEM','CARD_SET_LIMIT','GOAL_RESERVE','GOAL_RELEASE'}:
        _deny('SEMANTIC_CURRENCY_OPERATION_UNSUPPORTED')
    elif kind=='FX_TRANSFER' and currency not in {None,'USD'}:_deny('SEMANTIC_CURRENCY_OPERATION_UNSUPPORTED')


def _lifecycle(session,parent,execution_context=None,*,readonly_completed=False,phase_context=None):
    from .conversation_provenance import _DialogueExecutionContext
    execution=isinstance(execution_context,_DialogueExecutionContext) and execution_context.permits(parent)
    from .semantic_phase_context import verified_semantic_phase
    phase=verified_semantic_phase(session,parent,phase_context) if phase_context is not None else None
    completed=set(phase['completed_ids']) if phase else set()
    if parent.state not in _MUTABLE and not execution and not phase and not (readonly_completed and parent.state=='SUCCEEDED'):_deny('SEMANTIC_TASK_NOT_MUTABLE')
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id)))
    child_ids=[]
    for op in operations:
        if op.child_task_id:child_ids.append(op.child_task_id)
        child=session.get(Task,op.child_task_id) if op.child_task_id else None
        if op.action_kind not in READ_KINDS and (op.status=='UNKNOWN' or child and child.state=='UNKNOWN'):_deny()
        if not execution and op.id not in completed and op.action_kind not in READ_KINDS and (op.status in {'AUTHORIZED','RUNNING','SUCCEEDED'} or child and child.state in {'AUTHORIZED','EXECUTING','SUCCEEDED'}):_deny()
    completed_children={op.child_task_id for op in operations if op.id in completed}
    if not execution:
        for used in session.scalars(select(Confirmation).where(Confirmation.task_id.in_([parent.id,*child_ids]),
                (Confirmation.status=='CONSUMED') | Confirmation.consumed_at.is_not(None))):
            if used.task_id not in completed_children:_deny()


READ_CLONE_VERSION='completed-read-followup-v1'
READ_COLLECTION_CLONE_VERSION='completed-read-collection-followup-v2'


def _read_clone_marker(session,principal,thread,turn,receipt,offered,proposal,prior):
    """Derive a fresh-query origin from DB facts, never a reopening authority."""
    from .semantic_router import is_completed_read_followup
    from .semantic_query_scope import is_bound_current_read_request
    set_read=is_bound_current_read_request(receipt['decision'],offered['model'])
    if not (is_completed_read_followup(receipt['decision'],offered['model']) or set_read) or not prior \
            or not proposal.actions or any(action.kind not in READ_KINDS for action in proposal.actions):
        _deny('SEMANTIC_READ_CLONE_BINDING_INVALID')
    focus=receipt['decision']['focus_ref'];entry=offered['frame_bindings'].get(focus)
    record=session.get(RuntimeRecord,prior.id)
    snapshot=session.get(PlanSnapshot,entry.get('snapshot_id')) if isinstance(entry,dict) and entry.get('snapshot_id') else None
    if not _owned(prior,principal) or prior.state!='SUCCEEDED' or not record or record.kind!='PARENT' \
            or not entry or entry.get('task_id')!=prior.id or prior.version!=entry.get('task_version') \
            or not snapshot or snapshot.task_id!=prior.id or snapshot.version!=record.plan_version \
            or snapshot.version!=entry.get('plan_version') or snapshot.plan_hash!=prior.plan_hash \
            or snapshot.plan_hash!=entry.get('plan_hash') or canonical_hash(snapshot.plan_json)!=snapshot.plan_hash \
            or canonical_hash(prior.plan_json)!=snapshot.plan_hash or snapshot.proposal_json!=entry.get('proposal_json'):
        _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
    original=IntentProposal.model_validate(deepcopy(snapshot.proposal_json)).actions
    from .semantic_read_collection import current_read_collection
    selected=current_read_collection(receipt['decision'],offered['model']) if set_read else None
    collection_subset=bool(selected and set(action.kind for action in original)!=set(action.kind for action in proposal.actions))
    if (not collection_subset and (set(action.kind for action in original)!=set(action.kind for action in proposal.actions) if set_read else
            [action.kind for action in original]!=[action.kind for action in proposal.actions])) \
            or any(action.kind not in READ_KINDS for action in original):
        _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==prior.id)
        .order_by(RuntimeOperation.ordinal)))
    if len(operations)!=len(original):_deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
    child_ids=[]
    for op,action in zip(operations,original):
        child=session.get(Task,op.child_task_id) if op.child_task_id else None
        if op.action_kind!=action.kind or op.action_kind not in READ_KINDS or op.status!='SUCCEEDED' \
                or op.plan_version!=record.plan_version or not _owned(child,principal) or child.state!='SUCCEEDED':
            _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
        child_ids.append(child.id)
    if session.scalar(select(Confirmation.id).where(Confirmation.task_id.in_([prior.id,*child_ids])).limit(1)):
        _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
    origin_turn=session.get(ChatTurn,entry.get('source_turn_id'))
    _valid_turn(session,principal,thread,origin_turn)
    if origin_turn.task_id!=prior.id or origin_turn.status!='ANSWERED' or origin_turn.sequence>=turn.sequence:
        _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
    marker={'version':READ_CLONE_VERSION,'origin_task_id':prior.id,'origin_frame_ref':focus,
        'origin_plan_hash':snapshot.plan_hash,'origin_snapshot_id':snapshot.id}
    if collection_subset:
        origins=[];checked=set();by_id={op.id:op for op in operations}
        for target in selected['targets']:
            binding=offered['entity_bindings'].get(target['entity_ref']) or {}
            actual=binding.get('actual_entity') or {};catalog=_entity_catalog(offered,binding) or {}
            if binding.get('frame_ref')!=focus or catalog.get('selected_task_id')!=prior.id \
                    or actual not in catalog.get('entities',[]) or actual.get('read_kind')!=selected['read_kind'] \
                    or actual.get('domain')!=selected['domain'] or actual.get('field')!=selected['field']:
                _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
            if catalog.get('catalog_hash') not in checked:
                _audit_catalog_source(session,principal,thread,catalog);checked.add(catalog.get('catalog_hash'))
            sources=[row for row in catalog.get('sources',[]) if row.get('result_ref')==actual.get('result_ref')]
            if len(sources)!=1:_deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
            source=sources[0];op=by_id.get(source.get('operation_id'))
            if not op or op.action_kind!=selected['read_kind'] or source.get('read_kind')!=op.action_kind \
                    or source.get('origin_task_id')!=prior.id or source.get('child_task_id')!=op.child_task_id \
                    or source.get('origin_snapshot_id')!=snapshot.id or source.get('origin_plan_hash')!=snapshot.plan_hash:
                _deny('SEMANTIC_READ_CLONE_SOURCE_CHANGED')
            origins.append({**target,'source_operation_id':op.id,'source_action_ordinal':op.ordinal,
                'source_child_task_id':op.child_task_id,'result_ref':source['result_ref']})
        marker.update(version=READ_COLLECTION_CLONE_VERSION,selected_collection={
            **{key:selected[key] for key in ('set_ref','domain','field','read_kind')},'origins':origins})
    return marker


def _validate_read_clone_binding(session,principal,thread,parent,turn,receipt,offered,proposal,prior,binding):
    marker=binding.get('read_clone')
    if prior and prior.id!=parent.id:
        expected=_read_clone_marker(session,principal,thread,turn,receipt,offered,proposal,prior)
        if marker!=expected:_deny('SEMANTIC_READ_CLONE_BINDING_INVALID')
    elif marker is not None:
        _deny('SEMANTIC_READ_CLONE_BINDING_INVALID')


def build_semantic_intent_binding(session,principal,thread,turn,result,offered_context,*,parent):
    thread,scope,epoch=_valid_turn(session,principal,thread,turn)
    receipt=_validated_result(result,offered_context,turn.message)
    if offered_context.get('actor_scope_hash')!=epoch or offered_context.get('thread_id')!=thread.id \
            or offered_context.get('turn_id')!=turn.id or offered_context.get('message_hash')!=canonical_hash({'message':turn.message}) \
            or offered_context['model'].get('role')!=scope['role']:_deny('SEMANTIC_CONTEXT_ACTOR_MISMATCH')
    if turn.diagnostics_json.get(RECEIPT_KEY)!=receipt:_deny('SEMANTIC_RECEIPT_NOT_PERSISTED')
    if not _owned(parent,principal):_deny()
    _lifecycle(session,parent)
    credit_ledger=[]
    proposal,prior=_rebuild_actions(session,principal,thread,turn,receipt,offered_context,project_fx_unit=True,
        project_read_entity_scope=FAMILY_READ_SCOPE_PROJECTION_VERSION,project_credit_literal=True,credit_literal_ledger=credit_ledger,guard_funding_source=True)
    diagnostics=receipt.get('diagnostics') or {}
    identity=diagnostics.get('response_model') or diagnostics.get('requested_model')
    proposal=proposal.model_copy(update={'model_label':str(diagnostics.get('actual_provider') or 'deepseek')+':'+str(identity),'fallback':False})
    read_clone=None
    if prior and prior.id!=parent.id:
        # Only an untouched fresh parent can receive a read clone. A completed
        # origin is never revised, replanned, confirmed or written by this path.
        fresh_record=session.get(RuntimeRecord,parent.id)
        if parent.state!='QUEUED' or parent.message!=turn.message or parent.plan_hash or parent.plan_json \
                or not fresh_record or fresh_record.plan_version or fresh_record.kind!='PARENT' \
                or session.scalar(select(RuntimeOperation.id).where(RuntimeOperation.parent_task_id==parent.id).limit(1)) \
                or session.scalar(select(Confirmation.id).where(Confirmation.task_id==parent.id).limit(1)):
            _deny('SEMANTIC_PARENT_FOCUS_MISMATCH')
        read_clone=_read_clone_marker(session,principal,thread,turn,receipt,offered_context,proposal,prior)
    value={'version':VERSION,'task_id':parent.id,'conversation_id':thread.id,'actor_scope_hash':epoch,
        'source_turn_id':turn.id,'source_sequence':turn.sequence,'source_message_hash':canonical_hash({'message':turn.message}),
        'source_receipt_hash':canonical_hash(receipt),'input_hash':receipt['input_hash'],
        'source_parent_version':parent.version,'decision':deepcopy(receipt['decision']),
        'semantic_diagnostics':deepcopy(receipt['diagnostics']),
        'offered_context':deepcopy(offered_context),'offered_hash':canonical_hash(offered_context),
        'expected_proposal':proposal.model_dump(),'bank_write_authorized':False,'confirmation_inherited':False,
        'fx_money_unit_projection':FX_MONEY_UNIT_PROJECTION_VERSION,
        'fx_currency_source_ledger':_fx_currency_source_ledger(receipt,offered_context,proposal,turn.message)}
    from .semantic_funding_source import VERSION as funding_version
    value['funding_source_polarity']=funding_version
    family_scope=any(action.kind=='FAMILY_VIEW' for action in proposal.actions)
    value['read_entity_scope_projection']=FAMILY_READ_SCOPE_PROJECTION_VERSION if family_scope else READ_ENTITY_SCOPE_PROJECTION_VERSION
    value['read_entity_scope_ledger']=_read_entity_scope_ledger(receipt,offered_context,proposal,project_family_scope=family_scope)
    if credit_ledger:
        from .semantic_credit_identity import LITERAL_SELECTOR_VERSION
        value['credit_literal_selector_projection']=LITERAL_SELECTOR_VERSION
        value['credit_literal_selector_ledger']=credit_ledger
    if read_clone is not None:value['read_clone']=read_clone
    source_kinds=receipt['decision'].get('action_kinds') or ([row['kind'] for row in offered_context['frame_bindings'][receipt['decision']['focus_ref']]['proposal_json']['actions']] if prior else [])
    actual_kinds=[row.kind for row in proposal.actions]
    if source_kinds!=actual_kinds:
        credit_read_mapping=len(source_kinds)==len(actual_kinds) and all(source==actual or
            (source,actual)==('CARD_STATUS','CREDIT_VIEW') for source,actual in zip(source_kinds,actual_kinds))
        value['semantic_diagnostics'].update(server_normalization_reason='VERIFIED_OWNED_CREDIT_READ_TOOL_MAPPING' if credit_read_mapping else 'REGISTERED_DENOMINATION_TOOL_MAPPING',
            semantic_action_kinds=source_kinds,compiled_action_kinds=actual_kinds)
    if receipt['decision']['mode']=='RESUME':
        from .semantic_lifecycle import current_semantic_pause
        if not current_semantic_pause(session,principal,parent):_deny('SEMANTIC_RESUME_PAUSE_NOT_CURRENT')
        value['resume_pause']=deepcopy(session.get(RuntimeRecord,parent.id).metadata_json['semantic_pause'])
    from .temporal_fields import FIELDS,capture_temporal_context,temporal_origin_ledger
    if any(FIELDS.get(action.kind,frozenset()).intersection(action.slots.model_dump(exclude_none=True)) for action in proposal.actions):
        value['offered_context']=capture_temporal_context(session,receipt,offered_context,proposal)
        value['offered_hash']=canonical_hash(value['offered_context'])
        value['temporal_origins']=temporal_origin_ledger(session,principal,thread,value,current=True)
    value['binding_hash']=canonical_hash(value)
    return value


def _validate_resume_genesis(session,principal,thread,binding):
    pause=binding.get('resume_pause')
    if not isinstance(pause,dict) or pause.get('version')!='semantic-owned-plan-pause-v1':_deny()
    source=session.get(ChatTurn,pause.get('turn_id'))
    _valid_turn(session,principal,thread,source)
    _,epoch=dialogue_actor_scope(session,principal)
    if source.task_id!=binding['task_id'] or source.status!='ANSWERED' or source.mode!='FEEDBACK' \
            or source.diagnostics_json.get('semantic_control')!='PAUSE' or source.diagnostics_json.get('bank_write_authorized') is not False \
            or source.sequence>=binding['source_sequence'] or pause.get('actor_scope_hash')!=epoch \
            or pause.get('message_hash')!=canonical_hash({'message':source.message}) \
            or pause.get('bank_write_authorized') is not False:_deny()
    entry=binding['offered_context']['frame_bindings'].get(binding['decision']['focus_ref'])
    if not entry or entry.get('task_version')!=pause.get('task_version') or entry.get('plan_version')!=pause.get('plan_version') \
            or entry.get('plan_hash')!=pause.get('plan_hash') or entry.get('message_hash')!=pause.get('intent_message_hash'):_deny()


def _seal(task_id,message,proposal,binding):
    digest=canonical_hash({'task_id':task_id,'message':message,'proposal':proposal,'binding_hash':canonical_hash(binding)})
    return hmac.new(_SEAL_KEY,digest.encode(),hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VerifiedSemanticIntentProof:
    authority: object
    task_id: str
    original_message: str
    proposal_json: dict
    binding: dict
    seal: str

    @property
    def expected_proposal(self):
        return IntentProposal.model_validate(deepcopy(self.proposal_json))

    def public(self):
        return {'version':VERSION,'mode':self.binding['decision']['mode'],'action_kinds':[row.kind for row in self.expected_proposal.actions],
            'input_hash':self.binding['input_hash'],'binding_hash':self.binding['binding_hash'],
            'bank_write_authorized':False,'confirmation_inherited':False}


def load_verified_semantic_intent(session,parent,metadata,*,execution_context=None,phase_context=None):
    from .services import Principal
    from .semantic_router import SemanticDecision,semantic_input_hash
    binding=metadata.get(METADATA_KEY) if isinstance(metadata,dict) else None
    if binding is None:
        # Metadata removal does not turn a native cloud-understood task into
        # a legacy task. Derive this requirement from its real owned receipt.
        # An aside/control or detached pending reply to a legacy task is not
        # its current original message and does not require a new active proof.
        sources=session.scalars(select(ChatTurn).where(ChatTurn.task_id==parent.id,
            ChatTurn.user_id==parent.user_id,ChatTurn.sandbox_id==parent.sandbox_id,
            ChatTurn.status=='ANSWERED',ChatTurn.message==parent.message))
        for source in sources:
            receipt=(source.diagnostics_json or {}).get(RECEIPT_KEY)
            if isinstance(receipt,dict) and receipt.get('status')=='READY' \
                    and receipt.get('purpose')=='UNIFIED_SEMANTIC_UNDERSTANDING' \
                    and (receipt.get('decision') or {}).get('mode') in {'REQUEST','CONTINUE','REVISE','RESUME'}:
                _deny('SEMANTIC_BINDING_NOT_STORED')
        return None
    record=session.get(RuntimeRecord,parent.id)
    if not record or binding!=(record.metadata_json or {}).get(METADATA_KEY):_deny('SEMANTIC_BINDING_NOT_STORED')
    payload=deepcopy(binding);digest=payload.pop('binding_hash',None)
    if digest!=canonical_hash(payload) or binding.get('version')!=VERSION or binding.get('task_id')!=parent.id \
            or binding.get('bank_write_authorized') is not False or binding.get('confirmation_inherited') is not False:_deny()
    principal=Principal(parent.user_id,parent.sandbox_id,family_context=(record.metadata_json or {}).get('family_context_origin'))
    thread=session.get(ChatThread,binding.get('conversation_id'));turn=session.get(ChatTurn,binding.get('source_turn_id'))
    thread,scope,epoch=_valid_turn(session,principal,thread,turn)
    receipt=turn.diagnostics_json.get(RECEIPT_KEY)
    if turn.task_id!=parent.id or turn.status!='ANSWERED' or parent.message!=turn.message or epoch!=binding['actor_scope_hash'] \
            or turn.sequence!=binding['source_sequence'] or canonical_hash({'message':turn.message})!=binding['source_message_hash'] \
            or not isinstance(receipt,dict) or canonical_hash(receipt)!=binding['source_receipt_hash'] \
            or receipt.get('status')!='READY' or receipt.get('decision')!=binding['decision']:_deny()
    SemanticDecision.model_validate(deepcopy(binding['decision']))
    offered=binding['offered_context']
    if canonical_hash(offered)!=binding['offered_hash'] or semantic_input_hash(**{**offered['model'],'message':turn.message})!=binding['input_hash'] \
            or receipt.get('input_hash')!=binding['input_hash']:_deny()
    readonly=all(row.kind in READ_KINDS for row in IntentProposal.model_validate(deepcopy(binding['expected_proposal'])).actions)
    _lifecycle(session,parent,execution_context,readonly_completed=readonly,phase_context=phase_context)
    if binding['decision']['mode']=='RESUME':_validate_resume_genesis(session,principal,thread,binding)
    from .semantic_phase_context import verified_semantic_phase
    phase=verified_semantic_phase(session,parent,phase_context) if phase_context is not None else None
    from .conversation_provenance import _DialogueExecutionContext,current_plan_completed_write
    execution=isinstance(execution_context,_DialogueExecutionContext) and execution_context.permits(parent)
    already_completed=execution and any(current_plan_completed_write(session,parent,op) for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id)))
    credit_ledger=[]
    proposal,prior=_rebuild_actions(session,principal,thread,turn,receipt,offered,audit_only=bool(phase or already_completed),project_fx_unit=_fx_money_unit_projection(binding),project_read_entity_scope=_read_entity_scope_projection(binding),project_credit_literal=_credit_literal_projection(binding),credit_literal_ledger=credit_ledger,guard_funding_source=_funding_source_polarity(binding,live=True))
    _validate_credit_literal_ledger(binding,credit_ledger)
    _validate_fx_currency_source_ledger(binding,receipt,offered,proposal,turn.message)
    _validate_read_entity_scope_ledger(binding,receipt,offered,proposal)
    _validate_read_clone_binding(session,principal,thread,parent,turn,receipt,offered,proposal,prior,binding)
    diagnostics=receipt.get('diagnostics') or {}
    identity=diagnostics.get('response_model') or diagnostics.get('requested_model')
    proposal=proposal.model_copy(update={'model_label':str(diagnostics.get('actual_provider') or 'deepseek')+':'+str(identity),'fallback':False})
    if proposal.model_dump()!=IntentProposal.model_validate(deepcopy(binding['expected_proposal'])).model_dump():_deny()
    if 'temporal_origins' in binding:
        from .temporal_fields import checked_temporal_origins
        checked_temporal_origins(session,principal,binding)
    data=proposal.model_dump()
    return VerifiedSemanticIntentProof(_AUTHORITY,parent.id,turn.message,data,deepcopy(binding),_seal(parent.id,turn.message,data,binding))


def semantic_verified(context):
    proof=(context or {}).get(PRIVATE_KEY)
    if not isinstance(proof,VerifiedSemanticIntentProof) or proof.authority is not _AUTHORITY:return None
    expected=_seal(proof.task_id,proof.original_message,proof.proposal_json,proof.binding)
    return proof if hmac.compare_digest(expected,proof.seal) else None


def validate_paused_semantic_intent(session,principal,parent,binding):
    """Recheck stored pending fields without treating them as an active grant."""
    record=session.get(RuntimeRecord,parent.id)
    if not record or binding!=(record.metadata_json.get('semantic_pause') or {}).get('pending_binding'):_deny('SEMANTIC_PENDING_BINDING_NOT_STORED')
    _lifecycle(session,parent)
    thread=session.get(ChatThread,binding.get('conversation_id'))
    actions=_validate_prior_semantic(session,principal,thread,parent,binding)
    turn=session.get(ChatTurn,binding.get('source_turn_id'))
    proposal=IntentProposal.model_validate(deepcopy(binding['expected_proposal'])).model_dump()
    return VerifiedSemanticIntentProof(_AUTHORITY,parent.id,turn.message,proposal,deepcopy(binding),_seal(parent.id,turn.message,proposal,binding))


def ground_semantic_proposal(proposal,message,context):
    proof=semantic_verified(context)
    if proof is None or proof.original_message!=message:_deny('SEMANTIC_PRIVATE_PROOF_REQUIRED')
    # Revalidate from dictionaries even for Pydantic model_construct objects.
    supplied=IntentProposal.model_validate(_plain(proposal))
    expected=proof.expected_proposal
    if [(row.kind,row.depends_on) for row in supplied.actions]!=[(row.kind,row.depends_on) for row in expected.actions]:_deny('SEMANTIC_PROPOSAL_DAG_MISMATCH')
    for raw,proved in zip(supplied.actions,expected.actions):
        actual=raw.slots.model_dump(exclude_none=True);wanted=proved.slots.model_dump(exclude_none=True)
        if any(field not in wanted or value!=wanted[field] or type(value) is not type(wanted[field]) for field,value in actual.items()):_deny('SEMANTIC_PROPOSAL_FIELD_MISMATCH')
    return expected.model_copy(update={'fallback':False})


def validate_semantic_proposal(session,parent,proposal,metadata,*,execution_context=None,phase_context=None):
    proof=load_verified_semantic_intent(session,parent,metadata,execution_context=execution_context,phase_context=phase_context)
    if proof is None:return None
    supplied=IntentProposal.model_validate(_plain(proposal))
    clean=lambda p:[{'kind':row.kind,'slots':row.slots.model_dump(exclude_none=True),'depends_on':row.depends_on} for row in p.actions]
    if clean(supplied)!=clean(proof.expected_proposal):_deny('SEMANTIC_COMPILED_PROPOSAL_CHANGED')
    return proof


def _audit_catalog_source(session,principal,thread,catalog):
    """Historical read evidence only; deliberately no live bank-state refresh."""
    from .dialogue_references import _actor_epoch
    if not isinstance(catalog,dict):_deny()
    payload=deepcopy(catalog);digest=payload.pop('catalog_hash',None)
    epoch=_actor_epoch(session,principal)
    if digest!=canonical_hash(payload) or catalog.get('actor_epoch')!=epoch or catalog.get('source_role')!=epoch['role'] \
            or catalog.get('conversation_id')!=thread.id \
            or catalog.get('owner_hash')!=canonical_hash({'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}):_deny()
    for source in catalog.get('sources',[]):
        original=session.get(Task,source.get('origin_task_id'));child=session.get(Task,source.get('child_task_id'))
        turn=session.get(ChatTurn,source.get('source_turn_id'));op=session.get(RuntimeOperation,source.get('operation_id'))
        snapshot=session.get(PlanSnapshot,source.get('origin_snapshot_id'))
        if not _owned(original,principal) or not _owned(child,principal) or not _owned(turn,principal) \
                or turn.thread_id!=thread.id or turn.task_id!=original.id or turn.status!='ANSWERED' \
                or not snapshot or snapshot.task_id!=original.id or snapshot.plan_hash!=source.get('origin_plan_hash') \
                or canonical_hash(snapshot.plan_json)!=snapshot.plan_hash \
                or canonical_hash(snapshot.proposal_json)!=source.get('origin_snapshot_hash') \
                or canonical_hash({'message':turn.message})!=source.get('source_message_hash') \
                or not op or op.parent_task_id!=original.id or op.child_task_id!=child.id \
                or op.action_kind not in READ_KINDS or op.action_kind!=source.get('read_kind') or op.status!='SUCCEEDED' \
                or canonical_hash({'kind':op.action_kind,'args':op.args_json,'ordinal':op.ordinal,'version':op.plan_version})!=source.get('operation_hash') \
                or canonical_hash(child.result_json)!=source.get('read_result_hash') \
                or canonical_hash(child.analysis_json)!=source.get('analysis_hash'):_deny('SEMANTIC_HISTORICAL_READ_SOURCE_CHANGED')
        _valid_turn(session,principal,thread,turn)


def validate_semantic_source_snapshot(session,principal,parent,metadata,snapshot):
    """NAVIGATION_ONLY origin audit; cannot replan, confirm or execute a write.

    Completed/closed task states are allowed here, not in the executable proof
    loader. A legitimately changed bank object keeps its historical read source
    but is not represented as fresh, selectable write authority.
    """
    from .semantic_router import validate_semantic_decision
    from .read_projection_request import prepare_read_input_once
    binding=metadata.get(METADATA_KEY) if isinstance(metadata,dict) else None
    if binding is None:
        load_verified_semantic_intent(session,parent,metadata)  # Mandatory source detects missing native marker.
        return None
    record=session.get(RuntimeRecord,parent.id)
    if not _owned(parent,principal) or not record or binding!=record.metadata_json.get(METADATA_KEY):_deny()
    live=session.get(PlanSnapshot,getattr(snapshot,'id',None)) if snapshot is not None else None
    if live is None or live.task_id!=parent.id or live.version!=record.plan_version \
            or live.plan_hash!=parent.plan_hash or canonical_hash(live.plan_json)!=live.plan_hash \
            or live.proposal_json!=snapshot.proposal_json:_deny('SEMANTIC_NAVIGATION_SNAPSHOT_CHANGED')
    payload=deepcopy(binding);digest=payload.pop('binding_hash',None)
    if digest!=canonical_hash(payload) or binding.get('version')!=VERSION or binding.get('task_id')!=parent.id \
            or binding.get('bank_write_authorized') is not False or binding.get('confirmation_inherited') is not False:_deny()
    thread=session.get(ChatThread,binding.get('conversation_id'));turn=session.get(ChatTurn,binding.get('source_turn_id'))
    thread,scope,epoch=_valid_turn(session,principal,thread,turn)
    receipt=turn.diagnostics_json.get(RECEIPT_KEY);offered=binding['offered_context']
    params={**offered['model'],'message':turn.message}
    if turn.task_id!=parent.id or turn.status!='ANSWERED' or parent.message!=turn.message \
            or epoch!=binding['actor_scope_hash'] or turn.sequence!=binding['source_sequence'] \
            or canonical_hash({'message':turn.message})!=binding['source_message_hash'] \
            or canonical_hash(receipt)!=binding['source_receipt_hash'] or not isinstance(receipt,dict) \
            or receipt.get('status')!='READY' or receipt.get('decision')!=binding['decision'] \
            or canonical_hash(offered)!=binding['offered_hash']:_deny()
    prepared=prepare_read_input_once(session,principal,params,
        source_key=(parent.id,parent.version,turn.id,epoch,digest))
    if canonical_hash(prepared)!=binding['input_hash']:_deny()
    validate_semantic_decision(deepcopy(receipt['decision']),prepared)
    if receipt['decision']['mode']=='RESUME':_validate_resume_genesis(session,principal,thread,binding)
    credit_ledger=[]
    rebuilt,_frame_source=_rebuild_actions(session,principal,thread,turn,receipt,offered,audit_only=True,project_fx_unit=_fx_money_unit_projection(binding),project_read_entity_scope=_read_entity_scope_projection(binding),project_credit_literal=_credit_literal_projection(binding),credit_literal_ledger=credit_ledger,guard_funding_source=_funding_source_polarity(binding))
    _validate_credit_literal_ledger(binding,credit_ledger)
    _validate_fx_currency_source_ledger(binding,receipt,offered,rebuilt,turn.message)
    _validate_read_entity_scope_ledger(binding,receipt,offered,rebuilt)
    _validate_read_clone_binding(session,principal,thread,parent,turn,receipt,offered,rebuilt,_frame_source,binding)
    expected=IntentProposal.model_validate(deepcopy(binding['expected_proposal']))
    actual=IntentProposal.model_validate(deepcopy(live.proposal_json))
    clean=lambda p:[{'kind':a.kind,'slots':a.slots.model_dump(exclude_none=True),'depends_on':a.depends_on} for a in p.actions]
    if clean(rebuilt)!=clean(expected) or clean(actual)!=clean(expected):_deny('SEMANTIC_NAVIGATION_PROPOSAL_CHANGED')
    if 'temporal_origins' in binding:
        from .temporal_fields import checked_temporal_origins
        checked_temporal_origins(session,principal,binding)
    return {'mode':'NAVIGATION_ONLY','proposal_json':expected.model_dump(),'source_turn_id':turn.id,
        'binding_hash':binding['binding_hash'],'write_authorized':False,'continuation_allowed':False,
        'is_execution_proof':False,'historical_source_not_fresh_bank_facts':True}


SELECTED_SOURCE_VERSION='semantic-selected-result-source-v1'


def _semantic_selected_record(session,principal,thread,turn,receipt,offered,ref):
    from .dialogue_references import validate_result_reference_catalog,result_reference_explanation_sources
    from .dialogue_reference_facts import SOURCE_ID,selected_source_id
    thread,scope,epoch=_valid_turn(session,principal,thread,turn)
    entry=(offered.get('entity_bindings') or {}).get(ref)
    if not isinstance(entry,dict):_deny('SEMANTIC_ENTITY_NOT_OFFERED')
    catalog=_entity_catalog(offered,entry);validate_result_reference_catalog(session,principal,thread,catalog)
    actual=entry.get('actual_entity')
    if not actual or actual not in catalog.get('entities',[]):_deny('SEMANTIC_ENTITY_CHANGED')
    resolution={'status':'RESOLVED','entity_ref':actual['entity_ref'],'fact_refs':deepcopy(actual['fact_refs']),
        'source_binding':{'catalog_hash':catalog['catalog_hash']}}
    rows=result_reference_explanation_sources(session,principal,thread,catalog,resolution)
    if len(rows)!=1:_deny()
    from .semantic_source_bundle import OFFERED_V2,BUNDLE_KEY,SELECTED_VERSION,bundle_reference,compact_catalog
    is_v2=offered.get('schema_version')==OFFERED_V2
    binding={'version':SELECTED_SOURCE_VERSION,'mode':'EXPLANATION','conversation_id':thread.id,
        'source_turn_id':turn.id,'source_message_hash':canonical_hash({'message':turn.message}),
        'actor_scope_hash':epoch,'semantic_receipt_hash':canonical_hash(receipt),
        'input_hash':receipt['input_hash'],'offered_hash':canonical_hash(offered),
        'selected_entity_ref':ref,'resolution':resolution,'bank_write_authorized':False}
    if is_v2:
        bundle=(turn.diagnostics_json or {}).get(BUNDLE_KEY)
        if not isinstance(bundle,dict) or bundle.get('offered_hash')!=canonical_hash(offered):_deny()
        binding.update(version=SELECTED_VERSION,bundle_ref=bundle_reference(bundle),catalog=compact_catalog(catalog,actual))
    else:
        # Preserve the original v1 layout as well as canonical hashes. Old
        # records are validated in place, never rewritten into the new form.
        binding={'version':SELECTED_SOURCE_VERSION,'mode':'EXPLANATION','conversation_id':thread.id,
            'source_turn_id':turn.id,'source_message_hash':canonical_hash({'message':turn.message}),
            'actor_scope_hash':epoch,'semantic_receipt_hash':canonical_hash(receipt),
            'input_hash':receipt['input_hash'],'offered_context':deepcopy(offered),'offered_hash':canonical_hash(offered),
            'selected_entity_ref':ref,'catalog':deepcopy(catalog),'resolution':resolution,'bank_write_authorized':False}
    if receipt['decision'].get('question_aspects'):
        binding['question_aspects']=list(receipt['decision']['question_aspects'])
    if receipt['decision'].get('rule_aspects'):
        binding['rule_aspects']=list(receipt['decision']['rule_aspects'])
    if receipt['decision'].get('query_reference'):
        binding['query_reference']=deepcopy(receipt['decision']['query_reference'])
    from .bill_category_consultation import CONTEXT_MARKER,CONTEXT_VERSION,source_context
    if (turn.diagnostics_json or {}).get(CONTEXT_MARKER)==CONTEXT_VERSION:
        category_context=source_context(catalog,actual)
        if category_context is not None:
            binding['bill_category_source_context']=category_context
    content=rows[0]['content']
    source_id=selected_source_id(binding) if len(receipt['decision'].get('entity_refs',[]))>1 else SOURCE_ID
    return {'source_id':source_id,'title':'当前选择的已核验资料','kind':'SCOPED_BUSINESS_FACTS',
        'reference':'conversation:'+thread.id,'version':'selected-dialogue-object-v1',
        'content':json.dumps(content,ensure_ascii=False,sort_keys=True),'content_hash':canonical_hash(content),'source_binding':binding}


def semantic_selected_object_sources(session,principal,thread,turn,result,offered_context):
    """Only a model-selected offered read object, without keyword resolution.

    Each selected object keeps its own source identity and live source proof.
    Material supplied by a user is never merged into authoritative bank facts.
    """
    receipt=_validated_result(result,offered_context,turn.message)
    decision=receipt['decision']
    refs=decision.get('entity_refs',[])
    if decision['mode']!='CONSULT' or not refs:return []
    if turn.diagnostics_json.get(RECEIPT_KEY)!=receipt:_deny()
    from .bill_category_consultation import CONTEXT_MARKER,CONTEXT_VERSION,source_context
    if set(decision.get('question_aspects',[])) & {'SUMMARY','CHARGE'}:
        for selected_ref in refs:
            selected_entry=(offered_context.get('entity_bindings') or {}).get(selected_ref) or {}
            selected_actual=selected_entry.get('actual_entity')
            if selected_actual and selected_actual.get('domain')=='BILLS' and selected_actual.get('field')=='category_filter' \
                    and source_context(_entity_catalog(offered_context,selected_entry),selected_actual) is not None:
                # New turn only. Old persisted sources have no marker and keep
                # their exact historical layout/hashes when replay-validated.
                turn.diagnostics_json={**(turn.diagnostics_json or {}),CONTEXT_MARKER:CONTEXT_VERSION}
                break
    from .semantic_source_bundle import OFFERED_V2,BUNDLE_KEY,capture_bundle
    if offered_context.get('schema_version')==OFFERED_V2 and BUNDLE_KEY not in (turn.diagnostics_json or {}):
        capture_bundle(session,principal,thread,turn,offered_context)
    ref=refs[0]
    entry=(offered_context.get('entity_bindings') or {}).get(ref)
    if isinstance(entry,dict) and entry.get('source_kind')=='USER_ANALYSIS_MATERIAL':
        if len(refs)!=1:_deny('SEMANTIC_MATERIAL_SOURCE_MIXED')
        # Risk consultations may inspect user wording, but not reinterpret it
        # as bank truth or financial authority. Keep this source type separate
        # from typed result catalogs and every monetary-fact formatter.
        if 'FRAUD' not in decision.get('knowledge_topics',[]):_deny('SEMANTIC_MATERIAL_ACTION_NOT_ALLOWED')
        thread,_scope,_epoch=_valid_turn(session,principal,thread,turn)
        from .semantic_materials import analysis_material_sources
        return analysis_material_sources(session,principal,thread,entry)
    if any((offered_context.get('entity_bindings',{}).get(item) or {}).get('source_kind')=='USER_ANALYSIS_MATERIAL' for item in refs):
        _deny('SEMANTIC_MATERIAL_SOURCE_MIXED')
    if any((offered_context.get('entity_bindings',{}).get(item) or {}).get('source_kind')=='COMPLETED_ASSET_RECEIPT_IDENTITY' for item in refs):
        _deny('SEMANTIC_ASSET_IDENTITY_REQUIRES_NEW_READ')
    if any((offered_context.get('entity_bindings',{}).get(item) or {}).get('source_kind')=='COMPLETED_CREDIT_RECEIPT_IDENTITY' for item in refs):
        _deny('SEMANTIC_CREDIT_IDENTITY_REQUIRES_NEW_READ')
    if any((offered_context.get('entity_bindings',{}).get(item) or {}).get('source_kind') in {'COMPLETED_MERCHANT_PAYMENT_IDENTITY','COMPLETED_CREDIT_INSTALLMENT_IDENTITY'} for item in refs):
        _deny('SEMANTIC_CREDIT_IDENTITY_REQUIRES_NEW_READ')
    if any((offered_context.get('entity_bindings',{}).get(item) or {}).get('source_kind') in {'COMPLETED_TRANSFER_RECEIPT_IDENTITY','COMPLETED_TRANSFER_RECEIPT_FACTS','COMPLETED_WEALTH_CARD_IDENTITY','COMPLETED_FX_CARD_IDENTITY'} for item in refs):
        _deny('SEMANTIC_TRANSFER_IDENTITY_REQUIRES_EXPLICIT_READ')
    return [_semantic_selected_record(session,principal,thread,turn,receipt,offered_context,item) for item in refs]


def selected_records_from_bundle(session,principal,thread,turn,selector=None):
    from .semantic_source_bundle import SELECTOR_VERSION,load_bundle
    selector=selector if selector is not None else (turn.diagnostics_json or {}).get('semantic_knowledge_selection')
    if not isinstance(selector,dict) or selector.get('version')!=SELECTOR_VERSION:_deny()
    offered,receipt=load_bundle(session,principal,thread,turn,selector.get('bundle_ref'))
    decision=receipt.get('decision') or {};refs=decision.get('entity_refs',[])
    if decision.get('mode')!='CONSULT' or not isinstance(refs,list) or not refs or len(refs)>40 or len(refs)!=len(set(refs)):_deny()
    material=(offered.get('entity_bindings') or {}).get(refs[0],{})
    if material.get('source_kind')=='USER_ANALYSIS_MATERIAL':
        if len(refs)!=1 or 'FRAUD' not in decision.get('knowledge_topics',[]):_deny('SEMANTIC_MATERIAL_SOURCE_MIXED')
        from .semantic_materials import analysis_material_sources
        selected=analysis_material_sources(session,principal,thread,material)
    else:
        selected=[_semantic_selected_record(session,principal,thread,turn,receipt,offered,ref) for ref in refs]
    if [{'source_id':row['source_id'],'source_hash':canonical_hash(row)} for row in selected]!=selector.get('selected_source_refs'):_deny()
    return selected


def semantic_selected_object_sources_current(session,principal,records):
    """Publication recheck for this version; never old keyword-based resolver."""
    from .semantic_router import prepare_semantic_input,validate_semantic_decision,semantic_input_hash
    from .semantic_materials import KIND,analysis_material_sources_current
    from .semantic_source_bundle import SELECTED_VERSION,load_bundle
    if not analysis_material_sources_current(session,principal,records):return False
    for row in records:
        if row.get('kind')==KIND:continue
        binding=row.get('source_binding') if isinstance(row,dict) else None
        if not isinstance(binding,dict) or not isinstance(binding.get('version'),str) \
                or binding['version'] not in {SELECTED_SOURCE_VERSION,SELECTED_VERSION}:
            if str(row.get('source_id','')).startswith('facts:selected-dialogue-object'):return False
            continue
        try:
            thread=session.get(ChatThread,binding['conversation_id']);turn=session.get(ChatTurn,binding['source_turn_id'])
            thread,scope,epoch=_valid_turn(session,principal,thread,turn)
            if binding['version']==SELECTED_VERSION:
                offered,receipt=load_bundle(session,principal,thread,turn,binding.get('bundle_ref'))
            else:
                receipt=turn.diagnostics_json.get(RECEIPT_KEY);offered=binding['offered_context']
            params={**offered['model'],'message':turn.message}
            if epoch!=binding['actor_scope_hash'] or canonical_hash(receipt)!=binding['semantic_receipt_hash'] \
                    or canonical_hash({'message':turn.message})!=binding['source_message_hash'] \
                    or canonical_hash(offered)!=binding['offered_hash'] or semantic_input_hash(**params)!=binding['input_hash'] \
                    or not isinstance(receipt,dict) or receipt.get('status')!='READY' or receipt['decision']['mode']!='CONSULT' \
                    or binding['selected_entity_ref'] not in receipt['decision'].get('entity_refs',[]):return False
            expected_refs=receipt['decision'].get('entity_refs',[])
            actual_refs=[other['source_binding'].get('selected_entity_ref') for other in records
                if isinstance(other,dict) and isinstance(other.get('source_binding'),dict)
                and other['source_binding'].get('source_turn_id')==turn.id
                and other['source_binding'].get('version') in {SELECTED_SOURCE_VERSION,SELECTED_VERSION}]
            if len(actual_refs)!=len(set(actual_refs)) or set(actual_refs)!=set(expected_refs):return False
            validate_semantic_decision(receipt['decision'],prepare_semantic_input(**params))
            expected=_semantic_selected_record(session,principal,thread,turn,receipt,offered,binding['selected_entity_ref'])
            if expected!=row:return False
        except (DomainError,ValueError,TypeError,KeyError,AttributeError):return False
    return True
