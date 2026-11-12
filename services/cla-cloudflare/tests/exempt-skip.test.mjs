import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './publication-lifecycle-fixture.mjs';
import {iso} from './delegation-fixture.mjs';
import {admitHead} from '../src/native/automatic.ts';
import {bytes,canonical} from '../src/core/crypto.ts';

// Exempt-author PRs and all-exempt merge groups are skipped by the service (the trusted CI gate passes them by
// immutable id), so they must neither consume the capped operational tables nor the one-at-a-time queues.
const HOLDER={id:1,login:'erwanjun',type:'User'};
const count=async(f,table)=>(await f.get(`SELECT count(*) n FROM ${table}`)).n;
const exemptPR=x=>{x.pull_request.user={...HOLDER};return x;};
const makeExempt=(f,numbers)=>{for(const n of numbers)f.members.get(n).user={...HOLDER};};
const tables=['head_requests','head_deliveries','targets','deliveries','outbox','publication_attempts','published_checks','refresh_requests','reconciliation_progress'];

test('exempt-author PR webhook inserts nothing and makes zero GitHub or SQL calls',async()=>{
  const f=await fixture({delegated:false});try{
    assert.equal(await f.admit({change:exemptPR}),'exempt_author_skipped');
    for(const table of tables)assert.equal(await count(f,table),0,table);
    assert.equal(f.calls.length,0);assert.deepEqual(f.invocations.at(-1).budget.snapshot(),{sql:0,external:0});
    assert.equal(await f.dispatch(),'idle');assert.equal(f.mutations().length,0);
  }finally{f.close();}
});
test('non-exempt PR webhook is admitted and published exactly as before',async()=>{
  const f=await fixture({delegated:false});try{
    assert.equal(await f.admit(),'admitted');assert.equal(await count(f,'head_requests'),1);assert.equal(await count(f,'head_deliveries'),1);assert.ok(f.calls.length>0);
    assert.equal(await f.dispatch(),'automatic_publication_requires_reconciliation');assert.equal(f.mutations().length,1);
  }finally{f.close();}
});
test('a PR without an author, or with an unpinned or malformed id, is never skipped',async()=>{
  const f=await fixture({delegated:false});try{
    const users=[undefined,{id:3,login:'someone',type:'User'},{id:'1',login:'erwanjun',type:'User'},{id:null},{id:1.5}];
    for(let i=0;i<users.length;i++)assert.equal(await f.admit({delivery:'d-'+i,change:x=>{x.pull_request.user=users[i];return x;}}),'admitted');
    assert.equal(await count(f,'head_requests'),1);assert.equal(await count(f,'head_deliveries'),users.length);
  }finally{f.close();}
});

