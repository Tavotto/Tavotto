import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture as evidenceFixture} from './delegation-fixture.mjs';
import {fixture as activationFixture} from './activation-fixture.mjs';
import {GitHub} from '../src/native/github.ts';
import {LiveService} from '../src/native/service.ts';
import {LiveStore,nativeSQL} from '../src/native/store.ts';
import {InvocationBudget} from '../src/native/budget.ts';
import {MERGE_PREFIX_QUERY,queueEntries,prefixFor,readQueue} from '../src/native/merge-prefix.ts';
import {canonical,sha256,randomToken} from '../src/core/crypto.ts';
import {parseProof} from '../src/core/proof.ts';
import {eventLocator} from '../src/ci/consumer.ts';
import {candidate} from '../scripts/ci-candidate.mjs';
import {config} from '../src/native/worker.ts';

// Every identity/key/document below is ephemeral synthetic fixture data. The
// in-process transport permits only reads and synthetic token exchanges; no
// Worker fetch, check publication, or successful expiry-crossing scenario runs.
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const sha=n=>n.toString(16).padStart(40,'0');
const user=id=>({id,login:id===1?'erwanjun':id===2?'contributor':'user'+id,type:'User'});
const rsa=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const pkcs8=Buffer.from(await crypto.subtle.exportKey('pkcs8',rsa.privateKey)).toString('base64');
const entry=(position,pr=position+8)=>({id:'QE/'+position,position,baseCommit:{oid:sha(700+position-1)},headCommit:{oid:sha(700+position)},pullRequest:{number:pr,headRefOid:sha(pr),baseRefName:'main'}});
const page=(nodes,{total=nodes.length,next=false,cursor=null,repo=707}={})=>({data:{repository:{databaseId:repo,mergeQueue:{entries:{totalCount:total,pageInfo:{hasNextPage:next,endCursor:cursor},nodes}}}}});

