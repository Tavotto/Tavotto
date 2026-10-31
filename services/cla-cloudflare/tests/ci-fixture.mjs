import assert from 'node:assert/strict';
import {candidate} from '../scripts/ci-candidate.mjs';
import {verification} from '../src/native/verification.ts';import {randomToken} from '../src/core/crypto.ts';
export async function setup(f){
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
   else if(path.startsWith('/contents/')){if(hooks.contentRef)hooks.contentRef(u.searchParams.get('ref'));else assert.equal(u.searchParams.get('ref'),f.c.referenceSha);const name=path.slice('/contents/'.length);
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
 return {config,event,run,pr,calls,hooks,execute,transport};
}
