import {useState, type ReactNode} from 'react';
import {useCustomerLanguage} from './CustomerLanguageProvider';
import {useEndpoint} from './useEndpoint';
import {CardTile, dateText, money, Icon, SpendingChart} from './components';
import {PersonalServiceCards} from './PersonalServiceCards';
import {ProfileCreditCards} from './ProfileCreditCards';
import {OwnTransferHistoryPanel} from './OwnTransferHistoryPanel';
import {assertFinancialResource} from './financialResource';
import type {DemoSession, Card, BillAnalysis, JsonValue} from './types';
import './customerDataHome.css';

type Data=Record<string,unknown>;
const obj=(v:unknown):Data=>v&&typeof v==='object'&&!Array.isArray(v)?v as Data:{};
const rows=(v:unknown):Data[]=>Array.isArray(v)?v.filter(x=>x&&typeof x==='object'&&!Array.isArray(x)) as Data[]:[];
const txt=(v:unknown,other='—')=>typeof v==='string'&&v.trim()?v:other;
const cash=(v:unknown,currency:unknown='CNY')=>typeof v==='number'&&Number.isSafeInteger(v)?money(v,{currency:currency==='USD'?'USD':'CNY'}):'—';
const time=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v))?dateText(v,true):'—';
const statuses:Record<string,string>={ACTIVE:'正常',PENDING:'待付',PAYMENT_PENDING:'待确认',PAID:'已付',POSTED:'已转出',
  COMPLETED:'已完成',PARTIAL:'部分完成',WAITING:'待执行',FAILED:'未成功',CANCELLED:'已取消',EXPIRED:'已到期',DECLINED:'已拒绝',LOCKED:'已锁定',LOST:'已挂失'};
const status=(v:unknown)=>statuses[String(v)]||'待核对';

