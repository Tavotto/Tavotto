// /ui.js is client.toString(); any bundler helper it references is not shipped with it.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {deploymentPlan} from '../scripts/deployment-plan.mjs';
import fs from 'node:fs';
async function servedScript(keepNames){
  const out=await build({entryPoints:[new URL('../src/native/ui.ts',import.meta.url).pathname],bundle:true,format:'esm',platform:'neutral',write:false,keepNames,logLevel:'silent'});
  const mod=await import('data:text/javascript;base64,'+Buffer.from(out.outputFiles[0].text).toString('base64'));
  return mod.uiScript();
}
test('served /ui.js references no bundler helper when keep_names is off',async()=>{
  const script=await servedScript(false);
  assert.doesNotMatch(script,/\b__name\(/);
  assert.doesNotThrow(()=>new Function(script));
});
test('keep_names on injects __name into the served script (why manifests must turn it off)',async()=>{
  assert.match(await servedScript(true),/\b__name\(/);
});
for(const [file,profile] of [['wrangler.disabled.json','disabled'],['staging/wrangler.staging.json','staging']])
  test(`${profile} manifest must set keep_names:false`,()=>{
    const m=JSON.parse(fs.readFileSync(new URL('../deployment/'+file,import.meta.url)));
    assert.equal(m.keep_names,false);deploymentPlan(m,{profile});
    for(const value of [undefined,true]){const c=structuredClone(m);if(value===undefined)delete c.keep_names;else c.keep_names=value;
      assert.throws(()=>deploymentPlan(c,{profile}),e=>e.message==='keep_names_false_required');}
  });
