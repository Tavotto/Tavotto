// Staging rehearsal inputs: offline shape, config and seed checks only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {test} from 'node:test';
import {deploymentPlan,STAGING_ORIGIN} from '../scripts/deployment-plan.mjs';
import {stagingRebuildSQL,stagingSQL} from '../scripts/staging-d1.mjs';
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
  assert.throws(()=>deploymentPlan(manifest(),{profile:'production'}),e=>e.message==='production_route_or_cron_invalid');
  assert.throws(()=>deploymentPlan(manifest(),{profile:'prod'}),e=>e.message==='profile_invalid');
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
test('served agreement asset matches the pinned individual digest and is a marked staging copy of the real text',()=>{
  const bytes=read('assets/CLA_INDIVIDUAL.md'),text=bytes.toString('utf8');
  assert.equal(createHash('sha256').update(bytes).digest('hex'),config().policy.agreements.individual.sha256);
  assert.match(text,/^> \*\*STAGING COPY — NO LEGAL EFFECT\.\*\*/);assert.match(text,/has no legal effect of any kind/);
  // Everything after the staging notice is the real agreement, byte for byte.
  const real=fs.readFileSync(new URL('../../../docs/legal/CLA_INDIVIDUAL.md',import.meta.url),'utf8');
  assert.equal(text.slice(text.indexOf('\n\n')+2),real);
  assert.notEqual(config().policy.agreements.individual.sha256,createHash('sha256').update(real).digest('hex'),'a staging signature never carries the real digest');
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
test('staging rebuild SQL clears immutable rows and returns the database to readiness',()=>{
  const sql=new DatabaseSync(':memory:');sql.exec('PRAGMA foreign_keys=ON;');sql.exec(stagingSQL(config()));
  const objects=()=>sql.prepare("SELECT type,count(*) n FROM sqlite_master GROUP BY type ORDER BY type").all().map(r=>`${r.type}:${r.n}`).join(' ');
  const fresh=objects(),a=sql.prepare('SELECT kind,version,digest FROM cla_native_v2_agreements LIMIT 1').get();
  sql.prepare('INSERT INTO cla_native_v2_packets VALUES (?,?,?,?,?,?,?)').run('p1',261183172,a.kind,a.version,a.digest,1,2);
  sql.prepare("INSERT INTO cla_native_v2_eligibility_blocks VALUES ('p1',261183172,1,'party_requested_review')").run();
  assert.throws(()=>sql.exec('DELETE FROM cla_native_v2_eligibility_blocks'),/immutable/);
  sql.exec(stagingRebuildSQL(config()));
  assert.equal(sql.prepare('SELECT count(*) n FROM cla_native_v2_eligibility_blocks').get().n,0);
  assert.equal(sql.prepare('SELECT count(*) n FROM cla_native_v2_packets').get().n,0);
  assert.equal(sql.prepare('SELECT holder FROM cla_native_v2_authority').get().holder,config().holderId);
  assert.equal(objects(),fresh);
  assert.throws(()=>stagingRebuildSQL({...config(),origin:'https://cla.tavotto.com'}),e=>e.message==='staging_origin_required');
});
test('staging CI config mirrors the service identity and passes the trusted consumer validation',async()=>{
  const {readOnly}=await import('../src/ci/consumer.ts');
  const ci=JSON.parse(read('ci.json')),c=config();
  for(const k of ['repository','repositoryId','origin','appId','appSlug','checkName'])assert.deepEqual(ci[k],c[k],k);
  assert.equal(ci.enabled,true);assert.deepEqual(ci.wait,{enabled:false});
  const seen=[];const transport=async url=>{seen.push(url);return new Response('{}',{status:200,headers:{'content-type':'application/json'}});};
  await readOnly(ci,'token',transport)('https://api.github.com/repos/'+ci.repository,'github');
  assert.deepEqual(seen,['https://api.github.com/repos/'+ci.repository]);
  await assert.rejects(readOnly(ci,'token',transport)('https://api.github.com/repos/erwanjun/other','github'));
  await assert.rejects(readOnly({...ci,origin:'http://cla-staging.tavotto.com'},'token',transport)('https://api.github.com/repos/'+ci.repository,'github'),
    e=>e.code==='ci_configuration_invalid');
});
