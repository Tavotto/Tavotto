import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated,coauthor,sha,networkCalls} from './prefix-read-session-fixture.mjs';
import {validateLive} from '../src/native/config.ts';
import {config as workerConfig} from '../src/native/worker.ts';

const rejects=code=>error=>error?.code===code;
async function succeeds(fn){let result;await assert.doesNotReject(async()=>{result=await fn();});return result;}
const contents=f=>f.calls.filter(call=>call.path.includes('/contents/'));
function completeReads(f,prefix){
 assert.equal(f.state.metadataReads,2);assert.equal(f.state.branchReads,4);assert.equal(f.queries.length,4);
 assert.equal(f.state.queueRounds,4);assert.equal(f.state.tokenReads,1);
 for(let i=1;i<=prefix;i++){assert.equal(f.state.prReads.get(10+i),4);assert.equal(f.state.compareReads.get(10+i),2);}
 assert.equal(contents(f).length,3);assert.ok(contents(f).every(call=>call.search==='?ref='+f.c.referenceSha));
 assert.deepEqual(contents(f).map(call=>call.path.split('/contents/')[1]),['.github/cla-policy.json',f.c.policy.agreements.individual.path,f.c.policy.agreements.corporate.path]);
}

for(const [prefix,external] of [[2,24],[3,26],[4,28]])test('prefix read session actual P'+prefix+' costs '+external+' with all dynamic facts fresh',()=>isolated(async f=>{
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(result.publication,'not_attempted');
 assert.equal(result.protocol,'cla-merge-prefix-assessment-v1');assert.deepEqual(f.budget.snapshot(),{external,sql:6});assert.equal(f.calls.length,external);
 completeReads(f,prefix);assert.equal(f.state.installationReads,2);assert.equal(f.state.userReads,0);
 const base='/repos/'+f.c.repository;
 const prReads=()=>['/graphql',...Array.from({length:prefix},(_,i)=>base+'/compare/'+sha(2001+i)+'...'+sha(1001+i)),'/graphql'];
 const dynamic=[base,base+'/commits/main','/graphql',...prReads(),'/graphql',base+'/commits/main'];
 assert.deepEqual(f.calls.map(call=>call.path),[base+'/installation','/app/installations/'+f.c.installationId+'/access_tokens',...dynamic.slice(0,2),...contents(f).map(call=>call.path),...dynamic.slice(2),base+'/installation',...dynamic]);
},{prefix}));
test('prefix read session two personally completed contributors cost24 external and7 SQL',()=>isolated(async f=>{
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(result.publication,'not_attempted');
 assert.deepEqual(result.binding.contributors.map(person=>person.id),[2,3]);assert.deepEqual(f.budget.snapshot(),{external:24,sql:7});
 assert.equal(f.sqlReads.length,7);completeReads(f,2);assert.equal(f.state.installationReads,2);assert.equal(f.state.userReads,0);
},{personalContributors:2}));

test('prefix read session P8 refuses at unchanged35 external cap with no result or write',()=>isolated(async f=>{
 await assert.rejects(f.assess(),rejects('budget_deferred'));assert.equal(f.calls.length,35);assert.equal(f.budget.snapshot().external,35);
 assert.equal(f.state.tokenReads,1);assert.equal(f.state.installationReads,2);assert.equal(contents(f).length,3);
},{prefix:8}));
test('prefix read session P2 repeated coauthor performs fresh installation and identity twice for28 calls',()=>isolated(async f=>{
 coauthor(f);const result=await succeeds(f.assess);assert.equal(result.eligible,false);assert.equal(result.publication,'not_attempted');
 assert.equal(f.calls.length,28);assert.equal(f.budget.snapshot().external,28);assert.equal(f.state.installationReads,4);assert.equal(f.state.userReads,2);completeReads(f,2);
 const userIndexes=f.calls.flatMap((c,i)=>c.path==='/users/user3'?[i]:[]);assert.equal(userIndexes.length,2);
 assert.ok(userIndexes.every(i=>f.calls[i-1].path.endsWith('/installation')));assert.deepEqual(result.binding.contributors.map(p=>p.id),[2,3]);
}));
test('prefix read session P6 coauthor work refuses35 rather than reusing identity or relaxing capacity',()=>isolated(async f=>{
 coauthor(f);await assert.rejects(f.assess(),rejects('budget_deferred'));assert.equal(f.calls.length,35);assert.equal(f.budget.snapshot().external,35);
 assert.equal(f.state.tokenReads,1);assert.equal(f.state.installationReads,4);assert.equal(f.state.userReads,1);
},{prefix:6}));

