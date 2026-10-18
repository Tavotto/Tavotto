import test from 'node:test';import assert from 'node:assert/strict';
import {iso,deny} from './delegation-fixture.mjs';
import {canonical,open,randomToken,seal,sha256} from '../src/core/crypto.ts';
import {LiveService} from '../src/native/service.ts';import {InvocationBudget,successCapacity} from '../src/native/budget.ts';
import {LiveStore} from '../src/native/store.ts';import {config} from '../src/native/worker.ts';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
test('delegated receipt is distinct, encrypted, bound and personally signed contributor remains required',()=>iso(async f=>{
  await f.install();const p=await f.create();assert.equal((await f.s.evaluate(9)).conclusion,'failure');
  const result=await f.sign(p);assert.equal(result.countersignature,'recorded');assert.equal((await f.s.evaluate(9)).conclusion,'success');
  const rows=await f.store.signatures(p.id);assert.equal(rows.length,1);assert.equal(rows[0].role,'contributor');
  const r=await f.store.stmt('SELECT * FROM delegated_receipts').first();const record=JSON.parse(await open(f.key,r.cipher,`delegated:${p.id}:${r.control_hash}`));
  assert.equal(record.protocol,'cla-delegated-countersignature-v1');assert.equal(record.contributor_evidence_sha256,rows[0].evidence_hash);
  assert.equal(record.name,undefined);assert.equal(record.signature,undefined);
}));
test('config JSON cannot turn delegated or automatic capabilities on',()=>iso(async f=>{
  const c=config({CLA_CONFIG:JSON.stringify({...f.c,delegatedCountersigningEnabled:true,automaticVerificationEnabled:true})});
  assert.equal(c.delegatedCountersigningEnabled,false);assert.equal(c.automaticVerificationEnabled,false);
}));
test('disabled capability rejects existing delegated eligibility and preserves manual holder route',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);f.c.delegatedCountersigningEnabled=false;await deny(()=>f.s.evaluate(9));
  await f.sign(p,'holder');assert.equal((await f.s.evaluate(9)).conclusion,'success');assert.equal((await f.store.signatures(p.id)).length,2);
}));
test('unapproved or unsigned grant cannot create an authorization or receipt',()=>iso(async f=>{
  await assert.rejects(f.s.delegation.register({body:canonical(f.grant),signature:''}));
  const p=await f.create();assert.equal((await f.sign(p)).countersignature,'pending_manual_review');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts').first()).n,0);
  assert.equal((await f.store.signatures(p.id)).length,1);await deny(()=>f.s.evaluate(9));
}));
for(const [field,value]of Object.entries({holder_id:3,holder_name:'OTHER HOLDER',repository_id:999,repository:'other/repo',agreement_kind:'corporate',agreement_version:'2.0',agreement_sha256:'0'.repeat(64),scope:'all-rights',purpose:'sign-for-contributor',key_sha256:'0'.repeat(64),approved_at:1800000001,expires_at:1800000000}))
  test('authorization rejects exact scope mismatch '+field,()=>iso(async f=>{await assert.rejects(f.s.delegation.register(await f.signed({...f.grant,[field]:value})));assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_grants').first()).n,0);}));
test('tampered signature, changed body and unregistered key fail closed',()=>iso(async f=>{
  const signed=await f.signed(f.grant);await assert.rejects(f.s.delegation.register({...signed,signature:'A'.repeat(86)}));
  await assert.rejects(f.s.delegation.register({...signed,body:canonical({...f.grant,scope:'other'})}));
  f.c.holderAuthorizationKey=randomToken();await assert.rejects(f.s.delegation.register(signed));
}));
test('grant alone does not enable countersigning',()=>iso(async f=>{
  await f.s.delegation.register(await f.signed(f.grant));const p=await f.create();await f.sign(p);await deny(()=>f.s.evaluate(9));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts').first()).n,0);
}));
test('suspension invalidates receipts, revision advances, manual holder recovery still works',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const revision=await f.store.revision();
  await f.s.delegation.append(await f.control('suspend'));assert.equal(await f.store.revision(),revision+1);await deny(()=>f.s.evaluate(9));
  assert.equal((await f.s.status(f.args('holder')[0],p.id)).canSign,true);
  await f.sign(p,'holder');assert.equal((await f.s.evaluate(9)).conclusion,'success');
}));
test('revocation is terminal and signed old enable cannot replay',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const old=await f.control('enable');
  await f.s.delegation.append(await f.control('revoke'));await deny(()=>f.s.evaluate(9));
  await assert.rejects(f.s.delegation.countersign(p,(await f.store.signatures(p.id))[0]));
  await assert.rejects(f.s.delegation.append(old));await assert.rejects(f.s.delegation.append(await f.control('enable')));
}));
for(const overrides of [{sequence:3},{previous_sha256:'0'.repeat(64)},{issued_at:1800000001},{issued_at:1799999999}])
  test('signed control rejects fork gap or invalid timestamp '+JSON.stringify(overrides),()=>iso(async f=>{await f.install();await assert.rejects(f.s.delegation.append(await f.control('suspend',overrides)));}));
