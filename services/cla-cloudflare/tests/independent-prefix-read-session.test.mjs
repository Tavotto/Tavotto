import test from 'node:test';import assert from 'node:assert/strict';
import {isolated,sha,user,networkCalls} from './independent-prefix-read-session-fixture.mjs';
import {InvocationBudget} from '../src/native/budget.ts';
import {config} from '../src/native/worker.ts';
const invalid=e=>e?.code==='prefix_read_session_invalid';
const twice=async reader=>{await reader.snapshot();await reader.snapshot();return 'SYNTHETIC READ ONLY';};
async function succeeds(work){let value;await assert.doesNotReject(async()=>{value=await work();});return value;}

test('independent read session switch uses exact environment true',()=>{
 const CLA_CONFIG=JSON.stringify({mergePrefixReadSessionEnabled:true});for(const v of [undefined,'false','TRUE','1','true '])assert.equal(config({CLA_CONFIG,CLA_MERGE_PREFIX_READ_SESSION_ENABLED:v}).mergePrefixReadSessionEnabled,false);
 assert.equal(config({CLA_CONFIG,CLA_MERGE_PREFIX_READ_SESSION_ENABLED:'true'}).mergePrefixReadSessionEnabled,true);
});
test('independent read session disabled before callback or I/O',()=>isolated(async f=>{
 let callbacks=0;await assert.rejects(f.reads(async()=>{callbacks++;}),e=>e.code==='prefix_read_session_disabled');assert.equal(callbacks,0);assert.equal(f.calls.length,0);
},{session:false}));
for(const [prefix,external]of [[2,24],[3,26],[4,28]])test('independent read session complete prefix measured cost '+prefix,()=>isolated(async f=>{
 const before=await f.digest(),r=await succeeds(f.assess);assert.equal(r.eligible,true);assert.equal(r.publication,'not_attempted');assert.equal(r.binding.members.length,prefix);assert.equal(await f.digest(),before);
 assert.equal(f.budget.snapshot().external,external);assert.equal(f.state.installationReads,2);assert.equal(f.state.tokenReads,1);assert.equal(f.calls.filter(c=>c.path.includes('/contents/')).length,3);assert.equal(f.queries.length,4);
 for(const n of f.prs.keys()){assert.equal(f.state.prReads.get(n),4);assert.equal(f.state.compareReads.get(n),2);}
},{prefix}));
test('independent read session eighth prefix member cannot exceed unchanged35 work cap',()=>isolated(async f=>{
 const before=await f.digest();await assert.rejects(f.assess(),e=>e.code==='budget_deferred');assert.equal(f.calls.length,35);assert.equal(f.state.tokenReads,1);assert.equal(await f.digest(),before);
},{prefix:8}));
test('independent read session disabled assessment preserves ordinary32 path',()=>isolated(async f=>{
 assert.equal((await f.assess()).eligible,true);assert.equal(f.budget.snapshot().external,32);assert.equal(f.state.tokenReads,2);assert.equal(f.calls.filter(c=>c.path.includes('/contents/')).length,6);
},{prefix:2,session:false}));
test('independent read session identity stays fresh twice with four installation checks',()=>isolated(async f=>{
 f.commits.get(11)[0].commit.message='Co-authored-by: Synthetic <3+user3@users.noreply.github.com>';assert.equal((await f.assess()).eligible,false);
 assert.equal(f.state.userReads,2);assert.equal(f.state.installationReads,4);assert.equal(f.state.tokenReads,1);assert.equal(f.budget.snapshot().external,28);
},{prefix:2}));
test('independent read session identity change on second read rejects',()=>isolated(async f=>{
 f.commits.get(11)[0].commit.message='Co-authored-by: Synthetic <3+user3@users.noreply.github.com>';f.hooks.user=(data,login,state)=>{if(state.userReads===2)data.id=4;};await assert.rejects(f.assess(),e=>e.code==='identity_resolution_mismatch');assert.equal(f.state.tokenReads,1);
}));
for(const boundary of ['snapshot','identity'])test('independent read session installation pause is still observed '+boundary,()=>isolated(async f=>{
 f.hooks.installation=(data,state)=>{if(state.installationReads===2)data.suspended_at='SYNTHETIC PAUSE';};
 await assert.rejects(f.reads(async r=>{await r.snapshot();if(boundary==='identity')await r.resolveLogin('user3');await r.snapshot();}),e=>e.code==='installation_mismatch');assert.equal(f.state.tokenReads,1);assert.equal(f.state.installationReads,2);
}));
for(const field of ['provider','exemption','repository','budget','key','tick'])test('independent read session callback closing context pin '+field,()=>isolated(async f=>{
 await assert.rejects(f.reads(async r=>{await twice(r);if(field==='provider')f.c.policy.provider.check_name='CHANGED';if(field==='exemption')f.c.exemptionIds.other=999;if(field==='repository')f.c.repositoryId=708;if(field==='budget')f.github.budget=new InvocationBudget();if(field==='key')f.secrets.pkcs8='CHANGED';if(field==='tick')f.github.tick=()=>0;return 'MUST NOT RETURN';}),invalid);
 assert.equal(f.state.tokenReads,1);
}));
for(const change of ['time','context'])test('independent read session final SQL completion cannot hide '+change,()=>isolated(async f=>{
 const state=f.store.assessmentState.bind(f.store);f.store.assessmentState=async()=>{const result=await state();if(change==='time')f.advance(3600);else f.c.policy.provider.check_name='CHANGED';return result;};
 await assert.rejects(f.assess(),invalid);assert.equal(f.state.tokenReads,1);
}));
test('independent read session expired actual token refuses before second I/O',()=>isolated(async f=>{
 f.hooks.token=data=>{data.expires_at=new Date((f.now()+5)*1000).toISOString();};
 await assert.rejects(f.reads(async r=>{await r.snapshot();const count=f.calls.length;f.advance(5);await assert.rejects(r.snapshot(),invalid);assert.equal(f.calls.length,count);}),invalid);assert.equal(f.state.tokenReads,1);
}));
test('independent read session frozen wall cannot extend monotonic token deadline',()=>isolated(async f=>{
 f.hooks.token=data=>{data.expires_at=new Date((f.now()+5)*1000).toISOString();};await assert.rejects(f.reads(async r=>{await r.snapshot();f.state.tick=4000;await r.snapshot();}),invalid);assert.equal(f.state.tokenReads,1);
}));
test('independent read session mint transit consumes token monotonic lifetime',()=>isolated(async f=>{
 f.hooks.token=data=>{data.expires_at=new Date((f.now()+3)*1000).toISOString();f.state.tick=3000;};await assert.rejects(f.reads(twice),invalid);assert.equal(f.state.metadataReads,0);assert.equal(f.state.tokenReads,1);
}));
for(const clock of ['wall','monotonic'])test('independent read session observed clock rollback closes '+clock,()=>isolated(async f=>{
 await assert.rejects(f.reads(async r=>{await twice(r);if(clock==='wall')f.advance(-1);else f.state.tick=-1;return 'MUST NOT RETURN';}),invalid);assert.equal(f.state.tokenReads,1);
}));
test('independent read session fractional expiry is not rounded upward',()=>isolated(async f=>{
 f.hooks.token=data=>{data.expires_at=new Date((f.now()+3)*1000+500).toISOString();};await assert.rejects(f.reads(async r=>{await r.snapshot();f.state.tick=2000;await r.snapshot();}),invalid);
}));
test('independent read session source SHA changes on cache hit reject before content fetch',()=>isolated(async f=>{
 f.hooks.branch=(data,state)=>{if(state.branchReads>=3)data.sha=sha(999);};await assert.rejects(f.reads(twice),e=>e.code==='merge_prefix_changed');assert.equal(f.calls.filter(c=>c.path.includes('/contents/')).length,3);assert.equal(f.state.tokenReads,1);
}));
test('independent read session cached policy cannot be changed through first snapshot',()=>isolated(async f=>{
 await f.reads(async r=>{const first=await r.snapshot();first.policy.agreements.individual.version='FORGED';first.policy.provider.check_name='FORGED';const second=await r.snapshot();assert.equal(second.policy.agreements.individual.version,'1.0');assert.equal(second.policy.provider.check_name,f.c.checkName);});assert.equal(f.state.tokenReads,1);
}));
test('independent read session third snapshot is terminal even when caught',()=>isolated(async f=>{
 await assert.rejects(f.reads(async r=>{await twice(r);await assert.rejects(r.snapshot(),invalid);return 'MUST NOT RETURN';}),invalid);assert.equal(f.state.tokenReads,1);
}));
test('independent read session caught semantic failure cannot return success',()=>isolated(async f=>{
 await assert.rejects(f.reads(async r=>{await twice(r);await assert.rejects(r.resolveLogin('invalid login'),e=>e.code==='identity_unresolved');return 'MUST NOT RETURN';}),invalid);
}));
test('independent read session partial content failure cannot resume or fall back',()=>isolated(async f=>{
 f.hooks.content=(data,name)=>{if(name.includes('CORPORATE'))data.content=Buffer.from('SYNTHETIC CORRUPTION').toString('base64');};
 await assert.rejects(f.reads(async r=>{await assert.rejects(r.snapshot(),e=>e.code==='agreement_hash_mismatch');const calls=f.calls.length;delete f.hooks.content;await assert.rejects(r.snapshot(),invalid);assert.equal(f.calls.length,calls);}),invalid);assert.equal(f.state.tokenReads,1);assert.equal(f.queries.length,0);
}));
test('independent read session concurrent identity operations poison whole session',()=>isolated(async f=>{
 await assert.rejects(f.reads(async r=>{await twice(r);const values=await Promise.allSettled([r.resolveLogin('user3'),r.resolveLogin('user3')]);assert.ok(values.some(v=>v.status==='rejected'));return 'MUST NOT RETURN';}),invalid);
}));
test('independent read session callback cannot return with an in-flight read',()=>isolated(async f=>{
 let pending,release;const gate=new Promise(resolve=>{release=resolve;});f.hooks.installation=async(data,state)=>{if(state.installationReads===3)await gate;};
 try{await assert.rejects(f.reads(async r=>{await twice(r);pending=r.resolveLogin('user3');return 'MUST NOT RETURN';}),invalid);}finally{release();await pending?.catch(()=>{});}
}));
test('independent read session escaped reader cannot restart after close',()=>isolated(async f=>{
 let escaped;await f.reads(async r=>{escaped=r;return twice(r);});const calls=f.calls.length;await assert.rejects(escaped.resolveLogin('user3'),invalid);assert.equal(f.calls.length,calls);assert.equal(f.state.tokenReads,1);
}));
test('independent read session next callback receives fresh credentials and content',()=>isolated(async f=>{
 const marker=new Error('SYNTHETIC CALLBACK ABORT');await assert.rejects(f.reads(async r=>{await r.snapshot();throw marker;}),e=>e===marker);await f.reads(twice);
 assert.equal(f.state.tokenReads,2);assert.equal(f.state.installationReads,3);assert.equal(f.calls.filter(c=>c.path.includes('/contents/')).length,6);assert.equal(f.calls.length,35);
}));
test('independent read session ordinary same-instance PR read keeps own token path',()=>isolated(async f=>{
 await f.reads(twice);const r=await f.github.snapshot({kind:'pr',number:11});assert.equal(r.kind,'pr');assert.equal(f.state.tokenReads,2);assert.equal(f.state.installationReads,3);assert.equal(f.calls.filter(c=>c.path.includes('/contents/')).length,6);
}));
test('independent read session scope response cannot add permission',()=>isolated(async f=>{
 f.hooks.token=data=>{data.permissions.merge_queues='read';};await assert.rejects(f.reads(twice),e=>e.code==='token_scope_mismatch');assert.equal(f.state.metadataReads,0);assert.equal(f.state.tokenReads,1);
}));
test('independent read session reader has no token or generic network capability',()=>isolated(async f=>{
 await f.reads(async r=>{assert.deepEqual(Object.keys(r).sort(),['resolveLogin','snapshot']);assert.equal(Object.isFrozen(r),true);return twice(r);});
}));
test('independent read session tests never use global fetch',()=>assert.equal(networkCalls(),0));
