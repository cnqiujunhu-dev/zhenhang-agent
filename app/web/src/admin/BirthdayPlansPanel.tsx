import {useState} from "react";
import {useEndpoint} from "../useEndpoint";
import "./birthdayPlans.css";

type Job={job_id:string;kind:string;item_index:number|null;plan_version:number;status:string;scheduled_at:string;completed_at:string|null;attempts:number;depends_on:string[];error_code:string|null};
type Receipt={payment_id?:string;amount_minor?:number;cash_debit_minor?:number;ledger_balanced?:boolean;status?:string;instrument_type?:string};
type Order={order_id:string;merchant_name?:string;product_name?:string;amount_minor:number;status:string;payment?:Receipt;status_source?:string};
type Occurrence={occurrence_id:string;birthday:string;status:string;jobs:Job[];orders:Order[];unused_fund_release:{recorded:boolean;status:string;released_unused_minor:number|null;journal_id:string|null;recorded_at:string|null}};
type Plan={plan_id:string;name:string;username:string;display_name:string;version:number;status:string;budget_minor:number;reserved_minor:number;spent_minor:number;refunded_minor:number;net_cash_debit_minor:number;
 scope:{birthday?:string;reserve_at?:string;reserve_policy?:string;order_at?:string;delivery_start?:string;delivery_end?:string;expires_at?:string;instrument?:{card_number?:string;display_last4?:string};recipient?:{name?:string;phone?:string;address?:string};items?:{merchant_name:string;product_name:string;quantity:number;amount_minor:number}[]};
 authorization:{recorded:boolean;status:string;task_id:string|null;authorized_at:string|null;expires_at:string|null};occurrences:Occurrence[]};
type Result={schema_version:string;read_only:boolean;plans:Plan[];truncated:boolean};
const money=(v:number|null|undefined)=>typeof v==="number"?`¥${(v/100).toFixed(2)}`:"尚未记录";
const time=(v:string|null|undefined)=>v?new Date(v).toLocaleString("zh-CN",{hour12:false,timeZone:"Asia/Shanghai"}):"尚未记录";
const labels:Record<string,string>={DRAFT:"待本人授权",ACTIVE:"执行中",SUCCEEDED:"已完成",COMPLETED:"已完成",QUEUED:"等待约定时间",PAID:"已付款",FULFILLED:"已履约",FAILED:"已停止",PAUSED_RECONFIRM:"等待重新确认",NOT_AUTHORIZED:"尚未授权",NOT_RECORDED:"尚未记录",CANCELLED:"已取消",BLOCKED_RECONFIRM:"等待重新确认",REVOKED:"已撤销",EXPIRED:"已到期"};
const state=(s:string)=>labels[s]||s;
function jobName(j:Job,p:Plan){const item=j.item_index===null?undefined:p.scope.items?.[j.item_index];return j.kind==="RESERVE"?"预留本次预算":j.kind==="ORDER"?`${item?.product_name||`商品${(j.item_index??0)+1}`}下单`:j.kind==="FULFIL"?`${item?.product_name||`商品${(j.item_index??0)+1}`}履约`:j.kind==="EXPIRE"?"授权到期核对":j.kind==="REFUND"?"原路退款":j.kind;}
function Status({value}:{value:string}){return <span className={`birthday-state ${value==="SUCCEEDED"||value==="FULFILLED"||value==="COMPLETED"?"done":""}`}>{state(value)}</span>;}
function birthdayTitle(p:Plan){const recipient=p.scope.recipient?.name?.trim();return recipient?`${recipient}的生日安排`:p.scope.birthday?`${p.scope.birthday}的生日安排`:"生日安排";}

