import assert from 'node:assert/strict';
import vm from 'node:vm';
import { randomFillSync, createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../',import.meta.url));
const code=await fs.readFile(root+'android-client/assets/compat.js','utf8');
assert.match(code,/DOMContentLoaded/, 'Test the current rebuilt viewport compatibility bundle');
function viewportFixture(width=400) {
 const documentEvents=new Map(),windowEvents=new Map(),writes=[];
 const screen={width,height:800};
 const meta={content:`width=${Math.round(width/0.86)}, initial-scale=0.86`,setAttribute(name,value){assert.equal(name,'content');this.content=value;writes.push(value);}};
 const document={
  addEventListener(name,callback){documentEvents.set(name,callback);},
  querySelector(selector){assert.equal(selector,'meta[name="viewport"]');return meta;},
 };
 return {document,screen,meta,writes,
  addEventListener(name,callback){windowEvents.set(name,callback);},
  ready(){documentEvents.get('DOMContentLoaded')?.();},resize(){windowEvents.get('resize')?.();}};
}
const viewport=viewportFixture();
let copied='',removed=false,focused=false;
const navigator={};
const document={...viewport.document,activeElement:{focus(){focused=true;}},body:{appendChild(){}},createElement(){return {value:'',style:{},select(){},remove(){removed=true;}};},
  execCommand(command){assert.equal(command,'copy');copied='called';return true;}};
const context=vm.createContext({crypto:{getRandomValues:array=>randomFillSync(array)},navigator,document,
  screen:viewport.screen,addEventListener:viewport.addEventListener,
  innerWidth:465,innerHeight:800,scrollY:160,scrollTo(){throw new Error('Resize must not change reading position');},
  AbortSignal,AbortController,DOMException,setTimeout,clearTimeout,ArrayBuffer,Uint8Array,DataView,TextEncoder,console});
vm.runInContext(code,context,{filename:'bundled-compat.js'});
let tests=0;
const digest=async(data,algorithm='SHA-256')=>Buffer.from(await context.crypto.subtle.digest(algorithm,data)).toString('hex');
for(const value of ['', 'abc','圳行Agent 🌱','a'.repeat(1024*1024)]){
  const bytes=new TextEncoder().encode(value),expected=createHash('sha256').update(bytes).digest('hex');
  assert.equal(await digest(bytes.buffer),expected);tests++;
  assert.equal(await digest(bytes,{name:'SHA-256'}),expected);tests++;
}
const backing=new Uint8Array([0xde,0xad,0x61,0x62,0x63,0xbe,0xef]);
assert.equal(await digest(backing.subarray(2,5)),createHash('sha256').update('abc').digest('hex'));tests++;
assert.equal(await digest(new DataView(backing.buffer,2,3)),createHash('sha256').update('abc').digest('hex'));tests++;
await assert.rejects(digest(new Uint8Array([1]),'SHA-1'),e=>e.name==='NotSupportedError');tests++;
const ids=new Set();
for(let i=0;i<4096;i++){
 const id=context.crypto.randomUUID();assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);ids.add(id);
}
assert.equal(ids.size,4096);tests++;
await navigator.clipboard.writeText('银行卡尾号 04d1');assert.equal(copied,'called');assert.equal(removed,true);assert.equal(focused,true);tests++;
document.execCommand=()=>false;
await assert.rejects(navigator.clipboard.writeText('failed'),e=>e.name==='NotAllowedError');tests++;
// Existing native implementations must retain identity when this bundle is loaded.
const nativeRandom=()=> 'native-random';const nativeDigest=async()=>new ArrayBuffer(0);const nativeWrite=async()=>{};
const nativeViewport=viewportFixture();
const untouched={crypto:{randomUUID:nativeRandom,subtle:{digest:nativeDigest}},navigator:{clipboard:{writeText:nativeWrite}},
  document:nativeViewport.document,screen:nativeViewport.screen,addEventListener:nativeViewport.addEventListener,
  AbortSignal,AbortController,DOMException,setTimeout};
vm.runInNewContext(code,untouched);
assert.equal(untouched.crypto.randomUUID,nativeRandom);assert.equal(untouched.crypto.subtle.digest,nativeDigest);assert.equal(untouched.navigator.clipboard.writeText,nativeWrite);tests++;
// Exercise the actual fallback statics using real AbortController signal objects.
const fallbackStatics={};
const fallbackViewport=viewportFixture();
vm.runInNewContext(code,{crypto:untouched.crypto,navigator:untouched.navigator,
  document:fallbackViewport.document,screen:fallbackViewport.screen,addEventListener:fallbackViewport.addEventListener,
  AbortSignal:fallbackStatics,AbortController,DOMException,setTimeout});
const cancelled=new AbortController(),why={tag:'cancelled'};cancelled.abort(why);
assert.equal(fallbackStatics.any([cancelled.signal]).reason,why);tests++;
const first=new AbortController(),second=new AbortController(),combined=fallbackStatics.any([first.signal,second.signal]);
first.abort('first');second.abort('second');assert.equal(combined.reason,'first');tests++;
const timed=fallbackStatics.timeout(1);await new Promise(resolve=>timed.addEventListener('abort',resolve,{once:true}));
assert.equal(timed.reason.name,'TimeoutError');tests++;
// Initial 86% metadata comes from native HTML loading. Ordinary resizes must
// preserve user zoom and reading position; only a display-width change resets.
assert.equal(viewport.writes.length,0);tests++;
viewport.ready();assert.equal(viewport.writes.length,0);tests++;
context.innerHeight=430;viewport.resize();assert.equal(viewport.writes.length,0);assert.equal(context.scrollY,160);tests++;
context.innerHeight=800;viewport.resize();assert.equal(viewport.writes.length,0);tests++;
context.innerWidth=458;viewport.resize();assert.equal(viewport.writes.length,0);tests++;
viewport.meta.content='width=465, initial-scale=1.25'; // user pinch/zoom state
context.innerHeight=760;viewport.resize();assert.equal(viewport.meta.content,'width=465, initial-scale=1.25');tests++;
viewport.screen.width=800;viewport.screen.height=400;viewport.resize();
assert.equal(viewport.writes.length,1);assert.equal(viewport.meta.content,'width=930, initial-scale=0.86, minimum-scale=0.65, maximum-scale=2, user-scalable=yes');tests++;
viewport.resize();assert.equal(viewport.writes.length,1);tests++;
viewport.screen.width=400;viewport.screen.height=800;viewport.resize();
assert.equal(viewport.writes.length,2);assert.match(viewport.meta.content,/^width=465, initial-scale=0\.86,/);tests++;
document.querySelector=()=>null;viewport.screen.width=600;assert.doesNotThrow(()=>viewport.resize());tests++;
const result={tests,failures:0,sha256_vectors:10,uuid_samples:ids.size,viewport_tests:10,
  scope:'isolated JavaScript VM, no application data or network'};
await fs.mkdir(root+'var/offline-checks',{recursive:true});
await fs.writeFile(root+'var/offline-checks/compat-viewport-result.json',JSON.stringify(result,null,2),'utf8');
console.log(JSON.stringify(result));
