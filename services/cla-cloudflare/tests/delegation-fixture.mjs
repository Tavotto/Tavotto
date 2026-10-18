import {fixture as database} from './independent-oauth-db.mjs';
import {LiveService} from '../src/native/service.ts';
import {attestations} from '../src/native/config.ts';
import {importAes,sha256,randomToken,canonical,bytes,base64url} from '../src/core/crypto.ts';
export async function fixture(enabled=true){
  const f=database(),text='SYNTHETIC DELEGATED CLA ONLY',pair=await crypto.subtle.generateKey('Ed25519',true,['sign','verify']);
  const publicKey=new Uint8Array(await crypto.subtle.exportKey('raw',pair.publicKey));
  const c={origin:'https://cla.invalid',repositoryId:707,referenceSha:'a'.repeat(40),holderId:1,appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',
    repository:'synthetic/repo',installationId:2,clientId:'synthetic',holderName:'SYNTHETIC HOLDER',syntheticSigning:false,mockCheckPublishing:false,autoHolder:false,
    exemptionIds:{erwanjun:1},githubEnabled:false,oauthEnabled:false,signingEnabled:true,checkPublishingEnabled:false,delegatedCountersigningEnabled:enabled,
    holderAuthorizationKey:base64url(publicKey),holderAuthorizationId:randomToken(),
    policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:await sha256(text)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:await sha256('SYNTHETIC CORPORATE CLA')}},
      exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:true,protocol:'tavotto-cla-v1',app_id:123,app_slug:'synthetic-cla',check_name:'synthetic-cla'}}};
  await f.store.initialize(c);const keyBytes=crypto.getRandomValues(new Uint8Array(32)),key=await importAes(keyBytes);
  const gh={head:'c'.repeat(40),extra:[],resolveLogin:async login=>({id:login==='erwanjun'?1:2,login,type:'User'}),
    agreement:async()=>({digest:c.policy.agreements.individual.sha256,kind:'individual',version:'1.0',source_sha:c.referenceSha,content:text}),
    snapshot:async()=>({repositoryId:707,kind:'pr',headSha:gh.head,baseSha:'d'.repeat(40),policy:c.policy,members:[],
      prs:[{number:9,state:'open',headSha:gh.head,baseSha:'d'.repeat(40),expectedCommits:1+gh.extra.length,author:{id:2,login:'contributor',type:'User'},
        commits:[{sha:gh.head,author:{id:2,login:'contributor',type:'User'},email:'',message:'synthetic'},...gh.extra]}]}),
    publish:async()=>{throw Error('PUBLISH_FORBIDDEN');}};
  const s=new LiveService(c,f.store,gh,key),users={};
  for(const [role,actor,login]of [['contributor',2,'contributor'],['holder',1,'erwanjun']]){const token=randomToken(),csrf=randomToken();
    await f.store.addSession({token_hash:await sha256(token),actor,login,csrf_hash:await sha256(csrf),expires:f.now()+30*86400});users[role]={token,csrf};}
  const args=role=>[users[role].token,users[role].csrf,c.origin];
  const evidence=role=>{const name=role==='holder'?c.holderName:'SYNTHETIC CONTRIBUTOR';return {name,signature:name,address:'SYNTHETIC ADDRESS',attestations:Object.fromEntries(attestations(role).map(k=>[k,true]))};};
  const sign=async(p,role='contributor')=>s.sign(...args(role),p.id,await s.intent(...args(role),p.id,role),role,evidence(role));
  const signed=async obj=>({body:canonical(obj),signature:base64url(new Uint8Array(await crypto.subtle.sign('Ed25519',pair.privateKey,bytes(canonical(obj)))))});
  const grant={protocol:'cla-holder-authorization-v1',id:c.holderAuthorizationId,holder_id:c.holderId,holder_name:c.holderName,repository_id:c.repositoryId,
    repository:c.repository,agreement_kind:'individual',agreement_version:'1.0',agreement_sha256:c.policy.agreements.individual.sha256,
    purpose:'countersign-after-personal-contributor-signature',scope:'adult-independent-own-rights',approved_at:f.now(),not_before:f.now(),expires_at:f.now()+86400,key_sha256:await sha256(publicKey)};
  const control=async(action='enable',overrides={})=>{const e=await f.store.stmt('SELECT * FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first();
    return signed({protocol:'cla-holder-control-v1',authorization_id:grant.id,grant_sha256:await sha256(canonical(grant)),sequence:(e?.sequence??0)+1,
      previous_sha256:e?.hash??await sha256(canonical(grant)),action,issued_at:f.now(),...overrides});};
  const install=async()=>{await s.delegation.register(await signed(grant));await s.delegation.append(await control());};
  return {...f,c,s,gh,key,keyBytes,grant,args,evidence,sign,signed,control,install,create:()=>s.packet(...args('contributor'),'individual')};
}
export async function iso(fn,enabled=true){const f=await fixture(enabled);try{await fn(f);}finally{f.close();}}
export async function deny(fn){let rejected=false;try{const r=await fn();if(r?.conclusion==='failure')return;}catch(e){if(/PUBLISH_FORBIDDEN|OUTBOUND_FORBIDDEN/.test(String(e)))throw e;rejected=true;}
  if(!rejected)throw Error('expected rejection or negative eligibility');}
