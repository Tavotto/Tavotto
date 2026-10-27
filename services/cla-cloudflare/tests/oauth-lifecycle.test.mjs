import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/native/worker.ts';
import {fixture} from './independent-oauth-db.mjs';
import {sha256,base64url,bytes} from '../src/core/crypto.ts';

const c={origin:'https://cla.invalid',repositoryId:707,referenceSha:'a'.repeat(40),holderId:1,appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',repository:'synthetic/repo',installationId:2,clientId:'synthetic',holderName:'SYNTHETIC HOLDER',syntheticSigning:false,mockCheckPublishing:false,autoHolder:false,exemptionIds:{erwanjun:1},githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false,policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:'a'.repeat(64)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:'b'.repeat(64)}},exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:false}}};
async function isolated(run){
 const f=fixture();await f.store.initialize(c);let calls=0,challenge='';const hooks={};
 const env={DB:f.db,CLA_CONFIG:JSON.stringify(c),CLA_UI_ENABLED:'true',CLA_GITHUB_ENABLED:'true',CLA_OAUTH_ENABLED:'true',CLA_EVIDENCE_KEY:Buffer.alloc(32,7).toString('base64'),CLA_CLIENT_SECRET:'SYNTHETIC_NOT_A_REAL_SECRET',CLA_OAUTH_LIMITER:{limit:async()=>({success:true})}};
 const original=globalThis.fetch;
 // Worker constructs its real GitHub adapter. Both fixed provider hosts are
 // intercepted here; unknown requests fail, never reach an external transport.
 globalThis.fetch=async(url,init)=>{
  calls++;
  if(url==='https://github.com/login/oauth/access_token'){
   assert.equal(init.method,'POST');const b=JSON.parse(init.body);assert.equal(b.client_secret,env.CLA_CLIENT_SECRET);
   assert.equal(b.redirect_uri,c.origin+'/oauth/callback');
   assert.equal(base64url(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes(b.code_verifier)))),challenge);
   await hooks.exchange?.();return Response.json({access_token:'ghu_SYNTHETIC_ONLY',token_type:'bearer',scope:'',...hooks.token});
  }
  assert.equal(url,'https://api.github.com/user');assert.equal(init.method,'GET');assert.equal(init.headers.Authorization,'Bearer ghu_SYNTHETIC_ONLY');
  return Response.json({id:2,login:'synthetic-contributor',type:'User'});
 };
 const start=async()=>{
  const r=await worker.fetch(new Request(c.origin+'/oauth/start',{method:'POST',headers:{origin:c.origin,'content-type':'application/json'},body:'{}'}),env);assert.equal(r.status,200);
  const url=new URL((await r.json()).authorizationURL);challenge=url.searchParams.get('code_challenge');assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  return {state:url.searchParams.get('state'),browser:/__Host-cla-oauth=([^;]+)/.exec(r.headers.get('set-cookie'))[1]};
 };
 const callback=(flow,accept='application/json',query=new URLSearchParams({state:flow.state,code:'SYNTHETIC_CODE',iss:'https://github.com/login/oauth'}))=>
  worker.fetch(new Request(c.origin+'/oauth/callback?'+query,{headers:{cookie:'__Host-cla-oauth='+flow.browser,accept}}),env);
 const sessions=()=>f.sql.prepare('SELECT * FROM cla_native_v2_sessions').all();
 try{await run({f,env,start,callback,sessions,hooks,calls:()=>calls});}finally{globalThis.fetch=original;f.close();}
}
test('OAuth full callback binds PKCE, sets session cookies and rejects replay',()=>isolated(async x=>{
 const flow=await x.start(),r=await x.callback(flow);assert.equal(r.status,200);assert.match((await r.json()).csrf,/^[A-Za-z0-9_-]{43}$/);
 const cookies=r.headers.get('set-cookie');assert.match(cookies,/__Host-cla-session=.*Max-Age=900; Secure; HttpOnly; SameSite=Lax/);assert.match(cookies,/__Host-cla-csrf=.*SameSite=Strict/);assert.match(cookies,/__Host-cla-oauth=; Path=\/; Max-Age=0/);
 const token=/__Host-cla-session=([^;]+)/.exec(cookies)[1];assert.equal(x.sessions().length,1);assert.equal(x.sessions()[0].token_hash,await sha256(token));assert.equal(x.sessions()[0].expires,x.f.now()+900);
 const before=x.calls();assert.equal((await x.callback(flow)).status,403);assert.equal(x.calls(),before);assert.equal(x.sessions().length,1);
}));
test('OAuth issuer metadata rejects response parameter pollution before state consumption',()=>isolated(async x=>{
 // GitHub's public metadata declares this exact issuer and RFC 9207 support:
 // https://github.com/.well-known/oauth-authorization-server/login/oauth
 // Keep fixtures independent of the production constant; no network discovery
 // or real credentials are used by these tests.
 const flow=await x.start(),issuer='https://github.com/login/oauth';
 const base=[['code','SYNTHETIC_CODE'],['state',flow.state]],good=[...base,['iss',issuer]];
 const wrongIssuers=['','https://github.com','http://github.com/login/oauth','https://github.com/login/oauth/',
  'https://GITHUB.com/login/oauth','https://github.com:443/login/oauth','https://github.com/login/oauth?x=y',
  'https://github.com/login/oauth#fragment','https://github.com.evil.invalid/login/oauth',
  'https://github.com@evil.invalid/login/oauth',encodeURIComponent(issuer)];
 const cases=[base,...wrongIssuers.map(v=>[...base,['iss',v]]),
  [...good,['iss',issuer]],[...good,['code','SECOND_CODE']],[...good,['state','SECOND_STATE']],
  [...good,['extra','SYNTHETIC']],[...good,['error','access_denied']],[...good,['error_description','SYNTHETIC']],
  [['state',flow.state],['iss',issuer]],[['code','SYNTHETIC_CODE'],['iss',issuer]],
  [['installation_id','123'],['setup_action','install']]];
 for(const entries of cases){
  const r=await x.callback(flow,'application/json',new URLSearchParams(entries));
  assert.equal(r.status,403);assert.equal((await r.json()).error,'oauth_rejected');
  assert.equal(x.calls(),0);assert.equal(x.sessions().length,0);
  assert.equal(x.f.sql.prepare('SELECT used FROM cla_native_v2_oauth_states').get().used,0);
 }
 // A rejected response cannot burn the legitimate browser-bound state.
 assert.equal((await x.callback(flow)).status,200);assert.equal(x.calls(),2);assert.equal(x.sessions().length,1);
}));
test('OAuth exact state expiry rejects without exchange or session',()=>isolated(async x=>{
 const flow=await x.start();x.f.advance(300);const r=await x.callback(flow);assert.equal(r.status,403);assert.equal((await r.json()).error,'oauth_rejected');assert.equal(x.calls(),0);assert.equal(x.sessions().length,0);
}));
test('OAuth wrong browser cannot consume the original browser state',()=>isolated(async x=>{
 const flow=await x.start();assert.equal((await x.callback({...flow,browser:'x'.repeat(43)})).status,403);assert.equal(x.calls(),0);assert.equal(x.f.sql.prepare('SELECT used FROM cla_native_v2_oauth_states').get().used,0);
 assert.equal((await x.callback(flow)).status,200);assert.equal(x.sessions().length,1);
}));
test('OAuth exchange failure consumes state and requires a new login',()=>isolated(async x=>{
 const flow=await x.start();x.hooks.exchange=()=>{throw Error('SYNTHETIC_EXCHANGE_FAILURE');};assert.equal((await x.callback(flow)).status,403);assert.equal(x.sessions().length,0);assert.equal(x.f.sql.prepare('SELECT used FROM cla_native_v2_oauth_states').get().used,1);
 const before=x.calls();assert.equal((await x.callback(flow)).status,403);assert.equal(x.calls(),before);delete x.hooks.exchange;assert.equal((await x.callback(await x.start())).status,200);
}));
test('OAuth state is valid at consumption, not bounded by callback completion',()=>isolated(async x=>{
 const flow=await x.start();x.f.advance(299);x.hooks.exchange=()=>x.f.advance(2);const r=await x.callback(flow,'text/html');
 assert.equal(r.status,303);assert.equal(r.headers.get('location'),'/');assert.equal(x.f.now(),1800000301);assert.equal(x.sessions()[0].expires,1800001201);
}));
test('OAuth session exact expiry rejects using the database clock',()=>isolated(async x=>{
 const r=await x.callback(await x.start()),token=/__Host-cla-session=([^;]+)/.exec(r.headers.get('set-cookie'))[1];x.f.advance(900);
 const result=await worker.fetch(new Request(c.origin+'/session',{headers:{cookie:'__Host-cla-session='+token}}),x.env);assert.equal(result.status,403);assert.equal((await result.json()).error,'session_rejected');
}));
for(const token of [{scope:'repo'},{token_type:'other'}])test('OAuth token response rejects extra authority '+Object.keys(token)[0],()=>isolated(async x=>{
 x.hooks.token=token;const r=await x.callback(await x.start());assert.equal(r.status,403);assert.equal((await r.json()).error,'oauth_rejected');assert.equal(x.sessions().length,0);assert.equal(x.calls(),1);
}));
