import type { CheckProposal, D1Database, D1Statement, Job, Packet, Role, Session } from '../core/types.ts';
import type {LiveConfig} from './config.ts';
import {policyMaterial,packetAgreementMatches} from '../core/policy.ts';
import {activationProjection,verifyActivationRecord} from './activation-record.ts';
import {GitHub} from './github.ts';import type {NegativeWitness} from './github.ts';
import { Rejected } from '../core/types.ts';
import { canonical, randomToken } from '../core/crypto.ts';
import { Store } from '../core/store.ts';
import { BudgetDeferred, FREE_BUDGET, InvocationBudget, meterDatabase } from './budget.ts';

export const PREFIX = 'cla_native_v2_';
const objects = ['slot_policy','slot_policy_insert','slot_policy_update','slot_policy_delete','publication_slot_insert','publication_slot_enroll','publication_slot_release','publication_slot_update','publication_slot_delete','outbox_slot_delete','agreement_activations','activation_order','activations_update','activations_delete','head_requests','head_deliveries','head_deliveries_update','head_deliveries_delete','head_request_identity','head_revision','oauth_expiry','packets_subject_digest','authority','agreements','state','sessions','oauth_states','packets','intents','signatures',
  'eligibility_blocks','audit','targets','outbox','outbox_due','deliveries','published_checks','_guards',
  'revision_outbox','delivery_outbox','agreements_update','agreements_delete','packets_update','packets_delete',
  'signatures_update','signatures_delete','blocks_update','blocks_delete','audit_update','audit_delete',
  'publication_attempts','publication_unresolved','reconciliation_progress','refresh_requests','agreement_documents','documents_update','documents_delete','authority_update','authority_delete',
  'delegated_grants','delegated_controls','delegated_receipts','delegated_grants_update','delegated_grants_delete','delegated_controls_update','delegated_controls_delete','delegated_receipts_update','delegated_receipts_delete','delegated_control_order'];
// Only trusted source SQL is passed here; identifiers cannot come from a request.
export const nativeSQL = (sql: string) => sql.replace(/\b[A-Za-z_][A-Za-z_0-9]*\b/g,
  word => objects.includes(word) ? PREFIX+word : word);
