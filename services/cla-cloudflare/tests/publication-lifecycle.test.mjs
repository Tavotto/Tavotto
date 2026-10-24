import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated,networkCalls} from './publication-lifecycle-fixture.mjs';
import {verification} from '../src/native/verification.ts';

for(const delegated of [false,true])test('native successful publication confirms independently, expires, withdraws and refreshes delegated='+delegated,()=>isolated(async f=>{
 await f.admit();assert.equal(await f.dispatch(),'automatic_publication_requires_reconciliation');
 const original=await f.owned();assert.equal(original.phase,'reconcile');assert.equal(await f.slot(),original.id);
 await assert.rejects(f.ci(),e=>e.code==='ci_read_unavailable');
 const confirmed=await f.recover();assert.equal(confirmed.disposition,'verified_read_only');assert.equal((await f.owned()).phase,'confirmed');
 const reads=f.authorityReads;assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads-reads,2);
 assert.equal(await f.slot(),original.id,'confirmation is not a release witness');
 f.advance(original.expires-f.now());await assert.rejects(f.ci());
 assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
 const archived=await f.get('SELECT * FROM publication_attempts WHERE id=?',original.id);assert.equal(archived.phase,'resolved');assert.equal(archived.proof_text,original.proof_text);assert.equal(archived.expires,original.expires);
 f.advance(1);assert.equal((await f.recover()).disposition,'fresh_publication_requires_reconciliation');
 const fresh=await f.owned();assert.notEqual(fresh.id,original.id);assert.equal(fresh.run_id,original.run_id);assert.equal(fresh.phase,'reconcile');assert.ok(fresh.expires>original.expires);assert.equal(await f.slot(),fresh.id);
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
 await assert.rejects(verification(f.invocation().service,9,original.head_sha,original.run_id,original.id),e=>e.code==='proof_not_confirmed');
 assert.deepEqual(f.mutations().map(c=>[c.method,c.body.conclusion]),[['POST','success'],['PATCH','failure'],['PATCH','success']]);
 const withdrawal=f.calls.findIndex(c=>c.method==='PATCH'&&c.body.conclusion==='failure');
 assert.deepEqual([f.calls[withdrawal+1].method,f.calls[withdrawal+1].path],['GET',f.calls[withdrawal].path],'PATCH acknowledgement is followed by an independent exact failure GET');
}, {delegated}));

test('native applied success with lost response is observed without replay',()=>isolated(async f=>{
 let lost=false;f.hooks.afterMutation=()=>{if(!lost){lost=true;throw Error('SYNTHETIC_LOST_RESPONSE');}};
 await f.admit();assert.equal(await f.dispatch(),'automatic_publication_unknown_no_replay');assert.equal((await f.owned()).phase,'unknown');
 assert.equal(await f.dispatch(),'idle');assert.equal(f.mutations().length,1);delete f.hooks.afterMutation;
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');assert.equal(f.mutations().length,1);
}));

test('native late wakeup withdraws expired success instead of confirming or renewing it',()=>isolated(async f=>{
 await f.admit();await f.dispatch();const original=await f.owned();f.advance(150);
 await assert.rejects(f.ci());assert.equal((await f.recover()).disposition,'remote_failure_verified');
 assert.equal((await f.owned()).proof_text,original.proof_text);assert.equal((await f.owned()).expires,original.expires);assert.equal(await f.slot(),null);
 assert.deepEqual(f.mutations().map(c=>c.body.conclusion),['success','failure']);
}));

test('native missing unknown owner stays manual and its durable backoff does not replay success',()=>isolated(async f=>{
 f.hooks.afterMutation=()=>{throw Error('SYNTHETIC_LOST_RESPONSE');};await f.admit();await f.dispatch();delete f.hooks.afterMutation;f.setHidden(true);
 const first=await f.owned();assert.equal((await f.recover()).disposition,'manual_review_required');assert.equal((await f.owned()).phase,'manual');assert.equal(await f.slot(),first.id);
 assert.equal((await f.owned()).reconcile_due,f.now()+60);assert.equal((await f.get('SELECT due FROM outbox')).due,f.now()+60);
 assert.equal((await f.recover()).disposition,'idle');f.advance(60);await f.recover();assert.equal(await f.slot(),first.id);assert.equal(f.mutations().length,1);
 await assert.rejects(f.ci());f.setHidden(false);f.runs.get(first.run_id??101).conclusion='failure';f.advance(60);
 assert.equal((await f.recover()).disposition,'remote_failure_observed');assert.equal(await f.slot(),null);assert.equal((await f.owned()).proof_text,first.proof_text);assert.equal(f.mutations().length,1);
}));

