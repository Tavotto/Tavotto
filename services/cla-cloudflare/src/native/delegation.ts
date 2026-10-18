// No key generation, approval UI, personal-signature substitution or network I/O.
import {bytes,canonical,sha256,unbase64,base64url,seal,open} from '../core/crypto.ts';
import {Rejected} from '../core/types.ts';
import type {Packet} from '../core/types.ts';
import type {LiveService} from './service.ts';
import type {SignatureRow} from './store.ts';
import {DB_NOW} from './store.ts';

export interface SignedAuthorization {body:string;signature:string}
export interface Grant {
  protocol:'cla-holder-authorization-v1';id:string;holder_id:number;holder_name:string;
  repository_id:number;repository:string;agreement_kind:'individual';agreement_version:string;agreement_sha256:string;
  purpose:'countersign-after-personal-contributor-signature';scope:'adult-independent-own-rights';
  approved_at:number;not_before:number;expires_at:number;key_sha256:string;
}
export interface Control {
  protocol:'cla-holder-control-v1';authorization_id:string;grant_sha256:string;
  sequence:number;previous_sha256:string;action:'enable'|'suspend'|'revoke';issued_at:number;
}
interface GrantRow {id:string;body:string;signature:string;hash:string;public_key:string}
interface ControlRow {grant_id:string;sequence:number;body:string;signature:string;hash:string;action:string;issued_at:number}
interface ReceiptRow {grant_id:string;control_hash:string;contributor_hash:string;cipher:string;hash:string;created:number}
const exact=(o:object,keys:string[])=>canonical(Object.keys(o).sort())===canonical(keys.sort());
const integer=(n:number)=>Number.isSafeInteger(n) && n>=0;