export function namespace(db: D1Database): D1Database {
  return {prepare:sql=>db.prepare(nativeSQL(sql)),batch:<T>(ss:D1Statement[])=>db.batch<T>(ss)};
}
export const DB_NOW = "CAST(strftime('%s','now') AS INTEGER)";
export interface SignatureRow { role:Role; actor:number; evidence_cipher:string; evidence_hash:string; created:number }
export interface Document {digest:string;kind:string;version:string;source_sha:string;content:string}
export interface Publication {id:string;target_key:string;head_sha:string;revision:number;expires:number;phase:string;run_id:number|null;conclusion:'success'|'failure';proof_text:string;observation:string;position?:number;reconcile_attempts?:number;confirmation_slot?:number|null}
export interface RefreshRequest {target_key:string;source_attempt:string;revision:number;status:'pending'|'done'|'manual';attempts:number;due:number;reason:string;capacity_blocked?:boolean}
export interface Progress {cursor:number;horizon:number}
export interface ClaimedJob extends Job {lease_clock:number}
export class LiveStore extends Store {
  readonly budget:InvocationBudget|undefined;
  readonly expectedConfig:LiveConfig|undefined;
  constructor(db: D1Database,budget?:InvocationBudget,expectedConfig?:LiveConfig) { super(namespace(budget?meterDatabase(db,budget):db));this.budget=budget;this.expectedConfig=expectedConfig; }
  readiness(c:LiveConfig|undefined=this.expectedConfig):{sql:string;values:unknown[]} {
    if(!c)return {sql:'1',values:[]};
    const sql=`(SELECT count(*) FROM authority)=1 AND EXISTS(SELECT 1 FROM authority WHERE id=1 AND holder=?)
      AND EXISTS(SELECT 1 FROM agreements WHERE kind='individual' AND version=? AND digest=?)
      AND EXISTS(SELECT 1 FROM agreements WHERE kind='corporate' AND version=? AND digest=?)`;
    const values:unknown[]=[c.holderId,c.policy.agreements.individual.version,c.policy.agreements.individual.sha256,c.policy.agreements.corporate.version,c.policy.agreements.corporate.sha256];
    if(c.agreementActivationEnabled!==true)return {sql:sql+' AND (SELECT count(*) FROM agreements)=2 AND NOT EXISTS(SELECT 1 FROM agreement_activations)',values};
    return {sql:sql+` AND (SELECT count(*) FROM agreements)>2 AND EXISTS(SELECT 1 FROM agreement_activations a
      WHERE a.generation=(SELECT max(generation) FROM agreement_activations) AND a.hash=? AND a.repository_id=? AND a.holder_id=?
      AND a.reference_sha=? AND a.policy_json=? AND a.individual_version=? AND a.individual_digest=? AND a.corporate_version=? AND a.corporate_digest=?
      AND a.activation_revision<=(SELECT revision FROM state WHERE id=1) AND a.created<=${DB_NOW})`,
      values:[...values,c.agreementActivationHash,c.repositoryId,c.holderId,c.referenceSha,policyMaterial(c.policy),
        c.policy.agreements.individual.version,c.policy.agreements.individual.sha256,c.policy.agreements.corporate.version,c.policy.agreements.corporate.sha256]};
  }
  private async verifySelection(activation?:string):Promise<void> {
    if(this.expectedConfig?.agreementActivationEnabled!==true)return;
    if(!activation)throw new Rejected('database_configuration_uninitialized_or_mismatched');
    await verifyActivationRecord(JSON.parse(activation),this.expectedConfig);
  }
  private currentTuple():{sql:string;values:unknown[]} {
    const c=this.expectedConfig;if(!c)return {sql:'1',values:[]};
    return {sql:"((p.kind='individual' AND p.version=? AND p.digest=?) OR (p.kind='corporate' AND p.version=? AND p.digest=?))",
      values:[c.policy.agreements.individual.version,c.policy.agreements.individual.sha256,c.policy.agreements.corporate.version,c.policy.agreements.corporate.sha256]};
  }
  async assertReady():Promise<void> {
    const ready=this.readiness();const row=await this.stmt(`SELECT CASE WHEN (${ready.sql}) THEN 1 ELSE 0 END AS ready,
      ${this.expectedConfig?.agreementActivationEnabled===true?activationProjection:'NULL'} AS activation`,...ready.values).first<{ready:number;activation?:string}>();
    if(row?.ready!==1)throw new Rejected('database_configuration_uninitialized_or_mismatched');
    await this.verifySelection(row.activation);
  }
  override async revision():Promise<number> {
    const ready=this.readiness();const row=await this.stmt(`SELECT revision,${this.expectedConfig?.agreementActivationEnabled===true?activationProjection:'NULL'} AS activation
      FROM state WHERE id=1 AND (${ready.sql})`,...ready.values).first<{revision:number;activation?:string}>();
    if(!row)throw new Rejected('database_configuration_uninitialized_or_mismatched');await this.verifySelection(row.activation);return row.revision;
  }
  override async batch(stmts:D1Statement[]):Promise<void> {
    try{const results=await this.db.batch(stmts);if(results.some(r=>!r.success))throw new Error();}
    catch(error){if(error instanceof BudgetDeferred)throw error;throw new Rejected('transaction_rejected');}
  }
  async databaseNow(): Promise<number> {
    const row = await this.stmt(`SELECT ${DB_NOW} AS now`).first<{now:number}>();
    if (!row || !Number.isSafeInteger(row.now)) throw new Rejected('database_clock_unavailable'); return row.now;
  }
  override async session(hash:string,_now:number):Promise<Session|null> {
    return this.stmt(`SELECT * FROM sessions WHERE token_hash=? AND expires>${DB_NOW}`,hash).first<Session>();
  }
  positiveAdmission(enabled=this.expectedConfig?.confirmationSlotEnabled===true):string {
    return enabled?`NOT EXISTS(SELECT 1 FROM slot_policy WHERE active_attempt IS NOT NULL) AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE conclusion='success' AND phase!='resolved')`:
      `NOT EXISTS(SELECT 1 FROM slot_policy)`;
  }
  private withdrawalPriority():string {
    return `(${this.expectedConfig?.confirmationSlotEnabled===true?'1':'EXISTS(SELECT 1 FROM slot_policy)'}) AND p.conclusion='success'
      AND p.phase!='manual' AND (p.phase!='resolved' OR p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
      AND (p.expires<=${DB_NOW} OR p.revision!=(SELECT revision FROM state WHERE id=1) OR (p.phase='resolved' AND p.id=(SELECT active_attempt FROM slot_policy WHERE id=1)))`;
  }
  async beginPublication(key:string,check:CheckProposal):Promise<string> {
    // This holder-triggered path has no shared background budget/send fence.
    // Personal signing remains independent; positive publishing must use the
    // measured background paths in slot mode. SQL also fences old binaries.
    if(check.conclusion==='success' && this.expectedConfig?.confirmationSlotEnabled===true)throw new Rejected('holder_publication_requires_slot_background');
    const id=randomToken();
    // The durable issued record exists before HTTP. An uncertain attempt blocks later writes
    // for this target until an explicitly implemented reconciler resolves it.
    await this.batch([
      ...this.guard(`(SELECT revision FROM state WHERE id=1)=? AND ${DB_NOW}<?`,check.proof.revision,check.proof.expires_at),
      ...this.guard("NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND phase NOT IN ('confirmed','resolved'))",key),
      this.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text)
        VALUES(?,?,?,?,?,'issued',${DB_NOW},${DB_NOW},?,?)`,id,key,check.headSha,check.proof.revision,check.proof.expires_at,check.conclusion,check.text),
      this.stmt(`INSERT INTO outbox(target_key,wanted_revision,due) VALUES(?,(SELECT revision FROM state WHERE id=1),0)
        ON CONFLICT(target_key) DO UPDATE SET wanted_revision=excluded.wanted_revision,generation=outbox.generation+1,due=0,attempts=0`,key),
    ]); return id;
  }
  async beginAutomaticPublication(job:{request_key:string;pr:number;head:string;attempt:string},check:CheckProposal):Promise<string> {
    const slot=this.expectedConfig?.confirmationSlotEnabled===true && check.conclusion==='success';
    if(slot && !this.budget)throw new Rejected('invocation_budget_required');
    if(check.headSha!==job.head || check.proof.head_sha!==job.head || check.proof.pr!==job.pr || check.proof.kind!=='pr' ||
      check.text!=='tavotto-cla-v1\n'+canonical(check.proof) || check.proof.expires_at<=check.proof.issued_at || check.proof.expires_at>check.proof.issued_at+120 ||
      check.proof.expires_at>(check.authorityExpires ?? Number.MAX_SAFE_INTEGER))throw new Rejected('fresh_proof_unavailable');
    const target={kind:'pr' as const,number:job.pr},key=this.targetKey(check.proof.repository_id,target),json=canonical(target),id=randomToken();
    await this.batch([
      this.stmt('INSERT OR IGNORE INTO targets VALUES(?,?)',key,json),
      ...this.guard(`EXISTS(SELECT 1 FROM targets WHERE target_key=? AND target_json=?) AND
        (SELECT revision FROM state WHERE id=1)=? AND ?<=${DB_NOW} AND ${DB_NOW}<? AND
        EXISTS(SELECT 1 FROM head_requests WHERE request_key=? AND pr=? AND head=? AND status='running' AND attempt=? AND lease_until>${DB_NOW}) AND
        NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND (head_sha=? OR phase NOT IN ('confirmed','resolved'))) AND
        NOT EXISTS(SELECT 1 FROM refresh_requests WHERE target_key=? AND status='manual')`,
        key,json,check.proof.revision,check.proof.issued_at,check.proof.expires_at,job.request_key,job.pr,job.head,job.attempt,key,job.head,key),
      // The head claim's persisted lease outlives the invocation's 90-second cleanup
      // budget. A recovery tick/revision change cannot terminalize a live sender.
      ...(slot?[this.stmt('INSERT INTO _guards(id,ok) VALUES(?,1)','confirmation-slot:'+id)]:[]),
      this.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text,send_until)
        VALUES(?,?,?,?,?,'issued',${DB_NOW},${DB_NOW},?,?,(SELECT lease_until FROM head_requests WHERE request_key=? AND attempt=?))`,
        id,key,check.headSha,check.proof.revision,check.proof.expires_at,check.conclusion,check.text,job.request_key,job.attempt),
      this.stmt("UPDATE head_requests SET status='dispatched',result='' WHERE request_key=? AND attempt=?",job.request_key,job.attempt),
      this.stmt(`INSERT INTO outbox(target_key,wanted_revision,due) VALUES(?,?,0) ON CONFLICT(target_key) DO UPDATE SET
        wanted_revision=excluded.wanted_revision,generation=outbox.generation+1,due=0,attempts=0`,key,check.proof.revision),
    ]);return id;
  }
  async saveDocument(d:Document):Promise<void> {
    await this.batch([this.stmt('INSERT OR IGNORE INTO agreement_documents VALUES(?,?,?,?,?)',d.digest,d.kind,d.version,d.source_sha,d.content),
      ...this.guard('EXISTS(SELECT 1 FROM agreement_documents WHERE digest=? AND kind=? AND version=? AND content=?)',d.digest,d.kind,d.version,d.content)]);
  }
  async document(digest:string):Promise<Document|null> {return this.stmt('SELECT * FROM agreement_documents WHERE digest=?',digest).first<Document>();}
  async latestPublication(key:string):Promise<Publication|null> {
    return this.stmt(`SELECT p.*,CASE WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN 1 ELSE NULL END AS confirmation_slot FROM publication_attempts p WHERE target_key=? ORDER BY rowid DESC LIMIT 1`,key).first<Publication>();
  }
  async confirmedPublication(key:string,head:string,run:number,attempt:string):Promise<Publication|null> {
    return this.stmt(`SELECT p.*,CASE WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN 1 ELSE NULL END AS confirmation_slot FROM publication_attempts p WHERE target_key=? AND head_sha=? AND run_id=? AND id=? AND phase='confirmed'
      AND rowid=(SELECT max(q.rowid) FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha)`,key,head,run,attempt).first<Publication>();
  }
  async refreshRequest(job:Job):Promise<RefreshRequest|null> {
    const row=await this.stmt(`SELECT r.*,CASE WHEN (${this.positiveAdmission()}) THEN 0 ELSE 1 END AS capacity_blocked FROM refresh_requests r WHERE r.target_key=? AND r.status='pending'
      AND r.revision=(SELECT revision FROM state WHERE id=1) AND r.due<=${DB_NOW} AND r.attempts<3
      AND r.source_attempt=coalesce((SELECT p.id FROM publication_attempts p WHERE p.target_key=r.target_key ORDER BY p.rowid DESC LIMIT 1),'')
      AND NOT EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=r.target_key AND p.phase!='resolved')
      AND NOT EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=r.target_key AND p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
      AND EXISTS(SELECT 1 FROM outbox o WHERE o.target_key=r.target_key AND o.lease=? AND o.generation=? AND o.lease_until>${DB_NOW})`,
      job.target_key,job.lease,job.generation).first<Omit<RefreshRequest,'capacity_blocked'> & {capacity_blocked:number}>();
    if(!row)return null;const {capacity_blocked,...request}=row;return {...request,...(capacity_blocked?{capacity_blocked:true}:{})};
  }
  async refreshPredecessor(key:string,head:string):Promise<Publication|null> {
    return this.stmt(`SELECT p.*,CASE WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN 1 ELSE NULL END AS confirmation_slot FROM publication_attempts p WHERE p.target_key=? AND p.head_sha=? AND p.phase='resolved'
      AND p.run_id IS NOT NULL AND p.run_id>0
      AND p.rowid=(SELECT max(q.rowid) FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha)`,key,head).first<Publication>();
  }
  private refreshGuard(job:Job,r:RefreshRequest,extra='1',values:unknown[]=[]):D1Statement[] {
    if(r.target_key!==job.target_key || r.status!=='pending')throw new Rejected('refresh_changed');
    return this.guard(`EXISTS(SELECT 1 FROM outbox WHERE target_key=? AND lease=? AND generation=? AND lease_until>${DB_NOW})
      AND (SELECT revision FROM state WHERE id=1)=?
      AND EXISTS(SELECT 1 FROM refresh_requests r WHERE r.target_key=? AND r.source_attempt=? AND r.revision=?
        AND r.status='pending' AND r.attempts=? AND r.attempts<3 AND r.due=? AND r.due<=${DB_NOW} AND r.reason=?
        AND r.source_attempt=coalesce((SELECT p.id FROM publication_attempts p WHERE p.target_key=r.target_key ORDER BY p.rowid DESC LIMIT 1),''))
      AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=? AND phase!='resolved') AND (${extra})`,
      job.target_key,job.lease,job.generation,r.revision,job.target_key,r.source_attempt,r.revision,r.attempts,r.due,r.reason,job.target_key,...values);
  }
  async beginFreshPublication(job:Job,r:RefreshRequest,check:CheckProposal,predecessor:Publication):Promise<string> {
    const slot=this.expectedConfig?.confirmationSlotEnabled===true;
    if(slot && !this.budget)throw new Rejected('invocation_budget_required');
    if(check.conclusion!=='success' || check.proof.head_sha!==check.headSha || check.proof.revision!==r.revision ||
      !Number.isSafeInteger(check.proof.issued_at) || !Number.isSafeInteger(check.proof.expires_at) ||
      check.proof.expires_at>check.proof.issued_at+120 || check.proof.expires_at<=check.proof.issued_at ||
      check.proof.expires_at>(check.authorityExpires ?? Number.MAX_SAFE_INTEGER) || check.text!=='tavotto-cla-v1\n'+canonical(check.proof))throw new Rejected('fresh_proof_unavailable');
    if(predecessor.id!==r.source_attempt || predecessor.target_key!==job.target_key || predecessor.head_sha!==check.headSha ||
      predecessor.phase!=='resolved' || predecessor.run_id===null || !Number.isSafeInteger(predecessor.run_id) || predecessor.run_id<=0)throw new Rejected('fresh_predecessor_unavailable');
    const id=randomToken();
    // The request and the exact resolved source are consumed in the same transaction
    // as the new issued journal. A fresh attempt never mutates the old proof or expiry.
    await this.batch([
      ...this.refreshGuard(job,r,`${DB_NOW}<? AND ?<=${DB_NOW} AND EXISTS(SELECT 1 FROM publication_attempts p
        WHERE p.id=? AND p.target_key=? AND p.head_sha=? AND p.phase='resolved' AND p.run_id=? AND p.revision=?)`,
        [check.proof.expires_at,check.proof.issued_at,predecessor.id,job.target_key,predecessor.head_sha,predecessor.run_id,predecessor.revision]),
      ...(slot?[this.stmt('INSERT INTO _guards(id,ok) VALUES(?,1)','confirmation-slot:'+id)]:[]),
      this.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,run_id,phase,created,updated,conclusion,proof_text,send_until)
        VALUES(?,?,?,?,?,?,'issued',${DB_NOW},${DB_NOW},?,?,?)`,id,job.target_key,check.headSha,check.proof.revision,check.proof.expires_at,predecessor.run_id,check.conclusion,check.text,slot?job.lease_until:0),
      this.stmt("UPDATE refresh_requests SET status='done',reason='fresh_publication_issued' WHERE target_key=?",job.target_key),
    ]);return id;
  }
  async completeRefresh(job:Job,r:RefreshRequest,reason:string):Promise<void> {
    await this.batch([...this.refreshGuard(job,r),
      this.stmt("UPDATE refresh_requests SET status='done',reason=? WHERE target_key=?",reason,job.target_key),
      this.stmt('DELETE FROM outbox WHERE target_key=? AND lease=? AND generation=?',job.target_key,job.lease,job.generation)]);
  }
  async failRefresh(job:Job,r:RefreshRequest):Promise<void> {
    const blocked=`NOT (${this.positiveAdmission()})`;
    await this.batch([...this.refreshGuard(job,r),
      this.stmt(`UPDATE refresh_requests SET status=CASE WHEN ${blocked} THEN 'pending' WHEN attempts>=2 THEN 'manual' ELSE 'pending' END,
        reason=CASE WHEN ${blocked} THEN 'confirmation_slot_wait' WHEN attempts>=2 THEN 'refresh_retry_exhausted' ELSE 'refresh_retry_pending' END,
        attempts=CASE WHEN ${blocked} THEN attempts ELSE min(3,attempts+1) END,due=${DB_NOW}+CASE WHEN ${blocked} THEN 1 ELSE 60 END WHERE target_key=?`,job.target_key),
      this.stmt(`DELETE FROM outbox WHERE target_key=? AND lease=? AND generation=?
        AND EXISTS(SELECT 1 FROM refresh_requests r WHERE r.target_key=outbox.target_key AND r.status='manual')`,job.target_key,job.lease,job.generation),
      this.stmt(`UPDATE outbox SET due=(SELECT due FROM refresh_requests WHERE target_key=outbox.target_key),lease='',lease_until=0,attempts=0
        WHERE target_key=? AND lease=? AND generation=?`,job.target_key,job.lease,job.generation)]);
  }
  async deferRefreshCapacity(job:Job,r:RefreshRequest):Promise<void> {
    await this.batch([...this.refreshGuard(job,r),
      this.stmt(`UPDATE refresh_requests SET due=${DB_NOW}+1,reason='confirmation_slot_wait' WHERE target_key=?`,job.target_key),
      this.stmt(`UPDATE outbox SET due=${DB_NOW}+1,lease='',lease_until=0,attempts=0 WHERE target_key=? AND lease=? AND generation=?`,job.target_key,job.lease,job.generation)]);
  }
  async settleFreshPublication(job:Job,id:string,phase:'reconcile'|'unknown',run:number|null=null):Promise<void> {
    // HTTP may finish after the revision/generation changes. Preserve its journal,
    // but never release or reschedule an outbox belonging to the newer generation.
    await this.batch([
      this.stmt(`UPDATE publication_attempts SET phase=?,run_id=coalesce(?,run_id),updated=${DB_NOW},reconcile_due=0,send_until=0
        WHERE id=? AND target_key=? AND phase='issued' AND (? IS NULL OR run_id=?)`,phase,run,id,job.target_key,run,run),
      ...this.guard('changes()=1'),
      this.stmt(`UPDATE outbox SET due=0,lease='',lease_until=0,attempts=0
        WHERE target_key=? AND lease=? AND generation=?`,job.target_key,job.lease,job.generation),
    ]);
  }
  private leaseGuard(job:Job,revision:number):D1Statement[] {
    return this.guard(`EXISTS(SELECT 1 FROM outbox WHERE target_key=? AND lease=? AND lease_until>${DB_NOW} AND generation=?)
      AND (SELECT revision FROM state WHERE id=1)=?`,job.target_key,job.lease,job.generation,revision);
  }
  async openProgress(job:Job,revision:number):Promise<Progress> {
    await this.batch([...this.leaseGuard(job,revision),
      this.stmt('INSERT OR IGNORE INTO reconciliation_progress(target_key) VALUES(?)',job.target_key),
      this.stmt('UPDATE reconciliation_progress SET horizon=coalesce((SELECT max(rowid) FROM publication_attempts WHERE target_key=?),0) WHERE target_key=? AND horizon=0',job.target_key,job.target_key)]);
    return (await this.stmt('SELECT cursor,horizon FROM reconciliation_progress WHERE target_key=?',job.target_key).first<Progress>())!;
  }
  private eligible(job:Job):string {
    return `(p.phase NOT IN ('resolved','manual') OR (p.phase='resolved' AND p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))) AND p.reconcile_attempts<3 AND (p.reconcile_due IS NOT NULL OR p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
      AND (p.phase!='issued' OR p.send_until<=${DB_NOW})
      AND (p.id IS NOT (SELECT active_attempt FROM slot_policy WHERE id=1) OR p.send_until<=${DB_NOW})
      AND (p.reconcile_due<=${DB_NOW} OR (p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND p.phase='resolved') OR (p.observation!='budget_deferred' AND p.reconcile_attempts=0 AND (p.observed_generation!=${job.generation} OR p.expires<=${DB_NOW})))
      AND (p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) OR p.rowid=(SELECT max(q.rowid) FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha))`;
  }
  async publicationBatch(job:Job,progress:Progress):Promise<Publication[]> {
    return (await this.stmt(`SELECT p.*,CASE WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN 1 ELSE NULL END AS confirmation_slot,p.rowid AS position FROM publication_attempts p WHERE p.target_key=?
      AND p.rowid>? AND p.rowid<=? AND ${this.eligible(job)} ORDER BY CASE WHEN ${this.withdrawalPriority()} THEN 0 ELSE 1 END,p.rowid LIMIT ${FREE_BUDGET.heads}`,job.target_key,progress.cursor,progress.horizon).all<Publication>()).results;
  }
  private advance(job:Job,p:Publication):D1Statement[] {
    if(!Number.isSafeInteger(p.position) || p.position!<1)throw new Rejected('progress_unavailable');
    return [this.stmt('UPDATE reconciliation_progress SET cursor=? WHERE target_key=? AND cursor<? AND horizon>=?',p.position,job.target_key,p.position,p.position),...this.guard('changes()=1')];
  }
  async publicationFailure(job:Job,p:Publication,revision:number):Promise<void> {
    await this.batch([...this.leaseGuard(job,revision),this.stmt(`UPDATE publication_attempts SET
      phase=CASE WHEN reconcile_attempts>=2 THEN 'manual' ELSE phase END,
      observation=CASE WHEN reconcile_attempts>=2 THEN 'retry_exhausted' ELSE 'retry_pending' END,
      reconcile_due=CASE WHEN reconcile_attempts>=2 THEN NULL ELSE ${DB_NOW}+CASE reconcile_attempts WHEN 0 THEN 2 WHEN 1 THEN 4 ELSE 8 END END,
      reconcile_attempts=min(3,reconcile_attempts+1),observed_generation=?,updated=${DB_NOW} WHERE id=?
      AND phase=? AND (phase!='issued' OR send_until<=${DB_NOW})`,job.generation,p.id,p.phase),
      ...this.guard('changes()=1'),...this.advance(job,p)]);
  }
  async deferPublication(job:Job,p:Publication,revision:number):Promise<void> {
    await this.batch([...this.leaseGuard(job,revision),this.stmt(`UPDATE publication_attempts SET
      observation='budget_deferred',reconcile_due=${DB_NOW}+60,observed_generation=?,updated=${DB_NOW} WHERE id=?
      AND phase=? AND (phase!='issued' OR send_until<=${DB_NOW})`,job.generation,p.id,p.phase),
      ...this.guard('changes()=1'),...this.advance(job,p),
      this.stmt(`UPDATE outbox SET due=${DB_NOW}+1,lease='',lease_until=0 WHERE target_key=? AND lease=? AND generation=?`,job.target_key,job.lease,job.generation)]);
  }
  async deferTarget(job:Job):Promise<void> {
    await this.stmt(`UPDATE outbox SET due=${DB_NOW}+1,lease='',lease_until=0
      WHERE target_key=? AND lease=? AND generation=? AND lease_until>${DB_NOW}`,job.target_key,job.lease,job.generation).run();
  }
  async finishBatch(job:Job,revision:number):Promise<void> {
    const progress=(await this.stmt('SELECT cursor,horizon FROM reconciliation_progress WHERE target_key=?',job.target_key).first<Progress>())!;
    const ahead=await this.stmt(`SELECT 1 FROM publication_attempts p WHERE p.target_key=? AND p.rowid>? AND p.rowid<=? AND ${this.eligible(job)} LIMIT 1`,job.target_key,progress.cursor,progress.horizon).first();
    const next=await this.stmt(`SELECT min(CASE WHEN p.phase='issued' AND p.send_until>${DB_NOW} THEN p.send_until
      WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND p.send_until>${DB_NOW} THEN p.send_until
      WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND p.phase='manual' THEN ${DB_NOW}+60
      WHEN p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND p.phase='resolved' THEN 0
      WHEN p.observation!='budget_deferred' AND p.reconcile_attempts=0 AND (p.observed_generation!=? OR p.expires<=${DB_NOW}) THEN 0 ELSE p.reconcile_due END) AS due
      FROM publication_attempts p WHERE p.target_key=? AND (p.phase NOT IN ('resolved','manual') OR p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
      AND (p.reconcile_attempts<3 OR p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
      AND (p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) OR p.rowid=(SELECT max(q.rowid) FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha))`,job.generation,job.target_key).first<{due:number|null}>();
    const pending=`SELECT r.due FROM refresh_requests r WHERE r.target_key=outbox.target_key AND r.status='pending'
      AND r.revision=(SELECT revision FROM state WHERE id=1) AND r.attempts<3
      AND r.source_attempt=coalesce((SELECT p.id FROM publication_attempts p WHERE p.target_key=r.target_key ORDER BY p.rowid DESC LIMIT 1),'')
      AND NOT EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=r.target_key AND p.phase!='resolved')`;
    await this.batch([...this.leaseGuard(job,revision),
      ...(!ahead?[
        this.stmt('UPDATE reconciliation_progress SET cursor=0,horizon=0 WHERE target_key=?',job.target_key),
        this.stmt(`INSERT INTO refresh_requests(target_key,source_attempt,revision,status,attempts,due,reason)
          SELECT t.target_key,coalesce((SELECT id FROM publication_attempts WHERE target_key=t.target_key ORDER BY rowid DESC LIMIT 1),''),?,
            CASE WHEN EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=t.target_key AND p.phase!='resolved'
              AND EXISTS(SELECT 1 FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha AND q.rowid>p.rowid)) THEN 'manual' ELSE 'pending' END,
            0,${DB_NOW},CASE WHEN EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=t.target_key AND p.phase!='resolved'
              AND EXISTS(SELECT 1 FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha AND q.rowid>p.rowid))
              THEN 'superseded_history_requires_review' ELSE 'reconciliation_resolved' END
          FROM targets t WHERE t.target_key=? AND (
            NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=t.target_key AND phase!='resolved') OR
            EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=t.target_key AND p.phase!='resolved'
              AND EXISTS(SELECT 1 FROM publication_attempts q WHERE q.target_key=p.target_key AND q.head_sha=p.head_sha AND q.rowid>p.rowid)))
          ON CONFLICT(target_key) DO UPDATE SET source_attempt=excluded.source_attempt,revision=excluded.revision,
            status=excluded.status,attempts=0,due=excluded.due,reason=excluded.reason
          WHERE refresh_requests.status!='manual' AND (excluded.status='manual'
            OR refresh_requests.source_attempt!=excluded.source_attempt OR refresh_requests.revision!=excluded.revision)`,revision,job.target_key),
      ]:[]),
      ...(next?.due===null && !ahead?[this.stmt(`DELETE FROM outbox WHERE target_key=? AND lease=? AND generation=?
        AND NOT EXISTS(${pending}) AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE target_key=outbox.target_key AND id=(SELECT active_attempt FROM slot_policy WHERE id=1))`,job.target_key,job.lease,job.generation)]:[]),
      this.stmt(`UPDATE outbox SET due=max(${DB_NOW}+1,coalesce((${pending}),?)),lease='',lease_until=0,attempts=0
        WHERE target_key=? AND lease=? AND generation=?`,ahead?0:next?.due ?? 0,job.target_key,job.lease,job.generation),
    ]);
  }
  override async claim(_now:number):Promise<ClaimedJob|null> {
    const result=await this.db.batch<ClaimedJob>([this.stmt(`UPDATE outbox SET lease=?,lease_until=${DB_NOW}+120
      WHERE target_key=(SELECT target_key FROM outbox WHERE due<=${DB_NOW} AND lease_until<=${DB_NOW} AND attempts<3
      AND NOT EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=outbox.target_key AND p.phase='issued' AND p.send_until>${DB_NOW})
      AND NOT EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=outbox.target_key AND p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND p.send_until>${DB_NOW})
      ORDER BY CASE WHEN EXISTS(SELECT 1 FROM publication_attempts p WHERE p.target_key=outbox.target_key AND ${this.withdrawalPriority()}) THEN 0 ELSE 1 END,
      due,target_key LIMIT 1) AND lease_until<=${DB_NOW} RETURNING *,${DB_NOW} AS lease_clock`,randomToken())]);return result[0]?.results[0] ?? null;
  }
  override async owns(job:Job,_now:number):Promise<boolean> {
    return !!await this.stmt(`SELECT 1 FROM outbox WHERE target_key=? AND lease=? AND lease_until>${DB_NOW} AND generation=?`,job.target_key,job.lease,job.generation).first();
  }
  async reconcile(job:Job,p:Publication,phase:'confirmed'|'resolved'|'manual',run:number|null,observation:string,revision:number,due:number|null):Promise<void> {
    await this.recordReconciliation(job,p,phase,run,observation,revision);
    await this.completeReconciliation(job,revision,due);
  }
  async recordReconciliation(job:Job,p:Publication,phase:'confirmed'|'resolved'|'manual',run:number|null,observation:string,revision:number,due:number|null=null,
    release?:{source:GitHub;witness:NegativeWitness}):Promise<void> {
    const owned=p.confirmation_slot===1;let negative='';
    if(owned && phase==='resolved'){
      if(!release || !(release.source instanceof GitHub) || !this.budget || release.source.budget!==this.budget ||
        this.expectedConfig && release.source.c.repositoryId!==this.expectedConfig.repositoryId)throw new Rejected('fresh_negative_release_required');
      negative=release.source.consumeNegativeWitness(release.witness,p,job,revision);
      if(JSON.parse(negative).run_id!==run)throw new Rejected('fresh_negative_release_required');
    }else if(release)throw new Rejected('fresh_negative_release_required');
    await this.batch([
      ...this.guard(`EXISTS(SELECT 1 FROM outbox WHERE target_key=? AND lease=? AND lease_until>${DB_NOW} AND generation=?)
        AND (SELECT revision FROM state WHERE id=1)=?`,job.target_key,job.lease,job.generation,revision),
      this.stmt(`UPDATE publication_attempts SET phase=?,run_id=CASE WHEN id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN coalesce(?,run_id) ELSE ? END,
        observation=?,updated=${DB_NOW},reconcile_due=?,reconcile_attempts=0,observed_generation=? WHERE id=?
        AND phase=? AND (phase!='issued' OR send_until<=${DB_NOW})
        AND target_key=? AND head_sha=? AND revision=? AND expires=? AND proof_text=? AND run_id IS ?
        AND CASE WHEN id=(SELECT active_attempt FROM slot_policy WHERE id=1) THEN 1 ELSE 0 END=?
        AND (id IS NOT (SELECT active_attempt FROM slot_policy WHERE id=1) OR send_until<=${DB_NOW})
        AND (?='' OR (${DB_NOW}>=json_extract(?,'$.observed_at') AND ${DB_NOW}<json_extract(?,'$.valid_until')))
        AND (?!='confirmed' OR (revision=? AND expires>${DB_NOW}))`,phase,run,run,negative||observation,due,job.generation,
        p.id,p.phase,p.target_key,p.head_sha,p.revision,p.expires,p.proof_text,p.run_id,owned?1:0,
        negative,negative,negative,phase,revision),
      ...this.guard('changes()=1'),
      ...(p.position===undefined?[]:this.advance(job,p)),
    ]);
  }
  async completeReconciliation(job:Job,revision:number,due:number|null):Promise<void> {
    await this.batch([
      ...this.guard(`EXISTS(SELECT 1 FROM outbox WHERE target_key=? AND lease=? AND lease_until>${DB_NOW} AND generation=?)
        AND (SELECT revision FROM state WHERE id=1)=?`,job.target_key,job.lease,job.generation,revision),
      ...(due===null?[this.stmt('DELETE FROM outbox WHERE target_key=? AND lease=? AND generation=?',job.target_key,job.lease,job.generation)]:
        [this.stmt("UPDATE outbox SET due=?,lease='',lease_until=0,attempts=0 WHERE target_key=? AND lease=? AND generation=?",due,job.target_key,job.lease,job.generation)]),
    ]);
  }
  async settlePublication(id:string,phase:'confirmed'|'unknown'|'reconcile',run:number|null=null):Promise<void> {
    await this.batch([
      this.stmt(`UPDATE publication_attempts SET phase=?,run_id=?,updated=${DB_NOW} WHERE id=? AND phase='issued'
        AND (?!='confirmed' OR (revision=(SELECT revision FROM state WHERE id=1) AND expires>${DB_NOW}))`,phase,run,id,phase),
      ...this.guard('changes()=1'),
    ]);
  }
  async settleAutomaticPublication(id:string,phase:'unknown'|'reconcile',run:number|null=null):Promise<void> {
    await this.batch([
      this.stmt(`UPDATE publication_attempts SET phase=?,run_id=?,updated=${DB_NOW},send_until=0,reconcile_due=0
        WHERE id=? AND phase='issued' AND send_until>0`,phase,run,id),
      ...this.guard('changes()=1'),
      // Wake current evidence, without releasing any concurrently claimed lease
      // or changing its generation. Stale selected rows are rejected by phase CAS.
      this.stmt(`INSERT INTO outbox(target_key,wanted_revision,due)
        SELECT target_key,(SELECT revision FROM state WHERE id=1),0 FROM publication_attempts WHERE id=?
        ON CONFLICT(target_key) DO UPDATE SET wanted_revision=excluded.wanted_revision,due=0,attempts=0`,id),
    ]);
  }
  override async addOAuth(hash:string,browser:string,cipher:string,_expires:number):Promise<void> {
    // A global rolling burst bound, independent of the edge's per-location limit.
    // Consumed rows retain their slot until expiry; callback success cannot reset it.
    const results=await this.db.batch<{state_hash:string}>([
      this.stmt(`DELETE FROM oauth_states WHERE state_hash IN
        (SELECT state_hash FROM oauth_states WHERE expires<=${DB_NOW} ORDER BY expires,state_hash LIMIT 20)`),
      this.stmt(`INSERT INTO oauth_states(state_hash,browser_hash,verifier_cipher,expires)
        SELECT ?,?,?,${DB_NOW}+300 WHERE
        (SELECT COUNT(*) FROM (SELECT 1 FROM oauth_states WHERE expires>${DB_NOW} LIMIT 10))<10
        AND (SELECT COUNT(*) FROM (SELECT 1 FROM oauth_states LIMIT 100))<100
        ON CONFLICT(state_hash) DO NOTHING RETURNING state_hash`,hash,browser,cipher),
    ]);
    if(results.some(r=>!r.success))throw new Rejected('transaction_rejected');
    if(!results[1]?.results[0])throw new Rejected('oauth_busy');
  }
  override async consumeOAuth(hash:string,browser:string,_now:number):Promise<string> {
    const result = await this.db.batch<{verifier_cipher:string}>([this.stmt(`UPDATE oauth_states SET used=1
      WHERE state_hash=? AND browser_hash=? AND expires>${DB_NOW} AND used=0 RETURNING verifier_cipher`,hash,browser)]);
    const row = result[0]?.results[0];
    if (!row || result.some(x=>!x.success)) throw new Rejected('oauth_rejected'); return row.verifier_cipher;
  }
  override async createPacket(_p:Packet):Promise<Packet> {throw new Rejected('authenticated_lifecycle_required');}
  override async qualifiedUntil(_subject:number,_digest:string,_now:number):Promise<number|null> {throw new Rejected('verified_lifecycle_required');}
  async subjectBlocked(subject:number):Promise<boolean> {
    return !!await this.stmt('SELECT 1 AS blocked FROM eligibility_blocks b JOIN packets p ON p.id=b.packet WHERE p.subject=? LIMIT 1',subject).first();
  }
  async completed(id:string):Promise<boolean> {
    return !!await this.stmt(`SELECT 1 AS complete FROM packets p
      JOIN signatures c ON c.packet=p.id AND c.role='contributor' AND c.actor=p.subject
      JOIN signatures h ON h.packet=p.id AND h.role='holder' AND h.actor=(SELECT holder FROM authority WHERE id=1) AND h.actor!=p.subject
      WHERE p.id=?`,id).first();
  }
  async reusablePacket(subject:number,digest:string):Promise<Packet|null> {
    const rows=(await this.stmt(`SELECT p.* FROM packets p WHERE subject=? AND digest=? AND
      (p.valid_until>${DB_NOW} OR EXISTS(SELECT 1 FROM signatures c JOIN signatures h ON h.packet=c.packet
        WHERE c.packet=p.id AND c.role='contributor' AND c.actor=p.subject AND h.role='holder'
        AND h.actor=(SELECT holder FROM authority WHERE id=1) AND h.actor!=p.subject)) LIMIT 2`,subject,digest).all<Packet>()).results;
    if(rows.length>1)throw new Rejected('packet_ineligible');return rows[0] ?? null;
  }
  async createAuthenticatedPacket(s:Session,p:Packet):Promise<Packet> {
    const ready=this.readiness(),matches=!this.expectedConfig || packetAgreementMatches(this.expectedConfig,p);
    // D1 batch is one transaction. A racing creator sees the previous committed
    // active/completed row; no signature or prior packet is copied or extended.
    const usable=`(p.valid_until>${DB_NOW} OR EXISTS(SELECT 1 FROM signatures c JOIN signatures h ON h.packet=c.packet
      WHERE c.packet=p.id AND c.role='contributor' AND c.actor=p.subject AND h.role='holder'
      AND h.actor=(SELECT holder FROM authority WHERE id=1) AND h.actor!=p.subject))`;
    await this.batch([
      ...this.guard(`EXISTS(SELECT 1 FROM sessions WHERE token_hash=? AND actor=? AND csrf_hash=? AND expires>${DB_NOW}) AND ?=?
        AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=?)
        AND (${ready.sql}) AND ?=1`,s.token_hash,s.actor,s.csrf_hash,s.actor,p.subject,p.subject,...ready.values,matches?1:0),
      this.stmt(`INSERT INTO packets SELECT ?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM packets p WHERE subject=? AND digest=? AND ${usable})`,p.id,p.subject,p.kind,p.version,p.digest,p.created,p.valid_until,p.subject,p.digest),
      ...this.guard(`(SELECT count(*) FROM packets p WHERE subject=? AND digest=? AND ${usable})=1`,p.subject,p.digest),
    ]);
    const row=await this.stmt(`SELECT * FROM packets p WHERE subject=? AND digest=? AND ${usable}`,p.subject,p.digest).first<Packet>();
    if(!row || this.expectedConfig && !packetAgreementMatches(this.expectedConfig,row))throw new Rejected('packet_unavailable');return row;
  }
  async addAuthenticatedIntent(hash:string,s:Session,p:Packet,role:Role|'hold',expires:number):Promise<void> {
    const ready=this.readiness(),tuple=this.currentTuple();
    await this.batch([
      ...this.guard(`EXISTS(SELECT 1 FROM sessions s JOIN packets p ON p.id=? WHERE (${ready.sql}) AND (?='hold' OR (${tuple.sql})) AND s.token_hash=? AND s.actor=?
        AND s.csrf_hash=? AND s.expires>${DB_NOW} AND (?='hold' OR p.valid_until>${DB_NOW}) AND p.digest=?
        AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject)
        AND ((?='contributor' AND s.actor=p.subject) OR (?='holder' AND s.actor=(SELECT holder FROM authority WHERE id=1)
          AND s.actor!=p.subject AND EXISTS(SELECT 1 FROM signatures WHERE packet=p.id AND role='contributor' AND actor=p.subject))
          OR (?='hold' AND (s.actor=p.subject OR s.actor=(SELECT holder FROM authority WHERE id=1)))))`,
        p.id,...ready.values,role,...tuple.values,s.token_hash,s.actor,s.csrf_hash,role,p.digest,role,role,role),
      this.stmt('INSERT INTO intents(token_hash,session_hash,packet,role,digest,expires) VALUES(?,?,?,?,?,?)',hash,s.token_hash,p.id,role,p.digest,expires),
    ]);
  }
  async signAtomic(input:{session:Session;packet:Packet;intentHash:string;role:Role;cipher:string;evidenceHash:string;created:number;revision:number}):Promise<number> {
    const {session:s,packet:p,role} = input;
    const ready=this.readiness(),tuple=this.currentTuple();
    await this.batch([
      ...this.cas(input.revision),
      ...this.guard(`EXISTS(SELECT 1 FROM intents i JOIN sessions s ON s.token_hash=i.session_hash JOIN packets p ON p.id=i.packet
        WHERE (${ready.sql}) AND (${tuple.sql}) AND i.token_hash=? AND s.token_hash=? AND p.id=? AND i.used=0 AND i.expires>${DB_NOW}
        AND s.expires>${DB_NOW} AND p.valid_until>${DB_NOW} AND i.digest=p.digest AND p.digest=?
        AND p.kind=? AND p.version=? AND i.role=? AND s.actor=? AND s.login=? AND s.csrf_hash=?
        AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject)
        AND NOT EXISTS(SELECT 1 FROM signatures g WHERE g.packet=p.id AND g.role=i.role)
        AND ((i.role='contributor' AND s.actor=p.subject) OR (i.role='holder' AND s.actor=(SELECT holder FROM authority WHERE id=1)
          AND s.actor!=p.subject AND EXISTS(SELECT 1 FROM signatures g WHERE g.packet=p.id AND g.role='contributor' AND g.actor=p.subject))))`,
        ...ready.values,...tuple.values,input.intentHash,s.token_hash,p.id,p.digest,p.kind,p.version,role,s.actor,s.login,s.csrf_hash),
      this.stmt('INSERT INTO signatures VALUES(?,?,?,?,?,?,?)',p.id,role,s.actor,input.cipher,input.evidenceHash,null,input.created),
      this.stmt('UPDATE intents SET used=1 WHERE token_hash=?',input.intentHash),
      this.stmt('INSERT INTO audit(packet,actor,event,created) VALUES(?,?,?,?)',p.id,s.actor,'signed:'+role,input.created),
    ]);
    return input.revision+1; // Own committed revision, not an unrelated later writer's revision.
  }
  async holdAtomic(s:Session,p:Packet,intent:string,revision:number,created:number):Promise<number> {
    await this.batch([
      ...this.cas(revision),
      ...this.guard(`EXISTS(SELECT 1 FROM intents i JOIN sessions s ON s.token_hash=i.session_hash JOIN packets p ON p.id=i.packet
        WHERE i.token_hash=? AND i.session_hash=? AND p.id=? AND i.role='hold' AND i.used=0 AND i.expires>${DB_NOW}
        AND s.expires>${DB_NOW} AND s.actor=? AND s.csrf_hash=? AND i.digest=p.digest AND p.digest=?
        AND (s.actor=p.subject OR s.actor=(SELECT holder FROM authority WHERE id=1))
        AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject))`,intent,s.token_hash,p.id,s.actor,s.csrf_hash,p.digest),
      this.stmt('INSERT INTO eligibility_blocks VALUES(?,?,?,?)',p.id,s.actor,created,'party_requested_review'),
      this.stmt('UPDATE intents SET used=1 WHERE token_hash=?',intent),
      this.stmt('INSERT INTO audit(packet,actor,event,created) VALUES(?,?,?,?)',p.id,s.actor,'hold',created),
    ]); return revision+1;
  }
  async signatures(packet:string):Promise<SignatureRow[]> {
    return (await this.stmt('SELECT role,actor,evidence_cipher,evidence_hash,created FROM signatures WHERE packet=?',packet).all<SignatureRow>()).results;
  }
  async delegatedPacket(subject:number,digest:string,grant:string):Promise<Packet|null> {
    return this.stmt(`SELECT p.* FROM packets p JOIN signatures c ON c.packet=p.id AND c.role='contributor' AND c.actor=p.subject
      WHERE p.subject=? AND p.digest=? AND EXISTS(SELECT 1 FROM delegated_receipts WHERE packet=p.id AND grant_id=?)
      AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject)
      ORDER BY p.created DESC,p.id DESC LIMIT 1`,subject,digest,grant).first<Packet>();
  }
  async delegatedCandidate(subject:number,digest:string,grant:string):Promise<{packet:Packet;rows:SignatureRow[]}|null> {
    const row=await this.stmt(`SELECT p.*,
      (SELECT json_group_array(json_object('role',role,'actor',actor,'evidence_cipher',evidence_cipher,'evidence_hash',evidence_hash,'created',created))
        FROM signatures WHERE packet=p.id) signature_rows
      FROM packets p WHERE p.subject=? AND p.digest=?
      AND EXISTS(SELECT 1 FROM signatures WHERE packet=p.id AND role='contributor' AND actor=p.subject)
      AND (EXISTS(SELECT 1 FROM signatures WHERE packet=p.id AND role='holder' AND actor=(SELECT holder FROM authority WHERE id=1) AND actor!=p.subject)
        OR EXISTS(SELECT 1 FROM delegated_receipts WHERE packet=p.id AND grant_id=?))
      AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject)
      ORDER BY EXISTS(SELECT 1 FROM signatures WHERE packet=p.id AND role='holder') DESC,p.created DESC,p.id DESC LIMIT 1`,subject,digest,grant).first<Packet & {signature_rows:string}>();
    if(!row)return null;
    const {signature_rows,...packet}=row;return {packet,rows:JSON.parse(signature_rows)};
  }
  async qualification(subject:number,digest:string):Promise<Packet|null> {
    return this.stmt(`SELECT p.* FROM packets p JOIN signatures c ON c.packet=p.id AND c.role='contributor'
      JOIN signatures h ON h.packet=p.id AND h.role='holder' WHERE p.subject=? AND p.digest=?
      AND c.actor=p.subject AND h.actor=(SELECT holder FROM authority WHERE id=1) AND h.actor!=c.actor
      AND NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=p.subject)`,subject,digest).first<Packet>();
  }
}
