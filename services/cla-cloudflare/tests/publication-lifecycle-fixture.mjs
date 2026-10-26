import assert from 'node:assert/strict';
import {fixture as signingFixture} from './delegation-fixture.mjs';
import {GitHub} from '../src/native/github.ts';
import {LiveStore} from '../src/native/store.ts';
import {LiveService} from '../src/native/service.ts';
import {InvocationBudget} from '../src/native/budget.ts';
import {admitHead,verifyHeadOne} from '../src/native/automatic.ts';
import {reconcileOne} from '../src/native/background.ts';
import {verification} from '../src/native/verification.ts';
import {candidate} from '../scripts/ci-candidate.mjs';
import {bytes} from '../src/core/crypto.ts';

// Native code and cryptographic evidence run unchanged. Only the remote peer,
// database binding and clocks are synthetic. Never dispatch to the default fetch.
let outbound=0,keyPromise;
globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
export const networkCalls=()=>outbound;
async function appKey(){
 keyPromise??=crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']).then(async pair=>({publicKey:pair.publicKey,pkcs8:Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64')}));
 return keyPromise;
}
export async function fixture({delegated=true,grantLifetime,group=false,prefix=2}={}){
 const f=await signingFixture(delegated);
 if(grantLifetime!==undefined)f.grant.expires_at=f.now()+grantLifetime;
 if(delegated)await f.install();
 const packet=await f.create();await f.sign(packet);if(!delegated)await f.sign(packet,'holder');
 const c={...f.c,githubEnabled:true,backgroundEnabled:true,checkPublishingEnabled:true,automaticVerificationEnabled:true,automaticPublishingEnabled:true,confirmationSlotEnabled:true,...(group?{mergePrefixAssessmentEnabled:true,mergePrefixReadSessionEnabled:true}:{})};
 const credentials=await appKey(),tokens=new Map(),runs=new Map(),calls=[],invocations=[],hooks={};
 let tick=0,sequence=100,authorityReads=0,hidden=false;
 const head=()=>f.gh.head,pr=()=>({number:9,state:'open',user:{id:2,login:'contributor',type:'User'},commits:1,head:{sha:head()},base:{repo:{id:c.repositoryId},ref:'main',sha:'d'.repeat(40)}});
 const groupTarget={headSha:'e'.repeat(40),baseSha:'d'.repeat(40),baseRef:'refs/heads/main'};
 assert.ok(Number.isSafeInteger(prefix)&&prefix>=1&&prefix<=100);
 const entries=Array.from({length:prefix},(_,i)=>i+1).map(n=>({id:'SYNTHETIC_QUEUE_'+n,position:n,baseCommit:{oid:groupTarget.baseSha},headCommit:{oid:n===prefix?groupTarget.headSha:prefix===2?'f'.repeat(40):(2000+n).toString(16).padStart(40,'0')},pullRequest:{number:10+n,headRefOid:(n===1?'b':n===2?'c':n.toString(16)).repeat(40),baseRefName:'main'}}));
 const members=new Map(entries.map(e=>[e.pullRequest.number,{...pr(),number:e.pullRequest.number,head:{sha:e.pullRequest.headRefOid}}]));
 const get=(sql,...args)=>f.store.stmt(sql,...args).first();
 const transport=async(url,init)=>{
  const u=new URL(url),path=u.pathname,body=init.body===undefined?undefined:JSON.parse(init.body);
  assert.equal(u.origin,'https://api.github.com');assert.equal(init.redirect,'manual');assert.equal(init.cache,'no-store');
  const call={path,method:init.method,body};calls.push(call);await hooks.request?.(call);
  if(path===`/repos/${c.repository}/installation` || path===`/app/installations/${c.installationId}/access_tokens`){
   const parts=init.headers.Authorization.slice(7).split('.'),jwt=JSON.parse(Buffer.from(parts[1],'base64url'));
   assert.equal(jwt.iss,String(c.appId));assert.ok(jwt.iat<=f.now() && jwt.exp>f.now());
   assert.ok(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',credentials.publicKey,Buffer.from(parts[2],'base64url'),bytes(parts[0]+'.'+parts[1])));
   if(init.method==='GET')return Response.json({id:c.installationId,app_id:c.appId,app_slug:c.appSlug,suspended_at:null});
   assert.equal(init.method,'POST');assert.deepEqual(body.repository_ids,[c.repositoryId]);
   assert.deepEqual(Object.keys(body.permissions).sort(),['checks','contents','pull_requests']);
   assert.equal(body.permissions.contents,'read');assert.equal(body.permissions.pull_requests,'read');assert.ok(['read','write'].includes(body.permissions.checks));
   const expires=f.now()+(hooks.tokenLifetime??3600),token='SYNTHETIC_INSTALLATION_'+tokens.size;
   tokens.set(token,{expires,write:body.permissions.checks==='write'});
   return Response.json({token,expires_at:new Date(expires*1000).toISOString(),repositories:[{id:c.repositoryId}],permissions:{...body.permissions,metadata:'read'}});
  }
  const credential=init.headers.Authorization??init.headers.authorization,token=tokens.get(credential?.slice(7));
  if(credential!=='Bearer SYNTHETIC_CI_READ_ONLY'){
   assert.ok(token,'only a minted synthetic installation credential may reach the peer');
   if(token.expires<=f.now())return Response.json({message:'synthetic expired token'},{status:401});
  }else assert.equal(init.method,'GET');
  const base='/repos/'+c.repository;let data;
  if(group && path==='/graphql' && body.query.startsWith('query NativePrefixPRMetadata(')){
   assert.equal(init.method,'POST');assert.equal(token.write,false);
   assert.deepEqual(body.variables,{owner:c.repository.split('/')[0],name:c.repository.split('/')[1]});
   const numbers=[...body.query.matchAll(/p([0-9]+):pullRequest\(number:([0-9]+)\)/g)].map(m=>{assert.equal(m[1],m[2]);return Number(m[1]);});
   assert.ok(numbers.length>=2&&numbers.length<=100);assert.equal(new Set(numbers).size,numbers.length);
   const repository={databaseId:c.repositoryId};for(const n of numbers){const p=members.get(n);assert.ok(p);repository['p'+n]={number:p.number,state:p.state.toUpperCase(),headRefOid:p.head.sha,baseRefOid:p.base.sha,baseRefName:p.base.ref,baseRepository:{databaseId:p.base.repo.id},author:{__typename:p.user.type,login:p.user.login,databaseId:p.user.id},commits:{totalCount:p.commits}};}
   data={data:{repository}};await hooks.prMetadata?.(data,numbers);
  }else if(group && path==='/graphql'){
   assert.equal(init.method,'POST');assert.ok(body.query.startsWith('query('));assert.equal(body.variables.cursor,null);
   data={data:{repository:{databaseId:c.repositoryId,mergeQueue:{entries:{totalCount:entries.length,nodes:structuredClone(entries),pageInfo:{hasNextPage:false,endCursor:null}}}}}};
  }else if(path===base)data={id:c.repositoryId,full_name:c.repository,default_branch:'main'};
  else if(path===base+'/commits/main')data={sha:c.referenceSha};
  else if(path.startsWith(base+'/contents/')){
   const name=path.slice((base+'/contents/').length);assert.equal(u.searchParams.get('ref'),c.referenceSha);
   const content=name==='.github/cla-policy.json'?JSON.stringify(c.policy):name===c.policy.agreements.individual.path?'SYNTHETIC DELEGATED CLA ONLY':name===c.policy.agreements.corporate.path?'SYNTHETIC CORPORATE CLA':undefined;
   assert.notEqual(content,undefined);data={type:'file',path:name,encoding:'base64',content:Buffer.from(content).toString('base64')};
  }else if(path===base+'/pulls/9')data=pr();
  else if(group && path.startsWith(base+'/pulls/')){data=members.get(Number(path.split('/').at(-1)));assert.ok(data);}
  else if(group && path.startsWith(base+'/compare/')){
   const member=[...members.values()].find(p=>path===base+'/compare/'+p.base.sha+'...'+p.head.sha);assert.ok(member);
   data={total_commits:1,base_commit:{sha:member.base.sha},commits:[{sha:member.head.sha,author:member.user,commit:{author:{email:'2+contributor@users.noreply.github.com'},message:'SYNTHETIC GROUP MEMBER'}}]};
  }
  else if(path===base+'/compare/'+'d'.repeat(40)+'...'+head()){
   assert.equal(u.searchParams.get('per_page'),'100');assert.equal(u.searchParams.get('page'),'1');
   data={total_commits:1,base_commit:{sha:'d'.repeat(40)},commits:[{sha:head(),author:pr().user,commit:{author:{email:'2+contributor@users.noreply.github.com'},message:'SYNTHETIC COMMIT'}}]};
  }else if(path.includes('/commits/') && path.endsWith('/check-runs')){
   const matches=hidden?[]:[...runs.values()].filter(r=>path===base+'/commits/'+r.head_sha+'/check-runs');
   data={total_count:matches.length,check_runs:matches};
  }else if(path===base+'/check-runs' && init.method==='POST' || path.startsWith(base+'/check-runs/')){
   if(init.method==='GET'){data=runs.get(Number(path.split('/').at(-1)));assert.ok(data);}
   else{
    assert.equal(token.write,true);assert.ok(['POST','PATCH'].includes(init.method));
    const issued=await get('SELECT * FROM publication_attempts WHERE id=?',body.external_id);
    if(body.conclusion==='success'){
     assert.equal(issued?.phase,'issued','success mutation must follow the committed issued journal');
     assert.equal((await get('SELECT active_attempt FROM slot_policy')).active_attempt,issued.id);
     assert.equal(issued.proof_text,body.output.summary);assert.ok(issued.send_until>f.now());
    }
    const id=init.method==='POST'?++sequence:Number(path.split('/').at(-1)),old=runs.get(id);
    data={...old,...body,id,head_sha:old?.head_sha??body.head_sha,app:{id:c.appId,slug:c.appSlug}};
    runs.set(id,structuredClone(data));await hooks.afterMutation?.(call,data);
   }
  }else throw Error('UNEXPECTED_SYNTHETIC_PATH '+path);
  return Response.json(structuredClone(data));
 };
 const invocation=()=>{
  const budget=new InvocationBudget(()=>tick,()=>f.now()*1000),store=new LiveStore(f.db,budget,c),github=new GitHub(c,{pkcs8:credentials.pkcs8,clientSecret:'',evidenceKey:''},transport,f.now,budget,()=>tick),service=new LiveService(c,store,github,f.key);
  const result={service,budget};invocations.push(result);return result;
 };
 const admit=async({groupEvent=group,delivery=crypto.randomUUID(),change=x=>x}={})=>{
  const raw=bytes(JSON.stringify(change({action:groupEvent?'checks_requested':'opened',number:9,repository:{id:c.repositoryId,full_name:c.repository},installation:{id:c.installationId},...(groupEvent?{merge_group:{head_sha:groupTarget.headSha,base_sha:groupTarget.baseSha,base_ref:groupTarget.baseRef}}:{pull_request:pr()})})));
  const secret='SYNTHETIC_WEBHOOK_SECRET_NOT_A_REAL_CREDENTIAL',key=await crypto.subtle.importKey('raw',bytes(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const signature='sha256='+Buffer.from(await crypto.subtle.sign('HMAC',key,raw)).toString('hex');
  return admitHead(invocation().service,raw,signature,delivery,groupEvent?'merge_group':'pull_request',secret);
 };
 const ci=async()=>{
  const config={enabled:true,repository:c.repository,repositoryId:c.repositoryId,origin:c.origin,appId:c.appId,appSlug:c.appSlug,checkName:c.checkName};
  const event={repository:{id:c.repositoryId},...(group?{action:'checks_requested',merge_group:{head_sha:groupTarget.headSha,base_sha:groupTarget.baseSha,base_ref:groupTarget.baseRef}}:{number:9,pull_request:{number:9,head:{sha:head()}}})};
  return candidate(config,event,group?'merge_group':'pull_request','SYNTHETIC_CI_READ_ONLY',{clock:f.now,transport:async(url,init)=>{
   if(new URL(url).origin!==c.origin)return transport(url,init);
   assert.equal(init.method,'GET');assert.equal(init.headers.authorization,undefined);authorityReads++;await hooks.authority?.(authorityReads);
   const u=new URL(url);assert.equal(u.pathname,group?'/verification/group':'/verification/pr');
   try{return Response.json(await verification(invocation().service,group?{kind:'merge_group',headSha:groupTarget.headSha,baseSha:groupTarget.baseSha}:9,u.searchParams.get('head'),Number(u.searchParams.get('run')),u.searchParams.get('attempt')));}
   catch(e){return Response.json({error:e.code??'request_unavailable'},{status:403});}
  }});
 };
 return {...f,c,packet,groupTarget,entries,members,runs,calls,hooks,invocations,invocation,transport,admit,ci,get,
  get authorityReads(){return authorityReads;},setHidden:value=>{hidden=value;},
  advance:n=>{f.advance(n);tick+=n*1000;},advanceDatabase:f.advance,
  dispatch:()=>verifyHeadOne(invocation().service),recover:()=>reconcileOne(invocation().service),
  owned:()=>get('SELECT * FROM publication_attempts ORDER BY rowid DESC LIMIT 1'),
  slot:async()=>(await get('SELECT active_attempt FROM slot_policy'))?.active_attempt??null,
  mutations:()=>calls.filter(r=>['POST','PATCH'].includes(r.method)&&r.path.includes('/check-runs')),
  close:()=>{assert.equal(outbound,0);for(const i of invocations){assert.ok(i.budget.snapshot().sql<=40);assert.ok(i.budget.snapshot().external<=40);}f.close();}};
}
export async function isolated(fn,options){const f=await fixture(options);try{await fn(f);}finally{f.close();}}