async function fixture({delegated=false,size=2}={}){
 const f=await evidenceFixture(delegated),server={entries:Array.from({length:size},(_,i)=>entry(i+1)),prs:new Map(),commits:new Map(),requests:[],counts:new Map(),hook:undefined,source:f.c.referenceSha};
 for(const e of server.entries){const id=e.pullRequest.number===9?2:3;server.prs.set(e.pullRequest.number,{number:e.pullRequest.number,state:'open',base:{repo:{id:707},ref:'main',sha:sha(600)},head:{sha:e.pullRequest.headRefOid},commits:1,user:user(id)});server.commits.set(e.pullRequest.number,[{sha:e.pullRequest.headRefOid,author:user(id),commit:{author:{email:''},message:'SYNTHETIC COMMIT'}}]);}
 const target={headSha:server.entries[size-1].headCommit.oid,baseSha:server.entries[size-1].baseCommit.oid,baseRef:'refs/heads/main'};
 const transport=async(url,init)=>{
  const u=new URL(url),path=u.pathname,count=(server.counts.get(path)??0)+1;server.counts.set(path,count);server.requests.push({path,method:init.method});
  assert.equal(u.origin,'https://api.github.com');assert.equal(init.redirect,'manual');assert.equal(init.cache,'no-store');
  if(server.hook)await server.hook({path,count,u,init});
  if(path==='/repos/synthetic/repo/installation'){assert.equal(init.method,'GET');return Response.json({id:2,app_id:123,app_slug:'synthetic-cla',suspended_at:null});}
  if(path==='/app/installations/2/access_tokens'){
   assert.equal(init.method,'POST');assert.deepEqual(JSON.parse(init.body),{repository_ids:[707],permissions:{contents:'read',pull_requests:'read',checks:'read'}});
   return Response.json({token:'SYNTHETIC_EPHEMERAL_TOKEN',expires_at:new Date((f.now()+3600)*1000).toISOString(),repositories:[{id:707}],permissions:{contents:'read',pull_requests:'read',checks:'read',metadata:'read'}});
  }
  if(path==='/graphql'){
   assert.equal(init.method,'POST');const body=JSON.parse(init.body);assert.equal(body.query,MERGE_PREFIX_QUERY);assert.deepEqual(body.variables,{owner:'synthetic',name:'repo',branch:'main',cursor:null});
   return Response.json(page(structuredClone(server.entries)));
  }
  assert.equal(init.method,'GET','no publishing or correction request is permitted');assert.equal(init.headers.Authorization,'Bearer SYNTHETIC_EPHEMERAL_TOKEN');
  if(path==='/repos/synthetic/repo')return Response.json({id:707,full_name:'synthetic/repo',default_branch:'main'});
  if(path==='/repos/synthetic/repo/commits/main')return Response.json({sha:server.source});
  if(path.startsWith('/repos/synthetic/repo/contents/')){
   assert.equal(u.searchParams.get('ref'),server.source);const name=path.split('/contents/')[1];
   const text=name==='.github/cla-policy.json'?JSON.stringify(f.c.policy):name===f.c.policy.agreements.individual.path?'SYNTHETIC DELEGATED CLA ONLY':name===f.c.policy.agreements.corporate.path?'SYNTHETIC CORPORATE CLA':undefined;
   assert.notEqual(text,undefined);return Response.json({type:'file',path:name,encoding:'base64',content:Buffer.from(text).toString('base64')});
  }
  if(path.startsWith('/users/')){const login=decodeURIComponent(path.split('/').at(-1)),id=login==='erwanjun'?1:login==='contributor'?2:Number(login.slice(4));return Response.json(user(id));}
  if(path.startsWith('/repos/synthetic/repo/pulls/'))return Response.json(server.prs.get(Number(path.split('/').at(-1))));
  if(path.includes('/compare/')){
   const head=path.split('...').at(-1),pr=[...server.prs.values()].find(p=>p.head.sha===head);assert.ok(pr,'unexpected comparison');
   return Response.json({total_commits:pr.commits,base_commit:{sha:pr.base.sha},commits:server.commits.get(pr.number)});
  }
  assert.fail('unexpected synthetic request '+path);
 };
 let monotonic=0;const c={...f.c,githubEnabled:true,mergePrefixAssessmentEnabled:true},budget=new InvocationBudget(()=>monotonic,()=>f.now()*1000),store=new LiveStore(f.db,budget,c),github=new GitHub(c,{pkcs8,clientSecret:'',evidenceKey:''},transport,f.now,budget),s=new LiveService(c,store,github,f.key);
 const signPersonal=async(id=2)=>{
  if(id===2){const p=await f.create();await f.sign(p);await f.sign(p,'holder');return p;}
  const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:id,login:user(id).login,csrf_hash:await sha256(csrf),expires:f.now()+3600});
  const args=[token,csrf,f.c.origin],p=await f.s.packet(...args,'individual');
  await f.s.sign(...args,p.id,await f.s.intent(...args,p.id,'contributor'),'contributor',f.evidence('contributor'));await f.sign(p,'holder');return p;
 };
 const state=()=>f.sql.prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*' ORDER BY name").all().map(({name})=>[name,f.sql.prepare('SELECT * FROM "'+name+'" ORDER BY rowid').all()]);
 return {...f,server,target,c,budget,meteredStore:store,github,assessmentService:s,assess:()=>s.assessMergePrefix(target),signPersonal,state,advanceMonotonic:ms=>{monotonic+=ms;}};
}
async function iso(fn,options){const f=await fixture(options);try{await fn(f);}finally{f.close();}}
const hasCode=code=>error=>error?.code===code;

