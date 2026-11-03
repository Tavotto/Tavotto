import test from 'node:test';
import assert from 'node:assert/strict';
import {isolated,sha} from './prefix-read-session-fixture.mjs';
const metadata=f=>f.calls.filter(c=>c.body?.query?.startsWith('query NativePrefixPRMetadata('));
const has=code=>e=>e?.code===code;
test('batched metadata keeps four fresh reads of every P4 member and both complete commit snapshots',()=>isolated(async f=>{
 const result=await f.assess();assert.equal(result.eligible,true);assert.equal(result.binding.members.length,4);
 assert.equal(metadata(f).length,4);assert.equal(f.queries.length,4);assert.equal(f.state.branchReads,4);assert.equal(f.state.metadataReads,2);
 for(const n of [11,12,13,14]){assert.equal(f.state.prReads.get(n),4);assert.equal(f.state.compareReads.get(n),2);}
 assert.equal(f.calls.filter(c=>c.path.startsWith('/repos/'+f.c.repository+'/pulls/')).length,0);assert.equal(f.budget.snapshot().external,28);
},{prefix:4}));
for(const [name,change,code]of [
 ['missing member',d=>delete d.data.repository.p12,'merge_prefix_incomplete'],
 ['partial GraphQL error',d=>d.errors=[{message:'SYNTHETIC ERROR'}],'merge_prefix_unavailable'],
 ['extra member',d=>d.data.repository.p99=d.data.repository.p12,'merge_prefix_incomplete'],
 ['wrong repository',d=>d.data.repository.databaseId=708,'repository_mismatch'],
 ['wrong member number',d=>d.data.repository.p12.number=13,'pr_mismatch'],
 ['missing member object',d=>d.data.repository.p12=null,'pr_mismatch'],
 ['wrong base repository',d=>d.data.repository.p12.baseRepository.databaseId=708,'pr_mismatch'],
 ['retargeted base',d=>d.data.repository.p12.baseRefName='other','pr_mismatch'],
 ['closed PR',d=>d.data.repository.p12.state='CLOSED','pr_mismatch'],
 ['nonhuman identity kind',d=>d.data.repository.p12.author.__typename='Organization','identity_unresolved'],
 ['incomplete commit count',d=>d.data.repository.p12.commits.totalCount=0,'pr_mismatch'],
 ['unbounded commit count',d=>d.data.repository.p12.commits.totalCount=1001,'pr_mismatch'],
])test('batched metadata fails closed on '+name,()=>isolated(async f=>{
 f.hooks.prMetadata=change;await assert.rejects(f.assess(),has(code));
 assert.equal(metadata(f).length,1);assert.equal(f.calls.filter(c=>c.path.includes('/pulls/')).length,0);
},{prefix:4}));
for(const field of ['head','base','author','login','type','commits'])test('batched closing metadata rejects changed '+field,()=>isolated(async f=>{
 f.hooks.pr=(p,{read})=>{if(read===2){if(field==='head')p.head.sha=sha(999);if(field==='base')p.base.sha=sha(999);if(field==='author')p.user.id=3;if(field==='login')p.user.login='changed';if(field==='type')p.user.type='Bot';if(field==='commits')p.commits=2;}};
 await assert.rejects(f.assess(),has('state_changed'));assert.equal(metadata(f).length,2);
},{prefix:4}));
test('batched metadata denial is terminal without REST fallback or token retry',()=>isolated(async f=>{
 f.hooks.response=call=>call.body?.query?.startsWith('query NativePrefixPRMetadata(')?Response.json({message:'SYNTHETIC DENIED'},{status:403}):undefined;
 await assert.rejects(f.assess(),has('github_unavailable'));assert.equal(metadata(f).length,1);assert.equal(f.state.tokenReads,1);
 assert.equal(f.calls.filter(c=>c.path.includes('/pulls/')).length,0);
},{prefix:4}));
test('batched closing metadata cannot outlive its original read-token deadline',()=>isolated(async f=>{
 let n=0;f.hooks.prMetadata=()=>{if(++n===2)f.advance(3600);};await assert.rejects(f.assess(),has('prefix_read_session_invalid'));assert.equal(metadata(f).length,2);assert.equal(f.state.tokenReads,1);
},{prefix:4}));
