import { VERIFICATION_FINAL_SQL } from './budget.ts';
import { parseProof } from '../core/proof.ts';
import { sameBinding } from '../core/identity.ts';
import { sha256 } from '../core/crypto.ts';
import { validId, validSha } from '../core/policy.ts';
import { Rejected } from '../core/types.ts';
import type {Target} from '../core/types.ts';
import type { LiveService } from './service.ts';
export async function verification(s:LiveService,input:number|Target,head:string,run:number,attempt:string):Promise<unknown> {
  const target:Target=typeof input==='number'?{kind:'pr',number:input}:input;
  const pr=target.kind==='pr'?target.number:undefined;
  if((target.kind==='pr'?!validId(pr):target.kind!=='merge_group' || target.headSha!==head || !validSha(target.baseSha)) || !validId(run) || !validSha(head) || !/^[A-Za-z0-9_-]{43}$/.test(attempt))throw new Rejected('verification_invalid');
  const key=s.store.targetKey(s.c.repositoryId,target),p=await s.store.confirmedPublication(key,head,run,attempt);
  if(!p || p.conclusion!=='success')throw new Rejected('proof_not_confirmed');
  const proof=parseProof(p.proof_text,await s.store.databaseNow());
  if(p.revision!==proof.revision || p.expires!==proof.expires_at)throw new Rejected('proof_not_confirmed');
  const current=await s.evaluateTarget(target,VERIFICATION_FINAL_SQL,proof.revision);
  if(proof.kind!==target.kind || proof.pr!==pr || proof.head_sha!==head || proof.repository_id!==s.c.repositoryId || current.conclusion!=='success' ||
    current.proof.revision!==proof.revision || !sameBinding(proof,current.proof))throw new Rejected('qualification_changed');
  await s.github.readPublication(p);
  const observed=await s.store.databaseNow();
  const final=await s.store.confirmedPublication(key,head,run,attempt);
  if(observed>=proof.expires_at || observed>=current.proof.expires_at || await s.store.revision()!==proof.revision ||
    !final || final.conclusion!=='success' || final.revision!==p.revision || final.expires!==p.expires || final.proof_text!==p.proof_text)
    throw new Rejected('qualification_changed');
  return {protocol:'cla-verification-v1',repository_id:s.c.repositoryId,...(target.kind==='pr'?{pr}:{kind:'merge_group',members:proof.members,group:proof.group}),head_sha:head,base_sha:proof.base_sha,run_id:run,
    attempt,app_id:s.c.appId,app_slug:s.c.appSlug,check_name:s.c.checkName,revision:proof.revision,
    policy_sha256:proof.policy_sha256,proof_sha256:await sha256(p.proof_text),expires_at:proof.expires_at,observed_at:observed,eligible:true};
}
