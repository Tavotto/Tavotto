import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated,sha,user,networkCalls} from './merge-prefix-fixture.mjs';

// Subject: actual GitHub adapter + actual installationToken and SQLite reads.
// All HTTP-shaped responses are in-process synthetic data, never GitHub E2E.
// No publication method, Worker endpoint or successful expiry transition runs.
const reject=code=>error=>error?.code===code;
const connection=data=>data.data.repository.mergeQueue.entries;
async function succeeds(fn){let value;await assert.doesNotReject(async()=>{value=await fn();});return value;}

test('merge prefix adapter full prefix includes earlier PRs and excludes later queue members',()=>isolated(async f=>{
 const result=await succeeds(f.snapshot);
 assert.deepEqual(result.members,[{pr:11,headSha:sha(1001)},{pr:12,headSha:sha(1002)}]);
 assert.deepEqual(result.prs.map(pr=>pr.number),[11,12]);assert.equal(result.queueEntries.length,3);
 assert.equal(f.state.prReads.has(13),false);assert.equal(result.headSha,f.target.headSha);assert.equal(result.baseSha,f.target.baseSha);
 assert.equal(f.sqlReads.length,0);
},{prefix:2,tail:1}));

test('merge prefix adapter actual two-member assessment costs32 external and6 SQL with unchanged read-only state',()=>isolated(async f=>{
 const before=await f.digest(),result=await succeeds(f.assess);
 assert.equal(result.protocol,'cla-merge-prefix-assessment-v1');assert.equal(result.publication,'not_attempted');assert.equal(result.eligible,true);
 assert.equal(await f.digest(),before);assert.deepEqual(f.budget.snapshot(),{external:32,sql:6});assert.equal(f.calls.length,32);assert.equal(f.sqlReads.length,6);
 assert.equal(f.state.installationReads,2);assert.equal(f.state.tokenReads,2);assert.equal(f.queries.length,4);
 assert.equal(f.state.prReads.get(11),4);assert.equal(f.state.prReads.get(12),4);assert.equal(f.state.userReads,0);
 assert.ok(f.calls.every(call=>call.method==='GET'||call.path==='/graphql'||call.path==='/app/installations/2/access_tokens'));
}));

test('merge prefix adapter three-member assessment refuses unchanged35 external budget without a report or write',()=>isolated(async f=>{
 const before=await f.digest();await assert.rejects(f.assess(),reject('budget_deferred'));
 assert.equal(f.budget.snapshot().external,35);assert.equal(f.calls.length,35);assert.ok(f.budget.snapshot().sql<=20);assert.equal(await f.digest(),before);
},{prefix:3}));

test('merge prefix adapter disabled service and reader reject before SQL or token exchange',()=>isolated(async f=>{
 await assert.rejects(f.assess(),reject('merge_prefix_disabled'));await assert.rejects(f.snapshot(),reject('merge_prefix_disabled'));
 assert.equal(f.calls.length,0);assert.equal(f.sqlReads.length,0);
},{enabled:false}));

for(const change of [target=>target.baseRef='refs/heads/other',target=>target.headSha='invalid',target=>target.baseSha='invalid',target=>target.extra=true])
test('merge prefix adapter validates strict locator before any request '+String(change),()=>isolated(async f=>{
 const target=structuredClone(f.target);change(target);await assert.rejects(f.snapshot(target),reject('merge_prefix_target_invalid'));assert.equal(f.calls.length,0);
}));

test('merge prefix adapter real token handshake rejects added merge-queues permission',()=>isolated(async f=>{
 f.hooks.token=data=>{data.permissions.merge_queues='read';};
 await assert.rejects(f.snapshot(),reject('token_scope_mismatch'));assert.equal(f.calls.length,2);assert.equal(f.queries.length,0);
}));
test('merge prefix adapter real token handshake rejects wrong repository grant',()=>isolated(async f=>{
 f.hooks.token=data=>{data.repositories=[{id:708}];};
 await assert.rejects(f.snapshot(),reject('token_scope_mismatch'));assert.equal(f.queries.length,0);
}));
test('merge prefix adapter permission-denied queue response has no fallback or publication',()=>isolated(async f=>{
 f.hooks.response=call=>call.path==='/graphql'?Response.json({message:'SYNTHETIC DENIED'},{status:403}):undefined;
 await assert.rejects(f.snapshot(),reject('github_unavailable'));assert.equal(f.state.prReads.size,0);assert.equal(f.calls.at(-1).path,'/graphql');
}));