export class Delegation {
  readonly s:LiveService;private readonly key:CryptoKey;
  constructor(s:LiveService,key:CryptoKey){this.s=s;this.key=key;}
  private async signed<T extends object>(record:SignedAuthorization,keys:string[],encoded=this.s.c.holderAuthorizationKey):Promise<T> {
    if(!encoded || !/^[A-Za-z0-9_-]{43}$/.test(encoded) || !record || typeof record.body!=='string' || record.body.length>8192 ||
      !/^[A-Za-z0-9_-]{86}$/.test(record.signature))throw new Rejected('authorization_signature_required');
    let data:T;
    try {
      data=JSON.parse(record.body);
      if(!data || !exact(data,keys) || canonical(data)!==record.body)throw new Error();
      const raw=unbase64(encoded),sig=unbase64(record.signature);
      if(base64url(raw)!==encoded || base64url(sig)!==record.signature)throw new Error();
      const key=await crypto.subtle.importKey('raw',raw,'Ed25519',false,['verify']);
      if(!await crypto.subtle.verify('Ed25519',key,sig,bytes(record.body)))throw new Error();
    }catch{throw new Rejected('authorization_signature_invalid');}
    return data;
  }
  private async grant(record:SignedAuthorization,now:number,archive?:{key:string;packet:Packet}):Promise<Grant> {
    const g=await this.signed<Grant>(record,['protocol','id','holder_id','holder_name','repository_id','repository','agreement_kind','agreement_version',
      'agreement_sha256','purpose','scope','approved_at','not_before','expires_at','key_sha256'],archive?.key);
    const c=this.s.c,a=archive?{version:archive.packet.version,sha256:archive.packet.digest}:c.policy.agreements.individual;
    if(g.protocol!=='cla-holder-authorization-v1' || !/^[A-Za-z0-9_-]{43}$/.test(g.id) || !archive && g.id!==c.holderAuthorizationId || g.holder_id!==c.holderId || g.holder_name!==c.holderName ||
      g.repository_id!==c.repositoryId || g.repository!==c.repository || g.agreement_kind!=='individual' || g.agreement_version!==a.version || g.agreement_sha256!==a.sha256 ||
      g.purpose!=='countersign-after-personal-contributor-signature' || g.scope!=='adult-independent-own-rights' ||
      ![g.approved_at,g.not_before,g.expires_at].every(integer) || g.approved_at>now || g.approved_at>g.not_before || g.expires_at<=g.not_before ||
      g.key_sha256!==await sha256(unbase64(archive?.key ?? c.holderAuthorizationKey!)))throw new Rejected('authorization_scope_invalid');
    return g;
  }
  private async control(record:SignedAuthorization,g:Grant,hash:string,now:number,key?:string):Promise<Control> {
    const e=await this.signed<Control>(record,['protocol','authorization_id','grant_sha256','sequence','previous_sha256','action','issued_at'],key);
    if(e.protocol!=='cla-holder-control-v1' || e.authorization_id!==g.id || e.grant_sha256!==hash || !integer(e.sequence) || e.sequence<1 || e.sequence>100 ||
      !/^[0-9a-f]{64}$/.test(e.previous_sha256) || !['enable','suspend','revoke'].includes(e.action) ||
      !integer(e.issued_at) || e.issued_at<g.approved_at || e.issued_at>now)throw new Rejected('authorization_control_invalid');
    return e;
  }
  private async rows(id=this.s.c.holderAuthorizationId):Promise<{g:GrantRow|null;events:ControlRow[];e:ControlRow|null}> {
    const row=await this.s.store.stmt(`SELECT id,substr(body,1,8193) body,substr(signature,1,87) signature,hash,substr(public_key,1,44) public_key,
      (SELECT json_group_array(json_object('grant_id',grant_id,'sequence',sequence,'body',body,'signature',signature,'hash',hash,'action',action,'issued_at',issued_at))
       FROM (SELECT grant_id,sequence,substr(body,1,8193) body,substr(signature,1,87) signature,hash,action,issued_at
         FROM delegated_controls WHERE grant_id=g.id ORDER BY sequence LIMIT 101)) controls
      FROM delegated_grants g WHERE id=?`,id).first<GrantRow & {controls:string}>();
    const g=row ?? null,events:ControlRow[]=row?JSON.parse(row.controls):[];
    if(events.length>100)throw new Rejected('authorization_history_limit');
    return {g,events,e:events.at(-1) ?? null};
  }
  private async history(g:GrantRow,events:ControlRow[],now:number,packet?:Packet):Promise<{grant:Grant;event:Control|null}> {
    if(!packet && g.public_key!==this.s.c.holderAuthorizationKey)throw new Rejected('authorization_binding_invalid');
    const grant=await this.grant(g,now,packet?{key:g.public_key,packet}:undefined);let previous=g.hash,event:Control|null=null;
    if(g.id!==grant.id || g.hash!==await sha256(g.body))throw new Rejected('authorization_binding_invalid');
    for(const [index,row] of events.entries()) {
      const next=await this.control(row,grant,g.hash,now,packet?g.public_key:undefined);
      if(row.grant_id!==grant.id || next.sequence!==index+1 || next.previous_sha256!==previous || row.hash!==await sha256(row.body) ||
        row.sequence!==next.sequence || row.action!==next.action || row.issued_at!==next.issued_at || event?.action==='revoke' ||
        event && next.issued_at<event.issued_at)throw new Rejected('authorization_control_order');
      event=next;previous=row.hash;
    }
    return {grant,event};
  }
  // Internal preparation API only; no HTTP route accepts grants or controls.
  async register(record:SignedAuthorization):Promise<void> {
    await this.s.store.assertReady();const now=await this.s.store.databaseNow(),g=await this.grant(record,now),hash=await sha256(record.body),revision=await this.s.store.revision();
    await this.s.store.batch([...this.s.store.cas(revision),this.s.store.stmt('INSERT INTO delegated_grants VALUES(?,?,?,?,?)',g.id,record.body,record.signature,hash,this.s.c.holderAuthorizationKey)]);
  }
  async append(record:SignedAuthorization):Promise<void> {
    await this.s.store.assertReady();const now=await this.s.store.databaseNow(),revision=await this.s.store.revision(),{g,e,events}=await this.rows();
    if(!g)throw new Rejected('authorization_missing');
    const {grant}=await this.history(g,events,now),event=await this.control(record,grant,g.hash,now);
    if(g.hash!==await sha256(g.body) || event.sequence!==(e?.sequence ?? 0)+1 || event.previous_sha256!==(e?.hash ?? g.hash) ||
      e?.action==='revoke' || e && event.issued_at<e.issued_at)throw new Rejected('authorization_control_order');
    // Recheck the predecessor and database clock at the actual commit, not only before encryption/signature verification.
    await this.s.store.batch([...this.s.store.cas(revision),...this.s.store.guard(`${DB_NOW}>=? AND
      coalesce((SELECT hash FROM delegated_controls WHERE grant_id=? ORDER BY sequence DESC LIMIT 1),?)=?`,event.issued_at,g.id,g.hash,event.previous_sha256),
      this.s.store.stmt('INSERT INTO delegated_controls VALUES(?,?,?,?,?,?,?)',g.id,event.sequence,record.body,record.signature,await sha256(record.body),event.action,event.issued_at)]);
  }
  private async active(now:number):Promise<{g:Grant;e:Control;grantHash:string;controlHash:string}> {
    if(this.s.c.delegatedCountersigningEnabled!==true || !this.s.c.signingEnabled)throw new Rejected('delegated_countersigning_disabled');
    const {g,e,events}=await this.rows();if(!g || !e)throw new Rejected('authorization_not_enabled');
    const {grant,event}=await this.history(g,events,now);if(!event)throw new Rejected('authorization_not_enabled');
    if(g.hash!==await sha256(g.body) || e.hash!==await sha256(e.body) || e.sequence!==event.sequence || e.action!==event.action || e.issued_at!==event.issued_at ||
      grant.not_before>now || now>=grant.expires_at || event.action!=='enable')throw new Rejected('authorization_not_active');
    return {g:grant,e:event,grantHash:g.hash,controlHash:e.hash};
  }
  async countersign(p:Packet,contributor:SignatureRow):Promise<number> {
    this.s.signing();const now=await this.s.store.databaseNow(),revision=await this.s.store.revision(),a=await this.active(now);
    if(p.subject===a.g.holder_id || p.kind!=='individual' || p.version!==a.g.agreement_version || p.digest!==a.g.agreement_sha256 ||
      contributor.role!=='contributor' || contributor.actor!==p.subject || now<contributor.created || now>=p.valid_until)throw new Rejected('delegated_packet_ineligible');
    await this.s.stagedVerifyEvidence(p,contributor);
    const record={protocol:'cla-delegated-countersignature-v1',packet:p.id,holder_id:a.g.holder_id,system:'tavotto-cla',
      agreement_version:p.version,agreement_sha256:p.digest,authorization_id:a.g.id,authorization_sha256:a.grantHash,
      control_sha256:a.controlHash,contributor_evidence_sha256:contributor.evidence_hash,executed_at:now};
    const existing=await this.s.store.stmt('SELECT 1 FROM delegated_receipts WHERE packet=? AND grant_id=? AND control_hash=?',p.id,a.g.id,a.controlHash).first();
    if(existing){
      await this.qualified(p,contributor,now);
      await this.s.store.batch(this.s.store.guard(`(SELECT revision FROM state WHERE id=1)=? AND ${DB_NOW}<? AND ${DB_NOW}<? AND
        EXISTS(SELECT 1 FROM packets WHERE id=? AND subject=? AND digest=? AND created=? AND valid_until=? AND valid_until>${DB_NOW}) AND
        NOT EXISTS(SELECT 1 FROM signatures WHERE packet=? AND role='holder') AND
        NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=?) AND
        (SELECT hash FROM delegated_controls WHERE grant_id=? ORDER BY sequence DESC LIMIT 1)=?`,revision,p.valid_until,a.g.expires_at,p.id,p.subject,p.digest,p.created,p.valid_until,p.id,p.subject,a.g.id,a.controlHash));
      return revision;
    }
    const raw=canonical(record),hash=await sha256(raw),cipher=await seal(this.key,raw,`delegated:${p.id}:${a.controlHash}`);
    await this.s.store.batch([...this.s.store.cas(revision),...this.s.store.guard(`?<=${DB_NOW} AND ${DB_NOW}<? AND ${DB_NOW}<? AND
      EXISTS(SELECT 1 FROM packets WHERE id=? AND subject=? AND kind='individual' AND version=? AND digest=? AND created=? AND valid_until=? AND valid_until>${DB_NOW}) AND
      EXISTS(SELECT 1 FROM signatures WHERE packet=? AND role='contributor' AND actor=? AND evidence_hash=?) AND
      NOT EXISTS(SELECT 1 FROM signatures WHERE packet=? AND role='holder') AND
      NOT EXISTS(SELECT 1 FROM eligibility_blocks b JOIN packets old ON old.id=b.packet WHERE old.subject=?) AND
      (SELECT hash FROM delegated_controls WHERE grant_id=? ORDER BY sequence DESC LIMIT 1)=?`,
      now,p.valid_until,a.g.expires_at,p.id,p.subject,p.version,p.digest,p.created,p.valid_until,p.id,p.subject,contributor.evidence_hash,p.id,p.subject,a.g.id,a.controlHash),
      this.s.store.stmt('INSERT INTO delegated_receipts VALUES(?,?,?,?,?,?,?,?)',p.id,a.g.id,a.controlHash,contributor.evidence_hash,cipher,hash,now,a.g.expires_at)]);
    return revision+1;
  }
  // Returns an expiry ceiling, never a replacement personal signature.
  async qualified(p:Packet,contributor:SignatureRow,now:number):Promise<number> {
    const a=await this.active(now),r=await this.s.store.stmt(`SELECT * FROM delegated_receipts WHERE packet=? AND grant_id=? AND control_hash=?`,p.id,a.g.id,a.controlHash).first<ReceiptRow>();
    await this.receipt(p,contributor,now,a,r);return a.g.expires_at;
  }
  private async receipt(p:Packet,contributor:SignatureRow,now:number,a:{g:Grant;e:Control;grantHash:string;controlHash:string},r:ReceiptRow|null):Promise<Record<string,unknown>> {
    if(!r || r.contributor_hash!==contributor.evidence_hash || !integer(r.created) || r.created<contributor.created || r.created<p.created || r.created>=p.valid_until ||
      r.created<a.g.not_before || r.created>=a.g.expires_at || r.created<a.e.issued_at || r.created>now)throw new Rejected('delegated_receipt_unavailable');
    const raw=await open(this.key,r.cipher,`delegated:${p.id}:${a.controlHash}`);
    const expected={protocol:'cla-delegated-countersignature-v1',packet:p.id,holder_id:a.g.holder_id,system:'tavotto-cla',agreement_version:p.version,
      agreement_sha256:p.digest,authorization_id:a.g.id,authorization_sha256:a.grantHash,control_sha256:a.controlHash,
      contributor_evidence_sha256:contributor.evidence_hash,executed_at:r.created};
    if(await sha256(raw)!==r.hash || raw!==canonical(expected))throw new Rejected('delegated_receipt_invalid');
    return expected;
  }
  async audit(p:Packet,contributor:SignatureRow,blocked:boolean):Promise<null|{current:boolean;state:string;executed_at:number;expires_at:number;receipt:Record<string,unknown>;receipt_sha256:string;authorization:SignedAuthorization & {public_key:string;identity_assurance:string};controls:SignedAuthorization[];observed_at:number}> {
    const r=await this.s.store.stmt(`SELECT r.* FROM delegated_receipts r JOIN delegated_controls e ON e.hash=r.control_hash
      WHERE r.packet=? ORDER BY r.created DESC,e.sequence DESC,r.control_hash DESC LIMIT 1`,p.id).first<ReceiptRow>();
    if(!r)return null;
    const now=await this.s.store.databaseNow(),{g,e,events}=await this.rows(r.grant_id);
    if(!g || !e)throw new Rejected('authorization_missing');
    const {grant}=await this.history(g,events,now,p),atExecution=events.find(event=>event.hash===r.control_hash);
    if(!atExecution || atExecution.action!=='enable')throw new Rejected('delegated_receipt_invalid');
    const control=await this.control(atExecution,grant,g.hash,now,g.public_key);
    await this.s.stagedVerifyEvidence(p,contributor);
    const receipt=await this.receipt(p,contributor,now,{g:grant,e:control,grantHash:g.hash,controlHash:r.control_hash},r);
    // Historical cryptographic validity is distinct from current technical eligibility.
    const state=blocked?'blocked':this.s.c.delegatedCountersigningEnabled!==true || !this.s.c.signingEnabled?'disabled':
      grant.id!==this.s.c.holderAuthorizationId || g.public_key!==this.s.c.holderAuthorizationKey ||
      p.version!==this.s.c.policy.agreements.individual.version || p.digest!==this.s.c.policy.agreements.individual.sha256?'superseded':
      now>=grant.expires_at?'expired':now<grant.not_before?'not_started':e.action==='revoke'?'revoked':e.action==='suspend'?'suspended':
      e.hash!==r.control_hash?'superseded':'current';
    return {current:state==='current',state,executed_at:r.created,expires_at:grant.expires_at,receipt,receipt_sha256:r.hash,
      authorization:{body:g.body,signature:g.signature,public_key:g.public_key,identity_assurance:'not_determined_by_this_export'},
      controls:events.map(event=>({body:event.body,signature:event.signature})),observed_at:now};
  }
}
