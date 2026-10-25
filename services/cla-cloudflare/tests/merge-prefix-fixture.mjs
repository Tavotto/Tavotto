import assert from 'node:assert/strict';
import {GitHub} from '../src/native/github.ts';
import {LiveService} from '../src/native/service.ts';
import {LiveStore} from '../src/native/store.ts';
import {InvocationBudget} from '../src/native/budget.ts';
import {canonical,sha256} from '../src/core/crypto.ts';
import {fixture as signingFixture} from './delegation-fixture.mjs';

// Only this in-process peer receives synthetic App JWTs. The default transport
// is forbidden; no Check Run or other publishing endpoint exists in the peer.
let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
export const networkCalls=()=>outbound;
export const sha=n=>n.toString(16).padStart(40,'0');
export const user=(id,login='user'+id)=>({id,login,type:'User'});
let signingKey;
async function appKey(){
 if(!signingKey)signingKey=crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']).then(async pair=>({publicKey:pair.publicKey,pkcs8:Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64')}));
 return signingKey;
}
export function queueEntry(position){return {id:'QE:'+position,position,baseCommit:{oid:sha(9000+position)},headCommit:{oid:sha(9100+position)},pullRequest:{number:10+position,headRefOid:sha(1000+position),baseRefName:'main'}};}