test('independent merge prefix default switch requires exact environment true',()=>{
 const json=JSON.stringify({mergePrefixAssessmentEnabled:true});
 for(const value of [undefined,'false','TRUE','1','true '])assert.equal(config({CLA_CONFIG:json,CLA_MERGE_PREFIX_ASSESSMENT_ENABLED:value}).mergePrefixAssessmentEnabled,false);
 assert.equal(config({CLA_CONFIG:json,CLA_MERGE_PREFIX_ASSESSMENT_ENABLED:'true'}).mergePrefixAssessmentEnabled,true);
});
test('independent merge prefix disabled refuses before database or transport',()=>iso(async f=>{
 f.c.mergePrefixAssessmentEnabled=false;await assert.rejects(f.assess(),hasCode('merge_prefix_disabled'));await assert.rejects(f.github.mergePrefixSnapshot(f.target),hasCode('merge_prefix_disabled'));
 assert.equal(f.server.requests.length,0);assert.deepEqual(f.budget.snapshot(),{sql:0,external:0});
}));
for(const kind of ['missing','different'])test('independent merge prefix requires shared budget '+kind,()=>iso(async f=>{
 const store=kind==='missing'?f.store:new LiveStore(f.db,new InvocationBudget(()=>0,()=>f.now()*1000),f.c),s=new LiveService(f.c,store,f.github,f.key);
 await assert.rejects(s.assessMergePrefix(f.target),hasCode('invocation_budget_required'));assert.equal(f.server.requests.length,0);
}));
test('independent merge prefix all preceding and current real personal evidence qualify without writes',()=>iso(async f=>{
 await f.signPersonal(2);await f.signPersonal(3);const before=f.state(),r=await f.assess();
 assert.equal(r.protocol,'cla-merge-prefix-assessment-v1');assert.equal(r.strategy,'queue-prefix-v1');assert.equal(r.eligible,true);assert.equal(r.publication,'not_attempted');
 assert.equal(r.binding.kind,'merge_group');assert.equal(r.binding.pr,undefined);assert.deepEqual(r.binding.members,[{pr:9,head_sha:sha(9)},{pr:10,head_sha:sha(10)}]);assert.deepEqual(r.binding.contributors.map(p=>p.id),[2,3]);
 assert.equal(r.trusted_source_sha,f.c.referenceSha);assert.notEqual(r.binding.base_sha,r.trusted_source_sha,'queue base is a preceding candidate, not necessarily main');
 assert.equal(r.prefix_sha256,await sha256(canonical({target:f.target,entries:f.server.entries})));assert.equal(r.queue_sha256,await sha256(canonical(f.server.entries)));
 assert.equal(r.valid_until,f.now()+120);assert.deepEqual(f.state(),before);assert.deepEqual(f.budget.snapshot(),{sql:7,external:32});
}));
test('independent merge prefix unsigned predecessor blocks signed current PR',()=>iso(async f=>{
 await f.signPersonal(3);const r=await f.assess();assert.equal(r.eligible,false);assert.deepEqual(r.binding.contributors.map(p=>p.id),[2,3]);
}));
test('independent merge prefix unsigned current remains blocked by full union',()=>iso(async f=>{
 await f.signPersonal(2);assert.equal((await f.assess()).eligible,false);
}));
test('independent merge prefix author commit author and noreply coauthor all require actual signatures',()=>iso(async f=>{
 const pr=f.server.prs.get(9),commit=f.server.commits.get(9)[0];commit.author=user(3);commit.commit.message='SYNTHETIC\nCo-authored-by: Synthetic <4+user4@users.noreply.github.com>';
 await f.signPersonal(2);await f.signPersonal(3);await f.signPersonal(4);const r=await f.assess();assert.equal(r.eligible,true);assert.deepEqual(r.binding.contributors.map(p=>p.id),[2,3,4]);
 assert.equal(f.server.counts.get('/users/user4'),2);assert.equal(f.budget.snapshot().external,32);assert.equal(pr.user.id,2);
},{size:1}));
test('independent merge prefix malformed late coauthor rejects before any login resolution',()=>iso(async f=>{
 f.server.commits.get(9)[0].commit.message='Co-authored-by: Synthetic <4+user4@users.noreply.github.com>\nCo-authored-by: malformed';
 await assert.rejects(f.assess(),hasCode('coauthor_invalid'));assert.equal(f.server.counts.get('/users/user4')??0,0);
},{size:1}));
test('independent merge prefix unresolved unlinked author rejects without a guessed identity',()=>iso(async f=>{
 const commit=f.server.commits.get(9)[0];commit.author=null;commit.commit.author.email='unmapped@example.invalid';await assert.rejects(f.assess(),hasCode('coauthor_unresolved'));
},{size:1}));
for(const change of ['head','branch','author'])test('independent merge prefix PR before and after recheck '+change,()=>iso(async f=>{
 f.server.hook=({path,count})=>{if(path.endsWith('/pulls/9')&&count===2){const p=f.server.prs.get(9);if(change==='head')p.head.sha=sha(99);if(change==='branch')p.base.ref='elsewhere';if(change==='author')p.user.login='renamed';}};
 await assert.rejects(f.assess(),hasCode('state_changed'));
}));
for(const change of ['closed','repository','branch'])test('independent merge prefix rejects invalid live PR '+change,()=>iso(async f=>{
 const p=f.server.prs.get(9);if(change==='closed')p.state='closed';if(change==='repository')p.base.repo.id=708;if(change==='branch')p.base.ref='elsewhere';await assert.rejects(f.assess());
}));
test('independent merge prefix adapter rejects PR head disagreeing with queue entry',()=>iso(async f=>{
 f.server.entries[0].pullRequest.headRefOid=sha(90);await assert.rejects(f.github.mergePrefixSnapshot(f.target),hasCode('merge_prefix_changed'));
}));
test('independent merge prefix exact candidate base is required',()=>iso(async f=>{
 await assert.rejects(f.github.mergePrefixSnapshot({...f.target,baseSha:sha(999)}),hasCode('merge_prefix_candidate_absent'));
}));
test('independent merge prefix incomplete commit enumeration cannot yield assessment',()=>iso(async f=>{
 f.server.prs.get(9).commits=2;await assert.rejects(f.assess(),hasCode('commits_incomplete'));
}));
test('independent merge prefix queue drift within first snapshot rejects',()=>iso(async f=>{
 f.server.hook=({path,count})=>{if(path==='/graphql'&&count===2)f.server.entries[0].id='DIFFERENT';};await assert.rejects(f.github.mergePrefixSnapshot(f.target),hasCode('merge_prefix_changed'));
}));
for(const change of ['entry','order','source','contributor','tail'])test('independent merge prefix final full snapshot rejects '+change,()=>iso(async f=>{
 await f.signPersonal(2);await f.signPersonal(3);
 f.server.hook=({path,count})=>{
  if(path==='/graphql'&&count===3){
   if(change==='entry')f.server.entries[0].id='DIFFERENT';
   if(change==='order'){f.server.entries[0].position=2;f.server.entries[1].position=1;}
   if(change==='contributor')f.server.commits.get(9)[0].author=user(4);
   if(change==='tail')f.server.entries.push(entry(3));
  }
  if(change==='source'&&path.endsWith('/commits/main')&&count===3)f.server.source=sha(999);
 };
 await assert.rejects(f.assess(),hasCode('merge_prefix_changed'));
}));
test('independent merge prefix pre-existing hold blocks personal eligibility',()=>iso(async f=>{
 const p=await f.signPersonal();await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));assert.equal((await f.assess()).eligible,false);
},{size:1}));
test('independent merge prefix hold before final SQL invalidates prior eligibility',()=>iso(async f=>{
 const p=await f.signPersonal(),state=f.meteredStore.assessmentState.bind(f.meteredStore);f.meteredStore.assessmentState=async()=>{
  await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));return state();};
 await assert.rejects(f.assess(),hasCode('state_changed'));
},{size:1}));
test('independent merge prefix delegated authorization deadline caps assessment',()=>iso(async f=>{
 f.grant.expires_at=f.now()+5;await f.install();const p=await f.create();await f.sign(p);const before=f.state(),r=await f.assess();assert.equal(r.eligible,true);assert.equal(r.valid_until,f.now()+5);assert.deepEqual(f.state(),before);
},{delegated:true,size:1}));
test('independent merge prefix signed revocation during final snapshot rejects',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);f.server.hook=async({path,count})=>{if(path==='/graphql'&&count===3)await f.s.delegation.append(await f.control('revoke'));};await assert.rejects(f.assess(),hasCode('state_changed'));
},{delegated:true,size:1}));
test('independent merge prefix final SQL at assessment deadline rejects',()=>iso(async f=>{
 await f.signPersonal();const state=f.meteredStore.assessmentState.bind(f.meteredStore);f.meteredStore.assessmentState=async()=>{f.advance(120);return state();};await assert.rejects(f.assess(),hasCode('state_changed'));
},{size:1}));
test('independent merge prefix final cryptographic work cannot outlive shared work deadline',()=>iso(async f=>{
 await f.signPersonal();const state=f.meteredStore.assessmentState.bind(f.meteredStore);f.meteredStore.assessmentState=async()=>{const result=await state();f.advanceMonotonic(60000);return result;};
 await assert.rejects(f.assess(),e=>e.code==='budget_deferred'&&e.reason==='time');
},{size:1}));
test('independent merge prefix contributor capacity is checked before qualification fanout',()=>iso(async f=>{
 const p=f.server.prs.get(9);p.commits=7;f.server.commits.set(9,Array.from({length:7},(_,i)=>({sha:i===0?p.head.sha:sha(100+i),author:user(i+2),commit:{author:{email:''},message:'SYNTHETIC'}})));
 let qualifications=0;const old=f.meteredStore.qualification.bind(f.meteredStore);f.meteredStore.qualification=async(...args)=>{qualifications++;return old(...args);};
 await assert.rejects(f.assess(),hasCode('contributor_capacity_exceeded'));assert.equal(qualifications,0);
},{size:1}));
test('independent merge prefix stale agreement evidence is not inherited',async()=>{
 const f=await activationFixture();try{
  const old=await f.create();await f.sign(old);await f.sign(old,'holder');const next=await f.plan('2.0');await next.apply();
  const c={...next.config,githubEnabled:true,mergePrefixAssessmentEnabled:true},budget=new InvocationBudget(()=>0,()=>f.now()*1000),store=new LiveStore(f.db,budget,c);
  const e=entry(1),target={headSha:e.headCommit.oid,baseSha:e.baseCommit.oid,baseRef:'refs/heads/main'},pr={number:9,state:'open',headSha:sha(9),baseSha:sha(600),author:user(2),expectedCommits:1,commits:[{sha:sha(9),author:user(2),email:'',message:'SYNTHETIC'}]};
  const github={budget,resolveLogin:async login=>user(login==='erwanjun'?1:2),mergePrefixSnapshot:async()=>({repositoryId:707,kind:'merge_group',headSha:target.headSha,baseSha:target.baseSha,policy:c.policy,prs:[pr],members:[{pr:9,headSha:sha(9)}],queueEntries:[e],trustedSourceSha:c.referenceSha})};
  const s=new LiveService(c,store,github,f.key);assert.equal((await s.assessMergePrefix(target)).eligible,false);assert.equal((await f.store.packet(old.id)).version,'1.0');assert.equal((await f.store.signatures(old.id)).length,2);
 }finally{f.close();}
});
test('independent merge prefix evidence hash corruption is rejected',()=>iso(async f=>{
 await f.signPersonal();const triggers=f.sql.prepare("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name LIKE '%signatures'").all();for(const {name}of triggers)f.sql.exec('DROP TRIGGER "'+name+'"');
 f.sql.prepare(nativeSQL('UPDATE signatures SET evidence_hash=? WHERE role=?')).run('0'.repeat(64),'contributor');await assert.rejects(f.assess(),hasCode('evidence_unavailable'));
},{size:1}));
for(const resource of ['sql','external'])test('independent merge prefix cannot escape existing budget '+resource,()=>iso(async f=>{
 if(resource==='sql')f.budget.chargeSQL(20);else for(let i=0;i<35;i++)f.budget.externalSignal();const before=f.state();await assert.rejects(f.assess(),e=>e.code==='budget_deferred');assert.deepEqual(f.state(),before);
}));
test('independent merge prefix assessment remains rejected by publication proof CI and authority locator',()=>iso(async f=>{
 await f.signPersonal();const r=await f.assess();assert.throws(()=>parseProof('tavotto-cla-v1\n'+canonical(r),f.now()),hasCode('proof_invalid'));assert.throws(()=>eventLocator({repository:{id:707},merge_group:f.target},'merge_group'),hasCode('ci_event_rejected'));
 const before=f.server.requests.length;await assert.rejects(f.github.snapshot({kind:'merge_group',headSha:f.target.headSha,baseSha:f.target.baseSha}),hasCode('merge_queue_not_implemented'));
 await assert.rejects(candidate({enabled:true},{},'merge_group','',{transport:async()=>assert.fail('CI group transport forbidden')}));assert.equal(f.server.requests.length,before);
},{size:1}));

