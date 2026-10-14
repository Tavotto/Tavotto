import { canonical } from '../core/crypto.ts';
import { sameBinding } from '../core/identity.ts';
import type { Job, Proof } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import type { LiveService } from './service.ts';
import type { Publication } from './store.ts';
import { BudgetDeferred } from './budget.ts';

async function one(s:LiveService,job:Job,p:Publication,revision:number):Promise<string> {
  let previous:Proof;try{previous=JSON.parse(p.proof_text.split('\n').slice(1).join('\n'));}catch{throw new Rejected('stored_proof_unavailable');}
  if(previous.protocol!=='tavotto-cla-v1' || previous.repository_id!==s.c.repositoryId || previous.head_sha!==p.head_sha ||
    previous.revision!==p.revision || previous.expires_at!==p.expires || canonical(previous)!==p.proof_text.slice('tavotto-cla-v1\n'.length))throw new Rejected('stored_proof_unavailable');
  const remote=await s.github.observe(p);
  if(!remote){s.store.budget!.settlement();await s.store.recordReconciliation(job,p,'manual',null,'not_observed_no_retry',revision);return 'manual_review_required';}
  if(remote.conclusion==='failure'){
    s.store.budget!.settlement();
    await s.store.recordReconciliation(job,p,'resolved',remote.id,'remote_failure_observed',revision);return 'remote_failure_observed';
  }
  let valid=p.conclusion==='success' && revision===p.revision && await s.store.databaseNow()<p.expires;
  if(valid) {
    try {const current=await s.evaluate((await s.store.target(job.target_key) as {number:number}).number);
      valid=current.conclusion==='success' && current.proof.revision===p.revision && sameBinding(previous,current.proof);}
    catch(error){if(error instanceof Rejected && ['trusted_policy_changed','agreement_hash_mismatch','pr_mismatch'].includes(error.code))valid=false;else throw error;}
  }
  if(valid){s.store.budget!.settlement();await s.store.recordReconciliation(job,p,'confirmed',remote.id,'verified_read_only',revision,p.expires);return 'verified_read_only';}
  if(!s.c.checkPublishingEnabled)throw new Rejected('withdrawal_pending_publication_disabled');
  if(!await s.store.owns(job,0) || await s.store.revision()!==revision)throw new Rejected('state_changed');
  s.store.budget!.correction();
  await s.github.withdraw(p,remote.id,revision);
  s.store.budget!.settlement();
  await s.store.recordReconciliation(job,p,'resolved',remote.id,'remote_failure_verified',revision);return 'remote_failure_verified';
}

// A fixed durable pass horizon prevents a stream of newer rows from starving older heads.
// Cursor and each head's three-failure budget settle in one guarded D1 batch.
export async function reconcileOne(s:LiveService):Promise<{disposition:string;budget?:{sql:number;external:number}}> {
  if(!s.c.backgroundEnabled)return {disposition:'background_disabled'};
  const budget=s.store.budget;
  if(!budget || s.github.budget!==budget)throw new Rejected('invocation_budget_required');
  const result=(disposition:string)=>({disposition,budget:budget.snapshot()});
  const job=await s.store.claim(0);if(!job)return {disposition:'idle'};
  budget.bindLease(job.lease_until,job.lease_clock);
  let selected:Publication|undefined,revision:number|undefined,committed=false;
  try {
    if((await s.store.target(job.target_key)).kind!=='pr')throw new Rejected('merge_queue_not_implemented');
    revision=await s.store.revision();const progress=await s.store.openProgress(job,revision);
    const records=await s.store.publicationBatch(job,progress);let disposition='no_due_publication';
    for(const p of records) {
      selected=p;
      try{disposition=await one(s,job,p,revision);committed=true;}
      catch(error){if(error instanceof BudgetDeferred)throw error;budget.settlement();await s.store.publicationFailure(job,p,revision);committed=true;disposition='head_retry_pending_or_exhausted';}
    }
    budget.settlement();await s.store.finishBatch(job,revision);return result(disposition);
  } catch(error) {
    budget.settlement();
    if(error instanceof BudgetDeferred){
      // Advance only with the durable deferral, so a costly first head cannot starve
      // later heads. SQL/lease exhaustion leaves the original journal for reclaim.
      if(selected && revision!==undefined && !committed){
        try{await s.store.deferPublication(job,selected,revision);return result('budget_deferred');}catch{}
      }
      try{await s.store.deferTarget(job);}catch{}
      return result('budget_deferred');
    }
    // Infrastructure/lease errors cannot advance progress. A restarted worker reads first.
    try{await s.store.fail(job,await s.store.databaseNow());}
    catch(cleanup){if(cleanup instanceof BudgetDeferred){try{await s.store.deferTarget(job);}catch{}return result('budget_deferred');}throw cleanup;}
    return result('target_retry_pending_or_exhausted');
  }
}