async function groupFixture(options){
  const f=await fixture({delegated:false,group:true,prefix:2,...options});return f;
}
test('all-exempt merge group finishes without a check run, the slot or any leftover row',async()=>{
  const f=await groupFixture();try{
    makeExempt(f,[11,12]);await f.admit();
    assert.equal(await count(f,'targets'),1);assert.equal(await count(f,'outbox'),1);
    const done=await f.recover();
    assert.equal(done.disposition,'group_exempt_no_publication');assert.equal(f.mutations().length,0);
    assert.ok(done.budget.external<=35,'external '+done.budget.external);assert.ok(done.budget.sql<=20);
    for(const table of tables)assert.equal(await count(f,table),0,table);
    assert.equal(await f.slot(),null);assert.equal(await count(f,'slot_policy'),0);
    assert.equal((await f.recover()).disposition,'idle');
    // A later evidence revision cannot resurrect the finished target, and capacity is free again.
    f.sql.prepare('UPDATE cla_native_v2_state SET revision=revision+1').run();assert.equal((await f.recover()).disposition,'idle');
    console.log('all-exempt P2 group budget',JSON.stringify(done.budget));
  }finally{f.close();}
});
test('all-exempt merge group does not wait for a busy confirmation slot',async()=>{
  const f=await groupFixture();try{
    assert.equal(await f.admit({groupEvent:false}),'admitted');assert.equal(await f.dispatch(),'automatic_publication_requires_reconciliation');
    assert.ok(await f.slot(),'a PR success check owns the slot');
    makeExempt(f,[11,12]);await f.admit();
    const done=await f.recover();
    assert.equal(done.disposition,'group_exempt_no_publication');assert.ok(done.budget.external<=35);
    assert.equal(f.mutations().length,1,'only the earlier PR check was ever written');assert.ok(await f.slot(),'the slot is still held by the PR attempt');
    assert.equal(await count(f,'targets'),1,'only the PR target remains');
    console.log('all-exempt group, busy slot budget',JSON.stringify(done.budget));
  }finally{f.close();}
});
test('mixed merge group still publishes exactly as before and keeps the slot wait',async()=>{
  const base=await groupFixture();let baseline;try{await base.admit();baseline=await base.recover();assert.equal(baseline.disposition,'group_publication_requires_reconciliation');}finally{base.close();}
  const f=await groupFixture();try{
    makeExempt(f,[11]);await f.admit();
    const sent=await f.recover();
    assert.equal(sent.disposition,'group_publication_requires_reconciliation');assert.equal(f.mutations().length,1);
    const proof=JSON.parse((await f.owned()).proof_text.split('\n')[1]);assert.deepEqual(proof.contributors.map(p=>p.id),[1,2]);
    assert.ok(sent.budget.external<=35);assert.ok(sent.budget.external-baseline.budget.external<=4,'only the exempt identity lookups are added');
    assert.equal((await f.recover()).disposition,'verified_read_only');assert.equal((await f.ci()).status,'success');
    console.log('mixed P2 group budget',JSON.stringify(sent.budget),'baseline',JSON.stringify(baseline.budget));
  }finally{f.close();}
});
test('mixed merge group behind a busy slot defers with one probe and no check run',async()=>{
  const f=await groupFixture();try{
    assert.equal(await f.admit({groupEvent:false}),'admitted');assert.equal(await f.dispatch(),'automatic_publication_requires_reconciliation');
    makeExempt(f,[11]);await f.admit();
    const waited=await f.recover();
    assert.equal(waited.disposition,'budget_deferred');assert.equal(f.mutations().length,1);assert.equal(await count(f,'targets'),2);
    assert.equal((await f.get("SELECT count(*) n FROM publication_attempts WHERE target_key LIKE 'merge:%'")).n,0);
    assert.ok(waited.budget.external<=35);console.log('mixed group, busy slot probe budget',JSON.stringify(waited.budget));
  }finally{f.close();}
});
test('exempt contributors never decide a group: a non-exempt member keeps the target for the normal path',async()=>{
  const f=await groupFixture();try{
    makeExempt(f,[12]);await f.admit();
    const sent=await f.recover();assert.equal(sent.disposition,'group_publication_requires_reconciliation');assert.equal(await count(f,'targets'),1);
  }finally{f.close();}
});