test('prefix read session complete two-page queue is reread four times and charges28',()=>isolated(async f=>{
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(f.calls.length,28);assert.equal(f.budget.snapshot().external,28);
 assert.deepEqual(f.queries.map(q=>q.cursor),[null,'cursor:1',null,'cursor:1',null,'cursor:1',null,'cursor:1']);
 assert.equal(f.state.metadataReads,2);assert.equal(f.state.branchReads,4);assert.equal(f.state.installationReads,2);assert.equal(f.state.tokenReads,1);
},{pageSize:1}));
test('prefix read session extra fourth queue page exhausts existing35 budget without truncation',()=>isolated(async f=>{
 await assert.rejects(f.assess(),rejects('budget_deferred'));assert.equal(f.calls.length,35);assert.equal(f.budget.snapshot().external,35);assert.equal(f.state.tokenReads,1);
 assert.ok(f.queries.length>8);assert.equal(f.state.prReads.has(13),false);
},{pageSize:1,tail:2}));
test('prefix read session commit page101 is read anew in each snapshot and costs26',()=>isolated(async f=>{
 const original=f.commits.get(11)[0];f.prs.get(11).commits=101;f.commits.set(11,[...Array.from({length:100},(_,i)=>({...structuredClone(original),sha:sha(30000+i)})),original]);
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(f.calls.length,26);assert.equal(f.budget.snapshot().external,26);
 assert.equal(f.state.compareReads.get(11),4);assert.equal(f.state.compareReads.get(12),2);
 assert.deepEqual(f.calls.filter(c=>c.path.includes('/compare/'+sha(2001))).map(c=>c.search),['?per_page=100&page=1','?per_page=100&page=2','?per_page=100&page=1','?per_page=100&page=2']);
}));

for(const session of [false,'absent'])test('prefix read session '+session+' retains exact previous32 call behavior',()=>isolated(async f=>{
 const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(f.calls.length,32);assert.equal(f.state.installationReads,2);assert.equal(f.state.tokenReads,2);assert.equal(contents(f).length,6);
 await assert.rejects(f.github.withMergePrefixReads(f.target,async()=>{}),rejects('prefix_read_session_disabled'));assert.equal(f.calls.length,32);
},{session}));
test('prefix read session enabled flag leaves standalone snapshots uncached',()=>isolated(async f=>{
 await succeeds(f.snapshot);await succeeds(f.snapshot);assert.equal(f.calls.length,32);assert.equal(f.state.tokenReads,2);assert.equal(contents(f).length,6);assert.equal(f.sqlReads.length,0);
}));
for(const value of ['true',1,null])test('prefix read session config rejects nonboolean '+JSON.stringify(value),()=>isolated(async f=>{
 assert.throws(()=>validateLive({...f.c,mergePrefixReadSessionEnabled:value}),rejects('live_configuration_invalid'));assert.equal(f.calls.length,0);
}));
test('prefix read session config requires assessment and network capability',()=>isolated(async f=>{
 assert.throws(()=>validateLive({...f.c,mergePrefixAssessmentEnabled:false}),rejects('merge_prefix_disabled'));
 assert.throws(()=>validateLive({...f.c,githubEnabled:false}),rejects('network_disabled'));assert.equal(f.calls.length,0);
}));
test('prefix read session Worker enables only exact explicit environment flag',()=>isolated(async f=>{
 const env={CLA_CONFIG:JSON.stringify({...f.c,mergePrefixReadSessionEnabled:true}),CLA_GITHUB_ENABLED:'true',CLA_MERGE_PREFIX_ASSESSMENT_ENABLED:'true'};
 for(const value of [undefined,'false','TRUE','1',' true'])assert.equal(workerConfig({...env,CLA_MERGE_PREFIX_READ_SESSION_ENABLED:value}).mergePrefixReadSessionEnabled,false);
 assert.equal(workerConfig({...env,CLA_MERGE_PREFIX_READ_SESSION_ENABLED:'true'}).mergePrefixReadSessionEnabled,true);assert.equal(f.calls.length,0);
}));

