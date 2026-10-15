import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import worker from '../src/native/worker.ts';
import {LiveStore,nativeSQL} from '../src/native/store.ts';
import {LiveService} from '../src/native/service.ts';
import {verifyCI} from '../src/ci/consumer.ts';
import {currentPacket} from '../src/core/policy.ts';
import {importAes,sha256} from '../src/core/crypto.ts';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const c={origin:'https://cla.invalid',repositoryId:707,referenceSha:'a'.repeat(40),holderId:1,appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',repository:'synthetic/repo',installationId:2,clientId:'synthetic',holderName:'SYNTHETIC HOLDER',syntheticSigning:false,mockCheckPublishing:false,autoHolder:false,exemptionIds:{erwanjun:1},githubEnabled:false,oauthEnabled:false,signingEnabled:false,checkPublishingEnabled:false,policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:'a'.repeat(64)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:'b'.repeat(64)}},exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:true,protocol:'tavotto-cla-v1',app_id:123,app_slug:'synthetic-cla',check_name:'synthetic-cla'}}};
let dbCalls=0;const deniedDB={prepare(){dbCalls++;throw Error('DB_FORBIDDEN');},batch(){dbCalls++;throw Error('DB_FORBIDDEN');}};
const env={DB:deniedDB,CLA_CONFIG:JSON.stringify(c),CLA_UI_ENABLED:'true',CLA_EVIDENCE_KEY:Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64')};
async function rejected(path,code,options={},overrides={}){const r=await worker.fetch(new Request(c.origin+path,options),{...env,...overrides});assert.equal(r.status,403);assert.equal((await r.json()).error,code);}
test('HTTP defaults disabled even when config JSON requests enabled',async()=>{const r=await worker.fetch(new Request(c.origin+'/health'),{DB:deniedDB,CLA_CONFIG:JSON.stringify({...c,githubEnabled:true,signingEnabled:true})});assert.equal(r.status,503);assert.equal((await r.json()).enabled,false);});
test('HTTP rejects malformed config',async()=>{await rejected('/health','configuration_required',{}, {CLA_CONFIG:'bad'});});
test('HTTP rejects origin mismatch before DB/network',async()=>{const r=await worker.fetch(new Request('https://wrong.invalid/health'),env);assert.equal((await r.json()).error,'origin_rejected');});
test('OAuth callback rejects missing and duplicate query fields',async()=>{await rejected('/oauth/callback?code=x','oauth_rejected');await rejected('/oauth/callback?state=x&code=x&code=y','oauth_rejected');});
test('OAuth callback rejects malformed state even when enabled',async()=>{await rejected('/oauth/callback?state=bad&code=x','oauth_rejected',{}, {CLA_GITHUB_ENABLED:'true',CLA_OAUTH_ENABLED:'true'});});
test('OAuth start rejects wrong content type and cross-origin',async()=>{await rejected('/oauth/start','form_rejected',{method:'POST',headers:{origin:c.origin},body:'{}'});await rejected('/oauth/start','origin_rejected',{method:'POST',headers:{'content-type':'application/json',origin:'https://wrong.invalid'},body:'{}'});});
test('Signing rejects malformed intent before authentication or DB',async()=>{await rejected('/sign','intent_rejected',{method:'POST',headers:{'content-type':'application/json',origin:c.origin},body:JSON.stringify({packet:'x',intent:'bad',role:'holder',evidence:{}})}, {CLA_SIGNING_ENABLED:'true'});});
test('CI disabled and malformed locators reject without reading sources',async()=>{let reads=0;const read=async()=>{reads++;throw Error('READ_FORBIDDEN');};await assert.rejects(verifyCI({enabled:false},{},read),e=>e.code==='ci_provider_disabled');const cc={...c,enabled:true};await assert.rejects(verifyCI(cc,{repositoryId:707,pr:1,head:'bad'},read),e=>e.code==='ci_locator_invalid');assert.equal(reads,0);});

test('independent HTTP boundaries no outbound or DB calls',()=>{assert.equal(outbound,0);assert.equal(dbCalls,0);});
