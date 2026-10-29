import assert from "node:assert/strict";
import {after,test} from "node:test";
import {fileURLToPath} from "node:url";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createServer} from "vite";
const server=await createServer({root:fileURLToPath(new URL("../",import.meta.url)),configFile:false,envDir:false,
 server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:"automatic"}});
after(()=>server.close());
const {LifePlanCards}=await server.ssrLoadModule("/src/LifePlanCards.tsx");
const {CustomerLanguageScope}=await server.ssrLoadModule("/src/CustomerLanguageProvider.tsx");
const plan={plan_id:"PRIVATE_PLAN",name:"本次生日安排",version:1,status:"PAUSED_RECONFIRM",budget_minor:100000,
 reserved_minor:100000,spent_minor:0,refunded_minor:0,execution_review:{schema_version:"life-execution-review-v1",plan_version:1,
 plan_status:"PAUSED_RECONFIRM",reason:"PRICE_CHANGED",payment_count:0,paid_minor:0,refunded_minor:0,reserved_minor:100000,source:"LOCAL_BANK_AND_VIRTUAL_MERCHANT"}};
const render=(p,en=false)=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale:en?"en":"zh-CN"},React.createElement(LifePlanCards,{result:{plans:[p]}})));
test("recorded price stop explains the reason, no-payment effect and recovery",()=>{
 const html=render(plan);assert.match(html,/指定商品价格或条款已变化/);assert.match(html,/本计划尚无付款记录/);
 assert.match(html,/取消未执行计划以释放剩余预留/);assert.doesNotMatch(html,/PRIVATE_PLAN|PRICE_CHANGED|life-execution-review-v1/);
});
test("partial failure never claims no payment or an already received refund",()=>{
 const p={...plan,status:"PARTIAL",spent_minor:39900,reserved_minor:60100,execution_review:{...plan.execution_review,
 plan_status:"PARTIAL",reason:"ITEM_UNAVAILABLE",payment_count:1,paid_minor:39900,reserved_minor:60100}};
 const html=render(p);assert.match(html,/已有1笔付款记录/);assert.match(html,/先核对已有订单和退款进度/);
 assert.doesNotMatch(html,/尚无付款记录|已全部退款|未扣款/);
});
test("stale, inconsistent or expanded checkpoint stays on the generic recovery path",()=>{
 for(const change of [{plan_version:2},{plan_status:"ACTIVE"},{paid_minor:100},{reserved_minor:0},{reason:"<script>"},
   {payment_count:1},{refunded_minor:1},{internal_id:"PRIVATE_LEAK"}]){
  const html=render({...plan,execution_review:{...plan.execution_review,...change}});
  assert.doesNotMatch(html,/本计划尚无付款记录|指定商品价格或条款已变化|PRIVATE_LEAK|<script>/);
  assert.match(html,/安排已暂停，请核对变更后重新确认/);
 }
});
test("English review preserves the financial distinction",()=>{
 const html=render(plan,true);assert.match(html,/price or terms changed/);assert.match(html,/No payment has been recorded/);
 assert.match(html,/release unused reservations/);assert.doesNotMatch(html,/指定商品|PRIVATE_PLAN/);
});
test("a successful plan cannot acquire a stale paused explanation",()=>{
 const html=render({...plan,status:"SUCCEEDED"});assert.doesNotMatch(html,/本计划尚无付款记录|指定商品价格或条款已变化/);
});
