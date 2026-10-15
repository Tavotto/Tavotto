import type { Proof } from './types.ts';
import { Rejected, MAX_CONTRIBUTORS } from './types.ts';
import { canonical } from './crypto.ts';
import { validId, validSha } from './policy.ts';
export function parseProof(text:unknown,now:number):Proof {
  if(typeof text!=='string' || text.length>200_000 || !text.startsWith('tavotto-cla-v1\n'))throw new Rejected('proof_invalid');
  let p:Proof;try{p=JSON.parse(text.slice('tavotto-cla-v1\n'.length));}catch{throw new Rejected('proof_invalid');}
  const keys=['protocol','repository_id','head_sha','base_sha','kind','pr','members','contributors','policy_sha256','revision','issued_at','expires_at'];
  if(!p || canonical(Object.keys(p).sort())!==canonical(keys.sort()) || p.protocol!=='tavotto-cla-v1' || p.kind!=='pr' ||
    !validId(p.repository_id) || !validId(p.pr) || !validSha(p.head_sha) || !validSha(p.base_sha) || !/^[0-9a-f]{64}$/.test(p.policy_sha256) ||
    !Number.isSafeInteger(p.revision) || p.revision<0 || !Number.isSafeInteger(p.issued_at) || !Number.isSafeInteger(p.expires_at) ||
    !(p.issued_at<=now && now<p.expires_at && p.expires_at<=p.issued_at+120) ||
    !Array.isArray(p.members) || p.members.length!==0 || !Array.isArray(p.contributors) || p.contributors.length<1 || p.contributors.length>MAX_CONTRIBUTORS ||
    p.contributors.some((u,i)=>!u || canonical(Object.keys(u).sort())!==canonical(['id','login']) || !validId(u.id) ||
      typeof u.login!=='string' || !/^[a-z0-9][a-z0-9-]*(?:\[bot\])?$/.test(u.login) || i>0 && u.id<=p.contributors[i-1]!.id) ||
    text!=='tavotto-cla-v1\n'+canonical(p))throw new Rejected('proof_invalid');
  return p;
}
