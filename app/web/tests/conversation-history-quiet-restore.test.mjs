import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {after,test} from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

// Expose the actual private component only in this SSR test module. Vite handles
// its TypeScript, JSX and real imports; no source slices or export stripping.
const server=await createServer({root:fileURLToPath(new URL('../',import.meta.url)),configFile:false,envDir:false,
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:'automatic'},
  plugins:[{name:'test-private-result-read',enforce:'pre',transform(code,id){
    if(id.replaceAll('\\','/').split('?')[0].endsWith('/src/ConversationTimeline.tsx'))
      return {code:code+'\nexport { ConversationResultRead as __testConversationResultRead };',map:null};
  }}]});
after(()=>server.close());
const {__testConversationResultRead:Component}=await server.ssrLoadModule('/src/ConversationTimeline.tsx');
const {CustomerLanguageScope}=await server.ssrLoadModule('/src/CustomerLanguageProvider.tsx');
function render(status,{blocked=false,paused=false,locale='zh-CN'}={}){
  return renderToStaticMarkup(React.createElement(CustomerLanguageScope,{locale},React.createElement(Component,{taskId:'old',statusId:'read-status',
    result:{status},ready:false,knownQuery:true,taskResults:{permissionBlocked:blocked,paused}})));
}
for(const locale of ['zh-CN','en']) test(locale+': transient historical restore has no loading row or skeleton',()=>{
  assert.equal(render('LOADING',{locale}),'');
});
test('quiet restore never hides permission revocation, unavailable history or errors',()=>{
  assert.match(render('LOADING',{blocked:true}),/读取权限需要重新核验/);
  assert.match(render('LOADING',{paused:true}),/结果已隐藏/);
  assert.match(render('HIDDEN'),/目前不可查看/);
  assert.match(render('ERROR'),/暂未读取完成/);
});
