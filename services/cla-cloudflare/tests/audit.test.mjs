import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';import {randomToken,sha256} from '../src/core/crypto.ts';
import worker from '../src/native/worker.ts';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
test('private audit contains exact document personal consent and independently verifiable signed authorization',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const a=await f.s.audit(f.args('contributor')[0],p.id);
  assert.equal(a.protocol,'cla-private-audit-v1');assert.equal(await sha256(a.agreement.content),p.digest);
  assert.equal(a.personal_signatures.length,1);assert.equal(a.personal_signatures[0].role,'contributor');assert.equal(a.delegated.receipt.protocol,'cla-delegated-countersignature-v1');
  assert.equal(a.delegated.current,true);assert.equal(a.legal_effect,'not_determined');assert.equal(a.github_or_merge_eligibility,'not_asserted');
  assert.equal(a.delegated.receipt.name,undefined);assert.equal(a.delegated.receipt.signature,undefined);
  assert.equal((await f.s.status(f.args('holder')[0],p.id)).canSign,true,'independent personal holder route remains available');
}));
for(const mode of ['suspend','revoke','expire','disable','hold'])test('historical audit remains downloadable after '+mode+' without current eligibility',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);
  if(mode==='suspend'||mode==='revoke')await f.s.delegation.append(await f.control(mode));
  if(mode==='expire')f.advance(86400);
  if(mode==='disable')f.c.delegatedCountersigningEnabled=false;
  if(mode==='hold')await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));
  const a=await f.s.audit(f.args('contributor')[0],p.id);assert.equal(a.delegated.current,false);assert.ok(a.delegated.authorization.signature);
  assert.equal(a.delegated.state,{suspend:'suspended',revoke:'revoked',expire:'expired',disable:'disabled',hold:'blocked'}[mode]);
  const status=await f.s.status(f.args('contributor')[0],p.id);assert.equal(status.delegated.current,false);assert.equal(status.delegated.canAudit,true);
}));
test('private audit rejects other account and expired session including expiry during export',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const token=randomToken(),csrf=randomToken();
  await f.store.addSession({token_hash:await sha256(token),actor:3,login:'other',csrf_hash:await sha256(csrf),expires:f.now()+10});
  await assert.rejects(f.s.audit(token,p.id),e=>e.code==='packet_unavailable');
  const original=f.s.delegation.audit.bind(f.s.delegation);f.s.delegation.audit=async(...args)=>{const a=await original(...args);f.advance(31*86400);return a;};
  await assert.rejects(f.s.audit(f.args('contributor')[0],p.id),e=>e.code==='session_rejected');
}));
test('audit rejects tampered receipt and status preserves manual recovery without claiming completion',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_receipts_update');
  await f.store.stmt('UPDATE delegated_receipts SET hash=?','0'.repeat(64)).run();await assert.rejects(f.s.audit(f.args('contributor')[0],p.id));
  const status=await f.s.status(f.args('holder')[0],p.id);assert.equal(status.delegated.state,'unverifiable');assert.equal(status.completed,false);assert.equal(status.canSign,true);
}));
for(const mode of ['id','key','agreement'])test('archived audit survives '+mode+' rotation without restoring current eligibility',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const original=f.c.holderAuthorizationKey;
  if(mode==='id')f.c.holderAuthorizationId=randomToken();
  if(mode==='key')f.c.holderAuthorizationKey=randomToken();
  if(mode==='agreement')f.c.policy.agreements.individual.sha256='f'.repeat(64);
  const a=await f.s.audit(f.args('contributor')[0],p.id);assert.equal(a.delegated.current,false);assert.equal(a.delegated.state,'superseded');
  assert.equal(a.delegated.authorization.public_key,original);assert.equal(a.delegated.receipt.agreement_sha256,p.digest);
  const status=await f.s.status(f.args('holder')[0],p.id);assert.equal(status.delegated.canAudit,true);assert.equal(status.completed,false);
  await assert.rejects(f.s.delegation.qualified(p,(await f.store.signatures(p.id))[0],f.now()));
}));
test('archived audit rejects substituted registration-key snapshot',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_grants_update');
  await f.store.stmt('UPDATE delegated_grants SET public_key=?',randomToken()).run();await assert.rejects(f.s.audit(f.args('contributor')[0],p.id));
}));
test('worker audit route is exact authenticated attachment without accepting extra parameters',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const env={DB:f.db,CLA_CONFIG:JSON.stringify(f.c),CLA_SIGNING_ENABLED:'true',CLA_UI_ENABLED:'true',CLA_DELEGATED_COUNTERSIGNING_ENABLED:'true',CLA_EVIDENCE_KEY:Buffer.from(f.keyBytes).toString('base64')};
  const get=path=>worker.fetch(new Request(f.c.origin+path,{headers:{cookie:'__Host-cla-session='+f.args('contributor')[0]}}),env);
  const r=await get('/audit?packet='+p.id);assert.equal(r.status,200);assert.match(r.headers.get('content-disposition'),/^attachment/);assert.equal(r.headers.get('cache-control'),'no-store');
  assert.equal((await r.json()).delegated.current,true);
  assert.equal((await get('/audit?packet='+p.id+'&extra=1')).status,404);
  const anonymous=await worker.fetch(new Request(f.c.origin+'/audit?packet='+p.id),env);assert.equal(anonymous.status,403);
}));
test('audit suite never fetches outward',()=>assert.equal(outbound,0));
