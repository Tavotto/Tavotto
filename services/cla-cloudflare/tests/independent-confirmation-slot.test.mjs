import test from 'node:test';
import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';
import {LiveStore} from '../src/native/store.ts';
import {GitHub} from '../src/native/github.ts';
import {InvocationBudget} from '../src/native/budget.ts';
import {canonical,randomToken} from '../src/core/crypto.ts';

let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('SLOT_OUTBOUND_FORBIDDEN');};

// These tests create unsent local journals and fixed synthetic negative GET
// responses. They never invoke publishing, Worker fetch or a successful remote
// publication, and never transition a successful publication across expiry.
const local=fn=>iso(async f=>{
  Object.assign(f.c,{confirmationSlotEnabled:true,backgroundEnabled:true,checkPublishingEnabled:true,githubEnabled:true});
  await fn(f);
},false);
const invocation=(f,c=f.c)=>{const budget=new InvocationBudget();return {store:new LiveStore(f.db,budget,c),budget};};
function snapshot(f){
  return f.sql.prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'cla_native_v2_%' ORDER BY name").all()
    .map(({name})=>[name,f.sql.prepare('SELECT rowid,* FROM '+name+' ORDER BY rowid').all().map(row=>({...row}))]);
}
async function deniedUnchanged(f,action){const before=snapshot(f);await assert.rejects(action);assert.deepEqual(snapshot(f),before);}
const policy=f=>f.store.stmt('SELECT * FROM slot_policy WHERE id=1').first();
const markers=f=>f.store.stmt("SELECT count(*) n FROM _guards WHERE id LIKE 'confirmation-slot:%'").first();
async function proposal(f,pr=9){
  const check=await f.s.evaluate(9);check.conclusion='success';check.proof.pr=pr;check.text='tavotto-cla-v1\n'+canonical(check.proof);
  const request={request_key:`707:${pr}:${check.headSha}`,pr,head:check.headSha,attempt:randomToken()};
  await f.store.stmt("INSERT INTO head_requests VALUES(?,?,?,'running',?,?,?,'')",request.request_key,pr,request.head,request.attempt,f.now()+120,f.now()).run();
  return {check,request,key:f.store.targetKey(707,{kind:'pr',number:pr})};
}
async function owner(f,{settled=true,pr=9,run=77}={}){
  const proposed=await proposal(f,pr),current=invocation(f);
  const id=await current.store.beginAutomaticPublication(proposed.request,proposed.check);
  if(settled){current.budget.settlement();await current.store.settleAutomaticPublication(id,'unknown',run);}
  return {...proposed,...current,id,p:await f.store.latestPublication(proposed.key)};
}
async function jobFor(f,key){
  await f.store.stmt('UPDATE outbox SET lease=?,lease_until=? WHERE target_key=?',randomToken(),f.now()+120,key).run();
  return f.store.stmt('SELECT * FROM outbox WHERE target_key=?',key).first();
}
async function negative(f,p,job,revision,{clock=f.now,alter=x=>x,budget=new InvocationBudget()}={}){
  const requests=[],run={id:p.run_id??77,app:{id:f.c.appId,slug:f.c.appSlug},name:f.c.checkName,
    head_sha:p.head_sha,external_id:p.id,status:'completed',conclusion:'failure',output:{summary:'SYNTHETIC NEGATIVE ONLY'}};
  const github=new GitHub(f.c,{clientSecret:'',pkcs8:'',evidenceKey:''},async(url,init)=>{
    assert.equal(init.method,'GET','negative witness transport is strictly read-only');requests.push(url);
    const path=new URL(url).pathname;
    const body=path.includes('/commits/')?{total_count:1,check_runs:[run]}:alter(structuredClone(run));
    assert.ok(path.includes('/commits/') || path.endsWith('/check-runs/'+run.id),'bounded owned-run GET');
    return new Response(JSON.stringify(body),{headers:{'content-type':'application/json'}});
  },clock,budget);
  // No token exchange or credentials are used. All HTTP-shaped reads above are
  // in-process Response objects; global fetch remains forbidden.
  github.installationToken=async()=>'SYNTHETIC-READ-ONLY';
  const remote=await github.observe(p,{job,revision});assert.equal(remote?.conclusion,'failure');assert.ok(remote.negativeWitness);
  assert.equal(requests.length,2,'a list result alone cannot authorize release');
  return {github,witness:remote.negativeWitness,run:remote.id,requests,budget,store:new LiveStore(f.db,budget,f.c)};
}
async function release(f,seed,options={}){
  const job=await jobFor(f,seed.key),revision=await f.store.revision(),p=await f.store.latestPublication(seed.key);
  const read=await negative(f,p,job,revision,options);
  await read.store.recordReconciliation(job,p,'resolved',read.run,'ignored in favor of durable witness',revision,null,{source:read.github,witness:read.witness});
  return {...read,job,revision,p};
}
async function legacy(f,{pr=19,phase='resolved',conclusion='failure',id=randomToken(),expires=f.now()+120,sendUntil=0}={}){
  const key=f.store.targetKey(707,{kind:'pr',number:pr});await f.store.track(707,{kind:'pr',number:pr});
  await f.store.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,send_until)
    VALUES(?,?,?,?,?,?,77,?,?,?,?,?)`,id,key,f.gh.head,await f.store.revision(),expires,phase,f.now(),f.now(),conclusion,'SYNTHETIC UNSENT JOURNAL',sendUntil).run();
  return {id,key};
}

test('slot first enrollment atomically consumes marker and preserves an unsent journal within seven SQL',()=>local(async f=>{
  let seed;await assert.doesNotReject(async()=>{seed=await owner(f,{settled:false});});const row=await policy(f);assert.ok(row);
  assert.equal(seed.budget.snapshot().sql,7);assert.equal(seed.budget.snapshot().external,0);
  assert.equal(row.first_attempt,seed.id);assert.equal(row.active_attempt,seed.id);assert.equal(row.protocol,'cla-confirmation-slot-v1');
  assert.equal(seed.p.confirmation_slot,1);assert.equal(seed.p.proof_text,seed.check.text);assert.equal(seed.p.expires,seed.check.proof.expires_at);
  assert.equal((await markers(f)).n,0);assert.equal((await f.store.stmt('SELECT status FROM head_requests WHERE request_key=?',seed.request.request_key).first()).status,'dispatched');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM outbox WHERE target_key=?',seed.key).first()).n,1);
}));

test('slot first issued late failure rolls back every application table',()=>local(async f=>{
  const proposed=await proposal(f),{store}=invocation(f),original=store.batch.bind(store);
  store.batch=statements=>original([...statements,store.stmt('INSERT INTO _guards(id,ok) VALUES(?,0)',randomToken())]);
  await deniedUnchanged(f,()=>store.beginAutomaticPublication(proposed.request,proposed.check));
  assert.equal(await policy(f),null);assert.equal((await markers(f)).n,0);
}));

test('slot marker duplicate legacy id cannot commit an ignored journal no-op',()=>local(async f=>{
  const old=await legacy(f);
  await deniedUnchanged(f,()=>f.store.batch([
    f.store.stmt('INSERT INTO _guards(id,ok) VALUES(?,1)','confirmation-slot:'+old.id),
    f.store.stmt(`INSERT OR IGNORE INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text,send_until)
      VALUES(?,?,?,0,?,'issued',?,?,'success','SYNTHETIC',?)`,old.id,old.key,f.gh.head,f.now()+120,f.now(),f.now(),f.now()+120),
  ]));
  assert.equal((await markers(f)).n,0);assert.equal(await policy(f),null);
}));

for(const phase of ['issued','unknown','reconcile','confirmed','manual'])test('slot global legacy success blocks first enrollment '+phase,()=>local(async f=>{
  await legacy(f,{phase,conclusion:'success'});const proposed=await proposal(f),{store}=invocation(f);
  await deniedUnchanged(f,()=>store.beginAutomaticPublication(proposed.request,proposed.check));assert.equal(await policy(f),null);
}));

test('slot resolved legacy success and unresolved failure leave first enrollment available',()=>local(async f=>{
  await legacy(f,{conclusion:'success'});await legacy(f,{pr:20,conclusion:'failure',phase:'unknown'});
  const seed=await owner(f,{settled:false});assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot global occupied owner rejects another target atomically without suppressing negatives',()=>local(async f=>{
  const seed=await owner(f),proposed=await proposal(f,10),{store}=invocation(f);
  await deniedUnchanged(f,()=>store.beginAutomaticPublication(proposed.request,proposed.check));
  const negativeRow=await legacy(f,{pr:30,phase:'issued'});assert.equal((await f.store.latestPublication(negativeRow.key)).conclusion,'failure');
  assert.equal((await policy(f)).active_attempt,seed.id);assert.equal((await markers(f)).n,0);
}));

test('slot policy REPLACE cannot replace enrollment even with a matching marker',()=>local(async f=>{
  const seed=await owner(f,{settled:false});await f.store.stmt('INSERT INTO _guards(id,ok) VALUES(?,1)','confirmation-slot:'+seed.id).run();
  await deniedUnchanged(f,()=>f.store.stmt("INSERT OR REPLACE INTO slot_policy VALUES(1,'cla-confirmation-slot-v1',?,?,?)",seed.id,seed.id,0).run());
  assert.equal((await policy(f)).created,f.now());
}));

test('slot active journal REPLACE cannot turn its owner into a failure',()=>local(async f=>{
  const seed=await owner(f),other=await legacy(f,{pr:99});
  await deniedUnchanged(f,()=>f.store.stmt(`INSERT OR REPLACE INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text)
    VALUES(?,?,?,?,?,'issued',?,?,'failure','SYNTHETIC REPLACEMENT')`,seed.id,other.key,'e'.repeat(40),seed.p.revision,seed.p.expires,f.now(),f.now()).run());
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot policy rejects direct NULL no-op deletion and metadata transitions',()=>local(async f=>{
  const seed=await owner(f),other=await legacy(f);
  for(const action of [()=>f.store.stmt('UPDATE slot_policy SET active_attempt=NULL').run(),
    ()=>f.store.stmt('UPDATE slot_policy SET active_attempt=active_attempt').run(),
    ()=>f.store.stmt('UPDATE slot_policy SET active_attempt=?',other.id).run(),
    ()=>f.store.stmt('UPDATE slot_policy SET created=0').run(),()=>f.store.stmt('DELETE FROM slot_policy').run()])await deniedUnchanged(f,action);
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot immutable owner proof identity and known run survive attempted SQL edits',()=>local(async f=>{
  const seed=await owner(f);
  for(const [column,value] of [['id',randomToken()],['head_sha','e'.repeat(40)],['revision',999],['expires',f.now()+999],['conclusion','failure'],['proof_text','REPLACED'],['created',0],['run_id',88]]){
    await deniedUnchanged(f,()=>f.store.stmt('UPDATE publication_attempts SET '+column+'=? WHERE id=?',value,seed.id).run());
  }
  await deniedUnchanged(f,()=>f.store.stmt('DELETE FROM publication_attempts WHERE id=?',seed.id).run());
}));

test('slot old binary resolved without a negative witness is rejected and retains recovery signal',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision();
  await deniedUnchanged(f,()=>f.store.stmt("UPDATE publication_attempts SET phase='resolved',observation='remote_failure_observed' WHERE id=?",seed.id).run());
  await deniedUnchanged(f,()=>f.store.recordReconciliation(job,seed.p,'resolved',77,'remote_failure_observed',revision));
  assert.equal((await policy(f)).active_attempt,seed.id);assert.equal((await f.store.stmt('SELECT count(*) n FROM outbox').first()).n,1);
}));

test('slot owned outbox cannot be deleted while unrelated negative recovery remains usable',()=>local(async f=>{
  const seed=await owner(f);await deniedUnchanged(f,()=>f.store.stmt('DELETE FROM outbox WHERE target_key=?',seed.key).run());
  const other=await legacy(f,{phase:'unknown'});await f.store.enqueue(other.key);const job=await jobFor(f,other.key);
  await assert.doesNotReject(f.store.recordReconciliation(job,await f.store.latestPublication(other.key),'resolved',77,'remote_failure_observed',await f.store.revision()));
  await assert.doesNotReject(f.store.completeReconciliation(job,await f.store.revision(),null));
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot flag false still selects and projects its owner while blocking legacy positive insert',()=>local(async f=>{
  const seed=await owner(f),c={...f.c,confirmationSlotEnabled:false},current=invocation(f,c),job=await current.store.claim(0);
  assert.ok(job);const progress=await current.store.openProgress(job,await current.store.revision()),rows=await current.store.publicationBatch(job,progress);
  assert.equal(rows.length,1);assert.equal(rows[0].id,seed.id);assert.equal(rows[0].confirmation_slot,1);
  const next=await proposal(f,10);await f.store.track(707,{kind:'pr',number:10});
  await deniedUnchanged(f,()=>current.store.beginPublication(next.key,next.check));
}));

test('slot static expired original proof and changed revision cannot bypass a live send fence',()=>local(async f=>{
  const id=randomToken(),key=f.store.targetKey(707,{kind:'pr',number:9});await f.store.track(707,{kind:'pr',number:9});
  // Already-expired synthetic row at one fixed time, with no send and no clock
  // advancement. This tests only the persisted sender/recovery exclusion.
  await f.store.batch([f.store.stmt('INSERT INTO _guards(id,ok) VALUES(?,1)','confirmation-slot:'+id),
    f.store.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text,send_until)
      VALUES(?,?,?,0,?,'issued',?,?,'success','STATIC EXPIRED UNSENT',?)`,id,key,f.gh.head,f.now()-1,f.now(),f.now(),f.now()+120)]);
  await f.store.enqueue(key);await f.store.batch(f.store.cas(await f.store.revision()));
  assert.equal(await f.store.claim(0),null);const job=await jobFor(f,key),revision=await f.store.revision(),progress=await f.store.openProgress(job,revision);
  assert.deepEqual(await f.store.publicationBatch(job,progress),[]);assert.equal((await policy(f)).active_attempt,id);
}));

