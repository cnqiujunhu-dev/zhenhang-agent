import assert from 'node:assert/strict';
import {after} from 'node:test';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {parseSync} from 'rolldown/utils';
import {createServer} from 'vite';

// The test-only export uses App's actual JSX subtree, preparation statements,
// imports and components. The AST identifies those nodes without regex-based
// transpilation or a copied implementation. No production source is written.
const stateKeys=['pending','pendingNode','pendingResult','pendingTransferScope','trace','taskLocale','taskText',
 'taskViewStale','chat','hasProcessingTurn','confirmationValidity','confirmationResultUnknown','busy','bankActionBlocked',
 'authRecovery','effectiveAuthVerified','challenge','challengeCurrent','authError','authCodeInvalid','authCode','localRecovery',
 'canRevise','canCancel','revisionOpen','beginChallenge','verifyChallenge','readLatestAuthConfirmation','readConfirmationAloud',
 'confirmAction','setAuthCode','setAuthCodeInvalid','setAuthError','setRevisionOpen','prepareDraft','setCancelFeedback','setCancelOpen','authRecoveryText'];
function exposeConfirmation(code){
 const parsed=parseSync('App.tsx',code);assert.deepEqual(parsed.errors,[]);
 let cards=[],preludes=[];
 function walk(node){
  if(!node||typeof node!=='object')return;
  if(node.type==='JSXElement'&&node.openingElement?.name?.name==='div'&&node.openingElement.attributes.some(a=>a.type==='JSXAttribute'&&a.name?.name==='className'&&a.value?.value==='confirmation-card confirmation-compact'))cards.push(node);
  if(node.type==='BlockStatement'){
   const names=s=>s?.type==='VariableDeclaration'?(s.declarations||[]).map(d=>d.id?.name):[];
   const first=node.body.findIndex(s=>names(s).includes('pendingOrderPreview'));
   const end=node.body.findIndex(s=>names(s).includes('previewScope'));
   if(first>=0&&end>first)preludes.push(node.body.slice(first,end));
  }
  for(const [key,value]of Object.entries(node)){
   if(['parent','comments'].includes(key))continue;
   if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
  }
 }
 walk(parsed.program);assert.equal(cards.length,1,'One actual confirmation JSX region required');assert.equal(preludes.length,1,'One actual confirmation preparation block required');
 const card=code.slice(cards[0].start,cards[0].end);
 const prelude=preludes[0].map(n=>code.slice(n.start,n.end)).join('\n');
 assert.ok(card.startsWith('<div')&&card.endsWith('</div>'),'AST offsets preserve original JSX');
 return code+`\nexport function __testBankingConfirmation(state){const {${stateKeys.join(',')}}=state;\n${prelude}\nreturn (${card});}\n`;
}
const server=await createServer({root:fileURLToPath(new URL('../',import.meta.url)),configFile:false,envDir:false,
 server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:'automatic'},
 plugins:[{name:'test-app-confirmation-export',enforce:'pre',transform(code,id){
  if(id.replaceAll('\\','/').split('?')[0].endsWith('/src/App.tsx'))return {code:exposeConfirmation(code),map:null};
 }}]});
after(()=>server.close());
const {__testBankingConfirmation:Component}=await server.ssrLoadModule('/src/App.tsx');
const {CustomerLanguageScope}=await server.ssrLoadModule('/src/CustomerLanguageProvider.tsx');
const {translateCustomerText}=await server.ssrLoadModule('/src/customerLanguage.ts');
export const {subscriptionCancelConfirmation}=await server.ssrLoadModule('/src/confirmationPresentation.ts');

export function renderBankingAppDto(dto,{locale='zh-CN',verified=false}={}){
 const pending=dto.pending_confirmation;assert.ok(pending,'A real pending confirmation is required');
 const pendingNode=dto.nodes.find(n=>n.child_task_id===pending.task_id);assert.ok(pendingNode,'Pending child must match the DTO');
 const forbidden=()=>{throw Error('SSR must not invoke a banking or authentication handler');};
 const state={pending,pendingNode,pendingResult:pendingNode.result,pendingTransferScope:pending.scope,trace:dto,taskLocale:locale,
  taskText:text=>translateCustomerText(text,locale),taskViewStale:false,chat:{turns:[],pending:null},hasProcessingTurn:()=>false,
  confirmationValidity:{current:true,state:'CURRENT'},confirmationResultUnknown:false,busy:false,bankActionBlocked:false,
  authRecovery:{blocked:false,record:null,canRead:false},effectiveAuthVerified:verified,challenge:null,challengeCurrent:false,
  authError:'',authCodeInvalid:false,authCode:'',localRecovery:null,canRevise:dto.allowed_actions?.includes('revise'),canCancel:dto.allowed_actions?.includes('cancel'),revisionOpen:false,
  beginChallenge:forbidden,verifyChallenge:forbidden,readLatestAuthConfirmation:forbidden,readConfirmationAloud:forbidden,confirmAction:forbidden,
  setAuthCode:forbidden,setAuthCodeInvalid:forbidden,setAuthError:forbidden,setRevisionOpen:forbidden,prepareDraft:forbidden,setCancelFeedback:forbidden,setCancelOpen:forbidden,authRecoveryText:()=>''};
 return renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(Component,state)));
}
