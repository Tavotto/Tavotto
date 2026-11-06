import { VERIFICATION_FINAL_SQL } from './budget.ts';
import { validId, validSha } from '../core/policy.ts';
import { Rejected } from '../core/types.ts';
import type { Target } from '../core/types.ts';
import type { LiveService } from './service.ts';
// ADR 0172. Unauthenticated, read-only live eligibility for the trusted CI gate. Every call re-reads
// GitHub and D1 and re-verifies signature evidence; nothing is cached, stored or published. The answer
// carries no per-person state: contributors are public PR facts, listed so the gate can bind the answer
// to the people it saw. The caller's nonce is echoed so a stored answer cannot be replayed.
export const ELIGIBILITY_NONCE=/^[A-Za-z0-9_-]{32,64}$/;
export async function eligibility(s:LiveService,target:Target,head:string,nonce:string):Promise<unknown> {
  if((target.kind==='pr'?!validId(target.number):target.kind!=='merge_group' || target.headSha!==head || !validSha(target.baseSha)) ||
    !validSha(head) || !ELIGIBILITY_NONCE.test(nonce))throw new Rejected('eligibility_invalid');
  await s.store.assertReady();
  const check=await s.evaluateTarget(target,VERIFICATION_FINAL_SQL),proof=check.proof;
  if(proof.head_sha!==head)throw new Rejected('eligibility_head_changed');
  if(proof.kind!==target.kind || (target.kind==='pr' && proof.pr!==target.number) || proof.repository_id!==s.c.repositoryId)
    throw new Rejected('eligibility_target_mismatch');
  const observed=await s.store.databaseNow();
  // A hold, revocation or new signature committed during evaluation must not hide behind the older revision.
  if(await s.store.revision()!==proof.revision)throw new Rejected('state_changed');
  // Authorization expiry advances no revision. Answer only while the evaluated authorization still holds.
  if(observed>=proof.expires_at)throw new Rejected('authorization_expired');
  return {protocol:'cla-eligibility-v1',repository_id:s.c.repositoryId,kind:target.kind,...(target.kind==='pr'?{pr:target.number}:{members:proof.members}),
    head_sha:head,base_sha:proof.base_sha,policy_sha256:proof.policy_sha256,contributors:proof.contributors.map(({id,login})=>({id,login})),
    revision:proof.revision,observed_at:observed,nonce,eligible:check.conclusion==='success'};
}
