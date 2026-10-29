import { rolldown } from '../var/android-build/compat/node_modules/rolldown/dist/index.mjs';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const dir=fileURLToPath(new URL('.',import.meta.url));
const bundle=await rolldown({input:dir+'compat-entry.js'});
await bundle.write({file:dir+'assets/compat.js',format:'iife',minify:true});await bundle.close();
await fs.copyFile(dir+'../var/android-build/compat/node_modules/@noble/hashes/LICENSE',dir+'assets/THIRD_PARTY_LICENSE.txt');
console.log('COMPAT_BUNDLE_READY');