export async function fixture({prefix=2,tail=0,pageSize=100,signed=true,enabled=true}={}){
 const f=await signingFixture();
 if(signed){await f.install();await f.sign(await f.create());}
 const c={...f.c,githubEnabled:true,mergePrefixAssessmentEnabled:enabled};
 const credentials=await appKey(),calls=[],queries=[],sqlReads=[],forbidden=[],hooks={};
 const entries=Array.from({length:prefix+tail},(_,i)=>queueEntry(i+1));
 const current=entries[prefix-1],target={headSha:current.headCommit.oid,baseSha:current.baseCommit.oid,baseRef:'refs/heads/main'};
 const prs=new Map(entries.map(e=>[e.pullRequest.number,{number:e.pullRequest.number,state:'open',user:user(2,'contributor'),commits:1,head:{sha:e.pullRequest.headRefOid},base:{repo:{id:c.repositoryId},ref:'main',sha:sha(2000+e.position)}}]));
 const commits=new Map(entries.map(e=>[e.pullRequest.number,[{sha:e.pullRequest.headRefOid,author:user(2,'contributor'),commit:{author:{email:'2+contributor@users.noreply.github.com'},message:'SYNTHETIC PREFIX COMMIT'}}]]));
 const state={queueRounds:0,branchReads:0,metadataReads:0,tokenReads:0,installationReads:0,userReads:0,prReads:new Map(),compareReads:new Map()};
 const budget=new InvocationBudget(()=>0,()=>f.now()*1000);
 const tracked=(raw,query)=>({
  bind:(...values)=>tracked(raw.bind(...values),query),
  first:async()=>{sqlReads.push(query);return raw.first();},
  all:async()=>{sqlReads.push(query);return raw.all();},
  run:async()=>{sqlReads.push(query);return raw.run();},
  exec:()=>{sqlReads.push(query);return raw.exec();},
 });
 const db={prepare:query=>tracked(f.db.prepare(query),query),batch:statements=>f.db.batch(statements)};
 const token='SYNTHETIC_INSTALLATION_TOKEN';
 async function verifyJWT(header){
  assert.ok(header.startsWith('Bearer '));const jwt=header.slice(7),parts=jwt.split('.');assert.equal(parts.length,3);
  assert.equal(JSON.parse(Buffer.from(parts[0],'base64url')).alg,'RS256');
  assert.equal(JSON.parse(Buffer.from(parts[1],'base64url')).iss,String(c.appId));
  assert.equal(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',credentials.publicKey,Buffer.from(parts[2],'base64url'),new TextEncoder().encode(parts[0]+'.'+parts[1])),true);
 }
 const transport=async(url,init)=>{
  const u=new URL(url),body=init.body===undefined?undefined:JSON.parse(init.body);
  const call={path:u.pathname,search:u.search,method:init.method,body};calls.push(call);
  assert.equal(u.origin,'https://api.github.com');assert.equal(init.redirect,'manual');assert.equal(init.cache,'no-store');
  if(hooks.request)await hooks.request(call,state);
  let data;
  if(u.pathname==='/repos/'+c.repository+'/installation'){
   assert.equal(init.method,'GET');await verifyJWT(init.headers.Authorization);state.installationReads++;
   data={id:c.installationId,app_id:c.appId,app_slug:c.appSlug,suspended_at:null};
   if(hooks.installation)await hooks.installation(data,state);
  }else if(u.pathname==='/app/installations/'+c.installationId+'/access_tokens'){
   assert.equal(init.method,'POST');await verifyJWT(init.headers.Authorization);state.tokenReads++;
   assert.deepEqual(body,{repository_ids:[c.repositoryId],permissions:{contents:'read',pull_requests:'read',checks:'read'}});
   data={token,expires_at:new Date((f.now()+3600)*1000).toISOString(),repositories:[{id:c.repositoryId}],permissions:{contents:'read',pull_requests:'read',checks:'read',metadata:'read'}};
   if(hooks.token)await hooks.token(data,state);
  }else{
   assert.equal(init.headers.Authorization,'Bearer '+token);
   if(init.method!=='GET' && !(u.pathname==='/graphql' && init.method==='POST')){forbidden.push(call);throw Error('PUBLICATION_FORBIDDEN');}
   const base='/repos/'+c.repository;
   if(u.pathname==='/graphql'&&body.query.startsWith('query NativePrefixPRMetadata(')){
    assert.deepEqual(body.variables,{owner:c.repository.split('/')[0],name:c.repository.split('/')[1]});
    const numbers=[...body.query.matchAll(/p([0-9]+):pullRequest\(number:([0-9]+)\)/g)].map(m=>{assert.equal(m[1],m[2]);return Number(m[1]);});
    assert.ok(numbers.length>=2&&numbers.length<=100);assert.equal(new Set(numbers).size,numbers.length);
    const repository={databaseId:c.repositoryId};for(const number of numbers){assert.ok(prs.has(number));state.prReads.set(number,(state.prReads.get(number)??0)+1);const p=structuredClone(prs.get(number));if(hooks.pr)await hooks.pr(p,{number,read:state.prReads.get(number)},state);repository['p'+number]={number:p.number,state:p.state.toUpperCase(),headRefOid:p.head.sha,baseRefOid:p.base.sha,baseRefName:p.base.ref,baseRepository:{databaseId:p.base.repo.id},author:{__typename:p.user.type,login:p.user.login,databaseId:p.user.id},commits:{totalCount:p.commits}};}
    data={data:{repository}};if(hooks.prMetadata)await hooks.prMetadata(data,numbers,state);
   }else if(u.pathname==='/graphql'){
    assert.match(body.query,/^query\(/);assert.ok(body.query.includes('mergeQueue(branch:$branch)'));
    assert.deepEqual(Object.keys(body).sort(),['query','variables']);
    assert.equal(body.variables.owner,c.repository.split('/')[0]);assert.equal(body.variables.name,c.repository.split('/')[1]);assert.equal(body.variables.branch,'main');
    const cursor=body.variables.cursor;if(cursor===null)state.queueRounds++;
    const offset=cursor===null?0:Number(cursor.slice('cursor:'.length));assert.ok(cursor===null || /^cursor:[1-9][0-9]*$/.test(cursor));
    const nodes=structuredClone(entries.slice(offset,offset+pageSize)),hasNextPage=offset+nodes.length<entries.length;
    data={data:{repository:{databaseId:c.repositoryId,mergeQueue:{entries:{totalCount:entries.length,nodes,pageInfo:{hasNextPage,endCursor:hasNextPage?'cursor:'+(offset+nodes.length):null}}}}}};
    const context={round:state.queueRounds,page:Math.floor(offset/pageSize)+1,cursor,body,data};
    queries.push(context);if(hooks.queue)await hooks.queue(data,context,state);
   }else if(u.pathname===base){state.metadataReads++;data={id:c.repositoryId,full_name:c.repository,default_branch:'main'};if(hooks.metadata)await hooks.metadata(data,state);}
   else if(u.pathname===base+'/commits/main'){state.branchReads++;data={sha:c.referenceSha};if(hooks.branch)await hooks.branch(data,state);}
   else if(u.pathname.startsWith(base+'/contents/')){
    const name=u.pathname.slice((base+'/contents/').length);assert.equal(u.searchParams.get('ref'),c.referenceSha);
    const content=name==='.github/cla-policy.json'?JSON.stringify(c.policy):name===c.policy.agreements.individual.path?'SYNTHETIC DELEGATED CLA ONLY':name===c.policy.agreements.corporate.path?'SYNTHETIC CORPORATE CLA':undefined;
    assert.notEqual(content,undefined);data={type:'file',path:name,encoding:'base64',content:Buffer.from(content).toString('base64')};if(hooks.content)await hooks.content(data,name,state);
   }else if(u.pathname.startsWith(base+'/pulls/')){
    const number=Number(u.pathname.slice((base+'/pulls/').length));assert.ok(prs.has(number));
    state.prReads.set(number,(state.prReads.get(number)??0)+1);data=structuredClone(prs.get(number));if(hooks.pr)await hooks.pr(data,{number,read:state.prReads.get(number)},state);
   }else if(u.pathname.startsWith(base+'/compare/')){
    const comparison=u.pathname.slice((base+'/compare/').length),pr=[...prs.values()].find(p=>comparison===p.base.sha+'...'+p.head.sha);
    assert.ok(pr,'comparison must use this PR own base/head, never queue base/head');assert.equal(u.searchParams.get('per_page'),'100');
    const page=Number(u.searchParams.get('page'));state.compareReads.set(pr.number,(state.compareReads.get(pr.number)??0)+1);
    data={total_commits:pr.commits,base_commit:{sha:pr.base.sha},commits:structuredClone(commits.get(pr.number).slice((page-1)*100,page*100))};
    if(hooks.compare)await hooks.compare(data,{number:pr.number,page},state);
   }else if(u.pathname.startsWith('/users/')){
    state.userReads++;const login=decodeURIComponent(u.pathname.slice('/users/'.length));data=user(Number(login.replace(/^user/,'')),login);if(hooks.user)await hooks.user(data,login,state);
   }else{forbidden.push(call);throw Error('UNEXPECTED_SYNTHETIC_PATH');}
  }
  if(hooks.response){const response=await hooks.response(call,data,state);if(response)return response;}
  return Response.json(data);
 };
 const store=new LiveStore(db,budget,c),github=new GitHub(c,{pkcs8:credentials.pkcs8,clientSecret:'',evidenceKey:''},transport,f.now,budget),service=new LiveService(c,store,github,f.key);
 const digest=async()=>{
  const names=f.sql.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r=>r.name);
  return sha256(canonical(names.map(name=>({name,rows:f.sql.prepare('SELECT * FROM "'+name+'" ORDER BY rowid').all()}))));
 };
 return {...f,c,calls,queries,sqlReads,forbidden,hooks,entries,prs,commits,state,target,budget,store,github,service,digest,
  assess:input=>service.assessMergePrefix(input??target),snapshot:input=>github.mergePrefixSnapshot(input??target),
  assertReadOnly:()=>{assert.equal(forbidden.length,0);assert.ok(sqlReads.every(q=>/^\s*SELECT\b/.test(q)));assert.ok(calls.every(call=>call.method==='GET'||call.path==='/graphql'||/^\/app\/installations\/[0-9]+\/access_tokens$/.test(call.path)));assert.equal(outbound,0);},
  close:()=>{assert.equal(forbidden.length,0);f.close();}};
}
export async function isolated(fn,options){const f=await fixture(options);try{await fn(f);f.assertReadOnly();}finally{f.close();}}