test('slot read-only negative GET releases exact owner and preserves immutable historical evidence',()=>local(async f=>{
  const seed=await owner(f),read=await release(f,seed),row=await f.store.latestPublication(seed.key),archive=JSON.parse(row.observation);
  assert.equal((await policy(f)).active_attempt,null);assert.equal((await policy(f)).first_attempt,seed.id);
  assert.equal(row.phase,'resolved');assert.equal(row.confirmation_slot,null);assert.equal(row.proof_text,seed.p.proof_text);assert.equal(row.expires,seed.p.expires);
  assert.equal(archive.attempt,seed.id);assert.equal(archive.proof_text,seed.p.proof_text);assert.equal(archive.run_id,77);assert.equal(archive.source,'observed_get');
  assert.equal(read.budget.snapshot().sql,5);assert.equal(read.budget.snapshot().external,2);
  for(const action of [()=>f.store.stmt("UPDATE publication_attempts SET observation='retry_pending' WHERE id=?",seed.id).run(),
    ()=>f.store.stmt('DELETE FROM publication_attempts WHERE id=?',seed.id).run(),
    ()=>f.store.stmt('UPDATE slot_policy SET active_attempt=?',seed.id).run()])await deniedUnchanged(f,action);
  const next=await proposal(f,10);await deniedUnchanged(f,()=>f.store.beginPublication(next.key,next.check));
  const nextOwner=invocation(f);const id=await nextOwner.store.beginAutomaticPublication(next.request,next.check);
  assert.equal((await policy(f)).active_attempt,id);assert.equal((await policy(f)).first_attempt,seed.id);assert.equal((await markers(f)).n,0);
}));

