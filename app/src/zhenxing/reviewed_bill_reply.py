"""Fresh owner-checked image/bank comparison, supplemental to a verified READ.

The bank query remains independently readable if material access expires. This
does not alter the bill result, plan, confirmation or any banking record.
"""
from .contracts import DomainError
from .models import utcnow
from .planning import canonical_hash

VERSION='reviewed-bill-comparison-v1'
STATUSES={'NO_MATCH','ONE_CANDIDATE','MULTIPLE_CANDIDATES','INSUFFICIENT_FIELDS','CONFLICT'}


def attach_reviewed_bill_comparison(session,principal,parent,trace,*,source_turn_id=None,conversation_id=None):
    if trace.get('state')!='SUCCEEDED' or (trace.get('outcome') or {}).get('verified') is not True \
            or (trace.get('outcome') or {}).get('kind')!='QUERY_RESULT' or trace.get('pending_confirmation') \
            or not any(node.get('action')=='BILL_ANALYSIS' and node.get('status')=='SUCCEEDED' for node in trace.get('nodes',[]) if isinstance(node,dict)):
        return trace
    from .conversation_models import ChatThread,ChatTurn
    from .runtime_models import RuntimeRecord
    record=session.get(RuntimeRecord,parent.id)
    context=(record.metadata_json or {}).get('conversation_context') if record else None
    historical=source_turn_id is not None
    if not historical:
        if not isinstance(context,dict):return trace
        source_turn_id=context.get('turn_id');conversation_id=context.get('conversation_id')
        binding=(record.metadata_json or {}).get('cloud_semantic_intent')
        if binding is not None and (not isinstance(binding,dict) or binding.get('source_turn_id')!=source_turn_id
                or binding.get('conversation_id')!=conversation_id):
            return trace
    turn=session.get(ChatTurn,source_turn_id) if source_turn_id else None
    thread=session.get(ChatThread,conversation_id) if conversation_id else None
    owner=(principal.user_id,principal.sandbox_id)
    if not turn or not thread or (turn.user_id,turn.sandbox_id)!=owner or (thread.user_id,thread.sandbox_id)!=owner \
            or (parent.user_id,parent.sandbox_id)!=owner or turn.task_id!=parent.id or turn.thread_id!=thread.id \
            or turn.status!='ANSWERED' or turn.mode not in {'NEW_TASK','REVISE','CLARIFY'} or turn.error_code \
            or thread.status!='ACTIVE' or not historical and context.get('original_user_message')!=turn.message:
        return trace
    refs=turn.reviewed_evidence_refs_json
    if not refs:return trace
    value={'schema_version':VERSION,'source_turn_id':turn.id,'source_conversation_id':thread.id,
        'review_refs_hash':canonical_hash(refs),'status':'READY','read_only':True,'bank_data_changed':False,
        'screenshot_proves_posting':False,'items':[],'observed_at':utcnow().isoformat()}
    from .attachments import attachment_evidence_for_conversation,billing_comparison
    from .dialogue_state import dialogue_actor_scope
    from .permissions import require_banking_actor
    try:
        role=require_banking_actor(session,principal)
        if role=='FAMILY_HELPER':raise DomainError('ATTACHMENT_ROLE_UNAVAILABLE','图片范围不可访问',403)
        epoch=(turn.diagnostics_json or {}).get('dialogue_actor_scope_hash')
        if epoch and epoch!=dialogue_actor_scope(session,principal)[1]:
            raise DomainError('ATTACHMENT_ACTOR_CHANGED','图片范围已变化',403)
        if not isinstance(refs,list) or not 1<=len(refs)<=8:raise DomainError('ATTACHMENT_REFS_INVALID','材料引用无效',409)
        for ordinal,ref in enumerate(refs,1):
            if not isinstance(ref,dict) or set(ref)!={'review_id','attachment_version','sha256'}:
                raise DomainError('ATTACHMENT_REFS_INVALID','材料引用无效',409)
            evidence=attachment_evidence_for_conversation(session,principal,ref['review_id'],ref['attachment_version'],ref['sha256'])
            if evidence.get('purpose')!='BILLING':continue
            compared=billing_comparison(session,principal,evidence['fields'])
            if compared.get('status') not in STATUSES or compared.get('bank_data_changed') is not False or compared.get('screenshot_proves_posting') is not False:
                raise DomainError('ATTACHMENT_COMPARISON_INVALID','材料对照结果需要核对',409)
            value['items'].append({'ordinal':ordinal,'status':compared['status'],
                'reviewed_fields':{key:evidence['fields'][key] for key in ('amount_minor','currency','date','merchant') if evidence['fields'].get(key) is not None},
                'review_evidence_hash':evidence['evidence_hash'],'comparison_hash':canonical_hash(compared),
                'missing_fields':[key for key in ('amount_minor','currency','date','merchant') if evidence['fields'].get(key) is None or evidence['fields'].get(key)=='']})
    except (DomainError,KeyError,TypeError,ValueError):
        value.update(status='UNAVAILABLE',items=[])
    if value['status']=='READY' and not value['items']:return trace
    return {**trace,'reviewed_bill_comparison':value}


