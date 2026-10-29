import assert from "node:assert/strict";
import {after, test} from "node:test";
import {fileURLToPath} from "node:url";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createServer} from "vite";

const server = await createServer({root:fileURLToPath(new URL("../",import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:"automatic"}});
after(()=>server.close());
const {BusinessResultCards} = await server.ssrLoadModule("/src/BusinessResultCards.tsx");
const {compactTransferResult,recordedCompactTransfer} = await server.ssrLoadModule("/src/transferResultPresentation.ts");
const {CustomerLanguageScope} = await server.ssrLoadModule("/src/CustomerLanguageProvider.tsx");
const receipt = (changes={})=>({node_id:"actual-transfer",action:"TRANSFER",status:"SUCCEEDED",label:"转账",depends_on:[],
  result:{transfer_id:"PRIVATE_TRANSFER_ID",amount_minor:1234,transfer_channel:"INTERNAL_BANK",
    recipient_display_name:"林悦",recipient_username:"recording_family",recipient_account_id:"PRIVATE_ACCOUNT_ID",
    recipient_card_number:"zhenhangd18b0a0f",memo:"材料费",message:"双方账本与流水已同时入账。"},...changes});
const render = (node,locale="zh-CN")=>renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(BusinessResultCards,{node})));

for(const locale of ["zh-CN","en"])test(`posted TRANSFER has only four facts, no repeated amount, title, settlement narrative or disclosure: ${locale}`,()=>{
  const node=receipt(),before=JSON.stringify(node),html=render(node,locale);
  assert.equal((html.match(/<dt>/g)||[]).length,4);
  assert.match(html,/林悦/);assert.match(html,/zhenhangd18b0a0f/);assert.match(html,/材料费/);
  assert.match(html,locale==="en"?/<dt>Transfer status<\/dt><dd[^>]*>Successful/:/<dt>转账状态<\/dt><dd[^>]*>成功/);
  assert.doesNotMatch(html,/12\.34|1234|PRIVATE|recording_family|双方|入账|<h[1-6]|<details|到账|credited|posted/);
  assert.equal(JSON.stringify(node),before);
  assert.equal(recordedCompactTransfer(node),true);
});

test("exact receiver card comes from the actual recipient field, not source card, account suffix, UUID or reference",()=>{
  const original=receipt();
  const data={...original.result,source_card_number:"zhenhanga34c9a62",card_number:"zhenhanga34c9a62",
    recipient_reference:"ACCT-PRIVATE",payee_last4:"6387"};
  assert.equal(compactTransferResult({...original,result:data}).recipientCardNumber,"zhenhangd18b0a0f");
  delete data.recipient_card_number;delete data.card_number;
  assert.equal(compactTransferResult({...original,result:{...data,recipient_context:{recipient_card_number:"zhenhangd18b0a0f"}}}).recipientCardNumber,"zhenhangd18b0a0f");
  for(const fake of ["6387","ACCT-abcd","da692cde-908f-4538-a9fe-40dad04b68cf","debit:PRIVATE"]){
    assert.equal(compactTransferResult({...original,result:{...data,card_number:fake}}).recipientCardNumber,null);
  }
  assert.equal(compactTransferResult({...original,result:{...original.result,recipient_context:{recipient_card_number:"zhenhangf539c9c5"}}}).recipientCardNumber,null);
});

test("verified display name wins; a username, PAN or internal ID is never relabeled as a person's name",()=>{
  const node=receipt();
  assert.equal(compactTransferResult({...node,result:{...node.result,payee_name:"recording_family"}}).recipientName,"林悦");
  for(const fake of ["recording_family","zhenhangd18b0a0f","ACCT-example","da692cde-908f-4538-a9fe-40dad04b68cf"]){
    assert.equal(compactTransferResult({...node,result:{...node.result,recipient_display_name:null,payee_name:fake}}).recipientName,null);
  }
  assert.equal(compactTransferResult({...node,result:{...node.result,recipient_display_name:null,payee_name:"Alex Lee"}}).recipientName,"Alex Lee");
});

test("empty note is None, absent historical note stays unknown, free-form note is not interpreted as success",()=>{
  const node=receipt();
  for(const memo of ["", "   ",null]) assert.match(render({...node,result:{...node.result,memo}}),/<dt>备注<\/dt><dd[^>]*>无/);
  const old={...node.result};delete old.memo;
  assert.match(render({...node,result:old}),/<dt>备注<\/dt><dd[^>]*>未提供/);
  const html=render({...node,result:{...node.result,memo:'<script>do not run</script>\n付款确认'}});
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});

for(const [name,changes] of [
  ["failed operation",{status:"FAILED",result:{error:"BALANCE_INSUFFICIENT",recipient_display_name:"林悦",memo:""}}],
  ["unknown operation",{status:"UNKNOWN"}],
  ["missing actual receipt",{result:{message:"转账成功了",recipient_display_name:"林悦"}}],
  ["error despite completed status",{result:{...receipt().result,error:"OUTCOME_UNKNOWN"}}],
  ["failed receipt state",{result:{...receipt().result,status:"FAILED"}}],
  ["contradictory operation",{status:"FAILED"}],
  ["nonpositive amount",{result:{...receipt().result,amount_minor:0}}],
  ["boolean amount",{result:{...receipt().result,amount_minor:true}}],
  ["preview",{result:{...receipt().result,preview:true}}],
  ["preview object",{result:{...receipt().result,preview:{amount_minor:1234}}}],
  ["no state change",{result:{...receipt().result,bank_state_changed:false}}],
])test(`failure/unknown never gets a successful compact outcome or parent suppression: ${name}`,()=>{
  const node=receipt(changes),before=JSON.stringify(node);
  assert.equal(recordedCompactTransfer(node),false);assert.notEqual(compactTransferResult(node).status,"SUCCESS");
  const html=render(node);assert.doesNotMatch(html,/data-transfer-state="SUCCESS"|>成功<|双方|已入账/);
  assert.equal(JSON.stringify(node),before);
});

test("pending or closed TRANSFER never renders a completed receipt",()=>{
  for(const status of ["WAITING_CONFIRMATION","NEED_CLARIFICATION","RUNNING","CANCELLED","CLOSED"]){
    const node=receipt({status});assert.equal(compactTransferResult(node),null);assert.equal(render(node),"");
  }
});

test("scheduled registration and actual scheduled execution remain distinct, outside TRANSFER compact helper",()=>{
  const node=receipt({action:"TRANSFER_SCHEDULE",result:{schedule_id:"schedule",amount_minor:111,recipient_username:"recording_family",due_at:"2026-10-22T02:18:00+08:00"}});
  assert.equal(compactTransferResult(node),null);assert.equal(recordedCompactTransfer(node),false);
  assert.match(render(node),/预约已登记，到期时会再次核对；当前还未付款/);
  const posted=render({...node,result:{...node.result,transfer_id:"actual-scheduled-transfer",transfer_channel:"INTERNAL_BANK"}});
  assert.doesNotMatch(posted,/当前还未付款|transfer-result-card/);assert.match(posted,/1\.11/);
});

test("AA invitation and foreign-exchange semantics do not use ordinary-transfer compact projection",()=>{
  for(const action of ["AA_REQUEST","FX_TRANSFER","FX_EXCHANGE","OWN_ACCOUNT_TRANSFER"]){
    const node=receipt({action});assert.equal(compactTransferResult(node),null);assert.equal(recordedCompactTransfer(node),false);
    assert.doesNotMatch(render(node),/transfer-result-card/);
  }
});
