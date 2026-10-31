import test from 'node:test';
import assert from 'node:assert/strict';
import {parseProof} from '../src/core/proof.ts';
import {admitHead} from '../src/native/automatic.ts';
import worker from '../src/native/worker.ts';
import {canonical,bytes,randomToken,sha256} from '../src/core/crypto.ts';
import {isolated} from './publication-lifecycle-fixture.mjs';

test('legal complete-prefix group publishes once, independently confirms and passes both CI authority reads',()=>isolated(async f=>{
 await f.admit();
 assert.equal((await f.recover()).disposition,'group_publication_requires_reconciliation');
 const issued=await f.owned();assert.equal(issued.phase,'reconcile');assert.equal(await f.slot(),issued.id);
 assert.equal(f.mutations().length,1);
 assert.equal((await f.recover()).disposition,'verified_read_only');
 assert.equal((await f.owned()).phase,'confirmed');
 assert.equal((await f.ci()).status,'success');assert.equal(f.authorityReads,2);
 const proof=JSON.parse(issued.proof_text.split('\n')[1]);assert.equal(proof.kind,'merge_group');
 assert.equal(proof.members.length,2);assert.equal(proof.head_sha,f.groupTarget.headSha);assert.equal(proof.base_sha,f.groupTarget.baseSha);
},{group:true}));

for(const delegated of [false,true])test('group expiry withdraws with exact failure GET, refreshes immutable attempt and confirms delegated='+delegated,()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();const original=await f.owned();
 f.advance(original.expires-f.now());await assert.rejects(f.ci());
 assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
 f.advance(1);assert.equal((await f.recover()).disposition,'fresh_publication_requires_reconciliation');
 const fresh=await f.owned();assert.notEqual(fresh.id,original.id);assert.equal(fresh.run_id,original.run_id);assert.equal(await f.slot(),fresh.id);
 assert.equal((await f.get('SELECT * FROM publication_attempts WHERE id=?',original.id)).proof_text,original.proof_text);
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
 assert.deepEqual(f.mutations().map(c=>[c.method,c.body.conclusion]),[['POST','success'],['PATCH','failure'],['PATCH','success']]);
},{group:true,delegated}));

test('group lost response never replays a send and independently recovers',()=>isolated(async f=>{
 f.hooks.afterMutation=()=>{throw Error('SYNTHETIC_LOST_RESPONSE');};await f.admit();
 assert.equal((await f.recover()).disposition,'group_publication_unknown_no_replay');const original=await f.owned();assert.equal(original.phase,'unknown');
 delete f.hooks.afterMutation;assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
 assert.equal(f.mutations().length,1);assert.equal(await f.slot(),original.id);
},{group:true}));

test('group late wakeup and failed post-PATCH read keep the slot until exact failure observation',()=>isolated(async f=>{
 await f.admit();await f.recover();const original=await f.owned();f.advance(150);let patched=false;
 f.hooks.afterMutation=call=>{if(call.body.conclusion==='failure')patched=true;};
 f.hooks.request=call=>{if(patched&&call.method==='GET'&&call.path.endsWith('/check-runs/'+original.run_id))throw Error('SYNTHETIC_FAILED_FAILURE_READ');};
 await f.recover();assert.equal(patched,true);assert.equal(await f.slot(),original.id);assert.notEqual((await f.owned()).phase,'resolved');
 delete f.hooks.request;f.advance(2);assert.equal((await f.recover()).disposition,'remote_failure_observed');assert.equal(await f.slot(),null);
 assert.deepEqual(f.mutations().map(c=>c.body.conclusion),['success','failure']);
},{group:true}));

for(const mutation of ['partial-prefix','unsigned-predecessor','changed-prefix','changed-base'])test('group refuses publication for '+mutation,()=>isolated(async f=>{
 await f.admit();
 if(mutation==='partial-prefix')f.entries.shift();
 if(mutation==='unsigned-predecessor')f.members.get(11).user={id:3,login:'user3',type:'User'};
 if(mutation==='changed-base')f.entries[1].baseCommit.oid='9'.repeat(40);
 if(mutation==='changed-prefix'){let rounds=0;f.hooks.request=call=>{if(call.path==='/graphql'&&++rounds===3)f.entries[0].id='SYNTHETIC_CHANGED_ENTRY';};}
 if(mutation==='unsigned-predecessor')assert.equal((await f.invocation().service.evaluateTarget({kind:'merge_group',headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha})).conclusion,'failure');
 const result=await f.recover();assert.notEqual(result.disposition,'group_publication_requires_reconciliation');
 assert.equal(await f.owned(),null);assert.equal(await f.slot(),null);assert.equal(f.mutations().length,0);
},{group:true,delegated:mutation!=='unsigned-predecessor'}));

