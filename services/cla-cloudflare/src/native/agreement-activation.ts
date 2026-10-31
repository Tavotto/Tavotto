// Internal database mechanism only. No HTTP/CLI, credentials, signing or deployment.
import {canonical,sha256} from '../core/crypto.ts';import {Rejected} from '../core/types.ts';
import {policyMaterial,validSha} from '../core/policy.ts';import type {LiveConfig} from './config.ts';import {validateLive} from './config.ts';
import type {LiveStore,Document} from './store.ts';import {DB_NOW} from './store.ts';
import {activationBody,activationProjection,NO_ACTIVATION,verifyActivationRecord} from './activation-record.ts';
import type {ActivationRecord} from './activation-record.ts';
export interface ActivationExpected {generation:number;hash:string;revision:number}
export interface PreparedActivation {protocol:'cla-prepared-activation-v1';record:ActivationRecord;documents:Document[]}
export async function prepareAgreementActivation(configInput:LiveConfig,documents:Document[],expectedInput:ActivationExpected):Promise<PreparedActivation> {
  const config=structuredClone(configInput),expected=structuredClone(expectedInput);
  if(config.agreementActivationEnabled!==true)throw new Rejected('agreement_activation_disabled');
  validateLive({...config,agreementActivationHash:NO_ACTIVATION});
  if(!expected || canonical(Object.keys(expected).sort())!==canonical(['generation','hash','revision']) ||
    !Number.isSafeInteger(expected.generation) || expected.generation<0 || expected.generation>=1000 ||
    !/^[0-9a-f]{64}$/.test(expected.hash) || expected.generation===0 && expected.hash!==NO_ACTIVATION ||
    !Number.isSafeInteger(expected.revision) || expected.revision<0 || expected.revision>=Number.MAX_SAFE_INTEGER)throw new Rejected('activation_predecessor_invalid');
  if(!Array.isArray(documents) || documents.length!==2 || documents.filter(d=>d?.kind==='individual').length!==1 || documents.filter(d=>d?.kind==='corporate').length!==1)
    throw new Rejected('activation_documents_required');
  const copy:Document[]=structuredClone(documents).sort((a,b)=>a.kind.localeCompare(b.kind));
  for(const d of copy){
    if(!d || canonical(Object.keys(d).sort())!==canonical(['content','digest','kind','source_sha','version']) ||
      !validSha(d.source_sha) || typeof d.content!=='string' || new TextEncoder().encode(d.content).length>131072 || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(d.content))throw new Rejected('activation_document_invalid');
    const kind=d.kind as 'individual'|'corporate',a=config.policy.agreements[kind];
    const markers=[...d.content.matchAll(/^\*\*CLA_VERSION: ([^\r\n*]+)\*\*\r?$/gm)];
    if(!a || d.version!==a.version || d.digest!==a.sha256 || await sha256(d.content)!==d.digest || markers.length!==1 || markers[0]![1]!==d.version)
      throw new Rejected('activation_document_binding_invalid');
  }
  const individual=config.policy.agreements.individual,corporate=config.policy.agreements.corporate;
  const record:ActivationRecord={generation:expected.generation+1,previous_hash:expected.hash,activation_revision:expected.revision+1,
    repository_id:config.repositoryId,holder_id:config.holderId,reference_sha:config.referenceSha,policy_json:policyMaterial(config.policy),
    individual_version:individual.version,individual_digest:individual.sha256,corporate_version:corporate.version,corporate_digest:corporate.sha256,
    observation_json:canonical(Object.fromEntries(copy.map(d=>[d.kind,d.source_sha]))),created:0,body:'',hash:''};
  record.body=activationBody(record);record.hash=await sha256(record.body);await verifyActivationRecord(record);
  if(config.agreementActivationHash!==undefined && config.agreementActivationHash!==record.hash)throw new Rejected('activation_configuration_mismatch');
  return {protocol:'cla-prepared-activation-v1',record,documents:copy};
}
async function latest(store:LiveStore):Promise<ActivationRecord|null>{
  const row=await store.stmt(`SELECT ${activationProjection} AS activation`).first<{activation:string|null}>();
  if(!row?.activation)return null;const record:ActivationRecord=JSON.parse(row.activation);await verifyActivationRecord(record);return record;
}
function documentGuard(documents:Document[]):{sql:string;values:unknown[]}{
  return {sql:documents.map(()=>`EXISTS(SELECT 1 FROM agreements a JOIN agreement_documents d ON d.digest=a.digest
    WHERE a.kind=? AND a.version=? AND a.digest=? AND d.kind=a.kind AND d.version=a.version AND d.content=?
    AND length(d.source_sha)=40 AND d.source_sha NOT GLOB '*[^0-9a-f]*')`).join(' AND '),
    values:documents.flatMap(d=>[d.kind,d.version,d.digest,d.content])};
}
async function confirmCurrent(store:LiveStore,c:LiveConfig,p:PreparedActivation):Promise<boolean>{
  const current=await latest(store);if(!current || current.hash!==p.record.hash || current.body!==p.record.body)return false;
  await verifyActivationRecord(current,c);const ready=store.readiness(c),docs=documentGuard(p.documents);
  const row=await store.stmt(`SELECT CASE WHEN (${ready.sql}) AND (${docs.sql}) THEN 1 ELSE 0 END AS ok`,...ready.values,...docs.values).first<{ok:number}>();
  return row?.ok===1;
}
export async function applyAgreementActivation(store:LiveStore,configInput:LiveConfig,input:PreparedActivation):Promise<{status:'applied'|'already_active';hash:string}> {
  const c=structuredClone(configInput);
  if(c.agreementActivationEnabled!==true)throw new Rejected('agreement_activation_disabled');
  validateLive(c);
  // Rebuild immutable local values across awaits; a prepared JS object is not authority.
  const p:PreparedActivation=structuredClone(input),r=p?.record;
  if(!r || r.created!==0 || p.protocol!=='cla-prepared-activation-v1')throw new Rejected('activation_prepared_invalid');
  const rebuilt=await prepareAgreementActivation(c,p.documents,{generation:r.generation-1,hash:r.previous_hash,revision:r.activation_revision-1});
  if(canonical(rebuilt)!==canonical(p))throw new Rejected('activation_prepared_invalid');
  const current=await latest(store);
  if(current?.hash===r.hash){if(await confirmCurrent(store,c,p))return {status:'already_active',hash:r.hash};throw new Rejected('activation_current_unverifiable');}
  if((current?.generation ?? 0)!==r.generation-1 || (current?.hash ?? NO_ACTIVATION)!==r.previous_hash ||
    current && (current.repository_id!==r.repository_id || current.holder_id!==r.holder_id))throw new Rejected('activation_predecessor_changed');
  const docs=documentGuard(p.documents);
  try{
    await store.batch([
      ...store.guard(`(SELECT count(*) FROM authority)=1 AND EXISTS(SELECT 1 FROM authority WHERE id=1 AND holder=?)
        AND coalesce((SELECT max(generation) FROM agreement_activations),0)=?
        AND coalesce((SELECT hash FROM agreement_activations ORDER BY generation DESC LIMIT 1),?)=?`,r.holder_id,r.generation-1,NO_ACTIVATION,r.previous_hash),
      ...p.documents.map(d=>store.stmt('INSERT OR IGNORE INTO agreements VALUES(?,?,?)',d.kind,d.version,d.digest)),
      ...p.documents.map(d=>store.stmt('INSERT OR IGNORE INTO agreement_documents VALUES(?,?,?,?,?)',d.digest,d.kind,d.version,d.source_sha,d.content)),
      ...store.cas(r.activation_revision-1),
      // A real third retained agreement disables the old binary's count=2 readiness.
      // Never append a sentinel row or overwrite an old version to force this guard.
      ...store.guard(`(SELECT count(*) FROM agreements)>2 AND (${docs.sql})`,...docs.values),
      store.stmt(`INSERT INTO agreement_activations(generation,hash,previous_hash,activation_revision,repository_id,holder_id,reference_sha,
        policy_json,individual_version,individual_digest,corporate_version,corporate_digest,observation_json,body,created)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,${DB_NOW})`,r.generation,r.hash,r.previous_hash,r.activation_revision,r.repository_id,r.holder_id,r.reference_sha,
        r.policy_json,r.individual_version,r.individual_digest,r.corporate_version,r.corporate_digest,r.observation_json,r.body),
    ]);
  }catch(error){
    // Observe an identical concurrently committed result; never retry writes.
    if(await confirmCurrent(store,c,p))return {status:'already_active',hash:r.hash};throw error;
  }
  return {status:'applied',hash:r.hash};
}
