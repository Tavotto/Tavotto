import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {iso} from './delegation-fixture.mjs';
import {config} from '../src/native/worker.ts';import {validateLive} from '../src/native/config.ts';
import {deploymentPlan} from '../scripts/deployment-plan.mjs';
const template=JSON.parse(fs.readFileSync(new URL('../deployment/wrangler.disabled.json',import.meta.url)));
globalThis.fetch=async()=>{throw Error('OUTBOUND_FORBIDDEN');};
test('slot JSON cannot enable the runtime capability without the exact environment switch',()=>iso(async f=>{
 const env={CLA_CONFIG:JSON.stringify({...f.c,confirmationSlotEnabled:true})};
 for(const value of [undefined,'false','TRUE','1',' true '])assert.equal(config({...env,CLA_CONFIRMATION_SLOT_ENABLED:value}).confirmationSlotEnabled,false);
 assert.equal(config({...env,CLA_CONFIRMATION_SLOT_ENABLED:'true'}).confirmationSlotEnabled,true);
}));
for(const missing of ['backgroundEnabled','checkPublishingEnabled'])test('slot activation refuses missing independent recovery capability '+missing,()=>iso(async f=>{
 const c={...f.c,githubEnabled:true,confirmationSlotEnabled:true,backgroundEnabled:true,checkPublishingEnabled:true};
 assert.doesNotThrow(()=>validateLive(c));c[missing]=false;
 assert.throws(()=>validateLive(c),e=>e.code==='confirmation_slot_recovery_required');
}));
test('disabled plan refuses an enabled or missing slot switch',()=>{
 assert.equal(deploymentPlan(template).ready_to_deploy,false);
 const enabled=structuredClone(template);enabled.vars.CLA_CONFIRMATION_SLOT_ENABLED='true';assert.throws(()=>deploymentPlan(enabled));
 const missing=structuredClone(template);delete missing.vars.CLA_CONFIRMATION_SLOT_ENABLED;assert.throws(()=>deploymentPlan(missing));
});
