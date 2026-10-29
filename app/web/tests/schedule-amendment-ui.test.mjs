import assert from 'node:assert/strict';
import {after,test} from 'node:test';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {schedule,expiry} from './banking-confirmation-fixtures.mjs';
const server=await createServer({root:fileURLToPath(new URL('../',import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:'automatic'}});
after(()=>server.close());
const {BankingWorkflowConfirmation,bankingWorkflowConfirmationReady}=await server.ssrLoadModule('/src/BankingWorkflowConfirmation.tsx');
const {ScheduledTransferCards}=await server.ssrLoadModule('/src/ScheduledTransferCards.tsx');
const {CustomerLanguageScope}=await server.ssrLoadModule('/src/CustomerLanguageProvider.tsx');
const render=component=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale:'zh-CN'},component));
test('replacement review says precisely which future authority changes and exposes the default time',()=>{
  const scope={...schedule(),replaces_future_occurrences:true,default_execution_time:true};
  assert.equal(bankingWorkflowConfirmationReady(scope.action,scope,expiry),true);
  const html=render(React.createElement(BankingWorkflowConfirmation,{action:scope.action,scope,expiresAt:expiry}));
  assert.match(html,/确认后替换原安排的未执行期次/);assert.match(html,/09:00/);assert.match(html,/不代表保证该时刻到账/);
});
test('malformed replacement and clock provenance withdraw confirmation',()=>{
  for(const extra of [{replaces_future_occurrences:'true'},{replaces_future_occurrences:false},{default_execution_time:'true'}]){
    const value={...schedule(),...extra};
    assert.equal(bankingWorkflowConfirmationReady(value.action,value,expiry),false);
  }
});
test('series query displays remaining count and distinguishes debit from arrival',()=>{
  const node={node_id:'schedule-view',action:'TRANSFER_SCHEDULE_VIEW',status:'SUCCEEDED',result:{
    schema_version:'scheduled-transfers-read-v1',bank_state_changed:false,schedules:[{
      schedule_id:'schedule-one',payee_name:'林清',amount_minor:13,currency:'CNY',memo:'房租',status:'ACTIVE',
      due_at:'2027-01-31T01:00:00+00:00',next_due_at:'2027-02-28T01:00:00+00:00',settlement_status:'UNVERIFIED',
      series:{frequency:'MONTHLY',remaining_occurrences:2,end_at:'2027-03-31T01:00:00+00:00',occurrences:[]}}]}};
  const html=render(React.createElement(ScheduledTransferCards,{node}));
  assert.match(html,/每月/);assert.match(html,/剩余 2 次/);assert.match(html,/已转出，到账结果待核实/);
  assert.match(html,/预约有效/);assert.doesNotMatch(html,/执行中/);
});
