import test from 'node:test';import assert from 'node:assert/strict';
import {candidate} from '../scripts/ci-candidate.mjs';import {waiting} from '../src/ci/wait.ts';
import {setup} from './ci-fixture.mjs';import {iso} from './delegation-fixture.mjs';
import {canonical} from '../src/core/crypto.ts';
import {randomToken} from '../src/core/crypto.ts';import {CIPending,readOnly} from '../src/ci/consumer.ts';
import worker from '../src/native/worker.ts';
globalThis.fetch=async()=>{throw Error('OUTBOUND_FORBIDDEN');};
const wait={enabled:true,maxAttempts:3,intervalMs:1000,timeoutMs:10000};
const authority=url=>new URL(url).origin==='https://cla.invalid';
const checks=url=>new URL(url).pathname.endsWith('/check-runs');
const pending=()=>Response.json({error:'proof_not_confirmed'},{status:403});
function execute(f,t,{transport=t.transport,pause=async()=>{},monotonic=()=>0}={}){
 return candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{transport,clock:f.now,pause,monotonic});
}
// Successful reads below consume a pre-existing synthetic confirmed record at
// one fixed time. No HTTP/publication or successful-publication expiry transition.
test('CI wait absent or disabled retains immediate missing-check rejection',async()=>{
 for(const value of [undefined,{enabled:false}])await iso(async f=>{const t=await setup(f);t.config.wait=value;let pauses=0;
  await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>checks(u)?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)}),e=>e.code==='ci_check_ambiguous');assert.equal(pauses,0);
 });
});
test('CI wait revalidates missing-check visibility at fixed time',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let lists=0,pauses=0;const start=f.now();
 const result=await execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>checks(u)&&++lists===1?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)});
 assert.equal(result.status,'success');assert.equal(pauses,1);assert.equal(lists,2);assert.equal(f.now(),start);
 assert.equal(t.calls.filter(c=>authority(c.url)).length,2);assert.ok(t.calls.every(c=>c.init.method==='GET'));
}));
test('CI wait retries only first authority exact current Worker pending response',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let reads=0,pauses=0;const start=f.now();
 const result=await execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>authority(u)&&++reads===1?pending():t.transport(u,i)});
 assert.equal(result.status,'success');assert.equal(reads,3);assert.equal(pauses,1);assert.equal(f.now(),start);
}));
test('CI wait second authority pending is terminal',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let reads=0,pauses=0;
 await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>authority(u)&&++reads===2?pending():t.transport(u,i)}),e=>e.code==='ci_live_state_changed');
 assert.equal(reads,2);assert.equal(pauses,0);
}));
for(const [status,body] of [[403,{error:'qualification_changed'}],[503,{error:'proof_not_confirmed'}],[429,{error:'proof_not_confirmed'}],[403,{error:'proof_not_confirmed',eligible:true}]])
test('CI wait refuses other status or pending shape '+status+JSON.stringify(body),()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let pauses=0;
 await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>authority(u)?Response.json(body,{status}):t.transport(u,i)}));assert.equal(pauses,0);
}));
test('CI wait caps pending attempts without authority or write requests',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let lists=0,pauses=0;
 await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>{if(checks(u)){lists++;return Response.json({total_count:0,check_runs:[]});}return t.transport(u,i);}}),e=>e.code==='ci_wait_exhausted');
 assert.equal(lists,3);assert.equal(pauses,2);assert.equal(t.calls.filter(c=>authority(c.url)).length,0);
}));
test('CI wait pins trusted source before any check appears',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;t.hooks.branch=n=>n===1?f.c.referenceSha:'f'.repeat(40);let pauses=0;
 t.hooks.contentRef=ref=>assert.ok([f.c.referenceSha,'f'.repeat(40)].includes(ref));
 await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>checks(u)?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)}),e=>e.code==='ci_source_changed');assert.equal(pauses,1);
}));
for(const change of ['head','base'])test('CI wait binds original '+change+' across retries',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let lists=0;
 await assert.rejects(execute(f,t,{pause:async()=>{if(change==='head')t.pr.head.sha='e'.repeat(40);else t.pr.base.ref='other';},transport:async(u,i)=>checks(u)&&++lists===1?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)}),e=>e.code==='ci_head_changed');
}));
for(const change of ['missing','run','attempt','proof'])test('CI wait pins candidate '+change,()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let reads=0,after=false;
 await assert.rejects(execute(f,t,{pause:async()=>{after=true;if(change==='run')t.run.id=102;if(change==='attempt')t.run.external_id='z'.repeat(43);if(change==='proof'){const p=JSON.parse(t.run.output.summary.split('\n')[1]);p.revision++;t.run.output.summary='tavotto-cla-v1\n'+canonical(p);}},transport:async(u,i)=>{
  if(change==='missing'&&after&&checks(u))return Response.json({total_count:0,check_runs:[]});if(authority(u)&&++reads===1)return pending();return t.transport(u,i);
 }}),e=>e.code==='ci_candidate_changed');assert.equal(reads,1);
}));
test('CI wait refuses revocation after pending instead of cached success',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let reads=0;
 await assert.rejects(execute(f,t,{pause:async()=>{await f.s.delegation.append(await f.control('revoke'));},transport:async(u,i)=>authority(u)&&++reads===1?pending():t.transport(u,i)}));
}));
test('CI wait expiry boundary rejects before a new read or successful result',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let reads=0;
 await assert.rejects(execute(f,t,{pause:async()=>{f.advance(120);},transport:async(u,i)=>authority(u)&&++reads===1?pending():t.transport(u,i)}),e=>e.code==='ci_wait_deadline');assert.equal(reads,1);
}));
test('CI wait monotonic deadline and wall rollback reject',async()=>{
 for(const mode of ['deadline','wall rollback','monotonic rollback'])await iso(async f=>{const t=await setup(f);t.config.wait=wait;let tick=100;
  await assert.rejects(execute(f,t,{monotonic:()=>tick,pause:async()=>{if(mode==='wall rollback')f.advance(-1);else tick=mode==='deadline'?10100:99;},transport:async(u,i)=>checks(u)?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)}),e=>e.code==='ci_wait_deadline');
 });
});
for(const phase of ['transport','body'])test('CI wait bounds noncooperating '+phase,{timeout:4000},()=>iso(async f=>{
 const t=await setup(f);t.config.wait={...wait,timeoutMs:1000};let signal,cancelled=false;
 const transport=async(u,i)=>{signal=i.signal;if(phase==='transport')return new Promise(()=>{});return new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{'content-type':'application/json'}});};
 await assert.rejects(candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{transport,clock:f.now}),e=>e.code==='ci_wait_deadline');assert.equal(signal.aborted,true);if(phase==='body')assert.equal(cancelled,true);
}));
test('CI wait validates configuration before transport',async()=>{
 const c={enabled:true};for(const w of [null,{}, {enabled:false,maxAttempts:3},{...wait,maxAttempts:9},{...wait,maxAttempts:1},{...wait,intervalMs:0},{...wait,timeoutMs:60001}])assert.throws(()=>waiting({...c,wait:w}),e=>e.code==='ci_wait_configuration_invalid');
 let calls=0;await assert.rejects(candidate({enabled:true,wait:{...wait,maxAttempts:9}},{repository:{id:707},number:9,pull_request:{number:9,head:{sha:'a'.repeat(40)}}},'pull_request','',{transport:async()=>{calls++;throw Error();}}));assert.equal(calls,0);
});
for(const kind of ['ambiguous','incomplete','oversize'])test('CI wait rejects '+kind+' check enumeration without pause',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let pauses=0;
 const data=kind==='ambiguous'?{total_count:2,check_runs:[t.run,{...t.run,id:102}]}:kind==='incomplete'?{total_count:1,check_runs:[]}:{total_count:1001,check_runs:[]};
 await assert.rejects(execute(f,t,{pause:async()=>{pauses++;},transport:async(u,i)=>checks(u)?Response.json(data):t.transport(u,i)}),e=>e.code===(kind==='ambiguous'?'ci_check_ambiguous':'ci_checks_incomplete'));assert.equal(pauses,0);
}));
test('CI wait default does not classify authority rejection as pending',()=>iso(async f=>{
 const t=await setup(f);await assert.rejects(execute(f,t,{transport:async(u,i)=>authority(u)?pending():t.transport(u,i)}),e=>e.code==='ci_read_unavailable');
}));
test('CI wait final successful read at global deadline still rejects',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let tick=0;t.hooks.authority=n=>{if(n===2)tick=10000;};
 await assert.rejects(execute(f,t,{monotonic:()=>tick}),e=>e.code==='ci_wait_deadline');
}));
test('CI wait original event and trusted config cannot mutate during pause',()=>iso(async f=>{
 const t=await setup(f);t.config.wait={...wait};let lists=0;
 let result;await assert.doesNotReject(async()=>{result=await execute(f,t,{pause:async()=>{t.config.origin='https://evil.invalid';t.config.wait.maxAttempts=999;t.event.pull_request.head.sha='e'.repeat(40);},transport:async(u,i)=>checks(u)&&++lists===1?Response.json({total_count:0,check_runs:[]}):t.transport(u,i)});});
 assert.equal(result.head_sha,f.gh.head);assert.ok(t.calls.every(c=>!c.url.includes('evil')));
}));
test('CI wait current Worker pending contract is an exact 403 rejection',()=>iso(async f=>{
 const env={DB:f.db,CLA_CONFIG:JSON.stringify(f.c),CLA_CI_VERIFICATION_ENABLED:'true',CLA_EVIDENCE_KEY:Buffer.from(f.keyBytes).toString('base64'),CLA_VERIFICATION_LIMITER:{limit:async()=>({success:true})}};
 const url=f.c.origin+'/verification/pr?'+new URLSearchParams({pr:'9',head:f.gh.head,run:'101',attempt:randomToken()});
 const response=await worker.fetch(new Request(url),env);assert.equal(response.status,403);assert.deepEqual(await response.json(),{error:'proof_not_confirmed'});
 const c={enabled:true,repository:f.c.repository,repositoryId:f.c.repositoryId,origin:f.c.origin,appId:f.c.appId,appSlug:f.c.appSlug,checkName:f.c.checkName};
 const reader=readOnly(c,'SYNTHETIC_TOKEN',async()=>worker.fetch(new Request(url),env),true);
 await assert.rejects(reader(url,'authority'),e=>e instanceof CIPending && e.reason==='confirmation_pending' && e.anchor===undefined);
}));
test('CI wait maps short proof lifetime to one monotonic deadline',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let tick=0,authorityReads=0,repoReads=0;
 const proof=JSON.parse(t.run.output.summary.split('\n')[1]);proof.expires_at=f.now()+2;t.run.output.summary='tavotto-cla-v1\n'+canonical(proof);
 await f.store.stmt('UPDATE publication_attempts SET proof_text=?,expires=?',t.run.output.summary,proof.expires_at).run();
 await assert.rejects(execute(f,t,{monotonic:()=>tick,pause:async()=>{tick=2000;},transport:async(u,i)=>{
  if(new URL(u).pathname==='/repos/'+f.c.repository)repoReads++;
  if(authority(u)&&++authorityReads===1)return pending();return t.transport(u,i);
 }}),e=>e.code==='ci_wait_deadline');assert.equal(repoReads,1);assert.equal(authorityReads,1);
}));
for(const mode of ['first success path','slow pending'])test('CI wait anchors proof deadline before '+mode,()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let tick=0,reads=0;
 const proof=JSON.parse(t.run.output.summary.split('\n')[1]);proof.expires_at=f.now()+2;t.run.output.summary='tavotto-cla-v1\n'+canonical(proof);
 await f.store.stmt('UPDATE publication_attempts SET proof_text=?,expires=?',t.run.output.summary,proof.expires_at).run();
 if(mode==='first success path')t.hooks.authority=n=>{if(n===2)tick=3000;};
 await assert.rejects(execute(f,t,{monotonic:()=>tick,pause:async()=>{tick+=1000;},transport:async(u,i)=>{
  if(mode==='slow pending'&&authority(u)&&++reads===1){tick=1500;return pending();}return t.transport(u,i);
 }}),e=>e.code==='ci_wait_deadline');if(mode==='slow pending')assert.equal(reads,1);
}));
for(const phase of ['transport','body','default clock'])test('CI wait bounds subsecond proof expiry during authority '+phase,()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;
 const proof=JSON.parse(t.run.output.summary.split('\n')[1]);proof.expires_at=f.now()+2;t.run.output.summary='tavotto-cla-v1\n'+canonical(proof);
 await f.store.stmt('UPDATE publication_attempts SET proof_text=?,expires=?',t.run.output.summary,proof.expires_at).run();
 // Fixed existing synthetic proof. Capture and fire the cancellation timer; no
 // publication or successful read is carried over an expiry transition.
 const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout,originalDate=Date.now;let timer,delay,observed,cancelled=false,signal;
 globalThis.setTimeout=(callback,ms)=>{timer=callback;delay=ms;return 1;};globalThis.clearTimeout=()=>{};
 if(phase==='default clock')Date.now=()=>f.now()*1000+900;
 try{
  await assert.rejects(candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{
   ...(phase==='default clock'?{}:{clock:f.now,wallMilliseconds:()=>f.now()*1000+900}),monotonic:()=>0,
   transport:async(u,i)=>{
    if(!authority(u))return t.transport(u,i);observed=delay;signal=i.signal;
    if(phase!=='body'){queueMicrotask(()=>timer());return new Promise(()=>{});}
    return new Response(new ReadableStream({pull(){queueMicrotask(()=>timer());},cancel(){cancelled=true;}}),{headers:{'content-type':'application/json'}});
   }
  }),e=>e.code==='ci_wait_deadline');
 }finally{globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;Date.now=originalDate;}
 assert.equal(signal.aborted,true);if(phase==='body')assert.equal(cancelled,true);
 assert.equal(observed,1100,'cancellation uses the remaining milliseconds in the original proof');
}));
for(const mode of ['invalid','nonfinite','rollback','expiry'])test('CI wait rejects millisecond wall '+mode,()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let milliseconds=f.now()*1000+900,reads=0;
 const proof=JSON.parse(t.run.output.summary.split('\n')[1]);
 await assert.rejects(candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{
  clock:f.now,wallMilliseconds:()=>milliseconds,monotonic:()=>0,
  transport:async(u,i)=>{if(authority(u)){reads++;milliseconds=mode==='invalid'?milliseconds+0.5:mode==='nonfinite'?NaN:mode==='rollback'?milliseconds-1:proof.expires_at*1000;return mode==='expiry'?t.transport(u,i):pending();}return t.transport(u,i);}
 }),e=>e.code==='ci_wait_deadline');assert.equal(reads,1);
}));
test('CI wait validates initial clock samples',async()=>{
 for(const source of ['monotonic','seconds','milliseconds'])await iso(async f=>{
  const t=await setup(f);t.config.wait=wait;let samples=0,reads=0;
  const first=normal=>samples++===0?NaN:normal;
  await assert.rejects(candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{
   clock:source==='seconds'?()=>first(f.now()):f.now,
   wallMilliseconds:source==='milliseconds'?()=>first(f.now()*1000+900):()=>f.now()*1000+900,
   monotonic:source==='monotonic'?()=>first(0):()=>0,
   transport:async(u,i)=>{reads++;return t.transport(u,i);}
  }),e=>e.code==='ci_wait_deadline');assert.equal(reads,0);
 });
});
test('CI wait validates every observed millisecond sample',()=>iso(async f=>{
 const t=await setup(f);t.config.wait=wait;let samples=0;
 const options={clock:f.now,monotonic:()=>0,transport:t.transport,wallMilliseconds:()=>{samples++;return f.now()*1000+900;}};
 assert.equal((await candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',options)).status,'success');
 const count=samples;assert.ok(count>1 && count<1000);
 for(const mode of ['nonfinite','rollback'])for(let n=mode==='rollback'?2:1;n<=count;n++){
  samples=0;
  await assert.rejects(candidate(t.config,t.event,'pull_request','SYNTHETIC_TOKEN',{
   ...options,wallMilliseconds:()=>++samples===n?(mode==='nonfinite'?NaN:f.now()*1000+899):f.now()*1000+900
  }),e=>['ci_wait_deadline','ci_source_invalid'].includes(e.code),'reject '+mode+' sample '+n);
  assert.ok(samples>=n);
 }
}));
