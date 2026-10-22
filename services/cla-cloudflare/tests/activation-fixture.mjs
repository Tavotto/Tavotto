import {fixture as delegation} from './delegation-fixture.mjs';import {fixture as database} from './independent-oauth-db.mjs';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';
import {randomToken,sha256,canonical} from '../src/core/crypto.ts';import {attestations} from '../src/native/config.ts';
import {prepareAgreementActivation,applyAgreementActivation} from '../src/native/agreement-activation.ts';import {NO_ACTIVATION} from '../src/native/activation-record.ts';
export const text=(kind,version,variant='')=>`# SYNTHETIC ${kind} AGREEMENT ONLY\n\n**CLA_VERSION: ${version}**\n\nSYNTHETIC NON-LEGAL TEST CONTENT ${variant}\n`;
export async function fixture(){
  const original=await delegation(),f=database(),c=structuredClone(original.c),texts={individual:text('individual','1.0'),corporate:text('corporate','1.0')};
  original.close();for(const kind of ['individual','corporate'])c.policy.agreements[kind].sha256=await sha256(texts[kind]);
  await f.store.initialize(c);for(const kind of ['individual','corporate'])await f.store.saveDocument({kind,version:'1.0',digest:c.policy.agreements[kind].sha256,content:texts[kind],source_sha:c.referenceSha});
  const users={};for(const [role,actor,login] of [['contributor',2,'contributor'],['holder',1,'erwanjun']]){
    const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor,login,csrf_hash:await sha256(csrf),expires:f.now()+86400});users[role]={token,csrf};
  }
  const args=role=>[users[role].token,users[role].csrf,c.origin];
  function service(config=c,content=texts,store=new LiveStore(f.db,undefined,config)){
    const github={resolveLogin:async login=>({id:login==='erwanjun'?1:2,login,type:'User'}),
      agreement:async kind=>({kind,version:config.policy.agreements[kind].version,digest:config.policy.agreements[kind].sha256,content:content[kind],source_sha:config.referenceSha}),
      snapshot:async()=>({repositoryId:707,kind:'pr',headSha:'c'.repeat(40),baseSha:'d'.repeat(40),policy:config.policy,members:[],
        prs:[{number:9,state:'open',headSha:'c'.repeat(40),baseSha:'d'.repeat(40),expectedCommits:1,author:{id:2,login:'contributor',type:'User'},
          commits:[{sha:'c'.repeat(40),author:{id:2,login:'contributor',type:'User'},email:'',message:'SYNTHETIC COMMIT'}]}]}),publish:async()=>{throw Error('PUBLISH_FORBIDDEN');}};
    return new LiveService(config,store,github,original.key);
  }
  const s=service(),grant={...original.grant,agreement_sha256:c.policy.agreements.individual.sha256};
  const signed=original.signed,control=async(action='enable')=>{
    const e=await f.store.stmt('SELECT * FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first();
    return signed({protocol:'cla-holder-control-v1',authorization_id:grant.id,grant_sha256:await sha256(canonical(grant)),sequence:(e?.sequence??0)+1,
      previous_sha256:e?.hash??await sha256(canonical(grant)),action,issued_at:f.now()});
  };
  const install=async()=>{await s.delegation.register(await signed(grant));await s.delegation.append(await control());};
  const sign=async(p,role='contributor',using=s)=>{
    const name=role==='holder'?c.holderName:'SYNTHETIC CONTRIBUTOR';return using.sign(...args(role),p.id,await using.intent(...args(role),p.id,role),role,
      {name,signature:name,address:'SYNTHETIC ADDRESS',attestations:Object.fromEntries(attestations(role).map(k=>[k,true]))});
  };
  const plan=async(version='2.0',variant='',reference='e'.repeat(40))=>{
    const config={...structuredClone(c),referenceSha:reference,agreementActivationEnabled:true};delete config.agreementActivationHash;
    const content={...texts,individual:text('individual',version,variant)};config.policy.agreements.individual={...config.policy.agreements.individual,version,sha256:await sha256(content.individual)};
    const documents=['individual','corporate'].map(kind=>({kind,version:config.policy.agreements[kind].version,digest:config.policy.agreements[kind].sha256,content:content[kind],source_sha:reference}));
    const latest=await f.store.stmt('SELECT generation,hash FROM agreement_activations ORDER BY generation DESC LIMIT 1').first();
    const expected={generation:latest?.generation??0,hash:latest?.hash??NO_ACTIVATION,revision:await f.store.revision()};
    const proposal=await prepareAgreementActivation(config,documents,expected);config.agreementActivationHash=proposal.record.hash;
    return {config,content,documents,expected,proposal,apply:(store=f.store)=>applyAgreementActivation(store,config,proposal)};
  };
  return {...f,c,s,args,sign,service,plan,texts,grant,control,install,signed,key:original.key,create:(using=s)=>using.packet(...args('contributor'),'individual')};
}
export async function iso(fn){const f=await fixture();try{return await fn(f);}finally{f.close();}}
