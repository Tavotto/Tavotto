import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';import {canonical,randomToken} from '../src/core/crypto.ts';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';import {InvocationBudget} from '../src/native/budget.ts';
import {verifyHeadOne} from '../src/native/automatic.ts';import {reconcileOne} from '../src/native/background.ts';
globalThis.fetch=async()=>{throw Error('OUTBOUND_FORBIDDEN');};
const enable=f=>Object.assign(f.c,{githubEnabled:true,automaticVerificationEnabled:true,checkPublishingEnabled:true,backgroundEnabled:true,automaticPublishingEnabled:true});
const metered=(f,extra={})=>{const budget=new InvocationBudget();return new LiveService(f.c,new LiveStore(f.db,budget,f.c),{...f.gh,...extra,budget},f.key);};
async function seed(f,lease=10,life=120){
 enable(f);const request={request_key:`707:9:${f.gh.head}`,pr:9,head:f.gh.head,attempt:randomToken()};
 await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'running',?,?,?,'')",request.request_key,9,request.head,request.attempt,f.now()+lease,f.now()).run();
 const check=await f.s.evaluate(9);assert.equal(check.conclusion,'failure');check.proof.expires_at=f.now()+life;check.text='tavotto-cla-v1\n'+canonical(check.proof);
 const id=await f.store.beginAutomaticPublication(request,check),row=await f.store.stmt('SELECT *,rowid position FROM publication_attempts WHERE id=?',id).first();
 return {id,row,key:row.target_key,request};
}
async function authorizedJob(f,key){
 const lease=randomToken();await f.store.stmt('UPDATE outbox SET lease=?,lease_until=? WHERE target_key=?',lease,f.now()+120,key).run();
 return f.store.stmt('SELECT * FROM outbox WHERE target_key=?',key).first();
}
// Every proposal and simulated outcome below is negative; no successful HTTP
// publication or successful-publication transition across expiry is executed.
for(const revisionChange of [false,true])test('inflight recovery cannot terminalize a negative send revision='+revisionChange,()=>iso(async f=>{
 enable(f);const start=f.now(),key=`707:9:${f.gh.head}`;await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'pending','',0,?,'')",key,9,f.gh.head,start).run();
 let observed=0,attempt;const recovery=metered(f,{observe:async()=>{observed++;return null;}});
 const sender=metered(f,{publishNew:async(check,beforeSend)=>{
  assert.equal(check.conclusion,'failure');attempt=await beforeSend();
  const row=await f.store.stmt('SELECT phase,send_until FROM publication_attempts WHERE id=?',attempt).first();assert.equal(row.phase,'issued');assert.equal(row.send_until,start+120);
  if(revisionChange)await f.store.batch(f.store.cas(await f.store.revision()));
  assert.equal(await recovery.store.claim(0),null,'active send excludes even a due target claim');
  assert.equal((await reconcileOne(recovery)).disposition,'idle');assert.equal(observed,0);
  assert.equal((await f.store.stmt('SELECT phase FROM publication_attempts WHERE id=?',attempt).first()).phase,'issued');
  throw Error('SYNTHETIC_NEGATIVE_OUTCOME_UNKNOWN');
 }});
 assert.equal(await verifyHeadOne(sender),'automatic_publication_unknown_no_replay');assert.equal(sender.store.budget.snapshot().sql,18);
 const row=await f.store.stmt('SELECT phase,send_until FROM publication_attempts WHERE id=?',attempt).first();assert.equal(row.phase,'unknown');assert.equal(row.send_until,0);
 assert.equal((await f.store.stmt('SELECT count(*) n FROM outbox').first()).n,1);assert.equal(f.now(),start);
 assert.equal((await reconcileOne(metered(f,{observe:async()=>null}))).disposition,'manual_review_required','after explicit unknown settlement the original manual route remains');
}));
test('active send fence defeats changed generation and expired negative proof urgency',()=>iso(async f=>{
 const {row,key}=await seed(f,10,1);f.advance(2);await f.store.batch(f.store.cas(await f.store.revision()));
 assert.equal((await f.store.stmt('SELECT due FROM outbox WHERE target_key=?',key).first()).due,0);assert.equal(await f.store.claim(0),null);
 const job=await authorizedJob(f,key),revision=await f.store.revision(),progress=await f.store.openProgress(job,revision);
 assert.deepEqual(await f.store.publicationBatch(job,progress),[]);await f.store.finishBatch(job,revision);
 const outbox=await f.store.stmt('SELECT * FROM outbox WHERE target_key=?',key).first();assert.equal(outbox.due,row.send_until);assert.equal(outbox.lease,'');
}));
test('send fence expires exactly at its persisted claim deadline for a fresh negative proof',()=>iso(async f=>{
 const {row,key}=await seed(f,5);f.advance(4);assert.equal(await f.store.claim(0),null);f.advance(1);
 const job=await f.store.claim(0);assert.equal(job.target_key,key);assert.equal(f.now(),row.send_until);assert.ok(f.now()<row.expires);
}));
for(const operation of ['record','failure','defer'])test('stale recovery phase cannot overwrite concurrent negative settlement '+operation,()=>iso(async f=>{
 const {id,key}=await seed(f,5);f.advance(5);const job=await f.store.claim(0),revision=await f.store.revision(),progress=await f.store.openProgress(job,revision),[snapshot]=await f.store.publicationBatch(job,progress);
 assert.equal(snapshot.phase,'issued');await f.store.settleAutomaticPublication(id,'unknown');
 const action=operation==='record'?()=>f.store.recordReconciliation(job,snapshot,'manual',null,'not_observed_no_retry',revision):
  operation==='failure'?()=>f.store.publicationFailure(job,snapshot,revision):()=>f.store.deferPublication(job,snapshot,revision);
 await assert.rejects(action,e=>e.code==='transaction_rejected');assert.equal((await f.store.stmt('SELECT phase FROM publication_attempts WHERE id=?',id).first()).phase,'unknown');
 const current=await f.store.stmt('SELECT * FROM outbox WHERE target_key=?',key).first();assert.equal(current.lease,job.lease);assert.equal(current.generation,job.generation);
}));
test('automatic settlement wakes current revision without releasing another generation lease',()=>iso(async f=>{
 const {id,key}=await seed(f,5);await f.store.batch(f.store.cas(await f.store.revision()));f.advance(5);const job=await f.store.claim(0);
 await f.store.stmt('UPDATE outbox SET due=? WHERE target_key=?',f.now()+60,key).run();await f.store.settleAutomaticPublication(id,'unknown');
 const row=await f.store.stmt('SELECT * FROM outbox WHERE target_key=?',key).first();assert.equal(row.due,0);assert.equal(row.wanted_revision,await f.store.revision());
 assert.equal(row.lease,job.lease);assert.equal(row.lease_until,job.lease_until);assert.equal(row.generation,job.generation);
}));
test('negative settlement atomically restores a missing recovery signal and cannot release manual state',()=>iso(async f=>{
 const {id,key}=await seed(f,5);await f.store.stmt('DELETE FROM outbox WHERE target_key=?',key).run();await f.store.settleAutomaticPublication(id,'unknown');
 assert.equal((await f.store.stmt('SELECT count(*) n FROM outbox WHERE target_key=?',key).first()).n,1);
 await f.store.stmt("UPDATE publication_attempts SET phase='manual' WHERE id=?",id).run();await assert.rejects(f.store.settleAutomaticPublication(id,'unknown'));
 assert.equal((await f.store.stmt('SELECT phase FROM publication_attempts WHERE id=?',id).first()).phase,'manual');
}));
test('claim obtained before the journal is invalidated by enrollment generation',()=>iso(async f=>{
 enable(f);const key=f.store.targetKey(707,{kind:'pr',number:9});await f.store.track(707,{kind:'pr',number:9});await f.store.enqueue(key);const old=await f.store.claim(0);
 await seed(f);assert.equal(await f.store.owns(old,0),false);await assert.rejects(f.store.openProgress(old,await f.store.revision()));
}));
