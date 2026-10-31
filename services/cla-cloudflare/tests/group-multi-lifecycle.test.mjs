import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated} from './group-multi-fixture.mjs';
for(const delegated of [false,true])test('two independently signed contributors complete group across wakeups delegated='+delegated,()=>isolated(async f=>{
 await f.admit();assert.equal((await f.recover()).disposition,'group_publication_requires_reconciliation');
 const first=await f.owned();assert.equal(first.phase,'reconcile');assert.equal(await f.slot(),first.id);
 const proof=JSON.parse(first.proof_text.split('\n')[1]);assert.deepEqual(proof.contributors.map(p=>p.id),[2,3]);assert.equal(proof.members.length,2);
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads,2);
 f.advance(first.expires-f.now());assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
 f.advance(1);assert.equal((await f.recover()).disposition,'fresh_publication_requires_reconciliation');const fresh=await f.owned();assert.notEqual(fresh.id,first.id);
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads,4);
 assert.equal((await f.get('SELECT * FROM publication_attempts WHERE id=?',first.id)).proof_text,first.proof_text);
 assert.deepEqual(f.mutations().map(c=>[c.method,c.body.conclusion]),[['POST','success'],['PATCH','failure'],['PATCH','success']]);
},delegated));


test('two-person group lost response resumes from its journal without replay',()=>isolated(async f=>{
 await f.admit();f.hooks.afterMutation=()=>{throw Error('SYNTHETIC_LOST_MULTI_RESPONSE');};
 assert.equal((await f.recover()).disposition,'group_publication_unknown_no_replay');const issued=await f.owned();assert.equal(issued.phase,'unknown');
 delete f.hooks.afterMutation;assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.mutations().length,1);
}));

test('two-person group caller epoch remains fenced across a revocation during closing reads',()=>isolated(async f=>{
 await f.admit();let reads=0;f.hooks.request=async call=>{if(call.path.endsWith('/commits/main')&&++reads===3)await f.s.delegation.append(await f.control('revoke'));};
 assert.notEqual((await f.recover()).disposition,'group_publication_requires_reconciliation');assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
}));

test('group evaluator cannot replace an invocation supplied revision with a newer database epoch',()=>isolated(async f=>{
 const inv=f.invocation(),revision=await f.store.revision();
 await assert.rejects(inv.service.evaluateTarget({kind:'merge_group',headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha},0,revision-1),e=>e.code==='state_changed');
 assert.equal(await f.owned(),null);
}));

test('one newly held personal contributor invalidates a two-person group at the next wakeup',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();const {packet,args}=f.second;
 await f.s.hold(...args,packet.id,await f.s.intent(...args,packet.id,'hold'));await assert.rejects(f.ci());
 assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
},false));

test('group progress RETURNING preserves the durable pass horizon and rejects a stale lease',()=>isolated(async f=>{
 await f.admit();await f.recover();const inv=f.invocation(),revision=await f.store.revision(),job=await inv.service.store.claim(0);
 const first=await inv.service.store.openProgress(job,revision,true);assert.equal(first.horizon,1);
 await f.store.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text)
 VALUES(?,?,?, ?,?,'resolved',?,?,'failure','SYNTHETIC_UNRELATED_HISTORY')`,'z'.repeat(43),job.target_key,'f'.repeat(40),revision,f.now()+120,f.now(),f.now()).run();
 const resumed=await inv.service.store.openProgress(job,revision,true);assert.deepEqual(resumed,first);
 const stale=f.invocation();await assert.rejects(stale.service.store.openProgress({...job,lease:'x'.repeat(43)},revision,true));
 assert.deepEqual(await f.get('SELECT cursor,horizon FROM reconciliation_progress'),first);
}));


test('two-person group waiting on a PR slot retains its task and completes after exact withdrawal',()=>isolated(async f=>{
 await f.admit({groupEvent:false});await f.dispatch();const owner=await f.owned();await f.admit();
 assert.equal((await f.recover()).disposition,'budget_deferred');assert.equal(await f.slot(),owner.id);
 assert.equal((await f.get("SELECT attempts FROM outbox WHERE target_key LIKE 'merge:%'")).attempts,0);
 f.advance(120);assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
 assert.equal((await f.recover()).disposition,'group_publication_requires_reconciliation');assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
}));
