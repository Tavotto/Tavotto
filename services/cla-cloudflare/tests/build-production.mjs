import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {buildPdfClient} from '../scripts/build-pdf-client.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const result=await build({absWorkingDir:root,entryPoints:['src/entry.ts'],bundle:true,format:'esm',platform:'browser',write:false,metafile:true});
const inputs=Object.keys(result.metafile.inputs);
for(const input of inputs){assert.ok(input.startsWith('src/'));assert.ok(input.endsWith('.ts'));assert.ok(!input.split('/').some(s=>/^(tests?|fixtures?|faults?)$/.test(s)));}
await mkdir(path.join(root,'dist'),{recursive:true});
await writeFile(path.join(root,'dist/worker.mjs'),result.outputFiles[0].contents);
await writeFile(path.join(root,'dist/build.json'),JSON.stringify({inputs,bytes:result.outputFiles[0].contents.length,deployPerformed:false},null,2)+'\n');
console.log(`Built ${inputs.length} production inputs; no deployment performed.`);
// The browser-side signed-PDF builder is a separate bundle (never part of the Worker): check it still bundles
// from src/pdf plus the pinned pdf-lib packages only, with no bundler helper left over.
const client=await buildPdfClient();
for(const input of client.inputs)assert.ok(/^src\/pdf\/[a-z]+\.ts$/.test(input)||input.startsWith('node_modules/'),'unexpected client input '+input);
assert.ok(!/\b__name\(/.test(client.code));
assert.ok(!inputs.some(i=>i.startsWith('src/pdf/')||i.startsWith('node_modules/')),'the Worker bundle must not contain the PDF builder or any package');
await writeFile(path.join(root,'dist/cla-pdf.js'),client.code);
console.log(`Built cla-pdf.js (${client.code.length} bytes, ${client.inputs.length} inputs); not served until staged into the assets directory.`);