test('slot fresh current recovery may release an older original revision',()=>local(async f=>{
  const seed=await owner(f);await f.store.batch(f.store.cas(await f.store.revision()));
  const read=await release(f,seed);assert.notEqual(read.revision,read.p.revision);
  assert.equal((await policy(f)).active_attempt,null);const row=await f.store.latestPublication(seed.key);
  assert.equal(JSON.parse(row.observation).original_revision,seed.p.revision);assert.equal(JSON.parse(row.observation).recovery_revision,read.revision);
}));

test('slot unknown null run binds a fresh GET run only in its negative release',()=>local(async f=>{
  const seed=await owner(f,{run:null});assert.equal(seed.p.run_id,null);
  const read=await release(f,seed),row=await f.store.latestPublication(seed.key),archive=JSON.parse(row.observation);
  assert.equal(read.p.run_id,null);assert.equal(row.run_id,77);assert.equal(archive.old_run_id,null);assert.equal(archive.run_id,77);
  assert.equal((await policy(f)).active_attempt,null);
}));

test('slot negative witness cannot be manufactured or transferred to another adapter',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision(),read=await negative(f,seed.p,job,revision);
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,seed.p,'resolved',77,'fake',revision,null,{source:read.github,witness:{protocol:'cla-negative-witness-v1'}}));
  const other=new GitHub(f.c,{clientSecret:'',pkcs8:'',evidenceKey:''},async()=>{throw Error('NO_TRANSPORT');},f.now,read.budget);
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,seed.p,'resolved',77,'fake',revision,null,{source:other,witness:read.witness}));
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot negative GET cannot mint a witness for a changed owned run',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision();
  await deniedUnchanged(f,()=>negative(f,seed.p,job,revision,{alter:run=>({...run,id:88})}));
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot negative witness rejects expiry at its fixed ten second boundary',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision();let observed=f.now();
  const read=await negative(f,seed.p,job,revision,{clock:()=>observed});observed+=10;
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,seed.p,'resolved',77,'expired',revision,null,{source:read.github,witness:read.witness}));
  assert.equal((await policy(f)).active_attempt,seed.id);assert.equal(f.now(),1800000000);
}));

