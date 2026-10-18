import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';
import {admitHead,verifyHeadOne,AUTOMATIC_CRON} from '../src/native/automatic.ts';
import {bytes} from '../src/core/crypto.ts';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';import {InvocationBudget} from '../src/native/budget.ts';
import worker from '../src/native/worker.ts';
const secret='SYNTHETIC WEBHOOK SECRET TEST ONLY 1234567890';let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const enable=f=>{f.c.githubEnabled=true;f.c.automaticVerificationEnabled=true;};
const event=f=>({action:'synchronize',number:9,repository:{id:707,full_name:f.c.repository},installation:{id:2},pull_request:{number:9,state:'open',head:{sha:f.gh.head},base:{repo:{id:707},ref:'main'}}});
async function request(data){const raw=bytes(JSON.stringify(data)),key=await crypto.subtle.importKey('raw',bytes(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const sig=new Uint8Array(await crypto.subtle.sign('HMAC',key,raw));return {raw,signature:'sha256='+[...sig].map(b=>b.toString(16).padStart(2,'0')).join('')};}
async function admit(f,data=event(f),delivery='synthetic-delivery-1'){const r=await request(data);await admitHead(f.s,r.raw,r.signature,delivery,'pull_request',secret);}
test('automatic verification defaults off before parsing or DB reads',()=>iso(async f=>{
  await assert.rejects(admitHead(f.s,new Uint8Array(),'', 'id','pull_request',secret),e=>e.code==='automatic_verification_disabled');assert.equal(await verifyHeadOne(f.s),'automatic_verification_disabled');
}));
test('signed webhook duplicates are durable and produce one fresh observation without publishing',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enable(f);await admit(f);await admit(f);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM head_requests').first()).n,1);assert.equal(await verifyHeadOne(f.s),'head_verified_without_publication');
  const row=await f.store.stmt('SELECT * FROM head_requests').first(),observation=JSON.parse(row.result);assert.equal(observation.eligible,true);
  assert.equal(observation.proof.head_sha,f.gh.head);assert.equal(observation.agreement_sha256,f.grant.agreement_sha256);assert.equal(observation.publication,'not_attempted');
  await admit(f);assert.equal(await verifyHeadOne(f.s),'idle');assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('old head event rejects before enqueue and queued old head becomes manual with empty result',()=>iso(async f=>{
  enable(f);const old=event(f);await admit(f);f.gh.head='e'.repeat(40);await assert.rejects(admit(f,old,'old-delivery'));
  assert.equal(await verifyHeadOne(f.s),'head_requires_manual_review');assert.equal((await f.store.stmt('SELECT result FROM head_requests').first()).result,'');
  await admit(f,event(f),'new-delivery');assert.equal(await verifyHeadOne(f.s),'head_verified_without_publication');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM head_requests').first()).n,2);
}));
test('added unsigned contributor makes a new current-head observation negative',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enable(f);await admit(f);f.gh.extra=[{sha:'f'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic'}];
  await verifyHeadOne(f.s);const result=JSON.parse((await f.store.stmt('SELECT result FROM head_requests').first()).result);assert.equal(result.eligible,false);assert.equal(result.proof.contributors.length,2);
}));
test('same delivery with changed authenticated body is rejected transactionally',()=>iso(async f=>{
  enable(f);await admit(f);await assert.rejects(admit(f,{...event(f),action:'reopened'}));assert.equal((await f.store.stmt('SELECT count(*) n FROM head_deliveries').first()).n,1);
}));
for(const mode of ['bad-hmac','repo','installation','action','base','oversized'])test('webhook rejects '+mode,()=>iso(async f=>{
  enable(f);const data=event(f);if(mode==='repo')data.repository.id=999;if(mode==='installation')data.installation.id=999;if(mode==='action')data.action='closed';if(mode==='base')data.pull_request.base.ref='other';
  const r=await request(data);await assert.rejects(admitHead(f.s,mode==='oversized'?new Uint8Array(262145):r.raw,mode==='bad-hmac'?'sha256='+'0'.repeat(64):r.signature,'delivery','pull_request',secret));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM head_requests').first()).n,0);
}));
test('concurrent claim executes one request once',()=>iso(async f=>{
  enable(f);await admit(f);const results=await Promise.all([verifyHeadOne(f.s),verifyHeadOne(f.s)]);assert.equal(results.filter(r=>r==='head_verified_without_publication').length,1);
  assert.equal(results.filter(r=>r==='idle').length,1);
}));
test('expired running request becomes manual and is never replayed',()=>iso(async f=>{
  enable(f);await admit(f);await f.store.stmt("UPDATE head_requests SET status='running',attempt='synthetic-crash',lease_until=?",f.now()-1).run();assert.equal(await verifyHeadOne(f.s),'idle');
  await admit(f,event(f),'another-delivery');assert.equal(await verifyHeadOne(f.s),'idle');assert.equal((await f.store.stmt('SELECT status FROM head_requests').first()).status,'manual');
}));
test('revocation during binding recheck cannot commit a successful observation',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enable(f);await admit(f);const original=f.s.freshBinding.bind(f.s);let count=0;
  f.s.freshBinding=async pr=>{const bound=await original(pr);if(++count===2)await f.s.delegation.append(await f.control('revoke'));return bound;};
  assert.equal(await verifyHeadOne(f.s),'head_requires_manual_review');assert.equal((await f.store.stmt('SELECT result FROM head_requests').first()).result,'');
}));
test('new head observation meters all database operations within unchanged work budget',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enable(f);await admit(f);const budget=new InvocationBudget(),s=new LiveService(f.c,new LiveStore(f.db,budget,f.c),f.gh,f.key);
  assert.equal(await verifyHeadOne(s),'head_verified_without_publication');assert.equal(budget.snapshot().sql,13);
}));
test('automatic and recovery schedules are separate invocations with default disabled routes',async()=>{
  let touched=0;const env={DB:{prepare(){touched++;throw Error('DB_FORBIDDEN');}}};await worker.scheduled({cron:AUTOMATIC_CRON},env);await worker.scheduled({cron:'* * * * *'},env);assert.equal(touched,0);
  await worker.scheduled({cron:AUTOMATIC_CRON},{...env,CLA_BACKGROUND_ENABLED:'true'});assert.equal(touched,0);
});
test('automatic tests never publish or fetch outbound',()=>assert.equal(outbound,0));
test('base changes during final read never commits observation',()=>iso(async f=>{
  enable(f);await admit(f);const original=f.s.freshBinding.bind(f.s);let count=0;
  f.s.freshBinding=async pr=>{const bound=await original(pr);return ++count===2?{...bound,base_sha:'f'.repeat(40)}:bound;};
  assert.equal(await verifyHeadOne(f.s),'head_requires_manual_review');assert.equal((await f.store.stmt('SELECT result FROM head_requests').first()).result,'');
}));
test('signing after an initial negative observation queues a new revision and clears old result',()=>iso(async f=>{
  enable(f);await admit(f);await verifyHeadOne(f.s);assert.equal(JSON.parse((await f.store.stmt('SELECT result FROM head_requests').first()).result).eligible,false);
  const p=await f.create();await f.sign(p);await f.sign(p,'holder');
  const pending=await f.store.stmt('SELECT * FROM head_requests').first();assert.equal(pending.status,'pending');assert.equal(pending.result,'');
  assert.equal(await verifyHeadOne(f.s),'head_verified_without_publication');assert.equal(JSON.parse((await f.store.stmt('SELECT result FROM head_requests').first()).result).eligible,true);
}));
test('head request admission rejects the 101st distinct head',()=>iso(async f=>{
  enable(f);const insert=f.sql.prepare("INSERT INTO cla_native_v2_head_requests VALUES(?,?,?,'manual','',0,?,'')");
  for(let i=0;i<100;i++)insert.run('synthetic-head-'+i,9,String(i).padStart(40,'0'),f.now());
  await assert.rejects(admit(f));assert.equal((await f.store.stmt('SELECT count(*) n FROM head_requests').first()).n,100);
}));
test('delivery admission rejects the 1001st record while exact duplicate remains idempotent',()=>iso(async f=>{
  enable(f);await admit(f);const existing=await f.store.stmt('SELECT * FROM head_deliveries').first(),insert=f.sql.prepare('INSERT INTO cla_native_v2_head_deliveries VALUES(?,?,?,?)');
  for(let i=1;i<1000;i++)insert.run('synthetic-history-'+i,existing.body_hash,existing.request_key,f.now());
  await assert.rejects(admit(f,event(f),'new-over-capacity'));await admit(f);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM head_deliveries').first()).n,1000);
}));
