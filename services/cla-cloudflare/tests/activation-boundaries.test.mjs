import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './activation-fixture.mjs';
import {canonical} from '../src/core/crypto.ts';
import {prepareAgreementActivation,applyAgreementActivation} from '../src/native/agreement-activation.ts';
import {bootstrapSQL} from '../scripts/generate-bootstrap.mjs';
import {validateLive} from '../src/native/config.ts';

globalThis.fetch=async()=>{throw Error('OUTBOUND_FORBIDDEN');};
const journal=async f=>(await f.store.stmt('SELECT * FROM agreement_activations ORDER BY generation').all()).results;
test('activation rejects draft malformed and unbounded versions',()=>iso(async f=>{
 for(const version of ['2.0-draft','0.1','02.0','2.00','2','2.0.0.0','10000.0',"2.0';DROP TABLE state;--"]){
  const c={...structuredClone(f.c),agreementActivationEnabled:true,agreementActivationHash:'0'.repeat(64)};
  c.policy.agreements.individual.version=version;assert.throws(()=>validateLive(c));
  await assert.rejects(f.plan(version));
 }
 assert.equal((await journal(f)).length,0);
 await assert.doesNotReject(f.plan('2.0.1'));
}));
test('activation cannot reuse an installed version with different bytes',()=>iso(async f=>{
 const a=await f.plan();await a.apply();const before=await journal(f),revision=await f.store.revision(),b=await f.plan('2.0','DIFFERENT SYNTHETIC BYTES');
 await assert.rejects(b.apply());assert.deepEqual(await journal(f),before);assert.equal(await f.store.revision(),revision);
 assert.equal((await f.store.stmt("SELECT digest FROM agreements WHERE kind='individual' AND version='2.0'").first()).digest,a.config.policy.agreements.individual.sha256);
 assert.equal(await f.store.document(b.config.policy.agreements.individual.sha256),null);
}));
test('activation later source observation retains original archived source',()=>iso(async f=>{
 const a=await f.plan();await a.apply();const b=await f.plan('2.0','','f'.repeat(40));
 await assert.doesNotReject(b.apply());assert.equal((await journal(f)).length,2);
 const document=await f.store.document(a.config.policy.agreements.individual.sha256);
 assert.equal(document.source_sha,a.config.referenceSha);
 assert.equal(JSON.parse((await journal(f)).at(-1).observation_json).individual,b.config.referenceSha);
 await assert.rejects(f.service(a.config,a.content).store.assertReady());
 await assert.doesNotReject(f.service(b.config,b.content).store.assertReady());
}));
test('activation journal update and delete are immutable',()=>iso(async f=>{
 const plan=await f.plan();await plan.apply();const before=await journal(f);
 await assert.rejects(f.store.stmt('UPDATE agreement_activations SET created=created+1').run());
 await assert.rejects(f.store.stmt('DELETE FROM agreement_activations').run());
 assert.deepEqual(await journal(f),before);
}));
test('activation bootstrap refuses a prepared current-mode configuration',()=>iso(async f=>{
 const plan=await f.plan();assert.throws(()=>bootstrapSQL(plan.config));
}));
test('activation preparation snapshots all caller inputs before awaiting',()=>iso(async f=>{
 const plan=await f.plan(),c=structuredClone(plan.config),docs=structuredClone(plan.documents),expected=structuredClone(plan.expected);delete c.agreementActivationHash;
 const pending=prepareAgreementActivation(c,docs,expected);
 c.holderId=3;docs.find(d=>d.kind==='individual').content+='CALLER MUTATION';expected.revision++;
 let result;await assert.doesNotReject(async()=>{result=await pending;});
 assert.equal(canonical(result),canonical(plan.proposal));
}));
test('activation application snapshots caller proposal across rebuilding awaits',async()=>{
 for(const applied of [false,true])await iso(async f=>{
  const plan=await f.plan(),c=structuredClone(plan.config),proposal=structuredClone(plan.proposal);
  if(applied)await plan.apply();
  const pending=applyAgreementActivation(f.store,c,proposal);
  c.holderId=3;proposal.record.body+='CALLER MUTATION';
  let result;await assert.doesNotReject(async()=>{result=await pending;});assert.equal(result.status,applied?'already_active':'applied');
  await assert.doesNotReject(f.service(plan.config,plan.content).store.assertReady());
 });
});
