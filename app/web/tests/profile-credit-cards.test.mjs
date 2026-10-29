import assert from 'node:assert/strict';
import {after,test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({root:fileURLToPath(new URL('../',import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:'automatic'}});
after(()=>server.close());
const {ProfileCreditCards}=await server.ssrLoadModule('/src/ProfileCreditCards.tsx');
const {CustomerLanguageScope}=await server.ssrLoadModule('/src/CustomerLanguageProvider.tsx');
const card=(id='one',pool='PRIVATE_A',tail='1234')=>({card_id:id,credit_account_id:pool,card_number:'zhenhangabcd'+tail,
  instrument_type:'CREDIT',credit_is_cash:false,status:'ACTIVE',facility_version:3,base_currency:'CNY',
  supported_currencies:['CNY','USD'],shared_credit_limit:true,base_credit_limit_minor:3000000,
  used_base_exposure_minor:450000,authorization_base_exposure_minor:50000,available_base_credit_minor:2500000,
  policy:{policy_version:'sim-credit-v1',base_currency:'CNY',supported_currencies:['CNY','USD']},
  balances:['CNY','USD'].map(currency=>({currency,principal_minor:currency==='CNY'?12000:800,
    fees_minor:0,interest_minor:0,credit_balance_minor:0,outstanding_minor:currency==='CNY'?12000:800}))});
const render=(data,locale='zh-CN')=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(ProfileCreditCards,{data})));
for(const locale of ['zh-CN','en'])test(`${locale}: physical credit-card face has masked number and separate credit/debt fields`,()=>{
  const data={cards:[card()]},before=JSON.stringify(data),html=render(data,locale);
  assert.match(html,/bank-card-credit/);assert.match(html,/bank-card-number-row/);assert.match(html,/card-number-visibility/);
  assert.match(html,/simulation-card-copy/);assert.match(html,/1234/);
  assert.doesNotMatch(html,/zhenhangabcd1234|PRIVATE_A|单笔限额|人民币余额|16字符|卡片状态/);
  assert.match(html,/¥30,000\.00/);assert.match(html,/¥25,000\.00/);assert.match(html,/¥120\.00/);assert.match(html,/US\$8\.00/);
  assert.match(html,/<details class="profile-credit-details">/);assert.doesNotMatch(html,/<details[^>]* open/);
  assert.equal(JSON.stringify(data),before);
});
test('nonadjacent shared cards link by exact facility, never add credit limits or debt twice',()=>{
  const html=render({cards:[card(),card('two','PRIVATE_B','5678'),card('three','PRIVATE_A','90ab')]});
  assert.equal((html.match(/bank-card bank-card-credit/g)||[]).length,3);
  assert.equal((html.match(/¥30,000\.00/g)||[]).length,2);
  assert.equal((html.match(/¥25,000\.00/g)||[]).length,2);
  assert.match(html,/与尾号1234共享额度及欠款/);assert.doesNotMatch(html,/¥60,000|¥90,000|PRIVATE_/);
});
test('missing amounts and incomplete payloads are not shown as zero or no cards',()=>{
  const html=render({cards:[{card_id:'unknown',base_currency:'CNY',supported_currencies:['CNY','USD']}]});
  assert.match(html,/暂未提供/);assert.doesNotMatch(html,/¥0\.00|US\$0\.00/);
  assert.match(render({}),/资料暂未读取完整/);assert.match(render({cards:[]}),/目前没有信用卡/);
});
for(const [state,label] of [['LOCKED','已锁定'],['LOST','已挂失'],['ISSUED','待激活']])test(`${state} remains visibly restricted`,()=>{
  const html=render({cards:[{...card(),status:state}]});assert.match(html,/card-restriction/);assert.match(html,new RegExp(label));
});
test('mismatched card identity cannot expose or copy a contradictory number',()=>{
  const html=render({cards:[{...card(),display_last4:'ffff'}]});assert.doesNotMatch(html,/simulation-card-copy|zhenhangabcd1234/);
});
test('application records remain collapsed and do not invent approved card amounts',()=>{
  const html=render({cards:[],applications:[{application_id:'PRIVATE_APPLICATION',kind:'CARD',status:'PENDING_REVIEW'}]});
  assert.match(html,/申请记录（1）/);assert.match(html,/审核中/);assert.doesNotMatch(html,/PRIVATE_APPLICATION|<details[^>]* open|¥0/);
});
test('profile alone uses physical cards and keeps single credit card at debit-card column width',()=>{
  const source=readFileSync(new URL('../src/CustomerDataHome.tsx',import.meta.url),'utf8');
  assert.match(source,/<ProfileCreditCards data=\{data\}/);assert.doesNotMatch(source,/<CreditCards node=/);
  const css=readFileSync(new URL('../src/profileCreditCards.css',import.meta.url),'utf8');
  assert.match(css,/repeat\(2,minmax\(0,1fr\)\)/);assert.match(css,/@container\(max-width:598px\)/);
});
