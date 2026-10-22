import type {LiveConfig} from './config.ts';
import {canonical,sha256} from '../core/crypto.ts';import {Rejected} from '../core/types.ts';
import {policyMaterial,stableAgreementVersion,validId,validSha} from '../core/policy.ts';
export const NO_ACTIVATION='0'.repeat(64);
export interface ActivationRecord {
  generation:number;hash:string;previous_hash:string;activation_revision:number;repository_id:number;holder_id:number;reference_sha:string;
  policy_json:string;individual_version:string;individual_digest:string;corporate_version:string;corporate_digest:string;
  observation_json:string;body:string;created:number;
}
export const activationBody=(r:Omit<ActivationRecord,'hash'|'body'|'created'>)=>canonical({protocol:'cla-agreement-activation-v1',
  generation:r.generation,previous_hash:r.previous_hash,activation_revision:r.activation_revision,repository_id:r.repository_id,holder_id:r.holder_id,
  reference_sha:r.reference_sha,policy_material:r.policy_json,individual:{version:r.individual_version,digest:r.individual_digest},
  corporate:{version:r.corporate_version,digest:r.corporate_digest},document_source_pins:r.observation_json});
export async function verifyActivationRecord(r:ActivationRecord,c?:LiveConfig):Promise<void> {
  const hex=(v:unknown)=>typeof v==='string' && /^[0-9a-f]{64}$/.test(v);
  if(!r || !Number.isSafeInteger(r.generation) || r.generation<1 || r.generation>1000 || !Number.isSafeInteger(r.activation_revision) || r.activation_revision<1 ||
    !validId(r.repository_id) || !validId(r.holder_id) || !validSha(r.reference_sha) || !hex(r.hash) || !hex(r.previous_hash) ||
    !stableAgreementVersion(r.individual_version) || !stableAgreementVersion(r.corporate_version) || !hex(r.individual_digest) || !hex(r.corporate_digest) ||
    typeof r.policy_json!=='string' || r.policy_json.length>65536 || typeof r.observation_json!=='string' || r.observation_json.length>512 ||
    typeof r.body!=='string' || r.body.length>100000 || !Number.isSafeInteger(r.created) || r.created<0)throw new Rejected('activation_record_invalid');
  try {
    const sources=JSON.parse(r.observation_json),policy=JSON.parse(r.policy_json);
    if(canonical(sources)!==r.observation_json || Object.keys(sources).sort().join(',')!=='corporate,individual' || !Object.values(sources).every(validSha) ||
      canonical(policy)!==r.policy_json || r.body!==activationBody(r) || await sha256(r.body)!==r.hash)throw new Error();
  }catch{throw new Rejected('activation_record_invalid');}
  if(c && (c.agreementActivationEnabled!==true || c.agreementActivationHash!==r.hash || c.repositoryId!==r.repository_id || c.holderId!==r.holder_id ||
    c.referenceSha!==r.reference_sha || policyMaterial(c.policy)!==r.policy_json ||
    c.policy.agreements.individual.version!==r.individual_version || c.policy.agreements.individual.sha256!==r.individual_digest ||
    c.policy.agreements.corporate.version!==r.corporate_version || c.policy.agreements.corporate.sha256!==r.corporate_digest))throw new Rejected('activation_configuration_mismatch');
}
// One existing SELECT can return all immutable fields for a full hash/body check.
export const activationProjection=`(SELECT json_object('generation',a.generation,'hash',a.hash,'previous_hash',a.previous_hash,
  'activation_revision',a.activation_revision,'repository_id',a.repository_id,'holder_id',a.holder_id,'reference_sha',a.reference_sha,
  'policy_json',a.policy_json,'individual_version',a.individual_version,'individual_digest',a.individual_digest,
  'corporate_version',a.corporate_version,'corporate_digest',a.corporate_digest,'observation_json',a.observation_json,'body',a.body,'created',a.created)
  FROM agreement_activations a ORDER BY generation DESC LIMIT 1)`;
