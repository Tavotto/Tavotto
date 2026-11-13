// Production initialization and manifest profile: offline checks only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {test} from 'node:test';
import {deploymentPlan,PRODUCTION_ORIGIN} from '../scripts/deployment-plan.mjs';
import {expectedObjects,productionBootstrap,productionSchemaSQL} from '../scripts/production-d1.mjs';
import {createHash} from 'node:crypto';
import {PDFDocument} from 'pdf-lib';
import {validateLive} from '../src/native/config.ts';
import {HASH_KEYWORD} from '../scripts/render-agreement-pdf.mjs';
const staging=path=>JSON.parse(fs.readFileSync(new URL('../deployment/staging/'+path,import.meta.url)));
// Synthetic production identity derived from the staging rehearsal; no real production id is needed here.
const config=()=>({...staging('config.json'),origin:PRODUCTION_ORIGIN});
const manifest=()=>{const m=staging('wrangler.staging.json');
  return {...m,name:'tavotto-cla',routes:[{pattern:'cla.tavotto.com',custom_domain:true}],triggers:{crons:[]},
    vars:{...m.vars,CLA_CONFIG:JSON.stringify(config())},d1_databases:[{...m.d1_databases[0],database_name:'tavotto-cla'}]};};

// D1-shaped adapter over node:sqlite whose batch is one transaction, as D1 documents.
function d1(sql){
  const prepare=(q,args=[])=>{const exec=()=>{const s=sql.prepare(q),r=s.columns().length?s.all(...args):s.run(...args);return {success:true,results:Array.isArray(r)?r:[]};};
    return {bind:(...a)=>prepare(q,a),first:async()=>exec().results[0]??null,all:async()=>exec(),run:async()=>exec(),exec};};
  return {prepare,batch:async ss=>{sql.exec('BEGIN');try{const out=ss.map(s=>s.exec());sql.exec('COMMIT');return out;}catch(e){sql.exec('ROLLBACK');throw e;}}};
}
const fresh=()=>{const sql=new DatabaseSync(':memory:');sql.exec('PRAGMA foreign_keys=ON;');sql.exec(productionSchemaSQL(config()));return sql;};

test('production profile admits exactly cla.tavotto.com, no crons, and the tavotto-cla database',()=>{
  const plan=deploymentPlan(manifest(),{profile:'production'});
  assert.equal(plan.profile,'production');assert.equal(plan.ready_to_deploy,false);assert.equal(plan.network_operations,0);
  assert.deepEqual(plan.required_secret_names,['CLA_EVIDENCE_KEY','CLA_CLIENT_SECRET','CLA_APP_PKCS8']);
  assert.deepEqual(plan.proposed_separate_crons,{});
  const rejects=(change,code)=>{const m=manifest();change(m);assert.throws(()=>deploymentPlan(m,{profile:'production'}),e=>e.message===code);};
  rejects(m=>m.triggers.crons=['*/2 * * * *'],'production_route_or_cron_invalid');
  rejects(m=>m.routes=[{pattern:'cla-staging.tavotto.com',custom_domain:true}],'production_route_or_cron_invalid');
  rejects(m=>m.routes.push({pattern:'cla.tavotto.com/*',zone_name:'tavotto.com'}),'production_route_or_cron_invalid');
  rejects(m=>m.name='tavotto-cla-staging','production_route_or_cron_invalid');
  rejects(m=>m.vars.CLA_CONFIG=JSON.stringify(staging('config.json')),'production_origin_mismatch');
  rejects(m=>m.d1_databases[0].database_name='tavotto-cla-staging','production_database_invalid');
  rejects(m=>m.vars.CLA_CI_VERIFICATION_ENABLED='true','disabled_manifest_required');
  rejects(m=>m.workers_dev=true,'disabled_manifest_required');
  // The production manifest never passes the staging or disabled profiles.
  assert.throws(()=>deploymentPlan(manifest(),{profile:'staging'}));assert.throws(()=>deploymentPlan(manifest()));
});
test('schema SQL carries no seed row and is refused for a non-production origin',()=>{
  const sql=productionSchemaSQL(config());
  assert.doesNotMatch(sql,/INTO cla_native_v2_(authority|agreements)\b/);
  assert.throws(()=>productionSchemaSQL(staging('config.json')),e=>e.message==='production_origin_required');
  const db=fresh();
  assert.equal(db.prepare('SELECT count(*) n FROM cla_native_v2_authority').get().n,0);
  assert.equal(expectedObjects().filter(o=>o.startsWith('trigger:')).length,41);
});
test('bootstrap seeds a fresh schema to readiness in one batch and a repeat is a no-op',async()=>{
  const sql=fresh(),db=d1(sql);
  let batches=0;const counted={...db,batch:async ss=>{batches++;return db.batch(ss);}};
  assert.deepEqual(await productionBootstrap(counted,config()),{authority:1,holder:88193520,agreements:2,triggers:41});
  assert.equal(batches,1);
  assert.deepEqual(await productionBootstrap(counted,config()),{authority:1,holder:88193520,agreements:2,triggers:41});
});
test('a mismatched holder rolls the whole seed back',async()=>{
  const sql=fresh(),db=d1(sql);await productionBootstrap(db,config());
  await assert.rejects(productionBootstrap(db,{...config(),holderId:88193521,exemptionIds:{erwanjun:88193521}}));
  assert.equal(sql.prepare('SELECT holder FROM cla_native_v2_authority').get().holder,88193520);
  assert.equal(sql.prepare('SELECT count(*) n FROM cla_native_v2_agreements').get().n,2);
  // Nothing half-applied: a batch that fails on its last statement leaves a fresh database with no seed row at all.
  const other=fresh(),base=d1(other);
  const failing={...base,batch:ss=>base.batch([...ss,base.prepare('SELECT * FROM cla_native_v2_missing')])};
  await assert.rejects(productionBootstrap(failing,config()));
  assert.equal(other.prepare('SELECT count(*) n FROM cla_native_v2_authority').get().n,0);
  assert.equal(other.prepare('SELECT count(*) n FROM cla_native_v2_agreements').get().n,0);
});
test('bootstrap refuses a schema that differs from this checkout or a database with data',async()=>{
  const missing=fresh();missing.exec('DROP TRIGGER cla_native_v2_slot_policy_update');
  await assert.rejects(productionBootstrap(d1(missing),config()),e=>e.message==='production_schema_mismatch');
  const extra=fresh();extra.exec('CREATE TABLE cla_native_v2_extra(x)');
  await assert.rejects(productionBootstrap(d1(extra),config()),e=>e.message==='production_schema_mismatch');
  const empty=new DatabaseSync(':memory:');
  await assert.rejects(productionBootstrap(d1(empty),config()),e=>e.message==='production_schema_mismatch');
  const used=fresh();await productionBootstrap(d1(used),config());
  const a=used.prepare('SELECT kind,version,digest FROM cla_native_v2_agreements LIMIT 1').get();
  used.prepare('INSERT INTO cla_native_v2_packets VALUES (?,?,?,?,?,?,?)').run('p1',261183172,a.kind,a.version,a.digest,1,2);
  await assert.rejects(productionBootstrap(d1(used),config()),e=>e.message==='production_database_not_fresh');
  await assert.rejects(productionBootstrap(d1(fresh()),staging('config.json')),e=>e.message==='production_origin_required');
});