for(const [field,value] of [['id',3],['app_id',3],['app_slug','other'],['suspended_at','SYNTHETIC SUSPENDED']])
for(const stage of ['second snapshot','second identity'])test('prefix read session fresh installation rejects '+field+' at '+stage,()=>isolated(async f=>{
 if(stage==='second identity')coauthor(f);
 const read=stage==='second snapshot'?2:4;f.hooks.installation=(data,state)=>{if(state.installationReads===read)data[field]=value;};
 await assert.rejects(f.assess(),rejects('installation_mismatch'));assert.equal(f.state.installationReads,read);assert.equal(f.state.tokenReads,1);
 assert.equal(f.state.userReads,stage==='second snapshot'?0:1);assert.equal(f.calls.at(-1).path,'/repos/'+f.c.repository+'/installation');
}));
test('prefix read session second metadata response is actually checked',()=>isolated(async f=>{
 f.hooks.metadata=(data,state)=>{if(state.metadataReads===2)data.id=708;};
 await assert.rejects(f.assess(),rejects('repository_mismatch'));assert.equal(f.state.metadataReads,2);assert.equal(f.state.tokenReads,1);
}));
for(const read of [2,3,4])test('prefix read session fresh main read'+read+' rejects changed tip despite identical legal bytes',()=>isolated(async f=>{
 f.hooks.branch=(data,state)=>{if(state.branchReads===read)data.sha=sha(7777);};
 await assert.rejects(f.assess(),rejects('merge_prefix_changed'));assert.equal(f.state.branchReads,read);assert.equal(contents(f).length,3);
}));
test('prefix read session second complete queue binding rejects changed entry identity',()=>isolated(async f=>{
 f.hooks.queue=(data,{round})=>{if(round>=3)data.data.repository.mergeQueue.entries.nodes[0].id='replacement';};
 await assert.rejects(f.assess(),rejects('merge_prefix_changed'));assert.equal(f.queries.length,4);assert.equal(f.state.tokenReads,1);
}));
test('prefix read session fourth queue read rejects a change after second PR enumeration',()=>isolated(async f=>{
 f.hooks.queue=(data,{round})=>{if(round===4)data.data.repository.mergeQueue.entries.nodes[0].id='replacement';};
 await assert.rejects(f.assess(),rejects('merge_prefix_changed'));assert.equal(f.queries.length,4);assert.equal(f.state.prReads.get(11),4);assert.equal(f.state.tokenReads,1);
}));
test('prefix read session second PR reread rejects a changed base',()=>isolated(async f=>{
 f.hooks.pr=(data,{number,read})=>{if(number===11&&read===4)data.base.sha=sha(8888);};
 await assert.rejects(f.assess(),rejects('state_changed'));assert.equal(f.state.prReads.get(11),4);assert.equal(f.state.tokenReads,1);
}));
test('prefix read session second coauthor lookup detects changed numeric identity',()=>isolated(async f=>{
 coauthor(f);f.hooks.user=(data,login,state)=>{if(state.userReads===2)data.id=4;};
 await assert.rejects(f.assess(),rejects('identity_resolution_mismatch'));assert.equal(f.state.userReads,2);assert.equal(f.state.installationReads,4);assert.equal(f.state.tokenReads,1);
}));
test('prefix read session failed identity GET never retries or remints token',()=>isolated(async f=>{
 coauthor(f);f.hooks.response=(call,data,state)=>call.path==='/users/user3'&&state.userReads===2?Response.json({message:'SYNTHETIC DENIED'},{status:403}):undefined;
 await assert.rejects(f.assess(),rejects('github_unavailable'));assert.equal(f.state.userReads,2);assert.equal(f.state.installationReads,4);assert.equal(f.state.tokenReads,1);assert.equal(f.calls.at(-1).path,'/users/user3');
}));
for(const prior of [11,12])test('prefix read session existing invocation charge'+prior+' is preserved through both snapshots',()=>isolated(async f=>{
 for(let i=0;i<prior;i++)f.budget.externalSignal();
 if(prior===11){const result=await succeeds(f.assess);assert.equal(result.eligible,true);assert.equal(f.calls.length,24);}
 else{await assert.rejects(f.assess(),rejects('budget_deferred'));assert.equal(f.calls.length,23);}
 assert.equal(f.budget.snapshot().external,35);assert.equal(f.state.tokenReads,1);
}));
test('prefix read session suite performs no default network request',()=>assert.equal(networkCalls(),0));