test('group revoked evidence after publication fails authority and withdraws',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();await f.s.delegation.append(await f.control('revoke'));
 await assert.rejects(f.ci());assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
 f.advance(1);assert.notEqual((await f.recover()).disposition,'fresh_publication_requires_reconciliation');assert.equal(f.mutations().filter(c=>c.body.conclusion==='success').length,1);
},{group:true}));

test('group second authority read rejects an exact proof expiry',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();const original=await f.owned();
 f.hooks.authority=n=>{if(n===2)f.advance(original.expires-f.now());};
 await assert.rejects(f.ci());assert.equal(f.authorityReads,2);assert.equal(await f.slot(),original.id);assert.equal(f.mutations().length,1);
},{group:true}));

test('group member head changes after first CI read reject the second authority',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();f.hooks.authority=n=>{if(n===2){f.entries[0].pullRequest.headRefOid='8'.repeat(40);f.members.get(11).head.sha='8'.repeat(40);}};
 await assert.rejects(f.ci());assert.equal(f.authorityReads,2);
},{group:true}));

test('group duplicate delivery is idempotent and conflicting reuse rolls back the new target',()=>isolated(async f=>{
 const delivery='SYNTHETIC-GROUP-DELIVERY';await f.admit({delivery});await f.admit({delivery});
 assert.equal((await f.get('SELECT count(*) n FROM deliveries')).n,1);
 await assert.rejects(f.admit({delivery,change:d=>({...d,merge_group:{...d.merge_group,head_sha:'7'.repeat(40)}})}));
 assert.equal((await f.get('SELECT count(*) n FROM targets')).n,1);
 await f.recover();assert.equal(f.mutations().length,1);
},{group:true}));

test('concurrent group workers claim once and share the PR global confirmation slot',()=>isolated(async f=>{
 await f.admit();const results=await Promise.all([f.recover(),f.recover()]);assert.ok(results.some(r=>r.disposition==='idle'));assert.equal(f.mutations().length,1);
 const owner=await f.slot();await f.admit({groupEvent:false});assert.equal(await f.dispatch(),'idle');
 assert.equal(await f.slot(),owner);assert.equal(f.mutations().length,1);assert.equal((await f.get('SELECT status FROM head_requests')).status,'pending');
},{group:true}));

for(const field of ['kind','members','head_sha','base_sha','group'])test('group proof tampering rejects '+field,()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();const run=[...f.runs.values()][0],proof=JSON.parse(run.output.summary.split('\n')[1]);
 if(field==='members')proof.members.shift();else if(field==='group')proof.group.prefix_sha256='0'.repeat(64);else proof[field]=field==='kind'?'pr':'0'.repeat(40);
 run.output.summary='tavotto-cla-v1\n'+canonical(proof);await assert.rejects(f.ci());
},{group:true}));


test('group publication keeps the unchanged 35 external work cap for a larger full prefix',()=>isolated(async f=>{
 await f.admit();const first=await f.recover();assert.equal(first.disposition,'target_retry_pending_or_exhausted');assert.equal(first.budget.external,35);assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
 assert.equal((await f.get('SELECT attempts FROM outbox')).attempts,1);
 for(let n=0;n<2;n++){f.advance(60);assert.equal((await f.recover()).disposition,'target_retry_pending_or_exhausted');}
 f.advance(60);assert.equal((await f.recover()).disposition,'idle');assert.equal((await f.get('SELECT attempts FROM outbox')).attempts,3);
 assert.ok(f.invocations.at(-1).budget.snapshot().external<=35);
},{group:true,prefix:6}));


