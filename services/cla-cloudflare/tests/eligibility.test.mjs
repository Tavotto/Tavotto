import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/native/worker.ts';
import {eligibility} from '../src/native/eligibility.ts';
import {fixture} from './publication-lifecycle-fixture.mjs';

// ADR 0172: the public, read-only live answer the trusted CI gate reads instead of a published check.
const NONCE='SYNTHETIC_NONCE_0123456789_abcdefghijklmnop';
const PR_KEYS=['base_sha','contributors','eligible','head_sha','kind','nonce','observed_at','policy_sha256','pr','protocol','repository_id','revision'];
const GROUP_KEYS=PR_KEYS.filter(k=>k!=='pr').concat('members').sort();
const tables=['head_requests','head_deliveries','targets','deliveries','outbox','publication_attempts','published_checks','refresh_requests','signatures','packets','audit'];
const counts=async f=>Object.fromEntries(await Promise.all(tables.map(async t=>[t,(await f.get(`SELECT count(*) n FROM ${t}`)).n])));
const ask=(f,target={kind:'pr',number:9},head=f.gh.head,nonce=NONCE)=>eligibility(f.invocation().service,target,head,nonce);
const group=f=>({kind:'merge_group',headSha:f.groupTarget.headSha,baseSha:f.groupTarget.baseSha});
const hold=async f=>f.s.hold(...f.args('contributor'),f.packet.id,await f.s.intent(...f.args('contributor'),f.packet.id,'hold'));

