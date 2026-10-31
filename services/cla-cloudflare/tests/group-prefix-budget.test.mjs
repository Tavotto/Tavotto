import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './publication-lifecycle-fixture.mjs';
import {multi} from './group-multi-fixture.mjs';
import {randomToken,sha256} from '../src/core/crypto.ts';
for(const delegated of [false,true])for(const prefix of [3,4])test(`complete P${prefix} one-person group publishes and confirms inside unchanged budgets delegated=${delegated}`,async()=>{
 const f=await fixture({group:true,delegated,prefix});
 try{
  await f.admit();const sent=await f.recover();assert.equal(sent.disposition,'group_publication_requires_reconciliation');
  assert.ok(sent.budget.external<=35);const issued=await f.owned();assert.ok(issued);
  const proof=JSON.parse(issued.proof_text.split('\n')[1]);assert.equal(proof.members.length,prefix);assert.deepEqual(proof.contributors.map(x=>x.id),[2]);
  assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads,2);
  assert.equal(f.mutations().length,1);
 }finally{f.close();}
});
for(const delegated of [false,true])test('P4 complete prefix includes both independently signed contributors delegated='+delegated,async()=>{
 const f=await multi(delegated,{prefix:4});try{
  await f.admit();const sent=await f.recover();assert.equal(sent.disposition,'group_publication_requires_reconciliation');assert.equal(sent.budget.external,32);
  const proof=JSON.parse((await f.owned()).proof_text.split('\n')[1]);assert.equal(proof.members.length,4);assert.deepEqual(proof.contributors.map(p=>p.id),[2,3]);
  assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads,2);
 }finally{f.close();}
});
test('P4 loss and expiry resume the same durable journal without replay',async()=>{
 const f=await fixture({group:true,prefix:4});
 try{
  await f.admit();f.hooks.afterMutation=()=>{throw Error('SYNTHETIC_LOST_P4_RESPONSE');};
  assert.equal((await f.recover()).disposition,'group_publication_unknown_no_replay');const first=await f.owned();
  delete f.hooks.afterMutation;assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.mutations().length,1);
  f.advance(first.expires-f.now());assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
  f.advance(1);assert.equal((await f.recover()).disposition,'fresh_publication_requires_reconciliation');assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
  assert.equal((await f.get('SELECT proof_text FROM publication_attempts WHERE id=?',first.id)).proof_text,first.proof_text);
  assert.deepEqual(f.mutations().map(c=>[c.method,c.body.conclusion]),[['POST','success'],['PATCH','failure'],['PATCH','success']]);
 }finally{f.close();}
});
test('P4 three personally signed contributors retain the original SQL admission ceiling',async()=>{
 const f=await multi(false,{prefix:4});try{
  const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:4,login:'user4',csrf_hash:await sha256(csrf),expires:f.now()+86400});
  const args=[token,csrf,f.c.origin],packet=await f.s.packet(...args,'individual');await f.s.sign(...args,packet.id,await f.s.intent(...args,packet.id,'contributor'),'contributor',f.evidence('contributor'));await f.sign(packet,'holder');
  f.members.get(13).user={id:4,login:'user4',type:'User'};
  await f.admit();const sent=await f.recover();assert.equal(sent.disposition,'group_publication_requires_reconciliation');assert.deepEqual(sent.budget,{sql:24,external:32});
  const proof=JSON.parse((await f.owned()).proof_text.split('\n')[1]);assert.equal(proof.members.length,4);assert.deepEqual(proof.contributors.map(p=>p.id),[2,3,4]);
  assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
 }finally{f.close();}
});