test('independent merge prefix opaque entry IDs accept visible ASCII without encoding guesses',()=>{
 const e=entry(1);e.id='opaque/+.:@value';assert.doesNotThrow(()=>queueEntries([e]));
});
test('independent merge prefix queue positions must be complete from one',()=>{
 const e=entry(2);assert.throws(()=>queueEntries([e]),hasCode('merge_prefix_incomplete'));
});
test('independent merge prefix cannot truncate a prefix beyond its explicit size cap',()=>{
 const entries=queueEntries(Array.from({length:101},(_,i)=>entry(i+1))),last=entries.at(-1);assert.throws(()=>prefixFor(entries,{headSha:last.headCommit.oid,baseSha:last.baseCommit.oid,baseRef:'refs/heads/main'}),hasCode('merge_prefix_incomplete'));
});
for(const change of ['entry','pr','position'])test('independent merge prefix duplicate queue identity rejects '+change,()=>{
 const values=[entry(1),entry(2)];if(change==='entry')values[1].id=values[0].id;if(change==='pr')values[1].pullRequest.number=values[0].pullRequest.number;if(change==='position')values[1].position=1;assert.throws(()=>queueEntries(values),hasCode('merge_prefix_incomplete'));
});
test('independent merge prefix queue pagination assembles complete prefix with exact cursor',async()=>{
 const seen=[];const values=await readQueue('synthetic/repo',707,async(query,variables)=>{assert.equal(query,MERGE_PREFIX_QUERY);seen.push(variables.cursor);return variables.cursor===null?page([entry(1)],{total:2,next:true,cursor:'CURSOR'}):page([entry(2)],{total:2});});
 assert.deepEqual(seen,[null,'CURSOR']);assert.deepEqual(values.map(v=>v.position),[1,2]);assert.equal(prefixFor(values,{headSha:sha(702),baseSha:sha(701),baseRef:'refs/heads/main'}).length,2);
});
for(const change of ['repo','errors','truncated','total','cursor'])test('independent merge prefix queue malformed pagination rejects '+change,async()=>{
 let calls=0;await assert.rejects(readQueue('synthetic/repo',707,async()=>{calls++;if(change==='repo')return page([entry(1)],{repo:708});if(change==='errors')return {...page([entry(1)]),errors:[{message:'SYNTHETIC'}]};if(change==='truncated')return page([entry(1)],{total:2});if(change==='total')return calls===1?page([entry(1)],{total:2,next:true,cursor:'NEXT'}):page([entry(2)],{total:3});return page([entry(calls)],{total:3,next:true,cursor:'SAME'}); }));
});
test('independent merge prefix tests never call global fetch',()=>assert.equal(outbound,0));