test('a signed PR is eligible and the answer binds PR, head, author, policy and nonce',async()=>{
 const f=await fixture({delegated:false});try{
  const r=await ask(f);
  assert.deepEqual(Object.keys(r).sort(),PR_KEYS);
  assert.equal(r.protocol,'cla-eligibility-v1');assert.equal(r.eligible,true);assert.equal(r.pr,9);assert.equal(r.kind,'pr');
  assert.equal(r.head_sha,f.gh.head);assert.equal(r.repository_id,f.c.repositoryId);assert.equal(r.nonce,NONCE);
  assert.deepEqual(r.contributors,[{id:2,login:'contributor'}]);
  assert.equal(r.observed_at,f.now());assert.match(r.policy_sha256,/^[0-9a-f]{64}$/);
 }finally{f.close();}
});
test('the answer is computed live and nothing is written or published',async()=>{
 const f=await fixture({delegated:false});try{
  const before=await counts(f);await ask(f);await ask(f);
  assert.deepEqual(await counts(f),before);assert.equal(f.mutations().length,0);
  assert.ok(f.calls.every(c=>c.method==='GET' || c.path.endsWith('/access_tokens') || c.path==='/graphql'),'only reads and read-token minting');
 }finally{f.close();}
});
test('a held contributor is ineligible and the answer does not say who',async()=>{
 const f=await fixture({delegated:false});try{
  assert.equal((await ask(f)).eligible,true);await hold(f);
  const r=await ask(f);assert.equal(r.eligible,false);assert.deepEqual(Object.keys(r).sort(),PR_KEYS);
  assert.deepEqual(r.contributors,[{id:2,login:'contributor'}]);
 }finally{f.close();}
});
test('a stale head is refused, never answered about another commit',async()=>{
 const f=await fixture({delegated:false});try{
  const stale='f'.repeat(40);assert.notEqual(stale,f.gh.head);
  await assert.rejects(ask(f,{kind:'pr',number:9},stale),e=>e.code==='eligibility_head_changed');
 }finally{f.close();}
});
test('a revision committed during evaluation is refused',async()=>{
 const f=await fixture({delegated:false});try{
  // evaluate() reads the revision twice itself; the third read is this answer's own closing check.
  const {service}=f.invocation(),original=service.store.revision.bind(service.store);let reads=0;
  service.store.revision=async()=>(await original())+(++reads>2?1:0);
  await assert.rejects(eligibility(service,{kind:'pr',number:9},f.gh.head,NONCE),e=>e.code==='state_changed');
 }finally{f.close();}
});
test('an authorization that expires during evaluation is refused, not answered',async()=>{
 const f=await fixture({delegated:false});try{
  const {service}=f.invocation(),original=service.evaluateTarget.bind(service);
  service.evaluateTarget=async(...a)=>{const check=await original(...a);f.advanceDatabase(check.proof.expires_at-f.now());return check;};
  await assert.rejects(eligibility(service,{kind:'pr',number:9},f.gh.head,NONCE),e=>e.code==='authorization_expired');
 }finally{f.close();}
});
for(const [name,target,head,nonce] of [
 ['short nonce',{kind:'pr',number:9},null,'short'],['nonce with a slash',{kind:'pr',number:9},null,'a/'.repeat(20)],
 ['bad head',{kind:'pr',number:9},'zz',NONCE],['bad pr',{kind:'pr',number:0},null,NONCE],
 ['group head differs',{kind:'merge_group',headSha:'c'.repeat(40),baseSha:'d'.repeat(40)},'e'.repeat(40),NONCE],
])test('malformed input is rejected before any read: '+name,async()=>{
 const f=await fixture({delegated:false});try{
  await assert.rejects(eligibility(f.invocation().service,target,head??f.gh.head,nonce),e=>e.code==='eligibility_invalid');
  assert.equal(f.calls.length,0);
 }finally{f.close();}
});
test('a queued merge group is judged on its whole prefix inside the free budget',async()=>{
 const f=await fixture({group:true,delegated:false,prefix:2});try{
  const r=await ask(f,group(f),f.groupTarget.headSha);
  assert.deepEqual(Object.keys(r).sort(),GROUP_KEYS);assert.equal(r.kind,'merge_group');assert.equal(r.eligible,true);
  assert.equal(r.base_sha,f.groupTarget.baseSha);assert.equal(r.members.length,2);assert.equal(r.nonce,NONCE);
  const {sql,external}=f.invocations.at(-1).budget.snapshot();assert.ok(external<=35,String(external));assert.ok(sql<=20,String(sql));
  assert.equal(f.mutations().length,0);
 }finally{f.close();}
});
test('a merge group containing a held contributor is ineligible',async()=>{
 const f=await fixture({group:true,delegated:false,prefix:2});try{
  await hold(f);assert.equal((await ask(f,group(f),f.groupTarget.headSha)).eligible,false);
 }finally{f.close();}
});
test('a commit that is not in the queue is refused',async()=>{
 const f=await fixture({group:true,delegated:false,prefix:2});try{
  const absent={kind:'merge_group',headSha:'c'.repeat(40),baseSha:f.groupTarget.baseSha};
  await assert.rejects(ask(f,absent,absent.headSha),e=>e.code==='merge_prefix_candidate_absent');
 }finally{f.close();}
});
test('the public route keeps disabled, abuse-control and exact-query gates',async()=>{
 const f=await fixture({delegated:false});try{
  const base={DB:f.db,CLA_CONFIG:JSON.stringify(f.c),CLA_GITHUB_ENABLED:'true',CLA_CI_VERIFICATION_ENABLED:'true'},limiter={limit:async()=>({success:true})};
  const url=(path,query)=>f.c.origin+path+'?'+new URLSearchParams(query),pr=url('/eligibility/pr',{pr:'9',head:f.gh.head,nonce:NONCE});
  const code=async(u,env)=>{const r=await worker.fetch(new Request(u),env);return [r.status,(await r.json()).error];};
  assert.equal((await worker.fetch(new Request(pr),{DB:f.db,CLA_CONFIG:JSON.stringify(f.c)})).status,503);
  assert.deepEqual(await code(pr,{...base,CLA_CI_VERIFICATION_ENABLED:'false'}),[403,'ci_verification_disabled']);
  assert.deepEqual(await code(pr,base),[503,'verification_abuse_control_required']);
  assert.deepEqual(await code(pr,{...base,CLA_VERIFICATION_LIMITER:{limit:async()=>({success:false})}}),[429,'verification_busy']);
  assert.deepEqual(await code(pr,{...base,CLA_VERIFICATION_LIMITER:{limit:async()=>{throw Error('down');}}}),[503,'verification_abuse_control_unavailable']);
  const env={...base,CLA_VERIFICATION_LIMITER:limiter};
  for(const bad of [pr+'&extra=1',url('/eligibility/pr',{pr:'09',head:f.gh.head,nonce:NONCE}),url('/eligibility/pr',{pr:'9',head:'zz',nonce:NONCE}),url('/eligibility/pr',{pr:'9',head:f.gh.head,nonce:'short'}),
   url('/eligibility/group',{pr:'9',head:f.gh.head,nonce:NONCE}),url('/eligibility/group',{base:'zz',head:f.gh.head,nonce:NONCE})])
   assert.deepEqual(await code(bad,env),[403,'eligibility_invalid'],bad);
  assert.equal(f.calls.length,0);
 }finally{f.close();}
});
