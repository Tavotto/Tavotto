import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import {pathToFileURL,fileURLToPath} from 'node:url';import {DatabaseSync} from 'node:sqlite';
const ROOT=process.env.CLA_SLOT_REVIEW_ROOT??fileURLToPath(new URL('../',import.meta.url));
const load=name=>import(pathToFileURL(ROOT+'/src/'+name));
const {LiveStore,nativeSQL}=await load('native/store.ts'),{LiveService}=await load('native/service.ts'),{GitHub}=await load('native/github.ts'),{reconcileOne}=await load('native/background.ts'),{verification}=await load('native/verification.ts');
const {InvocationBudget}=await load('native/budget.ts'),{canonical,randomToken,importAes}=await load('core/crypto.ts');
const NOW=1800000000;let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('GLOBAL_FETCH_FORBIDDEN');};
const C={origin:'https://cla.invalid',repositoryId:707,repository:'synthetic/recovery',referenceSha:'a'.repeat(40),holderId:1,holderName:'SYNTHETIC',appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',installationId:2,clientId:'synthetic',autoHolder:false,syntheticSigning:false,mockCheckPublishing:false,githubEnabled:true,oauthEnabled:false,signingEnabled:true,checkPublishingEnabled:true,backgroundEnabled:true,confirmationSlotEnabled:true,delegatedCountersigningEnabled:false,exemptionIds:{erwanjun:1},policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:'b'.repeat(64)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:'c'.repeat(64)}},exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:true,protocol:'tavotto-cla-v1',app_id:123,app_slug:'synthetic-cla',check_name:'synthetic-cla'}}};
function proof(head,pr=9,expired=true){return {protocol:'tavotto-cla-v1',repository_id:707,kind:'pr',pr,head_sha:head,base_sha:'e'.repeat(40),members:[],contributors:[{id:2,login:'synthetic'}],policy_sha256:'f'.repeat(64),revision:0,issued_at:expired?NOW-120:NOW,expires_at:expired?NOW:NOW+120};}
async function fixture(){
 const sql=new DatabaseSync(':memory:');sql.function('fixed_now',()=>NOW);const adapt=s=>s.replaceAll("strftime('%s','now')",'fixed_now()');
 for(const f of ['base.sql','native/schema.sql'])sql.exec(adapt(nativeSQL(fs.readFileSync(ROOT+'/src/'+f,'utf8'))));
 const prepare=(q,args=[])=>{const exec=()=>{const s=sql.prepare(adapt(q)),r=s.columns().length?s.all(...args):s.run(...args);return {success:true,results:Array.isArray(r)?r:[]};};return {bind:(...a)=>prepare(q,a),first:async()=>exec().results[0]??null,all:async()=>exec(),run:async()=>exec(),exec};};
 const db={prepare,batch:async ss=>{sql.exec('BEGIN IMMEDIATE');try{const r=ss.map(s=>s.exec());sql.exec('COMMIT');return r;}catch(e){sql.exec('ROLLBACK');throw e;}}};
 const c=structuredClone(C),setup=new LiveStore(db);await setup.initialize(c);
 const run=(q,...a)=>sql.prepare(adapt(nativeSQL(q))).run(...a),get=(q,...a)=>{const r=sql.prepare(adapt(nativeSQL(q))).get(...a);return r?{...r}:null;};
 const target=pr=>{const key=`pr:707:${pr}`;run('INSERT OR IGNORE INTO targets VALUES(?,?)',key,canonical({kind:'pr',number:pr}));return key;};
 const enqueue=(key,due=0,attempts=0)=>run(`INSERT INTO outbox(target_key,wanted_revision,due,attempts) VALUES(?,(SELECT revision FROM state WHERE id=1),?,?) ON CONFLICT(target_key) DO UPDATE SET due=excluded.due,attempts=excluded.attempts,lease='',lease_until=0`,key,due,attempts);
 const insert=({key=target(9),head='d'.repeat(40),phase='confirmed',conclusion='failure',attempts=0,due=NOW,expired=true,id=randomToken(),held=false,liveFence=false,runId=77}={})=>{
  const p=proof(head,Number(key.split(':')[2]),expired),text='tavotto-cla-v1\n'+canonical(p);
  if(held)run('INSERT INTO _guards VALUES(?,1)','confirmation-slot:'+id);
  run(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,send_until,reconcile_due,reconcile_attempts)
   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,id,key,head,0,p.expires_at,held?'issued':phase,runId,NOW-120,NOW,conclusion,text,held?NOW+120:0,due,attempts);
  if(held&&!liveFence)run("UPDATE publication_attempts SET phase='unknown',send_until=0 WHERE id=?",id);
  if(held&&phase!==(liveFence?'issued':'unknown'))run('UPDATE publication_attempts SET phase=? WHERE id=?',phase,id);
  return {id,key,head,text,expires:p.expires_at};
 };
 const owner=(options={})=>{const p=insert({...options,held:true,conclusion:'success',phase:options.phase??'manual'});enqueue(p.key,options.outboxDue??0,options.outboxAttempts??0);return p;};
 const key=await importAes(new Uint8Array(32));
 const invocation=(options={})=>{
  const cfg={...c,confirmationSlotEnabled:options.flag??true},budget=new InvocationBudget(()=>0,()=>NOW*1000),store=new LiveStore(db,budget,cfg),requests=[];let workSql;
  const settle=budget.settlement.bind(budget);budget.settlement=()=>{workSql??=budget.snapshot().sql;settle();};
  const state=new Map();let hook=false;
  const descriptor=head=>{const p=get('SELECT * FROM publication_attempts WHERE head_sha=? ORDER BY rowid DESC LIMIT 1',head);assert.ok(p);return {id:p.run_id??77,app:{id:c.appId,slug:c.appSlug},name:c.checkName,head_sha:head,external_id:p.id,status:'completed',conclusion:state.get(head)??(options.success?'success':'failure'),output:{summary:p.proof_text}};};
  const github=new GitHub(cfg,{clientSecret:'',pkcs8:'',evidenceKey:''},async(url,init)=>{
   assert.notEqual(init.method,'POST','no publication or token POST is permitted');assert.ok(['GET','PATCH'].includes(init.method));const path=new URL(url).pathname;requests.push({method:init.method,path});
   if(options.error)throw Error('SYNTHETIC NETWORK FAILURE');
   if(path.includes('/commits/')){
    const head=path.split('/commits/')[1].split('/')[0];if(options.missing)return Response.json({total_count:0,check_runs:[]});const p=descriptor(head);
    const checks=options.ambiguous?[p,{...p,id:p.id+1,external_id:'OTHER'}]:[p];return Response.json({total_count:checks.length,check_runs:checks});
   }
   const row=get('SELECT * FROM publication_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1',Number(path.split('/').at(-1)));assert.ok(row);let p=descriptor(row.head_sha);
   if(init.method==='PATCH'){
    assert.equal(options.allowPatch,true,'GET-only cases cannot mutate a run');const body=JSON.parse(init.body);assert.equal(body.conclusion,'failure');assert.equal(body.external_id,row.id);state.set(row.head_sha,'failure');p={...p,conclusion:'failure',output:body.output};
   }else if(options.afterGet&&!hook){hook=true;await options.afterGet({run,get,setup,p:row});}
   return Response.json(p);
  },()=>NOW,budget);
  github.installationToken=async()=>'SYNTHETIC-READ-OR-NEGATIVE-ONLY';
  for(const method of ['publish','publishNew','publishFresh'])github[method]=async()=>assert.fail('POSITIVE PUBLICATION FORBIDDEN');
  const service=new LiveService(cfg,store,github,key);let evaluates=0,confirms=0,clockReads=0;
  service.evaluate=async()=>{evaluates++;throw Error('EVALUATE_FORBIDDEN');};
  const record=store.recordReconciliation.bind(store);store.recordReconciliation=(...args)=>{if(args[2]==='confirmed'){confirms++;throw Error('CONFIRMED_FORBIDDEN');}return record(...args);};
  const clock=store.databaseNow.bind(store);store.databaseNow=async()=>{clockReads++;return clock();};
  return {service,store,github,budget,requests,get workSql(){return workSql;},get evaluates(){return evaluates;},get confirms(){return confirms;},get clockReads(){return clockReads;},go:()=>reconcileOne(service)};
 };
 return {sql,db,c,setup,run,get,target,enqueue,insert,owner,invocation,close:()=>sql.close()};
}
async function isolated(fn){const f=await fixture();try{await fn(f);}finally{f.close();}}
const held=f=>f.get('SELECT active_attempt FROM slot_policy')?.active_attempt;
const row=(f,p)=>f.get('SELECT * FROM publication_attempts WHERE id=?',p.id);
function unchangedProof(p,r){assert.equal(r.proof_text,p.text);assert.equal(r.expires,p.expires);}
function bounded(current){assert.ok((current.workSql??current.budget.snapshot().sql)<=20);assert.ok(current.budget.snapshot().sql<=40);assert.ok(current.budget.snapshot().external<=40);}
for(const options of [{phase:'manual',attempts:0},{phase:'manual',attempts:3},{phase:'unknown',attempts:3},{phase:'confirmed',outboxAttempts:3}])test('exact negative GET drains owner '+JSON.stringify(options),()=>isolated(async f=>{
 const p=f.owner(options),current=f.invocation(),result=await current.go();assert.equal(result.disposition,'remote_failure_observed');assert.equal(held(f),null);assert.equal(row(f,p).phase,'resolved');unchangedProof(p,row(f,p));
 assert.deepEqual(current.requests.map(r=>r.method),['GET','GET']);assert.equal(current.evaluates,0);assert.equal(current.confirms,0);bounded(current);console.log(JSON.stringify({case:options,work:current.workSql,total:current.budget.snapshot()}));
}));
test('disabled slot flag still drains the latched manual owner',()=>isolated(async f=>{
 const p=f.owner({attempts:3}),current=f.invocation({flag:false});assert.equal((await current.go()).disposition,'remote_failure_observed');assert.equal(held(f),null);assert.equal(row(f,p).phase,'resolved');bounded(current);
}));
for(const options of [{phase:'manual',expired:false},{phase:'confirmed',expired:false,outboxAttempts:3}])test('owned success input is negative only '+JSON.stringify(options),()=>isolated(async f=>{
 const p=f.owner(options),current=f.invocation({success:true,allowPatch:true});assert.equal((await current.go()).disposition,'remote_failure_verified');assert.equal(held(f),null);assert.equal(row(f,p).phase,'resolved');
 assert.deepEqual(current.requests.map(r=>r.method),['GET','GET','PATCH','GET']);assert.equal(current.evaluates,0);assert.equal(current.confirms,0);assert.equal(current.clockReads,0);unchangedProof(p,row(f,p));bounded(current);
}));
for(const mode of ['missing','error','ambiguous'])test('unavailable evidence keeps manual pointer and durable60 '+mode,()=>isolated(async f=>{
 const p=f.owner({attempts:3,outboxAttempts:3}),current=f.invocation({[mode]:true});await current.go();assert.equal(held(f),p.id);const saved=row(f,p);assert.equal(saved.phase,'manual');assert.equal(saved.reconcile_due,NOW+60);assert.equal(saved.reconcile_attempts,3);unchangedProof(p,saved);
 const outbox=f.get('SELECT * FROM outbox WHERE target_key=?',p.key);assert.equal(outbox.due,NOW+60);assert.equal(outbox.lease,'');assert.equal(outbox.attempts,3);const next=f.invocation();assert.equal((await next.go()).disposition,'idle');assert.equal(next.requests.length,0);bounded(current);
}));
test('initial missing unknown becomes scheduled manual without reopening head or refresh',()=>isolated(async f=>{
 const p=f.owner({phase:'unknown',attempts:0,runId:null,expired:false});
 f.run("INSERT INTO head_requests VALUES('MANUAL-HEAD',9,?,'manual','',0,?,'MANUAL RESULT')",p.head,NOW);
 f.run("INSERT INTO refresh_requests VALUES(?,?,0,'manual',3,0,'MANUAL REASON')",p.key,p.id);
 const head=f.get('SELECT * FROM head_requests'),refresh=f.get('SELECT * FROM refresh_requests'),current=f.invocation({missing:true});await current.go();
 assert.equal(row(f,p).phase,'manual');assert.equal(row(f,p).reconcile_due,NOW+60);assert.equal(row(f,p).reconcile_attempts,1);assert.equal(row(f,p).run_id,null);assert.equal(held(f),p.id);
 assert.deepEqual(f.get('SELECT * FROM head_requests'),head);assert.deepEqual(f.get('SELECT * FROM refresh_requests'),refresh);assert.equal(current.evaluates,0);assert.equal(current.confirms,0);bounded(current);
}));
test('finish keeps the persisted manual deadline rather than restarting60',()=>isolated(async f=>{
 const p=f.owner({attempts:3,due:NOW+180,outboxAttempts:3}),current=f.invocation();await current.go();assert.equal(current.requests.length,0);assert.equal(held(f),p.id);
 assert.equal(f.get('SELECT due FROM outbox WHERE target_key=?',p.key).due,NOW+180);assert.equal(row(f,p).reconcile_due,NOW+180);
}));
test('manual backoff survives expiry generation and another due row',()=>isolated(async f=>{
 const key=f.target(9),older=f.insert({key,head:'f'.repeat(40),phase:'confirmed',conclusion:'failure'}),p=f.owner({due:NOW+60,attempts:0});
 await f.setup.batch(f.setup.cas(0));f.run('UPDATE outbox SET due=0 WHERE target_key=?',key);
 const current=f.invocation();await current.go();assert.equal(row(f,older).phase,'resolved');assert.equal(held(f),p.id);assert.equal(row(f,p).phase,'manual');assert.equal(row(f,p).reconcile_due,NOW+60);
 assert.ok(current.requests.every(r=>!r.path.includes(p.head)));bounded(current);
}));
test('older due rows progress before repeatedly deferred manual owner',()=>isolated(async f=>{
 const key=f.target(9),older=f.insert({key,head:'f'.repeat(40),phase:'confirmed',conclusion:'failure'}),p=f.owner({attempts:3}),first=f.invocation();await first.go();
 assert.equal(row(f,older).phase,'resolved');assert.equal(row(f,p).phase,'manual');assert.equal(held(f),p.id);
 f.run('UPDATE outbox SET due=0 WHERE target_key=?',key);const second=f.invocation();assert.equal((await second.go()).disposition,'remote_failure_observed');assert.equal(held(f),null);assert.equal(row(f,p).phase,'resolved');bounded(first);bounded(second);
}));
test('legacy nonowner manual is not revived',()=>isolated(async f=>{
 const p=f.owner({due:NOW+60,outboxDue:NOW+60}),key=f.target(10),old=f.insert({key,head:'f'.repeat(40),phase:'manual',attempts:3});f.enqueue(key,0,0);
 const current=f.invocation();await current.go();assert.equal(current.requests.length,0);assert.equal(row(f,old).phase,'manual');assert.equal(held(f),p.id);
}));
test('legacy exhausted outbox cannot cross cap without active ownership',()=>isolated(async f=>{
 const p=f.owner({due:NOW+60,outboxDue:NOW+60}),key=f.target(10),old=f.insert({key,head:'f'.repeat(40),phase:'confirmed'});f.enqueue(key,0,3);
 const current=f.invocation();assert.equal((await current.go()).disposition,'idle');assert.equal(current.requests.length,0);assert.equal(row(f,old).phase,'confirmed');assert.equal(held(f),p.id);
}));
for(const phase of ['issued','manual'])test('live send fence dominates recovery cap '+phase,()=>isolated(async f=>{
 const p=f.owner({phase,attempts:3,liveFence:true,outboxAttempts:3}),current=f.invocation();assert.equal((await current.go()).disposition,'idle');assert.equal(current.requests.length,0);assert.equal(held(f),p.id);assert.equal(row(f,p).send_until,NOW+120);
}));
test('live other lease cannot be stolen for recovery',()=>isolated(async f=>{
 const p=f.owner({attempts:3,outboxAttempts:3});f.run('UPDATE outbox SET lease=?,lease_until=? WHERE target_key=?',randomToken(),NOW+120,p.key);const current=f.invocation();assert.equal((await current.go()).disposition,'idle');assert.equal(current.requests.length,0);assert.equal(held(f),p.id);
}));
for(const change of ['generation','revision','phase','lease'])test('fresh failure cannot settle a stale '+change,()=>isolated(async f=>{
 const p=f.owner({attempts:3}),current=f.invocation({afterGet:async()=>{
  if(change==='generation')f.run('UPDATE outbox SET generation=generation+1 WHERE target_key=?',p.key);
  if(change==='revision')await f.setup.batch(f.setup.cas(0));
  if(change==='phase')f.run("UPDATE publication_attempts SET phase='unknown' WHERE id=?",p.id);
  if(change==='lease')f.run('UPDATE outbox SET lease=? WHERE target_key=?',randomToken(),p.key);
 }});await current.go();assert.equal(held(f),p.id);assert.notEqual(row(f,p).phase,'resolved');unchangedProof(p,row(f,p));assert.equal(current.confirms,0);assert.equal(current.evaluates,0);bounded(current);
}));
test('manual or resolved old proof never regains public verification',()=>isolated(async f=>{
 const p=f.owner({expired:false}),first=f.invocation();await assert.rejects(verification(first.service,9,p.head,77,p.id),e=>e.code==='proof_not_confirmed');assert.equal(first.requests.length,0);
 const recover=f.invocation();await recover.go();const after=f.invocation();await assert.rejects(verification(after.service,9,p.head,77,p.id),e=>e.code==='proof_not_confirmed');assert.equal(after.requests.length,0);
}));
test('strict due remains future even when exhausted outbox is separately due',()=>isolated(async f=>{
 const p=f.owner({attempts:3,due:NOW+60,outboxAttempts:3}),current=f.invocation();await current.go();assert.equal(current.requests.length,0);assert.equal(held(f),p.id);assert.equal(row(f,p).reconcile_due,NOW+60);assert.equal(f.get('SELECT due FROM outbox WHERE target_key=?',p.key).due,NOW+60);
}));
test('a repeatedly missing manual owner must not outrank older due work on every scheduled tick',()=>isolated(async f=>{
 const owner=f.owner({phase:'manual',attempts:3,due:NOW,outboxDue:NOW,outboxAttempts:3});
 const key=f.target(10),older=f.insert({key,head:'f'.repeat(40),phase:'confirmed',conclusion:'failure',due:NOW-600});f.enqueue(key,NOW-600,0);
 const current=f.invocation(),job=await current.store.claim(0);
 assert.equal(job?.target_key,key,'manual recovery recurrence must preserve cross-target progress');
 assert.equal(current.requests.length,0);assert.equal(held(f),owner.id);assert.equal(row(f,older).phase,'confirmed');
}));
test('no source called global fetch',()=>assert.equal(outbound,0));
