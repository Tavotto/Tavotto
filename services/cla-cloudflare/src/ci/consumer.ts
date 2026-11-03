import { canonical, sha256, unbase64 } from '../core/crypto.ts';
import { parseProof } from '../core/proof.ts';
import { policyDigest, validId, validSha } from '../core/policy.ts';
import { Rejected } from '../core/types.ts';
import type { Policy, Proof } from '../core/types.ts';
export interface CIWait {enabled:boolean;maxAttempts?:number;intervalMs?:number;timeoutMs?:number}
export interface CIConfig {enabled:boolean;repository:string;repositoryId:number;origin:string;appId:number;appSlug:string;checkName:string;wait?:CIWait}
export interface Locator {repositoryId:number;pr?:number;head:string;kind?:'merge_group';base?:string}
export type Channel='github'|'authority';
export type Read=(url:string,channel:Channel,signal?:AbortSignal)=>Promise<any>;
export interface CIAnchor {source:string;candidate?:string;expiresAt?:number}
export class CIPending extends Rejected {
  reason:'check_missing'|'confirmation_pending';anchor:CIAnchor|undefined;
  constructor(reason:'check_missing'|'confirmation_pending',anchor?:CIAnchor){super('ci_pending');this.reason=reason;this.anchor=anchor;}
}
const keys=(o:any,expected:string[])=>o && canonical(Object.keys(o).sort())===canonical(expected.sort());
function configuration(c:CIConfig):void {
  let origin:URL;try{origin=new URL(c.origin);}catch{throw new Rejected('ci_configuration_invalid');}
  if(c.enabled!==true || typeof c.repository!=='string' || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(c.repository) || !validId(c.repositoryId) || !validId(c.appId) ||
    c.appId===15368 || c.appSlug==='github-actions' || typeof c.appSlug!=='string' || !/^[a-z0-9][a-z0-9-]*$/.test(c.appSlug) || typeof c.checkName!=='string' || !c.checkName.trim() ||
    origin.protocol!=='https:' || origin.origin!==c.origin || origin.username || origin.password || origin.search || origin.hash)
    throw new Rejected('ci_configuration_invalid');
}
// Credentials are confined to GitHub. The authority GET carries only public proof locators.
export function readOnly(c:CIConfig,token:string,transport:(url:string,init:RequestInit)=>Promise<Response>=(u,i)=>fetch(u,i),pending=false):Read {
  return async(url,channel,signal)=>{
    if(!c.enabled)throw new Rejected('ci_provider_disabled');configuration(c);
    const u=new URL(url),repo='/repos/'+c.repository;
    if(u.username || u.password || u.hash || (channel==='github' ? u.origin!=='https://api.github.com' || !(u.pathname===repo || u.pathname.startsWith(repo+'/')) :
      u.origin!==c.origin || !['/verification/pr','/verification/group'].includes(u.pathname)))throw new Rejected('ci_origin_rejected');
    const headers:Record<string,string>={accept:'application/json','cache-control':'no-store'};
    if(channel==='github'){if(!token || /[\r\n]/.test(token))throw new Rejected('ci_token_unavailable');
      headers.authorization='Bearer '+token;headers.accept='application/vnd.github+json';headers['x-github-api-version']='2026-03-10';}
    const timeout=AbortSignal.timeout(10000);
    const requestSignal=signal?AbortSignal.any([signal,timeout]):timeout;
    let r:Response;try{r=await transport(url,{method:'GET',redirect:'manual',cache:'no-store',headers,signal:requestSignal});}catch{throw new Rejected('ci_read_unavailable');}
    if((!r.ok && !(pending && channel==='authority' && r.status===403)) || !/application\/(?:[^;]+\+)?json/i.test(r.headers.get('content-type') ?? ''))throw new Rejected('ci_read_unavailable');
    const reader=r.body?.getReader();if(!reader)throw new Rejected('ci_read_unavailable');const parts:Uint8Array[]=[];let size=0;
    const cancel=()=>{void reader.cancel().catch(()=>{});};requestSignal.addEventListener('abort',cancel,{once:true});
    try{if(requestSignal.aborted)throw new Rejected('ci_read_unavailable');
      for(;;){const item=await reader.read();if(item.done)break;size+=item.value.length;if(size>2_000_000){await reader.cancel();throw new Rejected('ci_response_limit');}parts.push(item.value);}
    }finally{requestSignal.removeEventListener('abort',cancel);reader.releaseLock();}
    const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length;}
    let data;try{data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{throw new Rejected('ci_read_unavailable');}
    if(!r.ok){if(keys(data,['error']) && data.error==='proof_not_confirmed')throw new CIPending('confirmation_pending');throw new Rejected('ci_read_unavailable');}
    return data;
  };
}
export function eventLocator(event:any,eventName:string):Locator {
  if(eventName==='merge_group'){
    const g=event?.merge_group,locator:Locator={repositoryId:event?.repository?.id,kind:'merge_group',head:g?.head_sha,base:g?.base_sha};
    if(event?.action!=='checks_requested' || g?.base_ref!=='refs/heads/main' || !validId(locator.repositoryId) || !validSha(locator.head) || !validSha(locator.base))throw new Rejected('ci_event_rejected');return locator;
  }
  if(eventName!=='pull_request' && eventName!=='pull_request_target')throw new Rejected('ci_event_rejected');
  // All other event fields, paths, scripts, policy/provider overrides and repository URLs are ignored.
  const locator={repositoryId:event?.repository?.id,pr:event?.number,head:event?.pull_request?.head?.sha};
  if(event?.pull_request?.number!==locator.pr || !validId(locator.repositoryId) || !validId(locator.pr) || !validSha(locator.head))throw new Rejected('ci_event_rejected');return locator;
}
export async function verifyCI(c:CIConfig,input:Locator,read:Read,clock=()=>Math.floor(Date.now()/1000),options?:{pending:boolean;anchor?:CIAnchor;observe?:(anchor:CIAnchor)=>void}):Promise<unknown> {
  if(c.enabled!==true)throw new Rejected('ci_provider_disabled');configuration(c);
  const group=input.kind==='merge_group';
  if(!keys(input,group?['repositoryId','kind','head','base']:['repositoryId','pr','head']) || input.repositoryId!==c.repositoryId || (group?!validSha(input.base):!validId(input.pr)) || !validSha(input.head))throw new Rejected('ci_locator_invalid');
  const began=clock(),repo='https://api.github.com/repos/'+c.repository,get=(path:string)=>read(repo+path,'github');
  const meta=await get('');if(group && meta.default_branch!=='main' || meta.id!==c.repositoryId || meta.full_name!==c.repository || typeof meta.default_branch!=='string' || !meta.default_branch)throw new Rejected('ci_repository_mismatch');
  const branchPath='/commits/'+encodeURIComponent(meta.default_branch),branch=await get(branchPath);if(!validSha(branch.sha))throw new Rejected('ci_source_invalid');
  if(options?.anchor && branch.sha!==options.anchor.source)throw new Rejected('ci_source_changed');
  async function content(path:string):Promise<string>{
    const d=await get('/contents/'+path+'?ref='+branch.sha);
    if(d.type!=='file' || d.path!==path || d.encoding!=='base64' || typeof d.content!=='string')throw new Rejected('ci_source_invalid');
    try{return new TextDecoder('utf-8',{fatal:true}).decode(unbase64(d.content.replace(/\s/g,'')));}catch{throw new Rejected('ci_source_invalid');}
  }
  let policy:Policy;try{policy=JSON.parse(await content('.github/cla-policy.json'));}catch{throw new Rejected('ci_source_invalid');}
  const digest=await policyDigest(policy),provider=policy.provider;
  if(provider.configured!==true || provider.protocol!=='tavotto-cla-v1' || provider.app_id!==c.appId || provider.app_slug!==c.appSlug || provider.check_name!==c.checkName)
    throw new Rejected('ci_provider_unconfigured');
  for(const [kind,path] of [['individual','docs/legal/CLA_INDIVIDUAL.md'],['corporate','docs/legal/CLA_CORPORATE.md']] as const){
    if(policy.agreements[kind].path!==path || await sha256(await content(path))!==policy.agreements[kind].sha256)throw new Rejected('ci_source_invalid');
  }
  const before=group?{base:{sha:input.base}}:await get('/pulls/'+input.pr);
  const prMatches=(p:any)=>group?p?.base?.sha===input.base:p?.number===input.pr && p.state==='open' && p.base?.repo?.id===c.repositoryId && p.base?.ref===meta.default_branch && p.head?.sha===input.head && validSha(p.base?.sha);
  if(!prMatches(before))throw new Rejected('ci_head_changed');
  const runs:any[]=[];let total=-1;
  for(let page=1;page<=10;page++){
    const data=await get('/commits/'+input.head+'/check-runs?per_page=100&page='+page+'&filter=all&check_name='+encodeURIComponent(c.checkName));
    if(!Number.isSafeInteger(data.total_count) || data.total_count<0 || data.total_count>1000 || total>=0 && total!==data.total_count ||
      !Array.isArray(data.check_runs) || data.check_runs.length>100)throw new Rejected('ci_checks_incomplete');
    total=data.total_count;runs.push(...data.check_runs);if(runs.length>=total)break;if(!data.check_runs.length)throw new Rejected('ci_checks_incomplete');
  }
  if(runs.length!==total || new Set(runs.map(r=>r.id)).size!==total)throw new Rejected('ci_checks_incomplete');
  const own=runs.filter(r=>r.app?.id===c.appId && r.app?.slug===c.appSlug && r.name===c.checkName && r.head_sha===input.head);
  if(own.length===0 && options?.pending){
    if(options.anchor?.candidate)throw new Rejected('ci_candidate_changed');
    throw new CIPending('check_missing',{source:branch.sha});
  }
  if(own.length!==1)throw new Rejected('ci_check_ambiguous');const run=own[0];
  const proof:Proof=parseProof(run.output?.summary,clock());
  if(run.status!=='completed' || run.conclusion!=='success' || !validId(run.id) || !/^[A-Za-z0-9_-]{43}$/.test(run.external_id) ||
    proof.repository_id!==c.repositoryId || proof.kind!==(group?'merge_group':'pr') || (group?proof.group?.trusted_source_sha!==branch.sha:proof.pr!==input.pr) || proof.head_sha!==input.head || proof.base_sha!==before.base.sha || proof.policy_sha256!==digest)
    throw new Rejected('ci_proof_mismatch');
  const proofHash=await sha256(run.output.summary),url=c.origin+(group?'/verification/group?':'/verification/pr?')+new URLSearchParams({...group?{base:input.base!}:{pr:String(input.pr)},head:input.head,run:String(run.id),attempt:run.external_id});
  const identity=canonical({run:run.id,attempt:run.external_id,proof:proofHash});
  if(options?.anchor?.candidate && identity!==options.anchor.candidate)throw new Rejected('ci_candidate_changed');
  // Fix the monotonic proof deadline before the first live authority request,
  // including the first-attempt success path and slow pending responses.
  options?.observe?.({source:branch.sha,candidate:identity,expiresAt:proof.expires_at});
  const receipt=(r:any)=>{
    const expected={protocol:'cla-verification-v1',repository_id:c.repositoryId,...(group?{kind:'merge_group',members:proof.members,group:proof.group}:{pr:input.pr}),head_sha:input.head,base_sha:proof.base_sha,run_id:run.id,
      attempt:run.external_id,app_id:c.appId,app_slug:c.appSlug,check_name:c.checkName,revision:proof.revision,policy_sha256:digest,proof_sha256:proofHash,expires_at:proof.expires_at,eligible:true};
    if(!keys(r,[...Object.keys(expected),'observed_at']) || Object.entries(expected).some(([k,v])=>canonical(r[k])!==canonical(v)) || !Number.isSafeInteger(r.observed_at) ||
      r.observed_at<began || r.observed_at>clock()+5 || r.observed_at<clock()-5 || clock()>=proof.expires_at)throw new Rejected('ci_live_state_changed');
  };
  try{receipt(await read(url,'authority'));}catch(error){
    if(options?.pending && error instanceof CIPending && error.reason==='confirmation_pending' && !error.anchor)
      throw new CIPending('confirmation_pending',{source:branch.sha,candidate:identity,expiresAt:proof.expires_at});
    throw error;
  }
  const finalRun=await get('/check-runs/'+run.id),after=group?before:await get('/pulls/'+input.pr),metadata=await get(''),lastBranch=await get(branchPath);
  if(!prMatches(after) || after.base.sha!==before.base.sha || metadata.id!==meta.id || metadata.full_name!==meta.full_name || metadata.default_branch!==meta.default_branch || lastBranch.sha!==branch.sha ||
    finalRun.id!==run.id || finalRun.app?.id!==c.appId || finalRun.app?.slug!==c.appSlug || finalRun.name!==c.checkName || finalRun.head_sha!==input.head ||
    finalRun.external_id!==run.external_id || finalRun.status!=='completed' || finalRun.conclusion!=='success' || finalRun.output?.summary!==run.output.summary)
    throw new Rejected('ci_final_state_changed');
  try{receipt(await read(url,'authority'));}catch(error){if(error instanceof CIPending)throw new Rejected('ci_live_state_changed');throw error;}
  const checked=clock();if(checked>=proof.expires_at)throw new Rejected('ci_live_state_changed');
  return {status:'success',repository_id:c.repositoryId,...(group?{kind:'merge_group',members:proof.members,group:proof.group}:{pr:input.pr}),head_sha:input.head,revision:proof.revision,proof_sha256:proofHash,
    expires_at:proof.expires_at,checked_at:checked,trusted_source_sha:branch.sha};
}
