// Staging rehearsal inputs: offline shape, config and seed checks only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {test} from 'node:test';
import {deploymentPlan,STAGING_ORIGIN} from '../scripts/deployment-plan.mjs';
import {stagingSQL} from '../scripts/staging-d1.mjs';
import {validateLive} from '../src/native/config.ts';
import {LiveStore} from '../src/native/store.ts';
const read=path=>fs.readFileSync(new URL('../deployment/staging/'+path,import.meta.url));
const manifest=()=>JSON.parse(read('wrangler.staging.json'));
const config=()=>JSON.parse(read('config.json'));
const rejects=(m,code)=>assert.throws(()=>deploymentPlan(m,{profile:'staging'}),e=>e.message===code);

test('staging manifest passes only the staging profile',()=>{
  const plan=deploymentPlan(manifest(),{profile:'staging'});
  assert.equal(plan.profile,'staging');assert.equal(plan.ready_to_deploy,false);assert.equal(plan.network_operations,0);
  assert.throws(()=>deploymentPlan(manifest()),e=>e.message==='disabled_manifest_required');
  assert.throws(()=>deploymentPlan(manifest(),{profile:'production'}),e=>e.message==='profile_invalid');
});
test('staging profile keeps one exact custom-domain route and known crons',()=>{
  let m=manifest();m.routes.push({pattern:'cla.tavotto.com',custom_domain:true});rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.routes=[{pattern:'cla.tavotto.com',custom_domain:true}];rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.routes[0].zone_name='tavotto.com';rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.name='tavotto-cla';rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.triggers.crons=['* * * * *'];rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.triggers.crons=['*/2 * * * *','*/2 * * * *'];rejects(m,'staging_route_or_cron_invalid');
  m=manifest();m.triggers.crons=['*/2 * * * *','7-59/15 * * * *'];deploymentPlan(m,{profile:'staging'});
});
test('staging file never turns a capability on and keeps workers.dev off',()=>{
  let m=manifest();m.vars.CLA_SIGNING_ENABLED='true';assert.throws(()=>deploymentPlan(m,{profile:'staging'}),e=>e.message==='disabled_manifest_required');
  m=manifest();m.workers_dev=true;assert.throws(()=>deploymentPlan(m,{profile:'staging'}),e=>e.message==='disabled_manifest_required');
  m=manifest();m.vars.CLA_CONFIG=JSON.stringify({...config(),origin:'https://cla.tavotto.com'});rejects(m,'staging_origin_mismatch');
});
test('embedded CLA_CONFIG is config.json and validates with S4 capabilities on',()=>{
  assert.deepEqual(JSON.parse(manifest().vars.CLA_CONFIG),config());
  const c=config();assert.equal(c.origin,STAGING_ORIGIN);
  validateLive({...c,githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false});
  validateLive({...c,githubEnabled:true,oauthEnabled:true,signingEnabled:true,checkPublishingEnabled:true,backgroundEnabled:true,
    automaticVerificationEnabled:true,automaticPublishingEnabled:true,confirmationSlotEnabled:true});
});
test('served agreement asset matches the pinned individual digest and is a staging text',()=>{
  const bytes=read('assets/CLA_INDIVIDUAL.md');
  assert.equal(createHash('sha256').update(bytes).digest('hex'),config().policy.agreements.individual.sha256);
  assert.match(bytes.toString('utf8'),/^# STAGING TEST TEXT/);assert.match(bytes.toString('utf8'),/no legal effect/);
});
test('generated staging SQL seeds a fresh database to readiness',async()=>{
  const sql=new DatabaseSync(':memory:');sql.exec(stagingSQL(config()));
  const prepare=(q,args=[])=>{const exec=()=>{const s=sql.prepare(q),r=s.columns().length?s.all(...args):s.run(...args);return {success:true,results:Array.isArray(r)?r:[]};};
    return {bind:(...a)=>prepare(q,a),first:async()=>exec().results[0]??null,all:async()=>exec(),run:async()=>exec(),exec};};
  const db={prepare,batch:async ss=>ss.map(s=>s.exec())};
  const live={...config(),githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false};
  await new LiveStore(db,undefined,live).assertReady();
  assert.throws(()=>stagingSQL({...config(),origin:'https://cla.tavotto.com'}),e=>e.message==='staging_origin_required');
  // A config whose holder differs from the seeded row fails closed.
  await assert.rejects(new LiveStore(db,undefined,{...live,holderId:88193521,exemptionIds:{erwanjun:88193521}}).assertReady(),
    e=>e.code==='database_configuration_uninitialized_or_mismatched');
});
