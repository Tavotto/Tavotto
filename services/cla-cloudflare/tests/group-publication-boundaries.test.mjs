import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated as prefix} from './prefix-read-session-fixture.mjs';
import {isolated as publication} from './publication-lifecycle-fixture.mjs';
import {parseProof} from '../src/core/proof.ts';
import {canonical} from '../src/core/crypto.ts';
import {candidate} from '../scripts/ci-candidate.mjs';

test('actual complete-prefix assessment stays read-only and cannot become a CI proof',()=>prefix(async f=>{
 const assessment=await f.assess();assert.equal(assessment.eligible,true);assert.equal(assessment.publication,'not_attempted');assert.equal(assessment.binding.members.length,2);
 assert.throws(()=>parseProof('cla-merge-prefix-assessment-v1\n'+canonical(assessment),f.now()),e=>e.code==='proof_invalid');
 assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,0);
}));
test('forged group cannot enter the PR proof parser or positive PR journal/send path',()=>publication(async f=>{
 const inv=f.invocation(),check=await inv.service.evaluate(9);check.proof.kind='merge_group';check.proof.members=[];check.text='tavotto-cla-v1\n'+canonical(check.proof);
 assert.throws(()=>parseProof(check.text,f.now()),e=>e.code==='proof_invalid');
 const count=f.calls.length;await assert.rejects(inv.service.github.publishNew(check,()=>assert.fail('GROUP_JOURNAL_FORBIDDEN')),e=>e.code==='check_invalid');assert.equal(f.calls.length,count);
 await assert.rejects(inv.service.store.beginAutomaticPublication({request_key:'synthetic-group',pr:9,head:check.headSha,attempt:'synthetic'},check),e=>e.code==='fresh_proof_unavailable');
 assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
}));
test('malformed native group CI event rejects before constructing any remote reader',async()=>{
 let calls=0;await assert.rejects(candidate({enabled:true},{merge_group:{head_sha:'a'.repeat(40)}},'merge_group','SYNTHETIC',{
  transport:async()=>{calls++;assert.fail('GROUP_REMOTE_READ_FORBIDDEN');},
 }),/ci_event_rejected/);assert.equal(calls,0);
});
