import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {transformSync} from 'rolldown/utils';
const source=readFileSync(new URL('../src/useConversationRefreshLayout.ts',import.meta.url),'utf8');
const compiled=transformSync('refresh.ts',source,{}).code.replace(/^import\b[\s\S]*?;\s*\n/gm,'').replace(/\bexport\s+/g,'');
function harness(){
  const frames=new Map();let serial=0;
  class Target{listeners=new Map();addEventListener(k,f){const a=this.listeners.get(k)||new Set();a.add(f);this.listeners.set(k,a);}removeEventListener(k,f){this.listeners.get(k)?.delete(f);}emit(k,event={target:this}){for(const f of this.listeners.get(k)||[])f(event);}}
  const window=new Target(),document=Object.assign(new Target(),{visibilityState:'visible'});
  const rows=Array.from({length:4},()=>({style:{minHeight:''},natural:300,getBoundingClientRect(){return {height:Math.max(this.natural,parseFloat(this.style.minHeight)||0)};}}));
  let top=720;
  const viewport=Object.assign(new Target(),{clientHeight:480,get scrollHeight(){return rows.reduce((sum,row)=>sum+row.getBoundingClientRect().height,0);},get scrollTop(){return top;},set scrollTop(v){top=v;}});
  // Define accessor properties explicitly: Object.assign evaluates getters.
  Object.defineProperties(viewport,{scrollHeight:{get:()=>rows.reduce((sum,row)=>sum+row.getBoundingClientRect().height,0)},scrollTop:{get:()=>top,set:v=>{top=v;}}});
  const timeline={dataset:{},closest:()=>viewport,querySelectorAll:()=>rows};
  let refs=[],at=0,effects=[],first=true,cleanups=[];
  const input={scopeKey:'actor:thread',refreshing:false,blocked:false};
  const hook=new Function('useRef','useLayoutEffect','window','document','requestAnimationFrame','cancelAnimationFrame',compiled+';return useConversationRefreshLayout;')(
    v=>refs[at++]||(refs[at-1]={current:v}),fn=>effects.push(fn),window,document,fn=>{frames.set(++serial,fn);return serial;},id=>frames.delete(id));
  const render=()=>{at=0;effects=[];hook({current:timeline},input);if(first){cleanups.push(effects[0]());first=false;}effects[1]();};
  const flush=()=>{for(const [id,fn]of [...frames]){frames.delete(id);fn();}};
  render();return {window,document,rows,viewport,timeline,input,render,flush,cleanup:()=>cleanups.forEach(f=>f()),frames,
    shrink:()=>{rows.forEach(row=>row.natural=40);top=Math.min(top,Math.max(0,viewport.scrollHeight-viewport.clientHeight));},
    restore:()=>rows.forEach(row=>row.natural=300)};
}
test('focus recheck clears content without collapsing rows or moving the viewport',()=>{
  const h=harness();h.window.emit('focus');h.input.refreshing=true;h.shrink();h.render();
  assert.equal(h.viewport.scrollHeight,1200);assert.equal(h.viewport.scrollTop,720);
  assert.equal(h.timeline.dataset.conversationRefreshing,'true');
  h.restore();h.input.refreshing=false;h.render();h.flush();h.flush();
  assert.equal(h.viewport.scrollTop,720);assert.equal(h.timeline.dataset.conversationRefreshing,undefined);
  assert(h.rows.every(row=>row.style.minHeight===''));h.cleanup();
});
test('reader midway through history stays at the same pixel through revalidation',()=>{
  const h=harness();h.viewport.scrollTop=270;h.window.emit('blur');h.window.emit('focus');
  h.input.refreshing=true;h.shrink();h.render();assert.equal(h.viewport.scrollTop,270);
  h.restore();h.input.refreshing=false;h.render();h.flush();h.flush();assert.equal(h.viewport.scrollTop,270);h.cleanup();
});
test('capture holds geometry before hidden-page clearing and restores only after visible reads finish',()=>{
  const h=harness();h.document.visibilityState='hidden';h.document.emit('visibilitychange');h.shrink();
  h.render();h.flush();h.flush();assert.equal(h.viewport.scrollTop,720);assert.equal(h.frames.size,0);
  h.document.visibilityState='visible';h.input.refreshing=true;h.document.emit('visibilitychange');h.render();
  h.restore();h.input.refreshing=false;h.render();h.flush();h.flush();assert.equal(h.viewport.scrollTop,720);h.cleanup();
});
for(const gesture of ['wheel','pointerdown','touchstart','keydown'])test(`${gesture} cancels restoration instead of fighting the user`,()=>{
  const h=harness();h.window.emit('focus');h.viewport.emit(gesture);h.viewport.scrollTop=135;h.render();h.flush();h.flush();
  assert.equal(h.viewport.scrollTop,135);assert.equal(h.timeline.dataset.conversationRefreshing,undefined);h.cleanup();
});
test('real access failure removes reserved space and never restores private content',()=>{
  const h=harness();h.window.emit('focus');h.shrink();h.input.blocked=true;h.render();h.flush();h.flush();
  assert(h.rows.every(row=>row.style.minHeight===''));assert.equal(h.timeline.dataset.conversationRefreshing,undefined);
  assert(h.rows.every(row=>row.natural===40));h.cleanup();
});
test('input/button focus events do not freeze the whole timeline',()=>{
  const h=harness();h.window.emit('focus',{target:{nodeName:'TEXTAREA'}});assert.equal(h.timeline.dataset.conversationRefreshing,undefined);h.cleanup();
});
test('scope disposal cancels late frames and restores original styles',()=>{
  const h=harness();h.rows[0].style.minHeight='24px';h.window.emit('focus');h.cleanup();h.flush();
  assert.equal(h.rows[0].style.minHeight,'24px');assert.equal(h.frames.size,0);
  assert([...h.window.listeners.values()].every(set=>set.size===0));
});
test('timeline integration does not keep old financial snapshots and tail honors recheck geometry',()=>{
  const timeline=readFileSync(new URL('../src/ConversationTimeline.tsx',import.meta.url),'utf8');
  const tail=readFileSync(new URL('../src/useConversationTail.ts',import.meta.url),'utf8');
  assert.match(timeline,/useConversationRefreshLayout\(timelineRef/);
  assert.match(tail,/data-conversation-refreshing/);
  assert.doesNotMatch(source,/localStorage|sessionStorage|innerHTML|cloneNode|fetch\(/);
});
