// Local HTTP WebView compatibility. Random IDs still use the OS CSPRNG.
import { sha256 } from '../var/android-build/compat/node_modules/@noble/hashes/sha2.js';
// Recalculate only on display-width changes, so keyboard/resume keeps reading position.
document.addEventListener('DOMContentLoaded',()=>{
 let lastWidth=screen.width;
 addEventListener('resize',()=>{
  if(screen.width===lastWidth)return;lastWidth=screen.width;
  document.querySelector('meta[name="viewport"]')?.setAttribute('content',`width=${Math.round(screen.width/0.86)}, initial-scale=0.86, minimum-scale=0.65, maximum-scale=2, user-scalable=yes`);
 });
});
if (!globalThis.crypto.randomUUID) Object.defineProperty(globalThis.crypto, 'randomUUID', {value: () => {
 const b = crypto.getRandomValues(new Uint8Array(16)); b[6]=(b[6]&15)|64; b[8]=(b[8]&63)|128;
 const h=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
 return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}});
if (!crypto.subtle) Object.defineProperty(crypto,'subtle',{value:{digest:async(algorithm,data)=>{
 const name=typeof algorithm==='string'?algorithm:algorithm?.name;
 if(String(name).toUpperCase().replace('-','')!=='SHA256')throw new DOMException('Only SHA-256 is supported','NotSupportedError');
 const input=data instanceof ArrayBuffer?new Uint8Array(data):new Uint8Array(data.buffer,data.byteOffset,data.byteLength);
 return sha256(input).buffer;
}}});
if (!AbortSignal.timeout) AbortSignal.timeout=(ms)=>{const c=new AbortController();setTimeout(()=>c.abort(new DOMException('Timed out','TimeoutError')),ms);return c.signal;};
if (!AbortSignal.any) AbortSignal.any=(signals)=>{const c=new AbortController();for(const s of signals){if(s.aborted){c.abort(s.reason);break;}s.addEventListener('abort',()=>c.abort(s.reason),{once:true,signal:c.signal});}return c.signal;};
if (!navigator.clipboard?.writeText) Object.defineProperty(navigator,'clipboard',{value:{writeText:async(value)=>{
 const previous=document.activeElement, area=document.createElement('textarea');area.value=String(value);
 area.style.cssText='position:fixed;top:0;left:-10000px';document.body.appendChild(area);area.select();
 const ok=document.execCommand('copy');area.remove();previous?.focus?.();
 if(!ok)throw new DOMException('Copy unavailable','NotAllowedError');
}}});
