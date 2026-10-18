import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const root=fileURLToPath(new URL('../',import.meta.url));
const result=await build({absWorkingDir:root,entryPoints:['src/entry.ts'],bundle:true,format:'esm',platform:'browser',write:false,metafile:true});
const inputs=Object.keys(result.metafile.inputs);
for(const input of inputs){assert.ok(input.startsWith('src/'));assert.ok(input.endsWith('.ts'));assert.ok(!input.split('/').some(s=>/^(tests?|fixtures?|faults?)$/.test(s)));}
await mkdir(path.join(root,'dist'),{recursive:true});
await writeFile(path.join(root,'dist/worker.mjs'),result.outputFiles[0].contents);
await writeFile(path.join(root,'dist/build.json'),JSON.stringify({inputs,bytes:result.outputFiles[0].contents.length,deployPerformed:false},null,2)+'\n');
console.log(`Built ${inputs.length} production inputs; no deployment performed.`);
