import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';import {verifyHeadOne} from '../src/native/automatic.ts';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';import {InvocationBudget} from '../src/native/budget.ts';
import {canonical} from '../src/core/crypto.ts';import {GitHub} from '../src/native/github.ts';import {config,default as worker} from '../src/native/worker.ts';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
async function pending(f,head=f.gh.head){
  const key=`707:9:${head}`;await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'pending','',0,?,'')",key,9,head,f.now()).run();return key;
}
function enabled(f){Object.assign(f.c,{githubEnabled:true,automaticVerificationEnabled:true,checkPublishingEnabled:true,backgroundEnabled:true,automaticPublishingEnabled:true});}
function metered(f,publishNew){const budget=new InvocationBudget(),gh={...f.gh,budget,publishNew},s=new LiveService(f.c,new LiveStore(f.db,budget,f.c),gh,f.key);return {s,budget};}
test('new-head publication cannot be enabled by JSON config or without recovery flags',()=>iso(async f=>{
  assert.equal(config({CLA_CONFIG:JSON.stringify({...f.c,automaticPublishingEnabled:true})}).automaticPublishingEnabled,false);
  assert.throws(()=>new LiveService({...f.c,automaticPublishingEnabled:true},f.store,f.gh,f.key),e=>e.code==='automatic_publication_recovery_required');
}));
test('new-head issued journal precedes ambiguous synthetic send; unknown never replays or confirms',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enabled(f);const key=await pending(f);let sends=0;
  const publish=async(check,beforeSend)=>{const attempt=await beforeSend();sends++;
    const row=await f.store.stmt('SELECT * FROM publication_attempts WHERE id=?',attempt).first();assert.equal(row.phase,'issued');assert.equal(row.head_sha,f.gh.head);assert.equal(row.proof_text,check.text);
    throw Error('SYNTHETIC_AMBIGUOUS_OUTCOME');};
  const first=metered(f,publish);assert.equal(await verifyHeadOne(first.s),'automatic_publication_unknown_no_replay');assert.equal(first.budget.snapshot().sql,20);
  const row=await f.store.stmt('SELECT * FROM publication_attempts').first();assert.equal(row.phase,'unknown');assert.equal(row.run_id,null);
  assert.equal((await f.store.stmt('SELECT status FROM head_requests WHERE request_key=?',key).first()).status,'dispatched');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM outbox').first()).n,1);
  const next=metered(f,publish);assert.equal(await verifyHeadOne(next.s),'idle');assert.equal(sends,1);
  await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));
  assert.equal((await f.store.stmt('SELECT status FROM head_requests WHERE request_key=?',key).first()).status,'dispatched');assert.equal(await verifyHeadOne(metered(f,publish).s),'idle');
}));
test('unresolved older head blocks another new-head POST for the whole target',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enabled(f);await pending(f);let sends=0;
  const publish=async(_check,beforeSend)=>{await beforeSend();sends++;throw Error('SYNTHETIC_UNKNOWN');};
  await verifyHeadOne(metered(f,publish).s);f.gh.head='e'.repeat(40);await pending(f);
  assert.equal(await verifyHeadOne(metered(f,publish).s),'head_requires_manual_review');assert.equal(sends,1);assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,1);
}));
test('two-person new-head dispatch rejects unconfirmable capacity before any journal or send',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enabled(f);f.gh.extra=[{sha:'f'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic'}];await pending(f);let called=0;
  const run=metered(f,async()=>{called++;throw Error('SEND_FORBIDDEN');});assert.equal(await verifyHeadOne(run.s),'head_requires_manual_review');assert.equal(called,0);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('revision change in final new-head binding prevents issued record and synthetic send',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);enabled(f);await pending(f);let sends=0;
  const run=metered(f,async(_check,beforeSend)=>{await f.s.delegation.append(await f.control('revoke'));await beforeSend();sends++;throw Error('SEND_FORBIDDEN');});
  assert.equal(await verifyHeadOne(run.s),'head_requires_manual_review');assert.equal(sends,0);assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('new-head adapter authenticates and reads before journal; existing own check never POSTs',()=>iso(async f=>{
  enabled(f);const g=new GitHub(f.c,{clientSecret:'',pkcs8:'',evidenceKey:''}),calls=[];g.installationToken=async()=>{calls.push('auth');return 'SYNTHETIC';};
  const check=await f.s.evaluate(9);g.runs=async()=>{calls.push('runs');return [{app:{id:f.c.appId,slug:f.c.appSlug},name:f.c.checkName,head_sha:check.headSha}];};
  g.json=async()=>{throw Error('POST_FORBIDDEN');};await assert.rejects(g.publishNew(check,async()=>{calls.push('journal');return 'x';}),e=>e.code==='new_head_run_already_exists');assert.deepEqual(calls,['auth','runs']);
  g.runs=async()=>{calls.push('runs');return [];};g.json=async()=>{calls.push('synthetic-post');throw Error('SYNTHETIC_UNKNOWN');};
  await assert.rejects(g.publishNew(check,async()=>{calls.push('journal');return 'x';}));assert.deepEqual(calls.slice(-4),['auth','runs','journal','synthetic-post']);
}));
test('fixed-time expired proof and missing lease cannot create automatic journal',()=>iso(async f=>{
  enabled(f);const key=await pending(f),check=await f.s.evaluate(9);await f.store.stmt("UPDATE head_requests SET status='running',attempt='test',lease_until=?",f.now()+120).run();
  check.proof.issued_at=f.now()-120;check.proof.expires_at=f.now();check.text='tavotto-cla-v1\n'+canonical(check.proof);
  await assert.rejects(f.store.beginAutomaticPublication({request_key:key,pr:9,head:f.gh.head,attempt:'test'},check));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('publication tests perform no outbound HTTP and no successful remote publication',()=>assert.equal(outbound,0));