/** A mounted section issues owned GET reads only; it has no task/write callbacks. */
function ReadSection({path,token,title,children}:{path:string;token:string;title:string;children:(data:unknown)=>ReactNode}) {
  const read=useEndpoint<unknown>(path,token);
  const en=useCustomerLanguage().locale==='en';
  return <section className="data-home-section"><header><h3>{title}</h3><button type="button" className="icon-button" aria-label={`${en?'Refresh':'刷新'}${title}`}
    disabled={read.loading} onClick={()=>read.refresh()}><Icon name="refresh" size={16}/></button></header>
    {read.loading?<p role="status">{en?'Loading your data…':'正在读取资料…'}</p>:read.error?<p role="alert">{read.error}</p>:read.data!==null?children(read.data):<p>{en?'No data loaded.':'暂未读取资料。'}</p>}
  </section>;
}
function SummaryCards({items}:{items:{name:string;value:string}[]}) {return <dl className="data-home-metrics">{items.map(x=><div key={x.name}><dt>{x.name}</dt><dd>{x.value}</dd></div>)}</dl>}
function Empty({children}:{children:ReactNode}){return <p className="data-home-empty">{children}</p>}
function PersonalRead({path,action,token,title}:{path:string;action:string;token:string;title:string}){
  return <ReadSection path={path} token={token} title={title}>{data=><PersonalServiceCards node={{node_id:`profile:${action}`,action,result:data as Record<string,JsonValue>}}/>}</ReadSection>;
}
export function CustomerDataHome({token,session,role,onBack}:{token:string;session:DemoSession|null;role:string;onBack:()=>void}) {
  const en=useCustomerLanguage().locale==='en';
  const tabs=[['cards','账户与卡片','Accounts & cards'],['assets','资产持仓','Holdings'],['bills','账单','Statements'],
    ['subscriptions','订阅服务','Subscriptions'],['transfers','转账与AA','Transfers & AA'],['family','家庭生日','Family'],['preferences','偏好与权益','Preferences & offers']];
  const [tab,setTab]=useState('cards');
  const personal=['CUSTOMER','DEV_TEST','DEMO_OWNER'].includes(role);
  return <section className="customer-data-home" aria-label={en?'Personal data homepage':'个人数据主页'}>
    <header className="data-home-heading"><div><p>{en?'User profile':'用户资料'}</p><h2>{session?.display_name||session?.username|| (en?'My data':'我的资料')}</h2>
      {session?.username&&<span>@{session.username}</span>}</div><button type="button" className="button secondary compact" onClick={onBack}>{en?'Back to conversation':'返回对话'}<Icon name="arrow" size={15}/></button></header>
    {!personal?<Empty>{en?'This identity has no personal account data.':'当前身份没有本人账户资料。'}</Empty>:<>
    <nav className="data-home-tabs" aria-label={en?'Profile sections':'资料分类'}>{tabs.map(([id,zh,english])=><button type="button" key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{en?english:zh}</button>)}</nav>
    <div key={tab} className="data-home-body">
    {tab==='cards'&&<>
      <ReadSection path="/api/v1/cards" token={token} title={en?'Debit cards':'借记卡'}>{data=>{
        let cards:Card[];try{cards=assertFinancialResource('cards',data) as Card[]}catch{return <Empty>{en?'Card data needs checking.':'卡片资料暂时无法核对。'}</Empty>}
        return cards.length?<div className="data-home-card-grid">{cards.map(card=><div key={card.card_id}><CardTile card={card}/>
          <SummaryCards items={[{name:en?'CNY balance':'人民币余额',value:cash(card.balances_by_currency?.CNY)},
            ...(card.supported_currencies?.includes('USD')?[{name:en?'USD balance':'美元余额',value:cash(card.balances_by_currency?.USD,'USD')}]:[])]}/></div>)}</div>:<Empty>{en?'No debit cards.':'暂无借记卡。'}</Empty>;
      }}</ReadSection>
      <ReadSection path="/api/v2/credit/cards" token={token} title={en?'Credit cards':'信用卡'}>{data=><ProfileCreditCards data={data}/>}</ReadSection>
      <PersonalRead path="/api/v2/profile/membership" action="MEMBERSHIP_VIEW" token={token} title={en?'Customer profile':'客户资料'}/>
    </>}
    {tab==='assets'&&<PersonalRead path="/api/v2/assets/positions" action="ASSET_POSITIONS_READ" token={token} title={en?'My assets':'我的资产'}/>}
    {tab==='bills'&&<ReadSection path="/api/v1/bills/summary" token={token} title={en?'Recorded statement overview':'已有账单概览'}>{data=>{
      let bill:BillAnalysis;try{bill=assertFinancialResource('bill',data) as BillAnalysis}catch{return <Empty>{en?'Statement data needs checking.':'账单资料暂时无法核对。'}</Empty>}
      return <><p>{bill.period_scope?.label||`${time(bill.period_start)} — ${time(bill.period_end)}`}</p>
        <SummaryCards items={[{name:en?'Recorded spending (CNY)':'已记录支出（人民币）',value:cash(bill.spending_minor)},
          {name:en?'Refunds (CNY)':'退款（人民币）',value:cash(bill.refund_minor)},{name:en?'Transactions':'交易笔数',value:String(bill.transaction_count)}]}/>
        <SpendingChart analysis={bill}/>{bill.coverage?.status!=='COMPLETE'&&<p>{en?'Totals cover the available records.':'以上为当前已有账单记录。'}</p>}</>;
    }}</ReadSection>}
    {tab==='subscriptions'&&<ReadSection path="/api/v1/subscriptions" token={token} title={en?'My subscriptions':'我的订阅'}>{data=><div className="data-home-record-grid">{rows(data).map((row,i)=><article key={i}><h4>{txt(row.merchant)}</h4>
      <SummaryCards items={[{name:en?'Fee per period':'每期费用',value:cash(row.amount_minor,row.currency)},{name:en?'Valid until':'权益有效至',value:time(row.entitlement_end_at)},
        {name:en?'Auto-renew':'自动续费',value:row.auto_renew===true?(en?'On':'开启'):row.auto_renew===false?(en?'Off':'关闭'):'—'}]}/></article>)}{!rows(data).length&&<Empty>{en?'No subscriptions.':'暂无订阅。'}</Empty>}</div>}</ReadSection>}
    {tab==='transfers'&&<>
      <section className="data-home-section"><OwnTransferHistoryPanel token={token} revision={0}/></section>
      <ReadSection path="/api/v2/transfers/schedules" token={token} title={en?'Scheduled transfers':'定时转账'}>{data=><div className="data-home-record-grid">{rows(data).map((row,i)=><article key={i}><h4>{txt(row.payee_name)}<span>{status(row.status)}</span></h4>
        <SummaryCards items={[{name:en?'Amount':'金额',value:cash(row.amount_minor)},{name:en?'Scheduled time':'约定时间',value:time(row.due_at)}]}/></article>)}{!rows(data).length&&<Empty>{en?'No scheduled transfers.':'暂无定时转账安排。'}</Empty>}</div>}</ReadSection>
      <ReadSection path="/api/v2/transfers/aa/invitations" token={token} title={en?'My AA shares':'我的AA份额'}>{data=><div className="data-home-record-grid">{rows(obj(data).invitations).map((row,i)=><article key={i}><h4>{txt(row.memo,en?'AA payment':'AA收款')}<span>{status(row.status)}</span></h4>
        <SummaryCards items={[{name:en?'Requested by':'发起人',value:txt(row.collector_display_name)},{name:en?'My share':'本人份额',value:cash(row.amount_minor)},
          {name:en?'Deadline':'截止时间',value:time(row.expires_at)}]}/></article>)}{!rows(obj(data).invitations).length&&<Empty>{en?'No AA shares.':'暂无AA份额。'}</Empty>}</div>}</ReadSection>
      <ReadSection path="/api/v2/transfers/aa" token={token} title={en?'My AA collections':'我发起的AA'}>{data=><div className="data-home-record-grid">{rows(data).map((row,i)=><article key={i}><h4>{txt(row.memo,en?'AA collection':'AA收款')}<span>{status(row.status)}</span></h4>
        <SummaryCards items={[{name:en?'Total':'总额',value:cash(row.total_minor)},{name:en?'Received':'已收',value:cash(row.received_minor)}]}/>
        <ul>{rows(row.participants).map((p,j)=><li key={j}>{txt(p.name)} · {cash(p.amount_minor)} · {status(p.status)}</li>)}</ul></article>)}{!rows(data).length&&<Empty>{en?'No collections.':'暂无收款记录。'}</Empty>}</div>}</ReadSection>
    </>}
    {tab==='family'&&<ReadSection path="/api/v2/profile/family" token={token} title={en?'Family and birthday plans':'家人与生日安排'}>{data=>{
      const family=rows(obj(obj(data).members).members),occasions=rows(obj(obj(data).occasions).occasions),plans=rows(obj(obj(data).plans).plans);
      return <><div className="data-home-record-grid">{family.map((member,i)=>{const birthday=occasions.find(o=>o.family_member_id===member.family_member_id);return <article key={i}><h4>{txt(member.family_member_name)}</h4>
        <p>{txt(member.family_relationship,'')}</p><p>{birthday?`${en?'Birthday':'生日'}：${birthday.month}${en?'/':'月'}${birthday.day}${en?'':'日'}`:en?'Birthday not recorded':'生日未登记'}</p></article>})}</div>
        {!family.length&&<Empty>{en?'No family profiles.':'暂无家庭成员资料。'}</Empty>}
        <h4>{en?'Birthday plans':'生日计划'}</h4>{plans.length?plans.map((plan,i)=><article key={i} className="data-home-plan"><h4>{txt(plan.name)}<span>{status(plan.status)}</span></h4>
          <SummaryCards items={[{name:en?'Budget':'预算',value:cash(plan.budget_minor)},{name:en?'Reserved':'预留',value:cash(plan.reserved_minor)},{name:en?'Paid':'已付款',value:cash(plan.spent_minor)}]}/></article>):<Empty>{en?'No birthday plans.':'暂无生日安排。'}</Empty>}</>;
    }}</ReadSection>}
    {tab==='preferences'&&<><PersonalRead path="/api/v2/preferences" action="PREFERENCE_VIEW" token={token} title={en?'Saved preferences':'已记住的偏好'}/>
      <PersonalRead path="/api/v2/benefits" action="BENEFITS_VIEW" token={token} title={en?'Available offers':'可用权益'}/></>}
    </div></>}
  </section>;
}
