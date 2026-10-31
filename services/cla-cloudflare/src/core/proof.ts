import type { Proof } from './types.ts';
import { Rejected, MAX_CONTRIBUTORS } from './types.ts';
import { canonical } from './crypto.ts';
import { validId, validSha } from './policy.ts';
export function parseProof(text:unknown,now:number):Proof {
  if(typeof text!=='string' || text.length>200_000 || !text.startsWith('tavotto-cla-v1\n'))throw new Rejected('proof_invalid');
  let p:Proof;try{p=JSON.parse(text.slice('tavotto-cla-v1\n'.length));}catch{throw new Rejected('proof_invalid');}
  const group=p?.kind==='merge_group';
  const keys=['protocol','repository_id','head_sha','base_sha','kind',...(group?['group']:['pr']),'members','contributors','policy_sha256','revision','issued_at','expires_at'];
  if(!p || canonical(Object.keys(p).sort())!==canonical(keys.sort()) || p.protocol!=='tavotto-cla-v1' || (!group && p.kind!=='pr') ||
    !validId(p.repository_id) || (!group && !validId(p.pr)) || !validSha(p.head_sha) || !validSha(p.base_sha) || !/^[0-9a-f]{64}$/.test(p.policy_sha256) ||
    !Number.isSafeInteger(p.revision) || p.revision<0 || !Number.isSafeInteger(p.issued_at) || !Number.isSafeInteger(p.expires_at) ||
    !(p.issued_at<=now && now<p.expires_at && p.expires_at<=p.issued_at+120) ||
    !Array.isArray(p.members) || (group ? p.members.length<1 || p.members.length>100 || p.members.some((m,i)=>!m || canonical(Object.keys(m).sort())!==canonical(['head_sha','pr']) || !validId(m.pr) || !validSha(m.head_sha) || i>0 && m.pr<=p.members[i-1]!.pr) : p.members.length!==0) || !Array.isArray(p.contributors) || p.contributors.length<1 || p.contributors.length>MAX_CONTRIBUTORS ||
    p.contributors.some((u,i)=>!u || canonical(Object.keys(u).sort())!==canonical(['id','login']) || !validId(u.id) ||
      typeof u.login!=='string' || !/^[a-z0-9][a-z0-9-]*(?:\[bot\])?$/.test(u.login) || i>0 && u.id<=p.contributors[i-1]!.id) ||
    text!=='tavotto-cla-v1\n'+canonical(p))throw new Rejected('proof_invalid');
  if(group && (!p.group || canonical(Object.keys(p.group).sort())!==canonical(['base_ref','prefix_sha256','queue_sha256','strategy','trusted_source_sha']) ||
    p.group.base_ref!=='refs/heads/main' || p.group.strategy!=='queue-prefix-v1' || !validSha(p.group.trusted_source_sha) ||
    !/^[0-9a-f]{64}$/.test(p.group.prefix_sha256) || !/^[0-9a-f]{64}$/.test(p.group.queue_sha256)))throw new Rejected('proof_invalid');
  return p;
}