test('group proof schema binds provenance and exact validity bounds',()=>isolated(async f=>{
 await f.admit();await f.recover();const original=await f.owned(),proof=JSON.parse(original.proof_text.split('\n')[1]);
 assert.deepEqual(parseProof(original.proof_text,f.now()),proof);
 for(const change of [p=>{p.pr=9;},p=>{p.group.strategy='partial';},p=>{p.group.base_ref='refs/heads/other';},p=>{p.group.prefix_sha256='invalid';},p=>{p.group.trusted_source_sha='invalid';},p=>{delete p.group;},p=>{p.members=[];},p=>{p.members.push(p.members[0]);},p=>{p.expires_at=p.issued_at+121;}]){
  const bad=structuredClone(proof);change(bad);assert.throws(()=>parseProof('tavotto-cla-v1\n'+canonical(bad),f.now()),e=>e.code==='proof_invalid');
 }
 assert.throws(()=>parseProof(original.proof_text,proof.expires_at),e=>e.code==='proof_invalid');
},{group:true}));

test('group admission rejects a forged HMAC before a durable delivery',()=>isolated(async f=>{
 const data={action:'checks_requested',repository:{id:f.c.repositoryId,full_name:f.c.repository},installation:{id:f.c.installationId},merge_group:{head_sha:f.groupTarget.headSha,base_sha:f.groupTarget.baseSha,base_ref:f.groupTarget.baseRef}};
 await assert.rejects(admitHead(f.invocation().service,bytes(JSON.stringify(data)),'sha256='+'0'.repeat(64),'SYNTHETIC-FORGED-GROUP','merge_group','SYNTHETIC_WEBHOOK_SECRET_NOT_REAL'),e=>e.code==='webhook_rejected');
 assert.equal((await f.get('SELECT count(*) n FROM deliveries')).n,0);assert.equal(f.mutations().length,0);
},{group:true}));

test('group journal rejects a stale lease generation without issuing or acquiring capacity',()=>isolated(async f=>{
 await f.admit();const inv=f.invocation(),job=await inv.service.store.claim(0),check=await inv.service.evaluateTarget({kind:'merge_group',...{headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha}});
 await assert.rejects(inv.service.store.beginGroupPublication({...job,generation:job.generation+1},check),e=>e.code==='transaction_rejected');
 assert.equal(await f.owned(),null);assert.equal(await f.slot(),null);assert.equal(f.mutations().length,0);
},{group:true}));

test('group fresh retry backoff is durable and never replays the resolved success',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();const original=await f.owned();f.advance(120);await f.recover();f.advance(1);
 let fail=true;f.hooks.request=call=>{if(fail&&call.path.endsWith('/installation'))throw Error('SYNTHETIC_TEMPORARY_FAILURE');};
 assert.equal((await f.recover()).disposition,'refresh_retry_pending_or_manual');const r=await f.get('SELECT * FROM refresh_requests');assert.equal(r.attempts,1);assert.equal(r.due,f.now()+60);
 assert.equal((await f.recover()).disposition,'idle');fail=false;f.advance(60);assert.equal((await f.recover()).disposition,'fresh_publication_requires_reconciliation');
 const fresh=await f.owned();assert.notEqual(fresh.id,original.id);assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
 assert.equal(f.mutations().filter(c=>c.method==='POST').length,1);
},{group:true}));

test('group unknown missing owner stays held and manual until an exact negative observation',()=>isolated(async f=>{
 f.hooks.afterMutation=()=>{throw Error('SYNTHETIC_LOST_RESPONSE');};await f.admit();await f.recover();delete f.hooks.afterMutation;const original=await f.owned();
 f.setHidden(true);assert.equal((await f.recover()).disposition,'manual_review_required');assert.equal(await f.slot(),original.id);
 assert.equal((await f.owned()).phase,'manual');assert.equal((await f.recover()).disposition,'idle');f.advance(60);await f.recover();assert.equal(await f.slot(),original.id);
 f.setHidden(false);f.runs.get(101).conclusion='failure';f.advance(60);assert.equal((await f.recover()).disposition,'remote_failure_observed');assert.equal(await f.slot(),null);assert.equal(f.mutations().length,1);
},{group:true}));

