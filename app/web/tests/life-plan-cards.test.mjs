import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
const server=await createServer({root:fileURLToPath(new URL("../",import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:"automatic"}});
after(()=>server.close());
const {LifePlanCards,FamilyOccasionCards}=await server.ssrLoadModule("/src/LifePlanCards.tsx");
const {TaskOutcomeCard}=await server.ssrLoadModule("/src/TaskOutcomeCard.tsx");
const {CustomerLanguageScope}=await server.ssrLoadModule("/src/CustomerLanguageProvider.tsx");
const render=plans=>renderToStaticMarkup(React.createElement(LifePlanCards,{result:{plans}}));
const base={plan_id:"PRIVATE_PLAN_ID",scope_hash:"PRIVATE_SCOPE_HASH",name:"生日计划",status:"ACTIVE",version:1,
  budget_minor:100000,reserved_minor:100000,spent_minor:0,scope:{birthday:"2026-11-20",items:[{merchant_name:"花店",product_name:"鲜花",quantity:1,amount_minor:39900}],
  recipient:{name:"成年子女",address:"已确认地址"}},occurrences:[]};
test("bounded authorization does not claim a locked card or a completed order",()=>{
  const html=render([base]); assert.match(html,/已授权/);assert.match(html,/¥1000\.00/);
  assert.doesNotMatch(html,/预留不等于锁卡|越界暂停|历史授权|PRIVATE_PLAN_ID|PRIVATE_SCOPE_HASH|已送达|<details[^>]* open/);
});

test("new reservation result states the verified old/new/delta without exposing private handoff IDs",()=>{
  const change={version:"life-reservation-adjustment-v1",previous_reserved_minor:100000,new_reserved_minor:120000,
    additional_reserved_minor:20000,released_difference_minor:0,currency:"CNY",schedule_preserved:true,synchronous_at_confirmation:true,cash_debit_minor:0};
  const value={plans:[{...base,budget_minor:120000,reserved_minor:120000}],reservation_adjustment:change,
    replaced_plan:{name:"旧生日安排",released_reserved_minor:100000}};
  const html=renderToStaticMarkup(React.createElement(LifePlanCards,{result:value}));
  for(const amount of ["¥1000.00","¥1200.00","¥200.00"])assert.ok(html.includes(amount));
  assert.match(html,/原排期保留，本次调整未扣款/);assert.doesNotMatch(html,/原未用预留已释放|PRIVATE_/);
  const bad=renderToStaticMarkup(React.createElement(LifePlanCards,{result:{...value,reservation_adjustment:{...change,reservation_id:"PRIVATE_HOLD"}}}));
  assert.match(bad,/预留调整尚未核验/);assert.doesNotMatch(bad,/PRIVATE_HOLD|¥200.00|已调整为/);
});
test("unknown monetary fields do not become zero",()=>{
  const html=render([{...base,reserved_minor:null,spent_minor:undefined}]);assert.match(html,/尚在预留<\/dt><dd>暂未提供/);
  assert.match(html,/已付款<\/dt><dd>暂未提供/);
});
test("partial, refund pending and completed refunds stay distinct",()=>{
  const html=render([{...base,status:"PARTIAL",occurrences:[{occurrence_id:"o",date:"2026-11-20",status:"PARTIAL",orders:[
    {order_id:"paid",status:"PAID",amount_minor:39900},{order_id:"refund",status:"REFUND_PENDING",amount_minor:59900}]}]}]);
  assert.match(html,/部分完成/);assert.match(html,/已付款，待履约/);assert.match(html,/到账待核验/);assert.doesNotMatch(html,/全部成功|已送达/);
});
test("unknown order statuses never look completed and private reason codes stay hidden",()=>{
  const html=render([{...base,status:"PAUSED_RECONFIRM",pause_reason:"PRIVATE_BANK_ERROR",occurrences:[{occurrence_id:"x",date:"2026-11-20",status:"UNKNOWN",orders:[]}]}]);
  assert.match(html,/已暂停/);assert.match(html,/状态待核验/);assert.doesNotMatch(html,/PRIVATE_BANK_ERROR/);
});
test("lunar mapping is explicit rather than invented",()=>{
  const html=renderToStaticMarkup(React.createElement(FamilyOccasionCards,{result:{occasions:[{occasion_id:"x",month:8,day:15,calendar:"LUNAR",timezone:"Asia/Shanghai"}]}}));
  assert.match(html,/需本人确认本年阳历日期/);assert.doesNotMatch(html,/自动换算/);
});

const readTrace=(action,result,locale="zh-CN",label=action)=>({task_id:"fixture-parent",version:1,state:"SUCCEEDED",reply_language:locale,
  customer_reply_status:"READY",nodes:[{node_id:"fixture-node",operation_id:"fixture-op",action,label,status:"SUCCEEDED",depends_on:[],result}],
  outcome:{kind:"QUERY_RESULT",state:"SUCCEEDED",verified:true,completed_operation_ids:["fixture-op"]},
  pending_confirmation:null,result_observed_at:"2026-10-19T02:00:00+00:00"});
const outcome=(trace,locale)=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(TaskOutcomeCard,{trace})));
const visible=html=>html.replace(/<[^>]*>/g,"");
for(const locale of ["zh-CN","en"])test(`actual empty birthday READ is a friendly verified empty result, ${locale}`,()=>{
  const trace=readTrace("OCCASION_VIEW",{schema_version:"family-occasion-v1",occasions:[],bank_state_changed:false,
    owner_scope_hash:"PRIVATE_OWNER",source_account_id:"PRIVATE_ACCOUNT_SOURCE"},locale),before=JSON.stringify(trace);
  const html=outcome(trace,locale),text=visible(html);
  assert.match(html,/data-outcome-kind="QUERY_RESULT"/);assert.match(text,locale==="en"?/No confirmed family birthdays yet/:/目前没有已确认的家庭生日/);
  assert.match(text,locale==="en"?/main chat/:/主输入框/);
  assert.doesNotMatch(html,/OCCASION_VIEW|PRIVATE_OWNER|PRIVATE_ACCOUNT_SOURCE|business-receipt|<button|<details/);
  assert.doesNotMatch(text,/失败|未完成|failed|not completed/i);assert.equal(JSON.stringify(trace),before);
});
test("absent birthday records do not become a confirmed empty list",()=>{
  for(const locale of ["zh-CN","en"]){
    const html=renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(FamilyOccasionCards,{result:{}})));
    assert.match(html,locale==="en"?/not been returned yet/:/尚未完整返回/);assert.doesNotMatch(html,/目前没有|No confirmed/);
  }
});
test("new READ fallback labels hide only raw enums and preserve descriptive labels",()=>{
  for(const locale of ["zh-CN","en"]){
    const raw=outcome(readTrace("AA_VIEW",{collections:[],invitations:[]},locale),locale);
    assert.match(visible(raw),locale==="en"?/View AA payments/:/查看AA收付款/);assert.doesNotMatch(visible(raw),/AA_VIEW|fixture-node|fixture-op/);
    const label=locale==="en"?"My outstanding shared lunch":"我的午餐AA待付份额";
    const custom=outcome(readTrace("AA_VIEW",{collections:[],invitations:[]},locale,label),locale);assert.ok(visible(custom).includes(label));
  }
});
function savedBirthdayTrace(locale="zh-CN"){
  const result={schema_version:"family-occasion-v1",occasion_state_changed:true,bank_state_changed:false,occasions:[{
    occasion_id:"PRIVATE_OCCASION_ID",family_member_id:"PRIVATE_MEMBER_ID",family_member_name:"女儿",month:10,day:20,calendar:"GREGORIAN",timezone:"Asia/Shanghai",
    source_reference:"PRIVATE_SOURCE_REFERENCE",status:"ACTIVE",version:1}]};
  const trace=readTrace("OCCASION_SET",result,locale);trace.outcome.kind="BUSINESS_RECEIPT";return trace;
}
for(const locale of ["zh-CN","en"])test(`saved birthday renders this confirmation's result, not a raw enum or fresh current-data claim ${locale}`,()=>{
  const trace=savedBirthdayTrace(locale),before=JSON.stringify(trace),html=outcome(trace,locale),text=visible(html);
  assert.match(html,/data-outcome-kind="COMMITTED"/);assert.match(text,locale==="en"?/Birthday saved/:/生日已保存/);
  assert.match(text,locale==="en"?/10\/20/:/10月20日/);assert.match(text,/女儿/);
  assert.doesNotMatch(text,/Asia\/Shanghai|授信|购物权限|现时最新|current birthday|fresh query|grant credit|account, credit|permission/i);
  assert.doesNotMatch(html,/OCCASION_SET|PRIVATE_|business-receipt/);assert.equal(JSON.stringify(trace),before);
});
test("unknown or failed birthday save cannot acquire a completed save card",()=>{
  for(const status of ["UNKNOWN","FAILED"]){
    const trace=savedBirthdayTrace();trace.state=trace.outcome.state=status;trace.outcome.kind=status;trace.nodes[0].status=status;
    const text=visible(outcome(trace,"zh-CN"));assert.doesNotMatch(text,/生日已保存|OCCASION_SET|10月20日/);
  }
});
for(const locale of ["zh-CN","en"])test(`saved birthday owns only exact repetitive narration, preserving extra risk ${locale}`,()=>{
  const trace=savedBirthdayTrace(locale);
  trace.assistant_response={state_kind:"COMMITTED",read_only:false,reply_language:locale,
    intro:locale==="en"?"The confirmed birthday record was saved.":"好的，生日资料已按本次确认保存。",
    detail:locale==="en"?"This grants the family member no account, credit or payment permission.":"这不会给家庭成员开通账户、授信或购物权限。"};
  const before=JSON.stringify(trace),html=outcome(trace,locale),text=visible(html);
  assert.doesNotMatch(text,/本次确认保存|授信|购物权限|account, credit|permission/);
  assert.equal((text.match(locale==="en"?/Birthday saved/g:/生日已保存/g)||[]).length,1);
  assert.doesNotMatch(html,/customer-result-narration|结果已查到|finishing the explanation/);assert.equal(JSON.stringify(trace),before);
  const warning=locale==="en"?"Please check the saved lunar date before making a plan.":"安排计划前请再核对已保存的农历日期。";
  trace.assistant_response.detail+="\n"+warning;
  assert.ok(visible(outcome(trace,locale)).includes(warning));
  const mixed=savedBirthdayTrace(locale);mixed.assistant_response=trace.assistant_response;
  mixed.nodes.push({...mixed.nodes[0],node_id:"second-result",operation_id:"second-op",action:"AA_REQUEST",label:"午餐AA",result:{aa_request_id:"PRIVATE_AA_ID",status:"COLLECTING"}});
  mixed.outcome.completed_operation_ids.push("second-op");
  assert.match(visible(outcome(mixed,locale)),locale==="en"?/account, credit or payment permission/:/授信或购物权限/);
});
test("birthday receipt's required timezone stays validated but is not displayed",()=>{
  const trace=savedBirthdayTrace();delete trace.nodes[0].result.occasions[0].timezone;
  const text=visible(outcome(trace,"zh-CN"));assert.match(text,/保存结果还需要核对/);assert.doesNotMatch(text,/生日已保存|10月20日/);
});
test("new WRITE result labels are display-only and preserve descriptive result labels",()=>{
  for(const locale of ["zh-CN","en"]){
    const trace=readTrace("AA_PAY",{transfer_id:"business-receipt",transfer_channel:"INTERNAL_BANK",amount_minor:3350,recipient_username:"wangwei",source_account_id:"PRIVATE_ACCOUNT_ID",
      payment_confirmation:{amount_minor:3350,currency:"CNY",source:{instrument_type:"DEBIT",currency:"CNY",display_last4:"5b03"},recipient:{instrument_type:"DEBIT",currency:"CNY",display_last4:"2bb3",username:"wangwei"}}},locale);
    trace.outcome.kind="BUSINESS_RECEIPT";
    const html=outcome(trace,locale),text=visible(html);assert.match(text,locale==="en"?/Pay AA share/:/支付AA份额/);assert.doesNotMatch(html,/AA_PAY|PRIVATE_ACCOUNT_ID/);
    const label=locale==="en"?"My shared-lunch payment":"我的午餐AA付款";trace.nodes[0].label=label;assert.ok(visible(outcome(trace,locale)).includes(label));
  }
});

for(const locale of ["zh-CN","en"])test(`family profile result card shows only saved metadata ${locale}`,()=>{
  const result={schema_version:"family-profile-v1",bank_state_changed:false,family_profile_state_changed:true,
    account_access_granted:false,members:[{family_member_id:"PRIVATE_MEMBER",family_member_name:"小苗",family_relationship:"女儿",version:1,account_access_granted:false}]};
  const trace=readTrace("OCCASION_MEMBER_SET",result,locale);trace.outcome.kind="BUSINESS_RECEIPT";
  const html=outcome(trace,locale),text=visible(html);
  assert.match(text,/小苗/);assert.match(text,/女儿/);assert.doesNotMatch(html,/PRIVATE_MEMBER|OCCASION_MEMBER_SET|business-receipt/);
  assert.match(text,locale==="en"?/No login|no login/:/不授予登录/);
});