for(const field of ['id','full_name','default_branch'])test('merge prefix adapter rejects metadata identity '+field,()=>isolated(async f=>{
 f.hooks.metadata=data=>{data[field]=field==='id'?708:'other';};
 await assert.rejects(f.snapshot(),reject('repository_mismatch'));assert.equal(f.queries.length,0);
}));
test('merge prefix adapter validates GraphQL repository ID before using queue members',()=>isolated(async f=>{
 f.hooks.queue=data=>{data.data.repository.databaseId=708;};
 await assert.rejects(f.snapshot(),reject('repository_mismatch'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter refuses GraphQL errors and null queue',async()=>{
 for(const mode of ['errors','null'])await isolated(async f=>{
  f.hooks.queue=data=>{if(mode==='errors')data.errors=[{message:'SYNTHETIC'}];else data.data.repository.mergeQueue=null;};
  await assert.rejects(f.snapshot(),reject(mode==='errors'?'merge_prefix_unavailable':'merge_prefix_incomplete'));assert.equal(f.state.prReads.size,0);
 });
});

test('merge prefix adapter complete pagination visits stable cursors in both queue reads',()=>isolated(async f=>{
 const result=await succeeds(f.snapshot);assert.equal(result.queueEntries.length,3);
 assert.deepEqual(f.queries.map(q=>q.cursor),[null,'cursor:1','cursor:2',null,'cursor:1','cursor:2']);
 assert.deepEqual(f.queries.map(q=>q.body.variables.branch),Array(6).fill('main'));assert.equal(f.budget.snapshot().external,20);
},{prefix:2,tail:1,pageSize:1}));
test('merge prefix adapter changing middle-page total refuses before any PR read',()=>isolated(async f=>{
 f.hooks.queue=(data,{page})=>{if(page===2)connection(data).totalCount++;};
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.queries.length,2);assert.equal(f.state.prReads.size,0);
},{prefix:2,tail:1,pageSize:1}));
test('merge prefix adapter missing final page refuses without partial eligibility',()=>isolated(async f=>{
 f.hooks.queue=data=>{connection(data).pageInfo.hasNextPage=false;};
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.state.prReads.size,0);
},{prefix:2,tail:1,pageSize:1}));
test('merge prefix adapter repeated cursor is rejected at the first repeated cursor',()=>isolated(async f=>{
 f.hooks.queue=(data,{page})=>{if(page===2)connection(data).pageInfo.endCursor='cursor:1';};
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.queries.length,2);assert.equal(f.state.prReads.size,0);
},{prefix:2,tail:1,pageSize:1}));
test('merge prefix adapter empty continuing page is rejected immediately',()=>isolated(async f=>{
 f.hooks.queue=data=>{connection(data).nodes=[];};
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.queries.length,1);assert.equal(f.state.prReads.size,0);
},{prefix:2,tail:1,pageSize:1}));
for(const mode of ['too many total','oversized page','noninteger total','nonboolean continuation'])test('merge prefix adapter bounded page rejects '+mode,()=>isolated(async f=>{
 f.hooks.queue=data=>{const page=connection(data);if(mode==='too many total')page.totalCount=1001;else if(mode==='oversized page')page.nodes=Array(101).fill(page.nodes[0]);else if(mode==='noninteger total')page.totalCount=2.5;else page.pageInfo.hasNextPage='false';};
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter stops after ten incomplete pages without PR enumeration',()=>isolated(async f=>{
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.queries.length,10);assert.equal(f.state.prReads.size,0);
},{prefix:1,tail:999,pageSize:1}));

for(const mode of ['entry ID','PR number','position duplicate','position gap'])test('merge prefix adapter rejects duplicate or incomplete queue '+mode,()=>isolated(async f=>{
 if(mode==='entry ID')f.entries[1].id=f.entries[0].id;
 else if(mode==='PR number')f.entries[1].pullRequest.number=f.entries[0].pullRequest.number;
 else f.entries[1].position=mode==='position gap'?3:1;
 await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter accepts opaque visible-ASCII entry IDs without guessing their encoding',()=>isolated(async f=>{
 f.entries[0].id='QE:/+?@!';const snapshot=await succeeds(f.snapshot);assert.equal(snapshot.queueEntries[0].id,'QE:/+?@!');
}));
for(const bad of ['', 'x'.repeat(201), 'contains space','bad\nnewline','nonasciiπ'])test('merge prefix adapter rejects unsafe entry ID '+JSON.stringify(bad.length),()=>isolated(async f=>{
 f.entries[0].id=bad;await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));
}));