export function BirthdayPlansPanel({token}:{token:string}){
 const [draft,setDraft]=useState("");const [query,setQuery]=useState("");
 const key=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(query)?"plan_id":"username";
 const endpoint=useEndpoint<Result>(query?`/api/v2/admin/life-plans?${key}=${encodeURIComponent(query)}`:null,token);
 return <section className="birthday-reader">
  <div className="birthday-filter"><label>客户账号或计划ID<input value={draft} onChange={e=>setDraft(e.target.value)} placeholder="输入客户账号或计划ID"/></label><button className="button primary" onClick={()=>setQuery(draft.trim())}>查看计划</button><button className="button secondary" disabled={!query} onClick={()=>endpoint.refresh()}>刷新状态</button></div>
  {!query&&<p>按客户账号查看生日计划的实际授权、资金、调度与商户回执。</p>}
  {endpoint.loading&&<p>正在读取已保存的计划…</p>}{endpoint.error&&<p role="alert">{endpoint.error}</p>}
  {endpoint.data?.plans.length===0&&<p>该账号暂无生日计划。</p>}
  {endpoint.data?.plans.map(p=><article className="birthday-detail" key={p.plan_id}>
   <header><span className="birthday-eyebrow">生日计划执行 · 只读观察</span><h2>{birthdayTitle(p)}</h2><p>{p.display_name||"客户"} · 计划第{p.version}版 <Status value={p.status}/></p></header>
   <div className="birthday-money">{[["本次预算",p.budget_minor],["尚在预留",p.reserved_minor],["已付款",p.spent_minor],["已原路退款",p.refunded_minor],["实付净额",p.net_cash_debit_minor]].map(([label,v])=><div key={String(label)}><span>{label}</span><strong>{money(Number(v))}</strong></div>)}</div>
   <section><h3>本次授权范围</h3><dl><dt>生日</dt><dd>{p.scope.birthday}</dd><dt>付款卡</dt><dd>{p.scope.instrument?.card_number||p.scope.instrument?.display_last4}</dd><dt>预留安排</dt><dd>{p.scope.reserve_policy==="ON_CONFIRMATION"?"本人确认后立即预留":time(p.scope.reserve_at)}</dd><dt>约定下单</dt><dd>{time(p.scope.order_at)}</dd><dt>配送／服务窗口</dt><dd>{time(p.scope.delivery_start)} — {time(p.scope.delivery_end)}</dd><dt>授权截止</dt><dd>{time(p.authorization.expires_at||p.scope.expires_at)}</dd><dt>收件人</dt><dd>{p.scope.recipient?.name} · {p.scope.recipient?.phone}</dd><dt>地址</dt><dd>{p.scope.recipient?.address}</dd></dl><p>仅本次授权；商品、价格、付款卡或地址变化时重新核对。</p>
    <div className="birthday-products">{p.scope.items?.map((x,i)=><div key={i}><strong>{x.product_name} × {x.quantity}</strong><span>{x.merchant_name} · {money(x.amount_minor)}</span></div>)}</div>
   </section>
   <section><h3>生命周期依赖与执行记录</h3><div className="birthday-phase"><div><strong>本人授权</strong><p>所有资金与订单动作的确认闸门</p><small>授权时间 {time(p.authorization.authorized_at)}</small></div><Status value={p.authorization.status}/></div>
    {p.occurrences.map(o=><div key={o.occurrence_id}>
     {o.jobs.filter(j=>j.kind!=="EXPIRE").map(j=><div className="birthday-phase" key={j.job_id}><div><strong>{jobName(j,p)}</strong><p>依赖：{j.depends_on.map(id=>id==="authorization"?"本人授权":jobName(o.jobs.find(x=>x.job_id===id)||j,p)).join("、")||"原始授权范围"}</p><small>约定执行 {time(j.scheduled_at)}<br/>实际完成 {time(j.completed_at)} · 已尝试 {j.attempts} 次</small>{j.error_code&&<p>停止原因：{j.error_code}</p>}</div><Status value={j.status}/></div>)}
     <div className="birthday-phase"><div><strong>释放未使用预留</strong><p>依赖两项履约完成；在最后一次履约事务内完成，非独立Job。</p><small>{o.unused_fund_release.recorded?`记录时间 ${time(o.unused_fund_release.recorded_at)} · 释放结余 ${money(o.unused_fund_release.released_unused_minor)}`:"尚未记录结余释放"}</small></div><Status value={o.unused_fund_release.status}/></div>
     <p>下单按商品顺序执行，后一项需前一项下单成功；各项履约分别依赖自己的订单。</p>
     <section><h3>商户订单与付款回执</h3>{o.orders.length===0?<p>尚无已付款商户订单。</p>:o.orders.map(order=><div className="birthday-order" key={order.order_id}><h4>{order.product_name} <Status value={order.status}/></h4><p>{order.merchant_name} · 订单金额 <strong>{money(order.amount_minor)}</strong></p><dl><dt>订单号</dt><dd>{order.order_id}</dd><dt>付款回执</dt><dd>{order.payment?.payment_id||"尚未记录"}</dd><dt>现金扣款</dt><dd>{money(order.payment?.cash_debit_minor)}</dd><dt>分录核验</dt><dd>{order.payment?.ledger_balanced===true?"借贷分录平衡":"尚未核验"}</dd></dl></div>)}</section>
     <section><h3>到期边界核对</h3>{o.jobs.filter(j=>j.kind==="EXPIRE").map(j=><p key={j.job_id}>{time(j.scheduled_at)} · {state(j.status)}{j.completed_at?` · 完成于${time(j.completed_at)}`:""}</p>)}<p>商户履约状态来自本地商户登记与订单台账。</p></section>
    </div>)}
   </section><details><summary>原始标识</summary><dl><dt>原始计划标题</dt><dd>{p.name}</dd><dt>登录账号</dt><dd>{p.username}</dd><dt>计划ID</dt><dd>{p.plan_id}</dd></dl></details><footer>记录来自真实计划、授权、作业、订单与支付台账。本页只读；授权和办理仍由客户确认。</footer>
  </article>)}{endpoint.data?.truncated&&<p>仅显示最新20份计划，请进一步核对具体客户。</p>}
 </section>;
}
