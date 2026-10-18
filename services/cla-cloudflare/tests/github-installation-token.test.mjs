import test from 'node:test';
import assert from 'node:assert/strict';
import {GitHub} from '../src/native/github.ts';
let external=0;globalThis.fetch=async()=>{external++;throw Error('OUTBOUND_FORBIDDEN');};
const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const pkcs8=Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64');
const now=1800000000;
const config={origin:'https://cla.invalid',repositoryId:707,referenceSha:'a'.repeat(40),holderId:1,appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',repository:'synthetic/repo',installationId:2,clientId:'synthetic',holderName:'SYNTHETIC HOLDER',syntheticSigning:false,mockCheckPublishing:false,autoHolder:false,exemptionIds:{erwanjun:1},githubEnabled:true,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false,policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:'a'.repeat(64)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:'b'.repeat(64)}},exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:false}}};
async function exercise(token,patch={}){let authenticatedReads=0;
 const transport=async(url,init)=>{
  assert.equal(init.redirect,'manual');
  if(url==='https://api.github.com/repos/synthetic/repo/installation')return Response.json({id:2,app_id:123,app_slug:'synthetic-cla',suspended_at:null});
  if(url==='https://api.github.com/app/installations/2/access_tokens'){
   assert.equal(init.method,'POST');assert.deepEqual(JSON.parse(init.body),{repository_ids:[707],permissions:{contents:'read',pull_requests:'read',checks:'read'}});
   return Response.json({token,expires_at:new Date((now+3600)*1000).toISOString(),repositories:[{id:707}],permissions:{contents:'read',pull_requests:'read',checks:'read',metadata:'read'},...patch});
  }
  assert.equal(url,'https://api.github.com/repos/synthetic/repo');assert.equal(init.headers.Authorization,'Bearer '+token);authenticatedReads++;return Response.json({id:707});
 };
 const github=new GitHub(config,{clientSecret:'',pkcs8,evidenceKey:''},transport,()=>now);
 const result=await github.stagedRead('/repos/synthetic/repo');assert.equal(result.id,707);assert.equal(authenticatedReads,1);
}
test('accepts stateless 520-character opaque installation token',()=>exercise('ghs_123_'+('x'.repeat(170)+'.').repeat(2)+'y'.repeat(170)));
test('accepts legacy installation token without relying on its format',()=>exercise('ghs_'+crypto.randomUUID().replaceAll('-','')));
test('accepts a nonlegacy opaque token alphabet without interpreting it',()=>exercise('opaque.synthetic-value~with.dot+slash/equal='));
for(const token of ['',null,123,'has space','has\rreturn','has\nnewline','has\0nul','has\ttab','non-ascii-π','x'.repeat(8193)])test('rejects invalid/header-unsafe installation token '+JSON.stringify(typeof token==='string'?token.length:typeof token),()=>assert.rejects(exercise(token),e=>e.code==='token_scope_mismatch'));
test('long token does not bypass repository restrictions',()=>assert.rejects(exercise('x'.repeat(600),{repositories:[{id:708}]}),e=>e.code==='token_scope_mismatch'));
test('long token does not bypass permission restrictions',()=>assert.rejects(exercise('x'.repeat(600),{permissions:{contents:'write',pull_requests:'read',checks:'read',metadata:'read'}}),e=>e.code==='token_scope_mismatch'));
test('long token does not bypass expiry restrictions',()=>assert.rejects(exercise('x'.repeat(600),{expires_at:new Date(now*1000).toISOString()}),e=>e.code==='token_scope_mismatch'));
test('accepts token at the service header resource bound',()=>exercise('x'.repeat(8192)));
test('installation-token tests never used external network',()=>assert.equal(external,0));
