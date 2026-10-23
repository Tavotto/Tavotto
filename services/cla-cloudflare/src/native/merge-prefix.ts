import type { Binding, Candidate, Config, GitHubPort } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { binding } from '../core/identity.ts';
import { canonical, sha256 } from '../core/crypto.ts';
import { validId, validSha } from '../core/policy.ts';

// Same query and queue-head-to-candidate rule as services/cla/github.py.
// This read-only candidate neither requests installation permissions nor emits proofs.
export const MERGE_PREFIX_QUERY = `query($owner:String!,$name:String!,$branch:String!,$cursor:String) {
  repository(owner:$owner,name:$name) { databaseId mergeQueue(branch:$branch) {
    entries(first:100,after:$cursor) { totalCount pageInfo { hasNextPage endCursor }
      nodes { id position baseCommit { oid } headCommit { oid }
        pullRequest { number headRefOid baseRefName } } } } } }`;
export interface MergePrefixTarget {headSha:string;baseSha:string;baseRef:string}
export interface QueueEntry {
  id:string;position:number;baseCommit:{oid:string}|null;headCommit:{oid:string}|null;
  pullRequest:{number:number;headRefOid:string;baseRefName:string};
}
export interface MergePrefixSnapshot extends Candidate {queueEntries:QueueEntry[];trustedSourceSha:string}
export interface MergePrefixBinding {
  binding:Binding;strategy:'queue-prefix-v1';prefix_sha256:string;queue_sha256:string;trusted_source_sha:string;
}
export interface MergePrefixAssessment extends MergePrefixBinding {
  protocol:'cla-merge-prefix-assessment-v1';eligible:boolean;revision:number;observed_at:number;valid_until:number;
  publication:'not_attempted';
}
type RecordValue=Record<string,unknown>;
function object(value:unknown):RecordValue {
  if(!value || typeof value!=='object' || Array.isArray(value))throw new Rejected('merge_prefix_incomplete');
  return value as RecordValue;
}
function commit(value:unknown):{oid:string}|null {
  if(value===null)return null;const result=object(value);
  if(!validSha(result.oid))throw new Rejected('merge_prefix_incomplete');return {oid:result.oid as string};
}
function entry(value:unknown):QueueEntry {
  const e=object(value),pr=object(e.pullRequest);
  if(typeof e.id!=='string' || e.id.length<1 || e.id.length>200 || /[^\x21-\x7e]/.test(e.id) || !validId(e.position) ||
    !validId(pr.number) || !validSha(pr.headRefOid) || typeof pr.baseRefName!=='string' || !pr.baseRefName || pr.baseRefName.length>255)
    throw new Rejected('merge_prefix_incomplete');
  return {id:e.id,position:e.position as number,baseCommit:commit(e.baseCommit),headCommit:commit(e.headCommit),
    pullRequest:{number:pr.number as number,headRefOid:pr.headRefOid as string,baseRefName:pr.baseRefName}};
}
export function queueEntries(values:unknown):QueueEntry[] {
  if(!Array.isArray(values) || values.length<1 || values.length>1000)throw new Rejected('merge_prefix_incomplete');
  const entries=values.map(entry).sort((a,b)=>a.position-b.position);
  if(new Set(entries.map(e=>e.id)).size!==entries.length || new Set(entries.map(e=>e.pullRequest.number)).size!==entries.length ||
    entries.some((e,i)=>e.position!==i+1))throw new Rejected('merge_prefix_incomplete');
  return entries;
}
export function prefixFor(entries:QueueEntry[],target:MergePrefixTarget):QueueEntry[] {
  if(!target || target.baseRef!=='refs/heads/main' || !validSha(target.headSha) || !validSha(target.baseSha))
    throw new Rejected('merge_prefix_target_invalid');
  const matches=entries.filter(e=>e.headCommit?.oid===target.headSha && e.baseCommit?.oid===target.baseSha);
  if(matches.length!==1)throw new Rejected('merge_prefix_candidate_absent');
  const prefix=entries.slice(0,matches[0]!.position);
  if(prefix.length>100 || prefix.some(e=>e.pullRequest.baseRefName!=='main'))throw new Rejected('merge_prefix_incomplete');
  return prefix;
}
export async function readQueue(repository:string,repositoryId:number,query:(query:string,variables:Record<string,unknown>)=>Promise<unknown>):Promise<QueueEntry[]> {
  const [owner,name]=repository.split('/');let cursor:string|null=null,total:number|undefined;
  const cursors=new Set<string>(),entries:QueueEntry[]=[];
  for(let page=0;page<10;page++) {
    const result=object(await query(MERGE_PREFIX_QUERY,{owner,name,branch:'main',cursor}));
    if(result.errors!==undefined && (!Array.isArray(result.errors) || result.errors.length))throw new Rejected('merge_prefix_unavailable');
    const repo=object(object(result.data).repository);
    if(repo.databaseId!==repositoryId)throw new Rejected('repository_mismatch');
    const connection=object(object(repo.mergeQueue).entries),info=object(connection.pageInfo);
    if(!Number.isSafeInteger(connection.totalCount) || (connection.totalCount as number)<1 || (connection.totalCount as number)>1000 ||
      total!==undefined && total!==connection.totalCount || !Array.isArray(connection.nodes) || connection.nodes.length>100 ||
      typeof info.hasNextPage!=='boolean')throw new Rejected('merge_prefix_incomplete');
    total=connection.totalCount as number;entries.push(...connection.nodes.map(entry));
    if(entries.length>total)throw new Rejected('merge_prefix_incomplete');
    if(!info.hasNextPage) {
      if(entries.length!==total)throw new Rejected('merge_prefix_incomplete');return queueEntries(entries);
    }
    if(entries.length>=total || connection.nodes.length===0 || typeof info.endCursor!=='string' || !info.endCursor ||
      info.endCursor.length>1000 || cursors.has(info.endCursor))throw new Rejected('merge_prefix_incomplete');
    cursors.add(info.endCursor);cursor=info.endCursor;
  }
  throw new Rejected('merge_prefix_incomplete');
}
export async function mergePrefixBinding(c:Config,port:GitHubPort,target:MergePrefixTarget,snapshot:MergePrefixSnapshot):Promise<MergePrefixBinding> {
  const entries=queueEntries(snapshot.queueEntries),prefix=prefixFor(entries,target);
  if(!validSha(snapshot.trustedSourceSha) || canonical(snapshot.members)!==canonical(prefix.map(e=>({pr:e.pullRequest.number,headSha:e.pullRequest.headRefOid}))))
    throw new Rejected('merge_prefix_changed');
  const bound=await binding(c,port,{kind:'merge_group',headSha:target.headSha,baseSha:target.baseSha},snapshot);
  return {binding:bound,strategy:'queue-prefix-v1',trusted_source_sha:snapshot.trustedSourceSha,
    prefix_sha256:await sha256(canonical({target,entries:prefix})),queue_sha256:await sha256(canonical(entries))};
}
