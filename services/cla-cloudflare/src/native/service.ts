import type { Binding, CheckProposal, Packet, Role, Session } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { base64url, bytes, canonical, open, randomToken, seal, sha256 } from '../core/crypto.ts';
import { binding, sameBinding } from '../core/identity.ts';
import { currentPacket, packetAgreementMatches, validId } from '../core/policy.ts';
import type { LiveConfig } from './config.ts';
import { attestations, validateLive } from './config.ts';
import { GitHub } from './github.ts';
import { LiveStore, type SignatureRow } from './store.ts';
import { Delegation } from './delegation.ts';
import { qualificationSQL, successCapacity } from './budget.ts';
import { mergePrefixBinding } from './merge-prefix.ts';
import type { MergePrefixAssessment, MergePrefixReader, MergePrefixTarget } from './merge-prefix.ts';

const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export interface EvidenceInput { name:string; address:string; signature:string; attestations:Record<string,boolean> }
export class LiveService {
  readonly c:LiveConfig; readonly store:LiveStore; readonly github:GitHub; private readonly key:CryptoKey;
  readonly delegation:Delegation;
  constructor(c:LiveConfig,store:LiveStore,github:GitHub,key:CryptoKey) {
    validateLive(c); this.c=c; this.store=store; this.github=github; this.key=key;
    this.delegation=new Delegation(this,key);
  }
  sameOrigin(origin:string|null):void { if (origin !== this.c.origin) throw new Rejected('origin_rejected'); }
  async session(token:string):Promise<Session> {
    if (!TOKEN.test(token)) throw new Rejected('session_rejected');
    const s = await this.store.session(await sha256(token),await this.store.databaseNow());
    if (!s) throw new Rejected('session_rejected'); return s;
  }
  async authorize(token:string,csrf:string,origin:string|null):Promise<Session> {
    this.sameOrigin(origin); const s = await this.session(token);
    if (!TOKEN.test(csrf) || await sha256(csrf) !== s.csrf_hash) throw new Rejected('csrf_rejected'); return s;
  }
  signing():void { if (!this.c.signingEnabled) throw new Rejected('signing_disabled'); }
  async startOAuth(origin:string|null):Promise<{browser:string;url:string}> {
    this.sameOrigin(origin); if (!this.c.oauthEnabled) throw new Rejected('oauth_disabled');
    await this.store.assertReady();
    const state=randomToken(),browser=randomToken(),verifier=randomToken(),hash=await sha256(state);
    await this.store.addOAuth(hash,await sha256(browser),await seal(this.key,verifier,'oauth:'+hash),(await this.store.databaseNow())+300);
    return {browser,url:this.github.authorizeURL(state,base64url(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes(verifier)))))};
  }
  async completeOAuth(state:string,browser:string,code:string):Promise<{token:string;csrf:string}> {
    if (!this.c.oauthEnabled || !TOKEN.test(state) || !TOKEN.test(browser) || !/^[A-Za-z0-9_.-]{1,256}$/.test(code)) throw new Rejected('oauth_rejected');
    await this.store.assertReady();
    const hash=await sha256(state),cipher=await this.store.consumeOAuth(hash,await sha256(browser),0);
    const user=await this.github.oauthIdentity(code,await open(this.key,cipher,'oauth:'+hash));
    const token=randomToken(),csrf=randomToken();
    await this.store.addSession({token_hash:await sha256(token),actor:user.id,login:user.login,csrf_hash:await sha256(csrf),
      expires:(await this.store.databaseNow())+900}); return {token,csrf};
  }
  async packetFor(s:Session,id:string):Promise<Packet> {
    if (!TOKEN.test(id)) throw new Rejected('packet_unavailable'); const p=await this.store.packet(id);
    if (!p || ![p.subject,this.c.holderId].includes(s.actor)) throw new Rejected('packet_unavailable'); return p;
  }
  async packet(token:string,csrf:string,origin:string|null,kind:unknown):Promise<Packet> {
    this.signing(); const s=await this.authorize(token,csrf,origin);
    if (kind !== 'individual') throw new Rejected('corporate_manual_review_required');
    if (s.actor===this.c.holderId) throw new Rejected('distinct_parties_required');
    await this.store.assertReady();
    const a=this.c.policy.agreements.individual,now=await this.store.databaseNow();
    if(await this.store.subjectBlocked(s.actor))throw new Rejected('packet_ineligible');
    const existing=await this.store.reusablePacket(s.actor,a.sha256);
    if(existing) {
      const d=await this.store.document(existing.digest);
      if(!packetAgreementMatches(this.c,existing) || !d || d.kind!==existing.kind || d.version!==existing.version ||
        await sha256(d.content)!==existing.digest || await this.store.subjectBlocked(s.actor))throw new Rejected('packet_ineligible');
      // Return metadata only. This does not create an intent, sign, or establish fresh eligibility.
      return existing;
    }
    await this.store.saveDocument(await this.github.agreement('individual'));
    const p=await this.store.createAuthenticatedPacket(s,{id:randomToken(),subject:s.actor,kind,version:a.version,digest:a.sha256,
      created:now,valid_until:now+7*86400});
    if (!packetAgreementMatches(this.c,p) || (!currentPacket(this.c,p,now) && !await this.store.completed(p.id)) || await this.store.subjectBlocked(p.subject)) throw new Rejected('packet_ineligible'); return p;
  }
  async intent(token:string,csrf:string,origin:string|null,packet:string,role:unknown):Promise<string> {
    this.signing(); const s=await this.authorize(token,csrf,origin),p=await this.packetFor(s,packet),now=await this.store.databaseNow();
    if (!['contributor','holder','hold'].includes(role as string) || (role!=='hold' && !currentPacket(this.c,p,now))) throw new Rejected('role_rejected');
    await this.store.assertReady();
    const nonce=randomToken(); await this.store.addAuthenticatedIntent(await sha256(nonce),s,p,role as Role|'hold',now+300); return nonce;
  }
  private validateEvidence(input:EvidenceInput,role:Role):void {
    if (!input || canonical(Object.keys(input).sort()) !== canonical(['address','attestations','name','signature']) ||
        [input.name,input.address,input.signature].some(v=>typeof v!=='string' || !v.trim() || v.length>1000) ||
        input.signature!==input.name || role==='holder' && input.name!==this.c.holderName ||
        !input.attestations || canonical(Object.keys(input.attestations).sort()) !== canonical(attestations(role).sort()) ||
        attestations(role).some(k=>input.attestations[k]!==true)) throw new Rejected('personal_evidence_required');
  }
  async sign(token:string,csrf:string,origin:string|null,packet:string,nonce:string,role:unknown,input:EvidenceInput):Promise<{revision:number;countersignature?:'recorded'|'pending_manual_review'}> {
    this.signing(); if (!TOKEN.test(nonce) || !['contributor','holder'].includes(role as string)) throw new Rejected('intent_rejected');
    const requested=role as Role,s=await this.authorize(token,csrf,origin),p=await this.packetFor(s,packet);
    const now=await this.store.databaseNow(); if (!currentPacket(this.c,p,now)) throw new Rejected('packet_ineligible');
    const d=await this.store.document(p.digest);
    if(!d || d.kind!==p.kind || d.version!==p.version || await sha256(d.content)!==p.digest)throw new Rejected('agreement_unavailable');
    this.validateEvidence(input,requested); const revision=await this.store.revision();
    const record={...input,protocol:'cla-signature-v1',packet:p.id,agreement_kind:p.kind,agreement_version:p.version,
      agreement_sha256:p.digest,role:requested,github_id:s.actor,github_login_at_signing:s.login,signed_at_unix:now,consent_ui_version:'1'};
    const raw=canonical(record),cipher=await seal(this.key,raw,`signature:${p.id}:${requested}:${p.digest}`);
    await this.store.assertReady();
    const committed=await this.store.signAtomic({session:s,packet:p,intentHash:await sha256(nonce),role:requested,cipher,
      evidenceHash:await sha256(raw),created:now,revision});
    if(requested==='contributor' && this.c.delegatedCountersigningEnabled===true) {
      // Contributor consent is already committed. A failed delegated operation never replays or fabricates it.
      try {
        const contributor=(await this.store.signatures(p.id)).find(row=>row.role==='contributor')!;
        return {revision:await this.delegation.countersign(p,contributor),countersignature:'recorded'};
      }catch{return {revision:committed,countersignature:'pending_manual_review'};}
    }
    return {revision:committed};
  }
  async hold(token:string,csrf:string,origin:string|null,packet:string,nonce:string):Promise<{revision:number}> {
    this.signing(); if (!TOKEN.test(nonce)) throw new Rejected('intent_rejected');
    const s=await this.authorize(token,csrf,origin),p=await this.packetFor(s,packet);
    await this.store.assertReady();
    return {revision:await this.store.holdAtomic(s,p,await sha256(nonce),await this.store.revision(),await this.store.databaseNow())};
  }
  private async evidenceRecord(p:Packet,row:SignatureRow):Promise<Record<string,unknown>> {
    // Completion can qualify after the deadline, but both signatures must have
    // actually been recorded within that packet's original signing interval.
    if(!Number.isSafeInteger(row.created) || row.created<p.created || row.created>=p.valid_until)
      throw new Rejected('evidence_signing_time_invalid');
    const raw=await open(this.key,row.evidence_cipher,`signature:${p.id}:${row.role}:${p.digest}`);
    if (await sha256(raw)!==row.evidence_hash) throw new Rejected('evidence_unavailable');
    let data:Record<string,unknown>; try { data=JSON.parse(raw); } catch { throw new Rejected('evidence_unavailable'); }
    const fields={protocol:'cla-signature-v1',packet:p.id,agreement_kind:p.kind,agreement_version:p.version,
      agreement_sha256:p.digest,role:row.role,github_id:row.actor,signed_at_unix:row.created,consent_ui_version:'1'};
    if (Object.entries(fields).some(([k,v])=>data?.[k]!==v) || typeof data.github_login_at_signing!=='string' ||
        !/^[A-Za-z0-9][A-Za-z0-9-]*$/.test(data.github_login_at_signing)) throw new Rejected('evidence_binding_mismatch');
    this.validateEvidence({name:data.name as string,address:data.address as string,signature:data.signature as string,
      attestations:data.attestations as Record<string,boolean>},row.role); return data;
  }
  // Evidence plaintext remains local; checkpoints retain only the verified row identity.
  async stagedVerifyEvidence(p:Packet,row:SignatureRow):Promise<void> {await this.evidenceRecord(p,row);}
  async readEvidence(token:string,packet:string,role:unknown):Promise<unknown> {
    const s=await this.session(token),p=await this.packetFor(s,packet);
    if (!['contributor','holder'].includes(role as string)) throw new Rejected('role_rejected');
    const row=(await this.store.signatures(p.id)).find(x=>x.role===role);
    if (!row) throw new Rejected('evidence_unavailable'); return this.evidenceRecord(p,row);
  }
  async audit(token:string,id:string):Promise<unknown> {
    const p=await this.packetFor(await this.session(token),id),revision=await this.store.revision(),d=await this.store.document(p.digest);
    if(!d || d.kind!==p.kind || d.version!==p.version || await sha256(d.content)!==p.digest)throw new Rejected('agreement_unavailable');
    const rows=await this.store.signatures(p.id),personal=[];
    for(const row of rows)personal.push(await this.evidenceRecord(p,row));
    const contributor=rows.find(row=>row.role==='contributor' && row.actor===p.subject);
    const delegated=contributor?await this.delegation.audit(p,contributor,await this.store.subjectBlocked(p.subject)):null;
    // Expiry/account changes during the bounded export cannot retain an old authorization to read.
    await this.packetFor(await this.session(token),id);
    if(await this.store.revision()!==revision)throw new Rejected('state_changed');
    if(delegated?.current && await this.store.databaseNow()>=delegated.expires_at){delegated.current=false;delegated.state='expired';}
    return {protocol:'cla-private-audit-v1',packet:p,agreement:d,personal_signatures:personal,delegated,
      legal_effect:'not_determined',github_or_merge_eligibility:'not_asserted'};
  }
  async document(token:string,packet?:string):Promise<unknown> {
    if(!packet)throw new Rejected('public_agreement_asset_required');
    const p=await this.packetFor(await this.session(token),packet),d=await this.store.document(p.digest);
    if(!d || d.version!==p.version || d.kind!==p.kind || await sha256(d.content)!==p.digest)throw new Rejected('agreement_unavailable');return d;
  }
  async status(token:string,id:string):Promise<unknown> {
    const s=await this.session(token),p=await this.packetFor(s,id);
    const revision=await this.store.revision(),rows=await this.store.signatures(p.id),blocked=await this.store.subjectBlocked(p.subject);
    for(const row of rows)await this.evidenceRecord(p,row);
    const personalCompleted=await this.store.completed(p.id),contributor=rows.find(row=>row.role==='contributor' && row.actor===p.subject);
    let delegated={recorded:false,current:false,state:'none',canAudit:false,executedAt:0,expiresAt:0};
    if(contributor)try {
      const audit=await this.delegation.audit(p,contributor,blocked);
      if(audit)delegated={recorded:true,current:audit.current,state:audit.state,canAudit:true,executedAt:audit.executed_at,expiresAt:audit.expires_at};
    }catch(error){if(!(error instanceof Rejected))throw error;delegated={...delegated,recorded:true,state:'unverifiable'};}
    const observed=await this.store.databaseNow();
    if(delegated.current && observed>=delegated.expiresAt)delegated={...delegated,current:false,state:'expired'};
    if(await this.store.revision()!==revision)throw new Rejected('state_changed');
    const completed=personalCompleted || delegated.current,current=packetAgreementMatches(this.c,p) && (completed || currentPacket(this.c,p,observed)),role=s.actor===this.c.holderId?'holder':'contributor';
    return {packet:p,completed,personalCompleted,delegated,signingDeadline:p.valid_until,actor:{id:s.actor,login:s.login,role},revision,now:observed,blocked,current,
      signatures:rows.map(r=>({role:r.role,actor:r.actor,created:r.created})),
      canSign:this.c.signingEnabled && currentPacket(this.c,p,observed) && !personalCompleted && !blocked && !rows.some(r=>r.role===role) && (role==='contributor' || rows.some(r=>r.role==='contributor')),
      canHold:this.c.signingEnabled && !blocked};
  }
  async packets(token:string,cursor?:string):Promise<unknown> {
    const s=await this.session(token);
    // Packet IDs are immutable cursors, not authorization. Reauthorize the
    // anchor and retain the subject predicate on every bounded page.
    const anchor=cursor===undefined?undefined:await this.packetFor(s,cursor);
    const rows=(await this.store.stmt(`SELECT id FROM packets WHERE (subject=? OR ?=?)
      ${anchor?'AND (created<? OR (created=? AND id<?))':''}
      ORDER BY created DESC,id DESC LIMIT 21`,s.actor,s.actor,this.c.holderId,
      ...(anchor?[anchor.created,anchor.created,anchor.id]:[])).all<{id:string}>()).results;
    const packets=rows.slice(0,20);
    return {actor:{id:s.actor,login:s.login,role:s.actor===this.c.holderId?'holder':'contributor'},packets,
      nextCursor:rows.length>20?packets[packets.length-1]!.id:null};
  }
  private async qualified(subject:number,ceiling?:{expires:number},now?:number):Promise<boolean> {
    if(this.c.delegatedCountersigningEnabled===true) {
      const candidate=await this.store.delegatedCandidate(subject,this.c.policy.agreements.individual.sha256,this.c.holderAuthorizationId!);
      if(!candidate || !packetAgreementMatches(this.c,candidate.packet))return false;
      const {packet:p,rows}=candidate,contributor=rows.find(row=>row.role==='contributor' && row.actor===subject),holder=rows.find(row=>row.role==='holder');
      if(!contributor || rows.length>(holder?2:1) || holder && (holder.actor!==this.c.holderId || holder.actor===subject || holder.created<contributor.created))throw new Rejected('evidence_binding_mismatch');
      for(const row of rows)await this.evidenceRecord(p,row);
      if(holder)return true;
      const expires=await this.delegation.qualified(p,contributor,now ?? await this.store.databaseNow());
      if(ceiling)ceiling.expires=Math.min(ceiling.expires,expires);return true;
    }
    const p=await this.store.qualification(subject,this.c.policy.agreements.individual.sha256);
    if (!p || !packetAgreementMatches(this.c,p)) return false;
    const rows=await this.store.signatures(p.id);
    if (rows.length!==2 || !rows.some(r=>r.role==='contributor' && r.actor===p.subject) ||
        !rows.some(r=>r.role==='holder' && r.actor===this.c.holderId && r.actor!==p.subject)) throw new Rejected('evidence_binding_mismatch');
    for (const row of rows) await this.evidenceRecord(p,row);
    if(rows.find(r=>r.role==='holder')!.created<rows.find(r=>r.role==='contributor')!.created) throw new Rejected('evidence_signing_order_invalid');
    return true;
  }
  async freshBinding(pr:number):Promise<Binding> {
    const target={kind:'pr' as const,number:pr}; return binding(this.c,this.github,target,await this.github.snapshot(target));
  }
  async evaluate(pr:number,reservedSQL?:number):Promise<CheckProposal> {
    if (!validId(pr)) throw new Rejected('pr_mismatch');
    const revision=await this.store.revision(),bound=await this.freshBinding(pr),now=await this.store.databaseNow();
    let good=this.c.policy.provider.configured===true,expires=now+120;
    const exempt=new Set(Object.values(this.c.exemptionIds));
    if(bound.contributors.filter(person=>!exempt.has(person.id)).length>successCapacity(this.c))
      throw new Rejected('contributor_capacity_exceeded');
    // Background callers reserve their remaining journal/CAS statements before
    // starting per-person reads. Qualification uses two SQL statements per actor.
    if(reservedSQL!==undefined)this.store.budget?.reserveWorkSQL(
      qualificationSQL(this.c)*bound.contributors.filter(person=>!exempt.has(person.id)).length+1+reservedSQL);
    const ceiling={expires:Number.MAX_SAFE_INTEGER};
    for (const person of bound.contributors) if (!exempt.has(person.id)) {
      if (!await this.qualified(person.id,ceiling,now)) good=false;
    }
    if (await this.store.revision()!==revision) throw new Rejected('state_changed');
    expires=Math.min(expires,ceiling.expires);
    if(expires<=now)throw new Rejected('authorization_expired');
    const proof={...bound,protocol:'tavotto-cla-v1' as const,revision,issued_at:now,expires_at:expires};
    return {name:this.c.checkName,appId:this.c.appId,appSlug:this.c.appSlug,headSha:bound.head_sha,
      conclusion:good?'success':'failure',proof,text:'tavotto-cla-v1\n'+canonical(proof),
      ...(ceiling.expires<Number.MAX_SAFE_INTEGER?{authorityExpires:ceiling.expires}:{})};
  }
  async assessMergePrefix(input:MergePrefixTarget,reservedSQL=0,expectedRevision?:number):Promise<MergePrefixAssessment> {
    if(this.c.mergePrefixAssessmentEnabled!==true)throw new Rejected('merge_prefix_disabled');
    const target=structuredClone(input);
    if(this.c.mergePrefixReadSessionEnabled===true)return this.github.withMergePrefixReads(target,reader=>this.assessMergePrefixFacts(target,reader,reservedSQL,expectedRevision));
    return this.assessMergePrefixFacts(target,{snapshot:()=>this.github.mergePrefixSnapshot(target),resolveLogin:login=>this.github.resolveLogin(login)},reservedSQL,expectedRevision);
  }
  private async assessMergePrefixFacts(target:MergePrefixTarget,reader:MergePrefixReader,reservedSQL:number,expectedRevision?:number):Promise<MergePrefixAssessment> {
    const budget=this.store.budget;
    if(!budget || this.github.budget!==budget)throw new Rejected('invocation_budget_required');
    const revision=expectedRevision ?? await this.store.revision();
    if(!Number.isSafeInteger(revision) || revision<0)throw new Rejected('state_changed');
    const first=await mergePrefixBinding(this.c,reader,target,await reader.snapshot());
    const now=await this.store.databaseNow(),exempt=new Set(Object.values(this.c.exemptionIds));
    const people=first.binding.contributors.filter(person=>!exempt.has(person.id));
    if(people.length>successCapacity(this.c))throw new Rejected('contributor_capacity_exceeded');
    budget.reserveWorkSQL(qualificationSQL(this.c)*people.length+1+reservedSQL);
    let eligible=this.c.policy.provider.configured===true;const ceiling={expires:Number.MAX_SAFE_INTEGER};
    for(const person of people)if(!await this.qualified(person.id,ceiling,now))eligible=false;
    const last=await mergePrefixBinding(this.c,reader,target,await reader.snapshot());
    if(canonical(first)!==canonical(last))throw new Rejected('merge_prefix_changed');
    // Revision and database time belong to one final SQL snapshot. A hold/revoke
    // committed while the closing read waits cannot hide behind an earlier revision.
    const final=await this.store.assessmentState(),expires=Math.min(now+120,ceiling.expires);
    budget.checkWork();
    if(final.revision!==revision || final.now<now || final.now>=expires)throw new Rejected('state_changed');
    return {...first,protocol:'cla-merge-prefix-assessment-v1',eligible,revision,observed_at:final.now,valid_until:expires,publication:'not_attempted'};
  }
  async evaluateTarget(target:import('../core/types.ts').Target,reservedSQL=0,expectedRevision?:number):Promise<CheckProposal> {
    if(target.kind==='pr')return this.evaluate(target.number,reservedSQL);
    const a=await this.assessMergePrefix({headSha:target.headSha,baseSha:target.baseSha,baseRef:'refs/heads/main'},reservedSQL,expectedRevision);
    const proof={...a.binding,protocol:'tavotto-cla-v1' as const,revision:a.revision,issued_at:a.observed_at,expires_at:a.valid_until,
      group:{base_ref:'refs/heads/main' as const,strategy:a.strategy,prefix_sha256:a.prefix_sha256,queue_sha256:a.queue_sha256,trusted_source_sha:a.trusted_source_sha}};
    return {name:this.c.checkName,appId:this.c.appId,appSlug:this.c.appSlug,headSha:target.headSha,conclusion:a.eligible?'success':'failure',proof,
      text:'tavotto-cla-v1\n'+canonical(proof),authorityExpires:a.valid_until};
  }
  async check(pr:number,expectedHead?:string):Promise<{disposition:string;check:CheckProposal}> {
    // Track before evaluation: each later evidence/hold revision enqueues a durable notification intent.
    await this.store.assertReady();
    await this.store.track(this.c.repositoryId,{kind:'pr',number:pr});
    const check=await this.evaluate(pr),fresh=await this.freshBinding(pr);
    if (expectedHead!==undefined && check.headSha!==expectedHead || !sameBinding(check.proof,fresh) || await this.store.revision()!==check.proof.revision ||
        await this.store.databaseNow()>=check.proof.expires_at) throw new Rejected('state_changed');
    if (!this.c.checkPublishingEnabled) return {disposition:'dry_run',check};
    const key=this.store.targetKey(this.c.repositoryId,{kind:'pr',number:pr});
    const attempt=await this.store.beginPublication(key,check);
    // GitHub writes cannot participate in the D1 transaction. No retries are sent here.
    try {
      const receipt=await this.github.publish(check,attempt);
      await this.store.published(key,receipt.id,
        check.headSha,check.conclusion,check.proof.revision,await this.store.databaseNow());
      const after=await this.freshBinding(pr);
      if (!sameBinding(check.proof,after) || await this.store.revision()!==check.proof.revision ||
          await this.store.databaseNow()>=check.proof.expires_at) {
        await this.store.settlePublication(attempt,'reconcile',receipt.id);
        return {disposition:'remote_outcome_requires_reconciliation',check};
      }
      await this.store.settlePublication(attempt,'confirmed',receipt.id);
      return {disposition:'published_point_in_time',check};
    } catch {
      // If D1 is unavailable, the precommitted issued row still remains unresolved.
      try { await this.store.settlePublication(attempt,'unknown'); } catch {}
      return {disposition:'remote_outcome_unknown',check};
    }
  }
}
