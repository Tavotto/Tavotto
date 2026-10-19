import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';import {InvocationBudget} from '../src/native/budget.ts';
import {GitHub} from '../src/native/github.ts';import {verifyHeadOne} from '../src/native/automatic.ts';
import {randomToken,sha256,canonical} from '../src/core/crypto.ts';
globalThis.fetch=async()=>{throw Error('INDEPENDENT_NETWORK_FORBIDDEN');};
function enable(f){Object.assign(f.c,{githubEnabled:true,automaticVerificationEnabled:true,checkPublishingEnabled:true,backgroundEnabled:true,automaticPublishingEnabled:true});}
async function running(f,head=f.gh.head){const job={request_key:`707:9:${head}`,pr:9,head,attempt:randomToken()};await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'running',?,?,?,'')",job.request_key,job.pr,job.head,job.attempt,f.now()+120,f.now()).run();return job;}
async function pending(f){const key=`707:9:${f.gh.head}`;await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'pending','',0,?,'')",key,9,f.gh.head,f.now()).run();}
function metered(f,publishNew){const budget=new InvocationBudget(),gh={...f.gh,budget,publishNew},s=new LiveService(f.c,new LiveStore(f.db,budget,f.c),gh,f.key);return {s,budget};}
for(const mode of ['id','key','agreement'])test('independent archive remains verifiable and inactive after current '+mode+' changes',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const originalKey=f.c.holderAuthorizationKey;
 if(mode==='id')f.c.holderAuthorizationId=randomToken();if(mode==='key')f.c.holderAuthorizationKey=randomToken();if(mode==='agreement')f.c.policy.agreements.individual.sha256='0'.repeat(64);
 const result=await f.s.audit(f.args('contributor')[0],p.id);assert.equal(result.delegated.current,false);assert.equal(result.delegated.state,'superseded');
 assert.equal(result.delegated.authorization.public_key,originalKey);assert.equal(JSON.parse(result.delegated.authorization.body).agreement_sha256,p.digest);
 assert.equal(result.legal_effect,'not_determined');assert.equal(result.github_or_merge_eligibility,'not_asserted');
}));
test('independent archive validates original stored public key rather than accepting corrupted registration',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_grants_update');
 await f.store.stmt('UPDATE delegated_grants SET public_key=?',randomToken()).run();await assert.rejects(f.s.audit(f.args('contributor')[0],p.id));
}));
test('independent audit marks subject-wide hold inactive without deleting historical receipt',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));
 const result=await f.s.audit(f.args('contributor')[0],p.id);assert.equal(result.delegated.current,false);assert.equal(result.delegated.state,'blocked');assert.ok(result.delegated.receipt_sha256);
}));
test('independent private audit rechecks session after evidence construction',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const original=f.s.delegation.audit.bind(f.s.delegation);
 f.s.delegation.audit=async(...args)=>{const result=await original(...args);f.advance(31*86400);return result;};
 await assert.rejects(f.s.audit(f.args('contributor')[0],p.id),e=>e.code==='session_rejected');
}));
test('independent private audit rejects an authenticated different contributor',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const token=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:3,login:'other',csrf_hash:await sha256(randomToken()),expires:f.now()+100});
 await assert.rejects(f.s.audit(token,p.id),e=>e.code==='packet_unavailable');
}));
test('independent journal rejects a stale evidence revision despite a fresh matching request lease',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);enable(f);const check=await f.s.evaluate(9);await f.s.delegation.append(await f.control('suspend'));
 const job=await running(f);await assert.rejects(f.store.beginAutomaticPublication(job,check));assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('independent journal rejects fixed-time expired proof before durable dispatch',()=>iso(async f=>{
 enable(f);const check=await f.s.evaluate(9),job=await running(f);check.proof.issued_at=f.now()-120;check.proof.expires_at=f.now();check.text='tavotto-cla-v1\n'+canonical(check.proof);
 await assert.rejects(f.store.beginAutomaticPublication(job,check));assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('independent unreconciled publication on another head blocks new head journal',()=>iso(async f=>{
 enable(f);const check=await f.s.evaluate(9),job=await running(f),target=f.store.targetKey(707,{kind:'pr',number:9});await f.store.track(707,{kind:'pr',number:9});
 await f.store.stmt("INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text) VALUES(?,?,?,?,?,'reconcile',?,?,?,?)",randomToken(),target,'a'.repeat(40),check.proof.revision,check.proof.expires_at,f.now(),f.now(),'failure',check.text).run();
 await assert.rejects(f.store.beginAutomaticPublication(job,check));assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,1);
}));
test('independent new head binding change before journal leaves no send or durable attempt',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);enable(f);await pending(f);let sends=0;
 const run=metered(f,async(_check,beforeSend)=>{f.gh.head='e'.repeat(40);await beforeSend();sends++;throw Error('INDEPENDENT_SEND_FORBIDDEN');});
 assert.equal(await verifyHeadOne(run.s),'head_requires_manual_review');assert.equal(sends,0);assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('independent external budget exhaustion stops before committing the send journal',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);enable(f);await pending(f);let sends=0;
 const run=metered(f,async(_check,beforeSend)=>{await beforeSend();sends++;throw Error('INDEPENDENT_SEND_FORBIDDEN');});
 for(let n=0;n<35;n++)run.budget.externalSignal();assert.equal(await verifyHeadOne(run.s),'head_requires_manual_review');assert.equal(sends,0);
 assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('independent existing owned remote check is rejected before durable journal or POST',()=>iso(async f=>{
 enable(f);const check=await f.s.evaluate(9),g=new GitHub(f.c,{clientSecret:'',pkcs8:'',evidenceKey:''}),calls=[];
 g.installationToken=async()=>{calls.push('auth');return 'SYNTHETIC';};g.runs=async()=>[{app:{id:f.c.appId,slug:f.c.appSlug},name:f.c.checkName,head_sha:check.headSha}];
 g.json=async()=>{calls.push('post');throw Error('INDEPENDENT_SEND_FORBIDDEN');};
 await assert.rejects(g.publishNew(check,async()=>{calls.push('journal');return randomToken();}));assert.deepEqual(calls,['auth']);
}));
test('independent automatic publication configuration requires the recovery capability',()=>iso(async f=>{
 enable(f);assert.throws(()=>new LiveService({...f.c,backgroundEnabled:false},f.store,f.gh,f.key),e=>e.code==='automatic_publication_recovery_required');
}));

test('independent status returns captured revision when a hold commits after the last revision read',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const original=f.store.revision.bind(f.store),snapshot=await original();let reads=0,raced=false;
 f.store.revision=async()=>{const result=await original();if(++reads===2){raced=true;await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));}return result;};
 const status=await f.s.status(f.args('contributor')[0],p.id);assert.equal(raced,true);assert.equal(status.revision,snapshot);assert.equal(await original(),snapshot+1);assert.equal(await f.store.subjectBlocked(p.subject),true);
}));
test('independent journal refuses proof for a different expected request head',()=>iso(async f=>{
 enable(f);const check=await f.s.evaluate(9);f.gh.head='e'.repeat(40);const job=await running(f);
 await assert.rejects(f.store.beginAutomaticPublication(job,check));assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('independent journal refuses lease at exact expiry with a still fresh proof',()=>iso(async f=>{
 enable(f);const check=await f.s.evaluate(9),job=await running(f);await f.store.stmt('UPDATE head_requests SET lease_until=?',f.now()).run();
 await assert.rejects(f.store.beginAutomaticPublication(job,check));assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