// --- bounded retention -------------------------------------------------------------------------------------------
const secret='SYNTHETIC WEBHOOK SECRET TEST ONLY 1234567890';
async function signed(data){const raw=bytes(JSON.stringify(data)),key=await crypto.subtle.importKey('raw',bytes(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const sig=new Uint8Array(await crypto.subtle.sign('HMAC',key,raw));return {raw,signature:'sha256='+[...sig].map(b=>b.toString(16).padStart(2,'0')).join('')};}
const enable=f=>{f.c.githubEnabled=true;f.c.automaticVerificationEnabled=true;f.c.mergePrefixAssessmentEnabled=true;};
const pullEvent=f=>({action:'synchronize',number:9,repository:{id:707,full_name:f.c.repository},installation:{id:2},pull_request:{number:9,state:'open',head:{sha:f.gh.head},base:{repo:{id:707},ref:'main'}}});
const groupEvent=(f,n)=>({action:'checks_requested',repository:{id:707,full_name:f.c.repository},installation:{id:2},
  merge_group:{base_ref:'refs/heads/main',head_sha:n.toString(16).padStart(40,'0'),base_sha:'d'.repeat(40)}});
async function admit(f,data,delivery,event='pull_request'){const r=await signed(data);return admitHead(f.s,r.raw,r.signature,delivery,event,secret);}
const n=async(f,table)=>(await f.store.stmt(`SELECT count(*) n FROM ${table}`).first()).n;
const addHead=(f,i,status,age)=>{
  f.sql.prepare("INSERT INTO cla_native_v2_head_requests VALUES(?,?,?,?,'',0,?,'')").run('k'+i,9,String(i).padStart(40,'0'),status,f.now()-age);
  f.sql.prepare('INSERT INTO cla_native_v2_head_deliveries VALUES(?,?,?,?)').run('h'+i,'x',  'k'+i,f.now()-age);
};
const DAY=86400;

test('retention: 200 lifetime finished heads do not stop admission, only terminal rows older than a day go',()=>iso(async f=>{
  enable(f);
  for(let i=0;i<100;i++)addHead(f,i,['done','dispatched','manual'][i%3],2*DAY);
  assert.equal(await n(f,'head_requests'),100);
  await admit(f,pullEvent(f),'new-1');                       // the 100-row cap is full, but its history is finished
  assert.equal(await n(f,'head_requests'),81,'20 finished rows pruned, then the new live head inserted');
  for(let r=0;r<4;r++)await admit(f,pullEvent(f),'new-1');
  assert.equal(await n(f,'head_requests'),1,'all finished history drained; only the live head remains');assert.equal(await n(f,'head_deliveries'),1);
  const live=await f.store.stmt('SELECT * FROM head_requests').first();assert.equal(live.status,'pending');
  let total=100;
  for(let round=0;round<5;round++){
    for(let i=0;i<20;i++)addHead(f,total++,'done',2*DAY);
    await admit(f,{...pullEvent(f),action:'reopened'},'round-'+round);
    assert.ok(await n(f,'head_requests')<=21);
  }
  assert.equal(total,200);assert.equal((await f.store.stmt("SELECT count(*) n FROM head_requests WHERE status='pending'").first()).n,1);
}));
test('retention never prunes live, recent or running head rows and refuses the 101st live head',()=>iso(async f=>{
  enable(f);
  for(let i=0;i<40;i++)addHead(f,i,'pending',10*DAY);        // old but live
  for(let i=40;i<60;i++)addHead(f,i,'running',10*DAY);
  for(let i=60;i<100;i++)addHead(f,i,'done',DAY-60);         // terminal but younger than a day
  await assert.rejects(admit(f,pullEvent(f),'blocked-1'));
  assert.equal(await n(f,'head_requests'),100);assert.equal(await n(f,'head_deliveries'),100);
  f.advance(120);                                               // the 'done' rows cross the one-day line
  await admit(f,pullEvent(f),'ok-1');
  assert.equal((await f.store.stmt("SELECT count(*) n FROM head_requests WHERE status IN ('pending','running')").first()).n,61);
  assert.equal((await f.store.stmt("SELECT count(*) n FROM head_requests WHERE status='done'").first()).n,20);
}));
test('head delivery rows stay immutable unless their request is terminal and over a day old',()=>iso(async f=>{
  addHead(f,1,'pending',10*DAY);addHead(f,2,'done',60);addHead(f,3,'manual',2*DAY);addHead(f,4,'running',2*DAY);
  for(const id of ['h1','h2','h4'])assert.throws(()=>f.sql.prepare('DELETE FROM cla_native_v2_head_deliveries WHERE id=?').run(id),/immutable/);
  assert.throws(()=>f.sql.prepare("UPDATE cla_native_v2_head_deliveries SET body_hash='y' WHERE id='h3'").run(),/immutable/);
  f.sql.prepare('DELETE FROM cla_native_v2_head_deliveries WHERE id=?').run('h3');assert.equal(await n(f,'head_deliveries'),3);
}));

const addTarget=(f,i,{outbox=false,phase,old=false,delivery=true}={})=>{
  const key=`merge:707:${i.toString(16).padStart(40,'0')}:${'d'.repeat(40)}`,json=canonical({kind:'merge_group',headSha:i.toString(16).padStart(40,'0'),baseSha:'d'.repeat(40)});
  f.sql.prepare('INSERT INTO cla_native_v2_targets VALUES(?,?)').run(key,json);
  if(delivery)f.sql.prepare('INSERT INTO cla_native_v2_deliveries VALUES(?,?,?,?)').run('g'+i,'x',key,f.now()-(old?2*DAY:60));
  // The delivery trigger queues every target; drop that entry unless the case needs queued work.
  if(delivery&&!outbox)f.sql.prepare('DELETE FROM cla_native_v2_outbox WHERE target_key=?').run(key);
  if(!delivery&&outbox)f.sql.prepare('INSERT INTO cla_native_v2_outbox(target_key,wanted_revision,due) VALUES(?,0,0)').run(key);
  if(phase)f.sql.prepare("INSERT INTO cla_native_v2_publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text) VALUES(?,?,?,0,1,?,5,1,1,'failure','p')")
    .run('a'+i,key,i.toString(16).padStart(40,'0'),phase);
  return key;
};
test('retention: resolved group history never counts against, nor is deleted by, the target cap',()=>iso(async f=>{
  enable(f);
  for(let i=1;i<=130;i++)addTarget(f,i,{phase:'resolved',old:true,outbox:i%2===0});   // revision bumps re-queue history; still not live
  await admit(f,groupEvent(f,9999),'grp-new','merge_group');
  assert.equal(await n(f,'targets'),131);assert.equal(await n(f,'publication_attempts'),130,'journal rows are audit and are never pruned');
  assert.equal(await n(f,'deliveries'),131-20,'at most 20 idle old delivery rows go per admission; targets with an outbox row or unresolved work keep theirs');
}));
test('retention: live targets (queued or unresolved) still hit the cap of 100 and are never pruned',()=>iso(async f=>{
  enable(f);
  for(let i=1;i<=50;i++)addTarget(f,i,{outbox:true,old:true});
  for(let i=51;i<=100;i++)addTarget(f,i,{phase:i%2?'manual':'issued',old:true});
  await assert.rejects(admit(f,groupEvent(f,9999),'grp-full','merge_group'));
  assert.equal(await n(f,'targets'),100);assert.equal(await n(f,'deliveries'),100);assert.equal(await n(f,'publication_attempts'),50);
}));
test('retention: only unreferenced merge targets are deleted; PR targets and referenced rows stay',()=>iso(async f=>{
  enable(f);
  const orphans=[];for(let i=1;i<=30;i++)orphans.push(addTarget(f,i,{delivery:false}));
  const queued=addTarget(f,200,{outbox:true,delivery:false}),withProgress=addTarget(f,201,{delivery:false}),withRefresh=addTarget(f,202,{delivery:false});
  f.sql.prepare('INSERT INTO cla_native_v2_reconciliation_progress(target_key) VALUES(?)').run(withProgress);
  f.sql.prepare("INSERT INTO cla_native_v2_refresh_requests VALUES(?,'a',0,'done',0,0,'r')").run(withRefresh);
  f.sql.prepare('INSERT INTO cla_native_v2_targets VALUES(?,?)').run('pr:707:9',canonical({kind:'pr',number:9}));
  await admit(f,groupEvent(f,9999),'grp-prune-1','merge_group');
  assert.equal(await n(f,'targets'),30+3+1+1-20);
  await admit(f,groupEvent(f,9998),'grp-prune-2','merge_group');
  const left=(await f.store.stmt('SELECT target_key FROM targets ORDER BY target_key').all()).results.map(r=>r.target_key);
  assert.ok(left.includes('pr:707:9'));for(const k of [queued,withProgress,withRefresh])assert.ok(left.includes(k));
  assert.equal(left.filter(k=>orphans.includes(k)).length,0,'orphans fully drained after two admissions');
}));

test('settling an exempt group is refused for a newer delivery generation or any journal row',()=>iso(async f=>{
  enable(f);
  await admit(f,groupEvent(f,1),'first','merge_group');const job=await f.store.claim(0);assert.ok(job);
  await admit(f,groupEvent(f,1),'second','merge_group');                       // a newer delivery bumps the generation
  await assert.rejects(f.store.settleExemptGroup(job),e=>e.code==='transaction_rejected');
  assert.equal(await n(f,'targets'),1);assert.equal(await n(f,'outbox'),1);assert.equal(await n(f,'deliveries'),2);
  f.advance(200);const again=await f.store.claim(0);assert.ok(again);
  f.sql.prepare("INSERT INTO cla_native_v2_publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text) VALUES('a','"+again.target_key+"','"+'1'.padStart(40,'0')+"',0,1,'resolved',5,1,1,'failure','p')").run();
  await assert.rejects(f.store.settleExemptGroup(again),e=>e.code==='transaction_rejected');assert.equal(await n(f,'targets'),1);
  f.sql.prepare("DELETE FROM cla_native_v2_publication_attempts").run();await f.store.settleExemptGroup(again);
  for(const t of ['targets','outbox','deliveries'])assert.equal(await n(f,t),0,t);
}));