test('current enable must match receipt; resume needs a fresh delegated operation',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);await f.s.delegation.append(await f.control('suspend'));await f.s.delegation.append(await f.control('enable'));
  await deny(()=>f.s.evaluate(9));await f.s.delegation.countersign(p,(await f.store.signatures(p.id))[0]);assert.equal((await f.s.evaluate(9)).conclusion,'success');
}));
test('authorization deadline caps proof and expiration rejects all old delegated receipts',()=>iso(async f=>{
  f.grant.expires_at=f.now()+60;await f.install();const p=await f.create();await f.sign(p);const check=await f.s.evaluate(9);
  assert.equal(check.proof.expires_at,f.grant.expires_at);assert.equal(check.authorityExpires,f.grant.expires_at);
  f.advance(60);await deny(()=>f.s.evaluate(9));assert.equal((await f.store.signatures(p.id)).length,1);
}));
test('not-before, expired packet, wrong actor and missing personal intent never auto-sign',()=>iso(async f=>{
  f.grant.not_before=f.now()+5;await f.install();const p=await f.create();await f.sign(p);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts').first()).n,0);
  await assert.rejects(f.s.sign(...f.args('contributor'),p.id,randomToken(),'contributor',f.evidence('contributor')));
  f.advance(5);const c=(await f.store.signatures(p.id))[0];await assert.rejects(f.s.delegation.countersign(p,{...c,actor:3}));
  f.advance(7*86400);await assert.rejects(f.s.delegation.countersign({...p,valid_until:f.now()+10},c));
}));
test('concurrent delegated operations commit one receipt; repeated operation is idempotent',()=>iso(async f=>{
  await f.install();const p=await f.create();f.c.delegatedCountersigningEnabled=false;await f.sign(p);f.c.delegatedCountersigningEnabled=true;
  const c=(await f.store.signatures(p.id))[0];await Promise.allSettled(Array.from({length:12},()=>f.s.delegation.countersign(p,c)));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts').first()).n,1);
  const revision=await f.store.revision();await f.s.delegation.countersign(p,c);assert.equal(await f.store.revision(),revision);
}));
test('concurrent control branches consume the predecessor once',()=>iso(async f=>{
  await f.install();const signed=await f.control('suspend'),results=await Promise.allSettled([f.s.delegation.append(signed),f.s.delegation.append(signed)]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_controls').first()).n,2);
}));
test('hold and stale contributor evidence block delegated signing',()=>iso(async f=>{
  await f.install();const p=await f.create();f.c.delegatedCountersigningEnabled=false;await f.sign(p);f.c.delegatedCountersigningEnabled=true;
  const c=(await f.store.signatures(p.id))[0];await assert.rejects(f.s.delegation.countersign(p,{...c,evidence_hash:'0'.repeat(64)}));
  await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));await assert.rejects(f.s.delegation.countersign(p,c));
}));
test('receipt transaction failure preserves contributor and manual route',()=>iso(async f=>{
  await f.install();f.sql.exec("CREATE TRIGGER fail_delegated BEFORE INSERT ON cla_native_v2_delegated_receipts BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END;");
  const p=await f.create();assert.equal((await f.sign(p)).countersignature,'pending_manual_review');assert.equal((await f.store.signatures(p.id)).length,1);
  await f.sign(p,'holder');assert.equal((await f.s.evaluate(9)).conclusion,'success');
}));
test('delegated qualification uses three reads per person without increasing ceilings',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const budget=new InvocationBudget(),store=new LiveStore(f.db,budget,f.c),s=new LiveService(f.c,store,f.gh,f.key);
  assert.equal(successCapacity(f.c),4);assert.equal((await s.evaluate(9,3)).conclusion,'success');assert.equal(budget.snapshot().sql,6);
  assert.equal((await f.store.signatures(p.id)).length,1);
}));
test('new head gets a fresh binding and added unsigned contributor never inherits eligibility',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const old=await f.s.evaluate(9);f.gh.head='e'.repeat(40);
  const fresh=await f.s.evaluate(9);assert.notEqual(old.headSha,fresh.headSha);assert.equal(fresh.proof.head_sha,f.gh.head);
  f.gh.extra=[{sha:'f'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic added actor'}];assert.equal((await f.s.evaluate(9)).conclusion,'failure');
}));
test('authorization and receipt history are immutable',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);for(const table of ['delegated_grants','delegated_controls','delegated_receipts']){
    await assert.rejects(f.store.stmt(`DELETE FROM ${table}`).run());
    const column=table==='delegated_receipts'?'hash':'body';await assert.rejects(f.store.stmt(`UPDATE ${table} SET ${column}='tamper'`).run());
  }
}));
test('held packet rejects idempotent delegated replay rather than returning recorded',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const contributor=(await f.store.signatures(p.id))[0];
  await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));
  await assert.rejects(f.s.delegation.countersign(p,contributor));assert.equal((await f.s.evaluate(9)).conclusion,'failure');
}));
test('unverifiable stored contributor evidence cannot receive a delegated receipt',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.store.stmt('INSERT INTO signatures VALUES(?,?,?,?,?,?,?)',p.id,'contributor',p.subject,'INVALID SYNTHETIC CIPHER','0'.repeat(64),null,f.now()).run();
  const contributor=(await f.store.signatures(p.id))[0];await assert.rejects(f.s.delegation.countersign(p,contributor));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts').first()).n,0);
}));
test('decrypted receipt identity must match its packet and holder even with a valid ciphertext hash',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const r=await f.store.stmt('SELECT * FROM delegated_receipts').first(),aad=`delegated:${p.id}:${r.control_hash}`;
  const original=JSON.parse(await open(f.key,r.cipher,aad)),raw=canonical({...original,holder_id:3});
  f.sql.exec('DROP TRIGGER cla_native_v2_delegated_receipts_update');
  await f.store.stmt('UPDATE delegated_receipts SET cipher=?,hash=?',await seal(f.key,raw,aad),await sha256(raw)).run();await deny(()=>f.s.evaluate(9));
}));
test('disabled delegated operation cannot use an existing personally signed packet',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);f.c.delegatedCountersigningEnabled=false;
  await assert.rejects(f.s.delegation.countersign(p,(await f.store.signatures(p.id))[0]),e=>e.code==='delegated_countersigning_disabled');
}));
test('authentic receipt ciphertext with wrong stored hash is rejected',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_receipts_update');
  await f.store.stmt("UPDATE delegated_receipts SET hash=?",'0'.repeat(64)).run();await deny(()=>f.s.evaluate(9));
}));
test('valid control body signed by no holder key cannot change the current authorization',()=>iso(async f=>{
  await f.install();const valid=await f.control('suspend'),revision=await f.store.revision();
  await assert.rejects(f.s.delegation.append({...valid,signature:'A'.repeat(86)}));assert.equal(await f.store.revision(),revision);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_controls').first()).n,1);
}));
test('delegated suite makes no outbound request or publication',()=>assert.equal(outbound,0));