def comparison_reply(trace,language='zh-CN'):
    """Finite display projection from the runtime-owned supplement only."""
    value=trace.get('reviewed_bill_comparison')
    if not isinstance(value,dict) or value.get('schema_version')!=VERSION or value.get('status') not in {'READY','UNAVAILABLE'} \
            or value.get('read_only') is not True or value.get('bank_data_changed') is not False or value.get('screenshot_proves_posting') is not False \
            or trace.get('state')!='SUCCEEDED' or (trace.get('outcome') or {}).get('verified') is not True \
            or (trace.get('outcome') or {}).get('kind')!='QUERY_RESULT' or trace.get('pending_confirmation'):
        return None
    english=language=='en'
    if value['status']=='UNAVAILABLE':
        text=('The image review is no longer available. Upload and review it again so I can compare it; your separate bank query remains available.' if english else
            '这份图片核对记录已失效或暂不可访问，重新上传并核对后，我再帮你对账；已有银行查询结果仍可查看。')
    else:
        items=value.get('items')
        if not isinstance(items,list) or not 1<=len(items)<=8 or any(not isinstance(item,dict) or item.get('status') not in STATUSES or type(item.get('ordinal')) is not int or not 1<=item['ordinal']<=8 for item in items):return None
        labels={
            'NO_MATCH':('没有找到与这张收据相匹配的银行记录。','No bank record matching this receipt was found.'),
            'ONE_CANDIDATE':('找到一条可能匹配的银行记录，还需要核对原交易回执。','One possible matching bank record was found. Check the original transaction receipt.'),
            'MULTIPLE_CANDIDATES':('找到多条可能匹配的银行记录，还不能确定是哪一笔，需要进一步核对。','Several possible bank matches were found, so I cannot identify a unique transaction yet.'),
            'INSUFFICIENT_FIELDS':('已核对的图片信息还不足以匹配银行记录，请补全图片中的金额、币种、日期或商户后再核对。','The reviewed image details are not sufficient to match a bank record. Check the amount, currency, date and merchant.'),
            'CONFLICT':('图片信息与银行记录有差异，暂时不能认定是同一笔交易，请进一步核对。','The image details differ from the bank records, so they cannot yet be treated as the same transaction.'),
        }
        lines=[]
        field_labels={'amount_minor':('金额','amount'),'currency':('币种','currency'),'date':('日期','date'),'merchant':('商户名称','merchant')}
        for item in items:
            explanation=labels[item['status']][1 if english else 0]
            missing=item.get('missing_fields',[])
            if item['status']=='INSUFFICIENT_FIELDS' and isinstance(missing,list) and missing and all(key in field_labels for key in missing):
                needed=(', ' if english else '、').join(field_labels[key][1 if english else 0] for key in missing)
                explanation=(f'Please check the {needed} in the image so I can compare it with bank records.' if english else f'还需要你核对图片中的{needed}，才能继续匹配银行记录。')
            lines.append(explanation if len(items)==1 else (f"Image {item['ordinal']}: " if english else f"第{item['ordinal']}张图片：")+explanation)
        same_missing=all(item.get('missing_fields',[])==items[0].get('missing_fields',[]) for item in items)
        if len(items)>1 and len({item['status'] for item in items})==1 and (items[0]['status']!='INSUFFICIENT_FIELDS' or same_missing):
            count=len(items)
            grouped={
                'NO_MATCH':(f'这{count}张收据暂未找到匹配的银行记录。',f'No matching bank record was found for these {count} receipts.'),
                'ONE_CANDIDATE':(f'这{count}张收据各有一条可能匹配的银行记录，还需要分别核对原交易回执。',f'Each of these {count} receipts has a possible bank match. Check the original transaction receipts.'),
                'MULTIPLE_CANDIDATES':(f'这{count}张收据都存在多条可能匹配的银行记录，暂时不能确定各自对应哪一笔。',f'Each of these {count} receipts has several possible bank matches; no unique match has been established.'),
                'CONFLICT':(f'这{count}张收据的信息与银行记录都有差异，需要进一步核对。',f'All {count} receipts differ from the available bank records and need further checking.'),
                'INSUFFICIENT_FIELDS':(f'这{count}张收据还缺少可核对的信息。{explanation}',f'These {count} receipts need more reviewed information. {explanation}'),
            }
            text=grouped[items[0]['status']][1 if english else 0]
        else:
            text='\n'.join(lines)
        details=[]
        for item in items:
            fields=item.get('reviewed_fields') or {};parts=[]
            if isinstance(fields.get('merchant'),str):parts.append(('Merchant: ' if english else '商户：')+fields['merchant'])
            if isinstance(fields.get('date'),str):parts.append(('Date: ' if english else '日期：')+fields['date'])
            if type(fields.get('amount_minor')) is int and fields.get('currency') in {'CNY','USD'}:
                from decimal import Decimal
                parts.append(('Amount: ' if english else '金额：')+f"{Decimal(fields['amount_minor'])/100:.2f} {fields['currency']}")
            if parts:details.append((f"Image {item['ordinal']}: " if english else f"第{item['ordinal']}张票据：")+'；'.join(parts))
        text=('Compared with currently accessible bank records: ' if english else '按当前可查的银行记录核对，')+text
        if details:text='\n'.join(details)+'\n'+text
        text+=('\nImages alone are not proof of bank settlement.' if english else '\n图片本身不能作为银行到账证明。')
    return {'schema_version':'customer-reviewed-bill-reply-v1','text':text,'status':value['status'],
        'read_only':True,'bank_data_changed':False,'screenshot_proves_posting':False}