test('slot witness capability itself is consumed exactly once',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision(),read=await negative(f,seed.p,job,revision);
  assert.equal(JSON.parse(read.github.consumeNegativeWitness(read.witness,seed.p,job,revision)).attempt,seed.id);
  assert.throws(()=>read.github.consumeNegativeWitness(read.witness,seed.p,job,revision));
  assert.equal((await policy(f)).active_attempt,seed.id,'consuming a capability alone never clears durable state');
}));

test('slot database deadline refuses release after a valid adapter consume and keeps the witness spent',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision(),read=await negative(f,seed.p,job,revision);
  const original=read.store.batch.bind(read.store);let batches=0;
  read.store.batch=statements=>{batches++;f.advance(10);return original(statements);};
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,seed.p,'resolved',77,'SQL deadline',revision,null,{source:read.github,witness:read.witness}));
  assert.equal(batches,1,'adapter consumed a still-live witness before the SQL-only time boundary');
  assert.equal((await policy(f)).active_attempt,seed.id);assert.equal((await f.store.latestPublication(seed.key)).observation,'');
  assert.throws(()=>read.github.consumeNegativeWitness(read.witness,seed.p,job,revision));
}));

test('slot SQL rejects a negative witness describing a different observed phase',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision(),read=await negative(f,seed.p,job,revision);
  const observation=JSON.parse(read.github.consumeNegativeWitness(read.witness,seed.p,job,revision));
  observation.observed_phase='manual';
  await deniedUnchanged(f,()=>f.store.stmt("UPDATE publication_attempts SET phase='resolved',run_id=77,observation=? WHERE id=?",canonical(observation),seed.id).run());
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

for(const changed of ['generation','revision','phase','proof'])test('slot nonrelease settlement independently rejects stale '+changed,()=>local(async f=>{
  const seed=await owner(f);let job=await jobFor(f,seed.key),p=seed.p;const revision=await f.store.revision();
  if(changed==='generation')await f.store.stmt('UPDATE outbox SET generation=generation+1 WHERE target_key=?',seed.key).run();
  if(changed==='revision'){
    await f.store.batch(f.store.cas(revision));job=await f.store.stmt('SELECT * FROM outbox WHERE target_key=?',seed.key).first();
  }
  if(changed==='phase')await f.store.stmt("UPDATE publication_attempts SET phase='manual' WHERE id=?",seed.id).run();
  if(changed==='proof')p={...p,proof_text:p.proof_text+' FORGED SNAPSHOT'};
  const current=invocation(f);
  await deniedUnchanged(f,()=>current.store.recordReconciliation(job,p,'manual',77,'stale observer',revision));
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

for(const changed of ['lease','generation','revision','phase'])test('slot stale negative recovery CAS rejects '+changed,()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision(),read=await negative(f,seed.p,job,revision);
  if(changed==='lease')await f.store.stmt('UPDATE outbox SET lease=? WHERE target_key=?',randomToken(),seed.key).run();
  if(changed==='generation')await f.store.stmt('UPDATE outbox SET generation=generation+1 WHERE target_key=?',seed.key).run();
  if(changed==='revision')await f.store.batch(f.store.cas(revision));
  if(changed==='phase')await f.store.stmt("UPDATE publication_attempts SET phase='manual' WHERE id=?",seed.id).run();
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,seed.p,'resolved',77,'stale',revision,null,{source:read.github,witness:read.witness}));
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot release late failure rolls back pointer witness and progress in one transaction',()=>local(async f=>{
  const seed=await owner(f),job=await jobFor(f,seed.key),revision=await f.store.revision();
  const progress=await f.store.openProgress(job,revision),[p]=await f.store.publicationBatch(job,progress),read=await negative(f,p,job,revision);
  const original=read.store.batch.bind(read.store);
  read.store.batch=statements=>original([...statements,read.store.stmt('INSERT INTO _guards(id,ok) VALUES(?,0)',randomToken())]);
  await deniedUnchanged(f,()=>read.store.recordReconciliation(job,p,'resolved',77,'late failure',revision,null,{source:read.github,witness:read.witness}));
  assert.equal((await policy(f)).active_attempt,seed.id);assert.equal((await f.store.latestPublication(seed.key)).observation,'');
}));