for(const flag of ['automaticVerificationEnabled','automaticPublishingEnabled','checkPublishingEnabled','confirmationSlotEnabled'])test('group publication requires explicit '+flag,()=>isolated(async f=>{
 await f.admit();f.c[flag]=false;
 if(flag==='automaticVerificationEnabled')await assert.rejects(async()=>f.recover(),e=>e.code==='automatic_publication_recovery_required');
 else if(flag==='checkPublishingEnabled')await assert.rejects(async()=>f.recover(),e=>e.code==='confirmation_slot_recovery_required');
 else await f.recover();assert.equal(await f.owned(),null);assert.equal(f.mutations().length,0);
},{group:true}));

test('group public authority route preserves disabled, abuse-control and exact-query gates',()=>isolated(async f=>{
 const base={DB:f.db,CLA_CONFIG:JSON.stringify(f.c),CLA_GITHUB_ENABLED:'true',CLA_CI_VERIFICATION_ENABLED:'true'},path=f.c.origin+'/verification/group?'+new URLSearchParams({head:f.groupTarget.headSha,base:f.groupTarget.baseSha,run:'101',attempt:'a'.repeat(43)});
 const off=await worker.fetch(new Request(path),{DB:f.db,CLA_CONFIG:JSON.stringify(f.c)});assert.equal(off.status,503);
 const absent=await worker.fetch(new Request(path),base);assert.equal(absent.status,503);assert.equal((await absent.json()).error,'verification_abuse_control_required');
 const limited=await worker.fetch(new Request(path),{...base,CLA_VERIFICATION_LIMITER:{limit:async()=>({success:false})}});assert.equal(limited.status,429);
 const wrong=await worker.fetch(new Request(path+'&pr=9'),base);assert.equal(wrong.status,403);assert.equal((await wrong.json()).error,'verification_invalid');
},{group:true}));


test('group queue entry identity change invalidates authority and reconciliation despite identical members',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();f.entries[0].id='SYNTHETIC_REPLACED_QUEUE_ENTRY';
 await assert.rejects(f.ci());await f.admit();assert.equal((await f.recover()).disposition,'remote_failure_verified');assert.equal(await f.slot(),null);
},{group:true}));

for(const field of ['action','repository','installation','base_ref','head_sha'])test('group admission rejects authenticated wrong '+field,()=>isolated(async f=>{
 await assert.rejects(f.admit({change:d=>{
  if(field==='action')d.action='destroyed';else if(field==='repository')d.repository.id++;else if(field==='installation')d.installation.id++;
  else d.merge_group[field]=field==='base_ref'?'refs/heads/other':'invalid';return d;
 }}),e=>e.code==='webhook_rejected');
 assert.equal((await f.get('SELECT count(*) n FROM deliveries')).n,0);assert.equal(f.mutations().length,0);
},{group:true}));


test('group admission keeps full two-person qualification before sending',()=>isolated(async f=>{
 const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:3,login:'user3',csrf_hash:await sha256(csrf),expires:f.now()+86400});
 const args=[token,csrf,f.c.origin],packet=await f.s.packet(...args,'individual');
 await f.s.sign(...args,packet.id,await f.s.intent(...args,packet.id,'contributor'),'contributor',f.evidence('contributor'));await f.sign(packet,'holder');
 f.members.get(11).user={id:3,login:'user3',type:'User'};
 const assessment=await f.invocation().service.evaluateTarget({kind:'merge_group',headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha});
 assert.equal(assessment.conclusion,'success');assert.equal(assessment.proof.contributors.length,2);
 await f.admit();assert.equal((await f.recover()).disposition,'group_publication_requires_reconciliation');assert.equal(f.mutations().length,1);
 assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
},{group:true,delegated:false}));


test('group refresh rejects a remote revocation from a future evidence revision before issuing',()=>isolated(async f=>{
 await f.admit();await f.recover();await f.recover();f.advance(120);await f.recover();f.advance(1);
 const run=[...f.runs.values()][0],revocation=JSON.parse(run.output.summary.split('\n')[1]);revocation.revision+=99;run.output.summary='tavotto-cla-revocation-v1\n'+canonical(revocation);
 const original=await f.owned();assert.equal((await f.recover()).disposition,'refresh_retry_pending_or_manual');assert.equal((await f.owned()).id,original.id);
 assert.equal(await f.slot(),null);assert.equal(f.mutations().filter(c=>c.body.conclusion==='success').length,1);
},{group:true}));