for(const field of ['headSha','baseSha'])test('merge prefix adapter candidate requires exact '+field+' AND its other SHA',()=>isolated(async f=>{
 const target={...f.target,[field]:sha(99999)};await assert.rejects(f.snapshot(target),reject('merge_prefix_candidate_absent'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter ambiguous head-and-base candidate refuses rather than selecting the first',()=>isolated(async f=>{
 f.entries[0].headCommit=structuredClone(f.entries[1].headCommit);f.entries[0].baseCommit=structuredClone(f.entries[1].baseCommit);
 await assert.rejects(f.snapshot(),reject('merge_prefix_candidate_absent'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter prefix branch mismatch refuses before PR enumeration',()=>isolated(async f=>{
 f.entries[0].pullRequest.baseRefName='other';await assert.rejects(f.snapshot(),reject('merge_prefix_incomplete'));assert.equal(f.state.prReads.size,0);
}));
test('merge prefix adapter later unqualified PR is outside the approved prefix',()=>isolated(async f=>{
 f.entries[2].pullRequest.baseRefName='other';f.prs.get(13).user=user(99);
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.deepEqual(result.binding.members.map(m=>m.pr),[11,12]);assert.equal(f.state.prReads.has(13),false);
},{prefix:2,tail:1}));

for(const mode of ['entry identity','ordered position'])test('merge prefix adapter second queue read detects '+mode+' change',()=>isolated(async f=>{
 f.hooks.queue=(data,{round})=>{if(round===2){const entries=connection(data).nodes;if(mode==='entry identity')entries[0].id='replacement';else [entries[0].position,entries[1].position]=[2,1];}};
 await assert.rejects(f.snapshot(),reject('merge_prefix_changed'));
}));
test('merge prefix adapter queued PR head must match the full current PR snapshot',()=>isolated(async f=>{
 f.entries[0].pullRequest.headRefOid=sha(7777);await assert.rejects(f.snapshot(),reject('merge_prefix_changed'));
}));
test('merge prefix adapter comparisons use each PR own base and head rather than queue SHAs',()=>isolated(async f=>{
 await succeeds(f.snapshot);const actual=f.calls.filter(c=>c.path.includes('/compare/')).map(c=>c.path.split('/compare/')[1]);
 assert.deepEqual(actual,[sha(2001)+'...'+sha(1001),sha(2002)+'...'+sha(1002)]);
 assert.ok(actual.every(value=>!value.includes(f.target.headSha)&&!value.includes(f.target.baseSha)));
}));
for(const field of ['head','base','author','branch'])test('merge prefix adapter current PR reread detects '+field+' change',()=>isolated(async f=>{
 f.hooks.pr=(data,{number,read})=>{if(number===11&&read===2){if(field==='author')data.user=user(3);else if(field==='branch')data.base.ref='other';else data[field].sha=sha(8888);}};
 await assert.rejects(f.snapshot(),reject('state_changed'));
}));
for(const mode of ['missing commits','wrong base','wrong total','head absent','duplicate commit'])test('merge prefix adapter complete commit comparison rejects '+mode,()=>isolated(async f=>{
 if(mode==='duplicate commit'){f.prs.get(11).commits=2;f.commits.get(11).push(structuredClone(f.commits.get(11)[0]));}
 f.hooks.compare=(data,{number})=>{if(number!==11)return;if(mode==='missing commits')data.commits=[];else if(mode==='wrong base')data.base_commit.sha=sha(7);else if(mode==='wrong total')data.total_commits++;else if(mode==='head absent')data.commits[0].sha=sha(8);};
 await assert.rejects(f.snapshot(),reject('commits_incomplete'));
}));
test('merge prefix adapter enumerates all commit pages using the same own PR SHA pair',()=>isolated(async f=>{
 const original=f.commits.get(11)[0];f.prs.get(11).commits=101;
 f.commits.set(11,[...Array.from({length:100},(_,i)=>({...structuredClone(original),sha:sha(30000+i)})),original]);
 const result=await succeeds(f.snapshot);assert.equal(result.prs[0].commits.length,101);
 assert.deepEqual(f.calls.filter(c=>c.path.includes('/compare/')).map(c=>c.search),['?per_page=100&page=1','?per_page=100&page=2']);
},{prefix:1}));

test('merge prefix adapter earlier unsigned author blocks a currently signed PR',()=>isolated(async f=>{
 f.prs.get(11).user=user(3);f.commits.get(11)[0].author=user(3);
 const result=await succeeds(f.assess);assert.equal(result.eligible,false);assert.deepEqual(result.binding.contributors.map(p=>p.id),[2,3]);
}));
for(const [message,code] of [['Co-authored-by: malformed','coauthor_invalid'],['Co-authored-by: Synthetic <missing@example.invalid>','coauthor_unresolved']])test('merge prefix adapter earlier malformed coauthor blocks before lookup '+code,()=>isolated(async f=>{
 f.commits.get(11)[0].commit.message=message;await assert.rejects(f.assess(),reject(code));assert.equal(f.state.userReads,0);assert.equal(f.sqlReads.length,1);
}));
test('merge prefix adapter coauthor numeric identity mismatch blocks after a real synthetic lookup',()=>isolated(async f=>{
 f.commits.get(11)[0].commit.message='Co-authored-by: Synthetic <3+user3@users.noreply.github.com>';
 f.hooks.user=data=>{data.id=4;};await assert.rejects(f.assess(),reject('identity_resolution_mismatch'));assert.equal(f.state.userReads,1);assert.equal(f.sqlReads.length,1);
}));
test('merge prefix adapter repeated coauthors deduplicate once per binding and re-resolve on the second snapshot',()=>isolated(async f=>{
 f.commits.get(11)[0].commit.message='Co-authored-by: Synthetic <3+user3@users.noreply.github.com>\nCo-authored-by: Synthetic <3+USER3@users.noreply.github.com>';
 const result=await succeeds(f.assess);assert.equal(result.eligible,false);assert.equal(f.state.userReads,2);assert.deepEqual(result.binding.contributors.map(p=>p.id),[2,3]);assert.equal(f.budget.snapshot().external,32);
},{prefix:1}));

test('merge prefix adapter suite made no default or real network request',()=>assert.equal(networkCalls(),0));
