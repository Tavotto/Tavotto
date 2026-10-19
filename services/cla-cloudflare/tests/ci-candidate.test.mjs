import test from 'node:test';import assert from 'node:assert/strict';
import {candidate} from '../scripts/ci-candidate.mjs';import {readOnly} from '../src/ci/consumer.ts';
import {verification} from '../src/native/verification.ts';import {randomToken} from '../src/core/crypto.ts';
import {iso} from './delegation-fixture.mjs';
import {spawnSync} from 'node:child_process';import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';import os from 'node:os';import path from 'node:path';
globalThis.fetch=async()=>{throw Error('OUTBOUND_FORBIDDEN');};
// A pre-existing fixed-time confirmed record is synthetic input. These tests
// never publish a Check Run or advance time across a successful publication.
async function setup(f){
 await f.install();const p=await f.create();await f.sign(p);const check=await f.s.evaluate(9),attempt=randomToken();
 const target=f.store.targetKey(f.c.repositoryId,{kind:'pr',number:9});await f.store.track(f.c.repositoryId,{kind:'pr',number:9});
 await f.store.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text)
 VALUES(?,?,?,?,?,'confirmed',101,?,?,'success',?)`,attempt,target,check.headSha,check.proof.revision,check.proof.expires_at,f.now(),f.now(),check.text).run();
 f.gh.readPublication=async()=>{};
 const config={enabled:true,repository:f.c.repository,repositoryId:f.c.repositoryId,origin:f.c.origin,appId:f.c.appId,appSlug:f.c.appSlug,checkName:f.c.checkName};
 const event={repository:{id:f.c.repositoryId},number:9,pull_request:{number:9,head:{sha:f.gh.head}}};
 const run={id:101,app:{id:f.c.appId,slug:f.c.appSlug},name:f.c.checkName,head_sha:f.gh.head,external_id:attempt,status:'completed',conclusion:'success',output:{summary:check.text}};
 const pr={number:9,state:'open',base:{repo:{id:f.c.repositoryId},ref:'main',sha:check.proof.base_sha},head:{sha:f.gh.head}};
 const calls=[],hooks={};let authority=0,prReads=0,branchReads=0;
 const transport=async(url,init)=>{
  const u=new URL(url);calls.push({url,init});assert.equal(init.method,'GET');assert.equal(init.redirect,'manual');assert.equal(init.cache,'no-store');
  let data;
  if(u.origin===f.c.origin){assert.equal(init.headers.authorization,undefined);authority++;if(hooks.authority)await hooks.authority(authority);
   data=await verification(f.s,Number(u.searchParams.get('pr')),u.searchParams.get('head'),Number(u.searchParams.get('run')),u.searchParams.get('attempt'));
  }else{assert.equal(init.headers.authorization,'Bearer SYNTHETIC_TOKEN');const path=u.pathname.replace('/repos/'+f.c.repository,'');
   if(path==='')data={id:f.c.repositoryId,full_name:f.c.repository,default_branch:'main'};
   else if(path==='/commits/main'){branchReads++;data={sha:hooks.branch?.(branchReads)??f.c.referenceSha};}
   else if(path.startsWith('/contents/')){assert.equal(u.searchParams.get('ref'),f.c.referenceSha);const name=path.slice('/contents/'.length);
    const value=name==='.github/cla-policy.json'?JSON.stringify(f.c.policy):name===f.c.policy.agreements.individual.path?'SYNTHETIC DELEGATED CLA ONLY':name===f.c.policy.agreements.corporate.path?'SYNTHETIC CORPORATE CLA':null;
    assert.notEqual(value,null);data={type:'file',path:name,encoding:'base64',content:Buffer.from(value).toString('base64')};
   }else if(path==='/pulls/9'){prReads++;data=structuredClone(pr);if(hooks.pr)hooks.pr(data,prReads);}
   else if(path.endsWith('/check-runs'))data={total_count:1,check_runs:[run]};
   else if(path==='/check-runs/101')data=run;
   else throw Error('UNEXPECTED_OFFLINE_READ '+path);
  }
  return Response.json(data);
 };
 const execute=()=>candidate(config,event,'pull_request','SYNTHETIC_TOKEN',{transport,clock:f.now});
 return {config,event,run,pr,calls,hooks,execute};
}
test('CI candidate consumes actual personal evidence and two live authority reads offline',()=>iso(async f=>{
 const t=await setup(f),result=await t.execute();assert.equal(result.status,'success');assert.equal(result.head_sha,f.gh.head);assert.equal(result.trusted_source_sha,f.c.referenceSha);
 assert.equal(t.calls.filter(c=>new URL(c.url).origin===f.c.origin).length,2);assert.ok(t.calls.every(c=>c.init.method==='GET'));
}));
test('CI candidate rejects a new head even when an old confirmed proof exists',()=>iso(async f=>{
 const t=await setup(f);t.event.pull_request.head.sha='e'.repeat(40);await assert.rejects(t.execute,e=>e.code==='ci_head_changed');
 assert.equal(t.calls.some(c=>new URL(c.url).origin===f.c.origin),false);
}));
test('CI candidate refuses a retargeted base branch at first and final read',async()=>{
 for(const at of [1,2])await iso(async f=>{const t=await setup(f);t.hooks.pr=(p,n)=>{if(n===at)p.base.ref='other';};await assert.rejects(t.execute);});
});
test('CI candidate rejects revocation between authority reads',()=>iso(async f=>{
 const t=await setup(f);t.hooks.authority=async n=>{if(n===2)await f.s.delegation.append(await f.control('revoke'));};await assert.rejects(t.execute);
}));
test('CI candidate rejects newly discovered unsigned contributors',()=>iso(async f=>{
 const t=await setup(f);f.gh.extra=[{sha:'e'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic unsigned'}];await assert.rejects(t.execute);
}));
test('CI candidate rejects changed trusted-main source during verification',()=>iso(async f=>{
 const t=await setup(f);t.hooks.branch=n=>n===2?'e'.repeat(40):f.c.referenceSha;await assert.rejects(t.execute,e=>e.code==='ci_final_state_changed');
}));
test('CI event fields cannot override provider, source or repository',()=>iso(async f=>{
 const t=await setup(f);Object.assign(t.event,{config:{enabled:true,origin:'https://evil.invalid'},script:'evil',policy:{provider:{configured:true}}});
 t.event.repository.url='https://evil.invalid';assert.equal((await t.execute()).status,'success');assert.ok(t.calls.every(c=>!c.url.includes('evil')));
}));
test('CI disabled and merge queue candidates reject before any transport',async()=>{
 let calls=0;const transport=async()=>{calls++;throw Error('MUST_NOT_READ');};
 await assert.rejects(candidate({enabled:false},{},'pull_request','',{transport}));await assert.rejects(candidate({enabled:true},{},'merge_group','',{transport}));assert.equal(calls,0);
});
test('checked-in disabled CI CLI actually exits nonzero without echoing credentials',()=>{
 const dir=mkdtempSync(path.join(os.tmpdir(),'cla-ci-cli-'));try{
  const event=path.join(dir,'event.json');writeFileSync(event,'{}');
  const r=spawnSync(process.execPath,['scripts/ci-candidate.mjs','--config','deployment/ci.disabled.json','--event-file',event,'--event','pull_request'],
   {encoding:'utf8',env:{...process.env,GH_TOKEN:'SYNTHETIC_MUST_NOT_PRINT_TOKEN'}});
  assert.equal(r.error,undefined);assert.equal(r.status,1);assert.equal(r.stdout,'');assert.match(r.stderr,/candidate rejected/);assert.ok(!r.stderr.includes('SYNTHETIC_MUST_NOT_PRINT_TOKEN'));
 }finally{rmSync(dir,{recursive:true,force:true});}
});
test('CI read-only transport confines token to GitHub and rejects redirects/origin escapes',async()=>{
 const c={enabled:true,repository:'synthetic/repo',repositoryId:707,origin:'https://cla.invalid',appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla'};
 let calls=0;const reader=readOnly(c,'SYNTHETIC_TOKEN',async(url,init)=>{calls++;assert.equal(init.redirect,'manual');return new Response(null,{status:302,headers:{location:'https://evil.invalid'}});});
 await assert.rejects(reader('https://api.github.com/repos/synthetic/repo','github'));await assert.rejects(reader('https://evil.invalid/verification/pr','authority'));
 await assert.rejects(reader('https://api.github.com/repos/other/repo','github'));assert.equal(calls,1);
});
