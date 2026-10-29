import {useEndpoint} from '../useEndpoint';
type Row={username:string;label:string;month:string;private_reason:string|null;has_billed_outstanding:boolean;source_review_required:boolean;private_sources:{currency:string;remaining_due_minor:number;statement_id:string}[]};
export function AccountRiskLabels({token}:{token:string}){
 const data=useEndpoint<{description:string;accounts:Row[];truncated:boolean}>('/api/v2/admin/credit-billing-status',token);
 return <section className="panel account-risk-panel"><style>{`.account-risk-panel>p{margin:16px 24px;color:#55717b;line-height:1.6}.account-risk-panel table{width:calc(100% - 48px);margin:16px 24px 24px;border-collapse:collapse}.account-risk-panel th,.account-risk-panel td{padding:16px 20px;text-align:left;border-bottom:1px solid #dce7e9}.account-risk-panel th{background:#f1f7f7;color:#506d78;font-weight:600}.account-risk-panel tbody td:nth-child(2){color:#956117;font-weight:600}.account-risk-panel{margin-bottom:24px}`}</style><div className="panel-heading"><h2>信用卡账单状态</h2><button className="text-button" onClick={data.refresh}>刷新账单状态</button></div>
 <p>显示已出账款项的待还情况，未出账消费不计入。</p>
 {data.error?<p role="alert">账单状态暂不可读取</p>:!data.data?<p>正在读取账单状态…</p>:<><p>已检查{data.data.accounts.length}个账户，有待还款项{data.data.accounts.filter(r=>r.has_billed_outstanding).length}个。</p><table><thead><tr><th>账号</th><th>账单状态</th><th>业务月</th><th>内部依据（仅后台）</th></tr></thead><tbody>{data.data.accounts.filter(r=>r.has_billed_outstanding||r.source_review_required).map(r=><tr key={r.username}><td>{r.username}</td><td>{r.label}</td><td>{r.month}</td><td>{r.private_sources.map(s=>`${s.currency} 已出账未结清 ${(s.remaining_due_minor/100).toFixed(2)}`).join('；')||'来源需核验'}</td></tr>)}</tbody></table></>}
 {data.data?.truncated&&<p>当前仅列出前100个有出账记录的账户。</p>}
 <p>这些记录仅供授权账务人员处理信用卡服务，不用于判断收款账户风险，也不向其他付款人展示。</p></section>;
}