test('slot consumed witness cannot replay against a released or later owner',()=>local(async f=>{
  const seed=await owner(f),read=await release(f,seed);
  await deniedUnchanged(f,()=>read.store.recordReconciliation(read.job,read.p,'resolved',77,'replay',read.revision,null,{source:read.github,witness:read.witness}));
  const next=await proposal(f,10),current=invocation(f),nextId=await current.store.beginAutomaticPublication(next.request,next.check);
  await deniedUnchanged(f,()=>read.store.recordReconciliation(read.job,read.p,'resolved',77,'replay',read.revision,null,{source:read.github,witness:read.witness}));
  assert.equal((await policy(f)).active_attempt,nextId);
}));

test('slot holder positive path fails before issuing while manual owner cannot be silently cleared',()=>local(async f=>{
  const seed=await owner(f),next=await proposal(f,10),{store}=invocation(f);
  await deniedUnchanged(f,()=>store.beginPublication(next.key,next.check));
  await f.store.stmt("UPDATE publication_attempts SET phase='manual',reconcile_due=NULL WHERE id=?",seed.id).run();
  await deniedUnchanged(f,()=>f.store.stmt('UPDATE slot_policy SET active_attempt=NULL').run());
  assert.equal((await policy(f)).active_attempt,seed.id);
}));

test('slot suite made no outbound request',()=>assert.equal(outbound,0));
