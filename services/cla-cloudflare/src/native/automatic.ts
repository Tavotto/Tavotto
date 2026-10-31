// Observations are default; independently enabled publication dispatches once and reconciles later.
import {bytes,canonical,randomToken,sha256,validWebhook} from '../core/crypto.ts';
import {sameBinding} from '../core/identity.ts';
import {validId,validSha} from '../core/policy.ts';
import {Rejected} from '../core/types.ts';
import type {LiveService} from './service.ts';
import {DB_NOW} from './store.ts';
import {BudgetDeferred,FREE_BUDGET,qualificationSQL} from './budget.ts';
interface HeadRequest {request_key:string;pr:number;head:string;status:string;attempt:string;lease_until:number}
// A separate scheduled invocation keeps both queues' existing per-invocation budgets intact.
export const AUTOMATIC_CRON='7-59/15 * * * *';
export async function admitHead(s:LiveService,raw:Uint8Array<ArrayBuffer>,signature:string,delivery:string,event:string,secret:string):Promise<void> {
  if(s.c.automaticVerificationEnabled!==true)throw new Rejected('automatic_verification_disabled');
  if(raw.length>262144 || !/^[A-Za-z0-9-]{1,100}$/.test(delivery) || !['pull_request','merge_group'].includes(event) || secret.length<32 || secret.length>256 ||
    !await validWebhook(bytes(secret),raw,signature))throw new Rejected('webhook_rejected');
  let data;try{data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));}catch{throw new Rejected('webhook_rejected');}
  if(event==='merge_group'){
    const g=data?.merge_group;
    if(s.c.mergePrefixAssessmentEnabled!==true || data?.action!=='checks_requested' || data?.repository?.id!==s.c.repositoryId || data.repository.full_name!==s.c.repository ||
      data?.installation?.id!==s.c.installationId || g?.base_ref!=='refs/heads/main' || !validSha(g?.head_sha) || !validSha(g?.base_sha))throw new Rejected('webhook_rejected');
    await s.store.assertReady();const target={kind:'merge_group' as const,headSha:g.head_sha,baseSha:g.base_sha},key=s.store.targetKey(s.c.repositoryId,target);
    const hash=await sha256(raw),now=await s.store.databaseNow();
    await s.store.batch([...s.store.guard(`(EXISTS(SELECT 1 FROM targets WHERE target_key=?) OR (SELECT count(*) FROM (SELECT 1 FROM targets LIMIT 100))<100)
      AND (EXISTS(SELECT 1 FROM deliveries WHERE id=?) OR (SELECT count(*) FROM (SELECT 1 FROM deliveries LIMIT 1000))<1000)`,key,delivery),
      s.store.stmt('INSERT OR IGNORE INTO targets VALUES(?,?)',key,canonical(target)),
      ...s.store.guard('EXISTS(SELECT 1 FROM targets WHERE target_key=? AND target_json=?) AND NOT EXISTS(SELECT 1 FROM deliveries WHERE id=? AND (body_sha!=? OR target_key!=?))',key,canonical(target),delivery,hash,key),
      s.store.stmt('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?)',delivery,hash,key,now)]);return;
  }
  const pr=data?.pull_request,head=pr?.head?.sha;
  if(!['opened','reopened','synchronize'].includes(data?.action) || data?.repository?.id!==s.c.repositoryId || data.repository.full_name!==s.c.repository ||
    data?.installation?.id!==s.c.installationId || !validId(data?.number) || data.number!==pr?.number || pr?.state!=='open' ||
    pr?.base?.repo?.id!==s.c.repositoryId || pr?.base?.ref!=='main' || !validSha(head))throw new Rejected('webhook_rejected');
  await s.store.assertReady();const bound=await s.freshBinding(data.number);
  if(bound.head_sha!==head)throw new Rejected('webhook_head_stale');
  const hash=await sha256(raw),key=`${s.c.repositoryId}:${data.number}:${head}`;
  await s.store.batch([
    ...s.store.guard('NOT EXISTS(SELECT 1 FROM head_deliveries WHERE id=? AND (body_hash!=? OR request_key!=?))',delivery,hash,key),
    ...s.store.guard(`EXISTS(SELECT 1 FROM head_requests WHERE request_key=?) OR (SELECT count(*) FROM (SELECT 1 FROM head_requests LIMIT 100))<100`,key),
    ...s.store.guard(`EXISTS(SELECT 1 FROM head_deliveries WHERE id=?) OR (SELECT count(*) FROM (SELECT 1 FROM head_deliveries LIMIT 1000))<1000`,delivery),
    s.store.stmt(`INSERT INTO head_requests(request_key,pr,head,status,attempt,lease_until,created,result) VALUES(?,?,?,'pending','',0,${DB_NOW},'') ON CONFLICT(request_key) DO NOTHING`,key,data.number,head),
    s.store.stmt(`INSERT INTO head_deliveries VALUES(?,?,?,${DB_NOW}) ON CONFLICT(id) DO NOTHING`,delivery,hash,key),
  ]);
}
export async function verifyHeadOne(s:LiveService):Promise<string> {
  if(s.c.automaticVerificationEnabled!==true)return 'automatic_verification_disabled';
  if(s.c.automaticPublishingEnabled && (!s.store.budget || s.github.budget!==s.store.budget))throw new Rejected('invocation_budget_required');
  await s.store.assertReady();const attempt=randomToken();
  // Expired workers require manual recovery. No uncertain operation is automatically replayed.
  await s.store.stmt(`UPDATE head_requests SET status='manual',result='' WHERE status='running' AND lease_until<=${DB_NOW}`).run();
  const result=await s.store.db.batch<HeadRequest>([s.store.stmt(`UPDATE head_requests SET status='running',attempt=?,lease_until=${DB_NOW}+120
    WHERE request_key=(SELECT request_key FROM head_requests WHERE status='pending' AND (${s.c.automaticPublishingEnabled?s.store.positiveAdmission(s.c.confirmationSlotEnabled===true):'1'}) ORDER BY created,request_key LIMIT 1) AND status='pending' RETURNING *`,attempt)]);
  if(result.some(r=>!r.success))throw new Rejected('transaction_rejected');const job=result[0]?.results[0];if(!job)return 'idle';
  try {
    const check=await s.evaluate(job.pr,s.c.automaticPublishingEnabled?(s.c.confirmationSlotEnabled===true?8:7):5);
    if(check.headSha!==job.head)throw new Rejected('automatic_head_changed');
    if(s.c.automaticPublishingEnabled) {
      const exempt=new Set(Object.values(s.c.exemptionIds));
      if(16+qualificationSQL(s.c)*check.proof.contributors.filter(person=>!exempt.has(person.id)).length>FREE_BUDGET.sqlWork)throw new BudgetDeferred('confirmation_capacity');
      let issued:string|undefined;
      try {
        const receipt=await s.github.publishNew(check,async()=>{
          const fresh=await s.freshBinding(job.pr);
          if(!sameBinding(check.proof,fresh))throw new Rejected('automatic_head_changed');
          if(await s.store.databaseNow()>=check.proof.expires_at)throw new Rejected('proof_expired');
          s.store.budget!.reserveWorkExternal(1);
          issued=await s.store.beginAutomaticPublication(job,check);return issued;
        });
        s.store.budget!.settlement();await s.store.settleAutomaticPublication(issued!,'reconcile',receipt.id);
        return 'automatic_publication_requires_reconciliation';
      }catch(error){
        if(!issued)throw error;
        s.store.budget!.settlement();try{await s.store.settleAutomaticPublication(issued,'unknown');}catch{}
        return 'automatic_publication_unknown_no_replay';
      }
    }
    const fresh=await s.freshBinding(job.pr),now=await s.store.databaseNow();
    if(!sameBinding(check.proof,fresh) || now>=check.proof.expires_at)throw new Rejected('automatic_head_changed');
    // This is a new read-only result; no earlier proof is accepted as input or extended.
    const observation=canonical({protocol:'cla-head-observation-v1',proof:check.proof,eligible:check.conclusion==='success',
      agreement_version:s.c.policy.agreements.individual.version,agreement_sha256:s.c.policy.agreements.individual.sha256,observed_at:now,
      publication:'not_attempted'});
    await s.store.batch([...s.store.guard(`(SELECT revision FROM state WHERE id=1)=? AND ${DB_NOW}<? AND
      EXISTS(SELECT 1 FROM head_requests WHERE request_key=? AND status='running' AND attempt=? AND lease_until>${DB_NOW})`,
      check.proof.revision,check.proof.expires_at,job.request_key,attempt),
      s.store.stmt("UPDATE head_requests SET status='done',result=? WHERE request_key=? AND attempt=?",observation,job.request_key,attempt)]);
    return 'head_verified_without_publication';
  }catch {
    // Clear the result on every rejected, uncertain or budget-exhausted evaluation.
    s.store.budget?.settlement();
    const waiting=s.c.automaticPublishingEnabled?`lease_until>${DB_NOW} AND NOT (${s.store.positiveAdmission(s.c.confirmationSlotEnabled===true)}) AND
      NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND head_sha=?)`:'0';
    await s.store.stmt(`UPDATE head_requests SET status=CASE WHEN ${waiting} THEN 'pending' ELSE 'manual' END,result=''
      WHERE request_key=? AND status='running' AND attempt=?`,
      ...(s.c.automaticPublishingEnabled?[s.store.targetKey(s.c.repositoryId,{kind:'pr',number:job.pr}),job.head]:[]),job.request_key,attempt).run();
    return 'head_requires_manual_review';
  }
}