// ── the committed production inputs (deployment/production/) ──
const prod=path=>new URL('../deployment/production/'+path,import.meta.url);
const prodConfig=()=>JSON.parse(fs.readFileSync(prod('config.json'),'utf8'));
const prodManifest=()=>JSON.parse(fs.readFileSync(prod('wrangler.production.json'),'utf8'));
const repoPolicy=()=>JSON.parse(fs.readFileSync(new URL('../../../.github/cla-policy.json',import.meta.url),'utf8'));
const sha=b=>createHash('sha256').update(b).digest('hex');

test('production manifest passes the production profile and embeds config.json verbatim',()=>{
  const m=prodManifest();deploymentPlan(m,{profile:'production'});
  assert.deepEqual(JSON.parse(m.vars.CLA_CONFIG),prodConfig());
  assert.equal(m.d1_databases[0].database_id,'1e6558fd-8ad9-4700-ab66-4fcf2e861ba2');
  const staging=JSON.parse(fs.readFileSync(new URL('../deployment/staging/wrangler.staging.json',import.meta.url),'utf8'));
  for(const r of m.ratelimits)assert.ok(!staging.ratelimits.some(s=>s.namespace_id===r.namespace_id),'production limiters never share staging counters');
});
test('production config carries the default-branch policy exactly — the service refuses anything else (trusted_policy_changed)',()=>{
  const c=prodConfig(),p=repoPolicy();
  assert.deepEqual(c.policy,{agreements:p.agreements,exemptions:p.exemptions,provider:p.provider});
  assert.equal(c.origin,p.authority_origin);assert.equal(c.appId,p.provider.app_id);assert.equal(c.appSlug,p.provider.app_slug);
  assert.equal(c.checkName,p.provider.check_name);
  assert.deepEqual(c.exemptionIds,Object.fromEntries(p.exemptions.map(e=>[e.login,e.github_id])));
  assert.equal(c.exemptionIds.mvanhorn,455140,'ADR 0183: the countersigned paper signer stays an exemption');
  for(const flags of [{},{githubEnabled:true,oauthEnabled:true,signingEnabled:true,ciVerificationEnabled:true,mergePrefixAssessmentEnabled:true,mergePrefixReadSessionEnabled:true}])
    validateLive({...c,githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false,...flags});
});
test('production serves the real agreement bytes and a PDF rendered from them',async()=>{
  const md=fs.readFileSync(prod('assets/CLA_INDIVIDUAL.md'));
  assert.deepEqual(md,fs.readFileSync(new URL('../../../docs/legal/CLA_INDIVIDUAL.md',import.meta.url)),'byte copy of docs/legal/CLA_INDIVIDUAL.md');
  assert.equal(sha(md),prodConfig().policy.agreements.individual.sha256);
  assert.doesNotMatch(md.toString('utf8'),/STAGING/);
  const doc=await PDFDocument.load(fs.readFileSync(prod('assets/CLA_INDIVIDUAL.pdf')));
  assert.deepEqual((doc.getKeywords()??'').split(/\s+/).filter(x=>x.startsWith(HASH_KEYWORD)),[HASH_KEYWORD+sha(md)],'PDF is stale: rerun npm run render:agreement-pdf:production');
  const meta=JSON.parse((doc.getSubject()??'').replace(/^tavotto-agreement-meta:/,''));
  assert.equal(meta.publishedUrl,'https://github.com/Tavotto/Tavotto/blob/main/docs/legal/CLA_INDIVIDUAL.md');
  for(const p of doc.getPages())assert.deepEqual([p.getWidth(),p.getHeight()],[612,792],'US Letter');
});
