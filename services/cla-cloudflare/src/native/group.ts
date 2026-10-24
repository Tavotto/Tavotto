// Group dispatch shares the existing target journal, outbox lease and global slot.
import type {Target,Job,CheckProposal} from '../core/types.ts';
import type {LiveService} from './service.ts';
import type {RefreshRequest,Publication} from './store.ts';
import {Rejected} from '../core/types.ts';
import {BudgetDeferred,FREE_BUDGET,qualificationSQL} from './budget.ts';
export async function publishGroup(s:LiveService,job:Job,target:Extract<Target,{kind:'merge_group'}>,refresh?:{request:RefreshRequest;predecessor:Publication}):Promise<string> {
  if(!s.c.automaticVerificationEnabled || !s.c.automaticPublishingEnabled || !s.c.checkPublishingEnabled || s.c.confirmationSlotEnabled!==true)throw new Rejected('publication_disabled');
  const budget=s.store.budget!;let issued:string|undefined;
  try {
    if(!refresh && !await s.store.stmt(`SELECT 1 AS ready WHERE ${s.store.positiveAdmission()}`).first())throw new BudgetDeferred('confirmation_capacity');
    const receipt=await s.github.publishGroup(target,async(withdrawnRevision)=>{
      const check:CheckProposal=await s.evaluateTarget(target,7),exempt=new Set(Object.values(s.c.exemptionIds));
      if(withdrawnRevision!==undefined && withdrawnRevision>check.proof.revision)throw new Rejected('refresh_predecessor_unavailable');
      if(check.conclusion!=='success')throw new Rejected('qualification_not_complete');
      // Group reconciliation has one extra target-history read; reserve the two withdrawal reads too.
      if(17+qualificationSQL(s.c)*check.proof.contributors.filter(u=>!exempt.has(u.id)).length>FREE_BUDGET.sqlWork)throw new BudgetDeferred('confirmation_capacity');
      budget.reserveWorkExternal(1);
      issued=refresh?await s.store.beginFreshPublication(job,refresh.request,check,refresh.predecessor):await s.store.beginGroupPublication(job,check);
      return {check,attempt:issued};
    },refresh?.predecessor);
    budget.settlement();
    if(refresh)await s.store.settleFreshPublication(job,issued!,'reconcile',receipt.id);
    else await s.store.settleGroupPublication(job,issued!,'reconcile',receipt.id);
    return refresh?'fresh_publication_requires_reconciliation':'group_publication_requires_reconciliation';
  }catch(error){
    if(!issued)throw error;
    budget.settlement();try{if(refresh)await s.store.settleFreshPublication(job,issued,'unknown');else await s.store.settleGroupPublication(job,issued,'unknown');}catch{}
    return refresh?'fresh_publication_unknown_no_replay':'group_publication_unknown_no_replay';
  }
}