test('native expired installation credential before journal cannot issue success',()=>isolated(async f=>{
 await f.admit();f.hooks.tokenLifetime=1;let advanced=false;f.hooks.request=call=>{if(!advanced&&call.path.endsWith('/commits/main')){advanced=true;f.advance(1);}};
 assert.equal(await f.dispatch(),'head_requires_manual_review');assert.equal(await f.owned(),null);assert.equal(await f.slot(),null);assert.equal(f.mutations().length,0);
}));

test('native cleanup exhaustion remains durable until the next invocation reclaims the expired head',()=>isolated(async f=>{
 await f.admit();let advanced=false;f.hooks.request=call=>{if(!advanced&&call.path.endsWith('/check-runs')){advanced=true;f.advance(120);}};
 await assert.rejects(f.dispatch(),e=>e.code==='budget_deferred'&&e.reason==='time');assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
 assert.equal((await f.get('SELECT status FROM head_requests')).status,'running');
 assert.equal(await f.dispatch(),'idle');assert.equal((await f.get('SELECT status FROM head_requests')).status,'manual');
}));

test('native database-clock proof and lease exact expiry before journal rejects without mutation',()=>isolated(async f=>{
 await f.admit();let advanced=false;f.hooks.request=call=>{if(!advanced&&call.path.endsWith('/check-runs')){advanced=true;f.advanceDatabase(120);}};
 assert.equal(await f.dispatch(),'head_requires_manual_review');assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
}));

test('native installation token expiry after journal retains uncertainty without resending',()=>isolated(async f=>{
 await f.admit();f.hooks.tokenLifetime=1;f.hooks.request=call=>{if(call.path.endsWith('/check-runs')&&call.method==='POST')f.advance(1);};
 assert.equal(await f.dispatch(),'automatic_publication_unknown_no_replay');const original=await f.owned();assert.equal(original.phase,'unknown');assert.equal(await f.slot(),original.id);assert.equal(f.runs.size,0);
 assert.equal(await f.dispatch(),'idle');delete f.hooks.request;assert.equal((await f.recover()).disposition,'manual_review_required');assert.equal(f.mutations().length,1);assert.equal(await f.slot(),original.id);
}));

test('native delegated authorization exact expiry caps proof and prevents final dispatch',()=>isolated(async f=>{
 await f.admit();let advanced=false;f.hooks.request=call=>{if(!advanced&&call.path.endsWith('/check-runs')){advanced=true;f.advance(5);}};
 assert.equal(await f.dispatch(),'head_requires_manual_review');assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
},{grantLifetime:5}));

test('native second CI authority read cannot return an expired earlier success',()=>isolated(async f=>{
 await f.admit();await f.dispatch();await f.recover();const original=await f.owned();
 f.hooks.authority=count=>{if(count===2)f.advance(original.expires-f.now());};
 await assert.rejects(f.ci());assert.equal(f.authorityReads,2);assert.equal(f.mutations().length,1);assert.equal(await f.slot(),original.id);
}));

test('native withdrawal acknowledgement alone cannot release held capacity',()=>isolated(async f=>{
 await f.admit();await f.dispatch();await f.recover();const original=await f.owned();f.advance(120);let acknowledged=false;
 f.hooks.afterMutation=call=>{if(call.method==='PATCH'&&call.body.conclusion==='failure')acknowledged=true;};
 f.hooks.request=call=>{if(acknowledged&&call.method==='GET'&&call.path.endsWith('/check-runs/'+original.run_id))throw Error('SYNTHETIC_POST_PATCH_READ_FAILURE');};
 await f.recover();assert.equal(acknowledged,true);assert.equal(await f.slot(),original.id);assert.notEqual((await f.owned()).phase,'resolved');
 delete f.hooks.request;f.advance(2);assert.equal((await f.recover()).disposition,'remote_failure_observed');assert.equal(await f.slot(),null);
 assert.deepEqual(f.mutations().map(c=>c.body.conclusion),['success','failure']);
}));

test('publication lifecycle uses no real network',()=>assert.equal(networkCalls(),0));
