import { canonical } from '../core/crypto.ts';
import { sameBinding } from '../core/identity.ts';
import type { Job, Proof } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import type { LiveService } from './service.ts';
import type { Publication, RefreshRequest } from './store.ts';
import { BudgetDeferred, FREE_BUDGET, qualificationSQL } from './budget.ts';

async function one(s:LiveService,job:Job,p:Publication,revision:number,pr:number):Promise<string> {
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
    try {const current=await s.evaluate(pr);
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

// A fresh evaluation is a different invocation from withdrawal. No eligibility
// checkpoint is reused; only the source attempt/revision deduplication survives.
async function refreshOne(s:LiveService,job:Job,request:RefreshRequest):Promise<string> {
  const budget=s.store.budget!;let attempt:string|undefined;
  try {
    if(!s.c.checkPublishingEnabled)throw new Rejected('publication_disabled');
    const target=await s.store.target(job.target_key);
    if(target.kind!=='pr')throw new Rejected('merge_queue_not_implemented');
    // After evaluate: predecessor read (1), final clock (1), issued batch (4).
    // Including initial readiness/revision, claim/task/target and evaluate: 13 + 2*N work SQL.
    const check=await s.evaluate(target.number,6);
    if(check.proof.revision!==request.revision)throw new Rejected('state_changed');
    if(check.conclusion!=='success') {
      budget.settlement();await s.store.completeRefresh(job,request,'qualification_not_complete');
      return 'refresh_not_qualified';
    }
    // Confirmation uses 14 fixed work statements; reserve two additional
    // reads to authorize withdrawal if revalidation fails, plus two per actor.
    const exemptions=new Set(Object.values(s.c.exemptionIds));
    if(16+qualificationSQL(s.c)*check.proof.contributors.filter(person=>!exemptions.has(person.id)).length>FREE_BUDGET.sqlWork)
      throw new BudgetDeferred('sql');
    const predecessor=await s.store.refreshPredecessor(job.target_key,check.headSha);
    if(!predecessor || predecessor.id!==request.source_attempt)throw new Rejected('refresh_predecessor_unavailable');
    const receipt=await s.github.publishFresh(check,predecessor,async()=>{
      // All token exchange and predecessor reads have already completed.
      // Mint an independent proof, then commit the send intent before HTTP.
      const fresh=await s.freshBinding(target.number);
      if(!sameBinding(check.proof,fresh))throw new Rejected('state_changed');
      const now=await s.store.databaseNow();check.proof.issued_at=now;check.proof.expires_at=Math.min(now+120,check.authorityExpires ?? Number.MAX_SAFE_INTEGER);
      if(check.proof.expires_at<=now)throw new Rejected('authorization_expired');
      check.text='tavotto-cla-v1\n'+canonical(check.proof);
      attempt=await s.store.beginFreshPublication(job,request,check,predecessor);return attempt;
    });
    budget.settlement();
    await s.store.settleFreshPublication(job,attempt!,'reconcile',receipt.id);
    return 'fresh_publication_requires_reconciliation';
  }catch {
    budget.settlement();
    if(attempt) {
      try{await s.store.settleFreshPublication(job,attempt,'unknown');}catch{}
      return 'fresh_publication_unknown_no_replay';
    }
    // If an issued transaction committed but its receipt was lost, this guarded
    // pending-only update cannot reset it. Reclaim will observe the issued row.
    try{await s.store.failRefresh(job,request);}catch{}
    return 'refresh_retry_pending_or_manual';
  }
}

// A fixed durable pass horizon prevents a stream of newer rows from starving older heads.
// Cursor and each head's three-failure budget settle in one guarded D1 batch.
export async function reconcileOne(s:LiveService):Promise<{disposition:string;budget?:{sql:number;external:number}}> {
  if(!s.c.backgroundEnabled)return {disposition:'background_disabled'};
  const budget=s.store.budget;
  if(!budget || s.github.budget!==budget)throw new Rejected('invocation_budget_required');
  const result=(disposition:string)=>({disposition,budget:budget.snapshot()});
  const initialRevision=await s.store.revision();
  const job=await s.store.claim(0);if(!job)return {disposition:'idle'};
  budget.bindLease(job.lease_until,job.lease_clock);
  let selected:Publication|undefined,revision:number|undefined,committed=false;
  try {
    const refresh=await s.store.refreshRequest(job);
    if(refresh)return result(await refreshOne(s,job,refresh));
    const target=await s.store.target(job.target_key);
    if(target.kind!=='pr')throw new Rejected('merge_queue_not_implemented');
    revision=initialRevision;const progress=await s.store.openProgress(job,revision);
    const records=await s.store.publicationBatch(job,progress);let disposition='no_due_publication';
    for(const p of records) {
      selected=p;
      try{disposition=await one(s,job,p,revision,target.number);committed=true;}
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
