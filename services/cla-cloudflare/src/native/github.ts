import type { Candidate, CheckProposal, CheckReceipt, GitHubPort, Identity, Job, Policy, PrSnapshot, Target } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { appJwtWithKey, importAppSigningKey, bytes, canonical, sha256, unbase64 } from '../core/crypto.ts';
import { identity } from '../core/identity.ts';
import { policyDigest, validId, validSha } from '../core/policy.ts';
import type { LiveConfig, Secrets } from './config.ts';
import { validateLive } from './config.ts';
import type { Document, Publication } from './store.ts';
import { BudgetDeferred, InvocationBudget } from './budget.ts';
import { readQueue, prefixFor } from './merge-prefix.ts';
import type { MergePrefixSnapshot, MergePrefixTarget } from './merge-prefix.ts';

type Json = Record<string, any>;
type AppJwtCache={material:string;context:string;mintedAt:number;expiresAt:number;promise:Promise<string>};
export type Transport = (url: string, init: RequestInit) => Promise<Response>;
export interface NegativeWitness {readonly protocol:'cla-negative-witness-v1'}
export interface NegativeContext {job:Job;revision:number}
interface NegativeRelease {
  protocol:'cla-negative-release-v1';repository_id:number;attempt:string;target_key:string;head:string;original_revision:number;
  expires:number;proof_text:string;old_run_id:number|null;run_id:number;observed_phase:string;
  recovery_revision:number;lease:string;generation:number;observed_at:number;valid_until:number;source:'observed_get'|'withdrawal_get';
}
// Production hosts are fixed. Local tests intercept fetch outside the bundle via Miniflare.
export class GitHub implements GitHubPort {
  readonly mode = 'real' as const;
  readonly c:LiveConfig; private readonly secrets:Secrets; private readonly transport:Transport; private readonly now:()=>number;
  readonly budget:InvocationBudget|undefined;
  #appSigningKey:{material:string;promise:Promise<CryptoKey>}|undefined;
  #appJwtCache:AppJwtCache|undefined;
  #jsonDecoder:TextDecoder|undefined;
  // Invocation-local, single-use receipts. JSON or a previous adapter's object
  // cannot manufacture a release capability, and no request state is global.
  #negativeWitnesses=new WeakMap<NegativeWitness,{data:NegativeRelease;budget:InvocationBudget}>();
  constructor(c: LiveConfig, secrets: Secrets,
    transport: Transport = (u,i) => fetch(u,i), now = () => Math.floor(Date.now()/1000),budget?:InvocationBudget) {
    validateLive(c); this.c=c; this.secrets=secrets; this.transport=transport; this.now=now;this.budget=budget;
  }
  private async json(url: string, method: string, token: string | null, body?: unknown, oauth = false): Promise<Json> {
    if (!this.c.githubEnabled) throw new Rejected('network_disabled');
    const u = new URL(url);
    if (u.username || u.password || u.hash || !(u.origin === 'https://api.github.com' || oauth &&
        u.origin === 'https://github.com' && u.pathname === '/login/oauth/access_token')) throw new Rejected('api_host_rejected');
    const headers: Record<string,string> = {'Cache-Control':'no-store',Accept: oauth ? 'application/json' : 'application/vnd.github+json',
      'User-Agent':'Tavotto-CLA-native','X-GitHub-Api-Version':'2026-03-10'};
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const signal=this.budget?.externalSignal() ?? AbortSignal.timeout(10000);
    let pendingAbort:((reason:unknown)=>void)|undefined;
    const abort=()=>{const reject=pendingAbort;pendingAbort=undefined;reject?.(signal.reason);};
    signal.addEventListener('abort',abort,{once:true});
    const within=<T>(operation:Promise<T>):Promise<T>=>new Promise((resolve,reject)=>{
      pendingAbort=reject;
      // Attach both handlers even when already aborted, so a later I/O rejection
      // cannot escape as an unhandled promise. Only one I/O operation is awaited.
      operation.then(value=>{if(pendingAbort===reject)pendingAbort=undefined;signal.aborted?reject(signal.reason):resolve(value);},
        error=>{if(pendingAbort===reject)pendingAbort=undefined;reject(error);});
      if(signal.aborted){if(pendingAbort===reject)pendingAbort=undefined;reject(signal.reason);}
    });
    try {
    let response: Response;
    try { response = await within(this.transport(url,{method,headers,body:body === undefined ? undefined : JSON.stringify(body),
      redirect:'manual',cache:'no-store',signal}));this.budget?.checkWork(); }
    catch(error) {if(error instanceof BudgetDeferred)throw error;this.budget?.checkWork();throw new Rejected('github_unavailable'); }
    if (!response.ok || !/application\/(?:[^;]+\+)?json/i.test(response.headers.get('content-type') ?? ''))
      throw new Rejected('github_unavailable');
    const reader = response.body?.getReader(); if (!reader) throw new Rejected('github_invalid_response');
    const chunks: Uint8Array[] = []; let size = 0;
    try { for (;;) { this.budget?.checkWork();const {done,value} = await within(reader.read());this.budget?.checkWork();if (done) break;
      size += value.byteLength; if (size > 2_000_000) throw new Error();chunks.push(value); }
      let all:Uint8Array;
      if(chunks.length===1)all=chunks[0]!;
      else {all=new Uint8Array(size);let offset=0;for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.length;}}
      const result = JSON.parse((this.#jsonDecoder??=new TextDecoder('utf-8',{fatal:true})).decode(all));
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error(); return result;
    } catch(error) {void reader.cancel().catch(()=>{});if(error instanceof BudgetDeferred)throw error;this.budget?.checkWork();throw new Rejected('github_invalid_response'); }    } finally {pendingAbort=undefined;signal.removeEventListener('abort',abort);}
  }
  // Typed staged adapter: credentials never leave this invocation-local instance.
  async stagedRead(path:string,write=false):Promise<Json> {
    return this.api(path,await this.installationToken(write));
  }
  async stagedReadPair(paths:string[]):Promise<Json[]> {
    if(paths.length<1 || paths.length>2)throw new Rejected('stage_read_bound');
    const token=await this.installationToken();const out:Json[]=[];
    for(const path of paths)out.push(await this.api(path,token));return out;
  }
  async stagedContent(path:string,ref:string):Promise<string> {
    return this.content(path,ref,await this.installationToken());
  }
  stagedOwn(data:Json,p:Publication):void {this.own(data,p);}
  async stagedWithdrawal(p:Publication,run:number,revision:number,pr:number,base:string,branch:string,trusted:string,
    beforeSend:(observation:{head:string;base:string;branch:string})=>Promise<void>):Promise<{alreadyFailure:boolean}> {
    if(!this.c.checkPublishingEnabled)throw new Rejected('publication_disabled');
    const token=await this.installationToken(true),repo=`/repos/${this.c.repository}`,path=`${repo}/check-runs/${run}`;
    const current=await this.api(`${repo}/pulls/${pr}`,token),tip=await this.api(`${repo}/commits/${encodeURIComponent(branch)}`,token);
    if(current.number!==pr || !['open','closed'].includes(current.state) || current.base?.repo?.id!==this.c.repositoryId ||
       !validSha(current.head?.sha) || !validSha(current.base?.sha) || !validSha(tip.sha))throw new Rejected('state_changed');
    const before=await this.api(path,token);this.own(before,p);if(before.id!==run)throw new Rejected('check_source_mismatch');
    if(before.conclusion==='failure')return {alreadyFailure:true};
    // Dynamic changes invalidate the previous success. The owned old-head check
    // can still be changed to failure; it is never transferred to the new head.
    await beforeSend({head:current.head.sha,base:current.base.sha,branch:tip.sha});
    const data=await this.json('https://api.github.com'+path,'PATCH',token,{name:this.c.checkName,status:'completed',conclusion:'failure',external_id:p.id,
      completed_at:new Date(this.now()*1000).toISOString(),output:{title:this.c.checkName,summary:'tavotto-cla-revocation-v1\n'+canonical({
        repository_id:this.c.repositoryId,head_sha:p.head_sha,attempt:p.id,revision,reason:'eligibility_requires_review'})}});
    this.own(data,p);if(data.id!==run || data.conclusion!=='failure')throw new Rejected('check_source_mismatch');
    return {alreadyFailure:false};
  }
  authorizeURL(state: string, challenge: string): string {
    if (!this.c.oauthEnabled) throw new Rejected('oauth_disabled');
    const url = new URL('https://github.com/login/oauth/authorize');
    url.search = new URLSearchParams({client_id:this.c.clientId,redirect_uri:this.c.origin+'/oauth/callback',
      state,code_challenge:challenge,code_challenge_method:'S256',allow_signup:'false'}).toString(); return url.href;
  }
  async oauthIdentity(code: string, verifier: string): Promise<Identity> {
    if (!this.c.oauthEnabled || !/^[A-Za-z0-9_-]{43}$/.test(verifier) ||
        !/^[A-Za-z0-9_.-]{1,256}$/.test(code) || !this.secrets.clientSecret) throw new Rejected('oauth_rejected');
    const data = await this.json('https://github.com/login/oauth/access_token','POST',null,
      {client_id:this.c.clientId,client_secret:this.secrets.clientSecret,code,code_verifier:verifier,
       redirect_uri:this.c.origin+'/oauth/callback',repository_id:String(this.c.repositoryId)},true);
    if (data.error || typeof data.access_token !== 'string' || !/^ghu_[A-Za-z0-9_]{8,256}$/.test(data.access_token) ||
        data.token_type !== 'bearer' || data.scope !== '') throw new Rejected('oauth_rejected');
    const user = identity(await this.json('https://api.github.com/user','GET',data.access_token) as Identity);
    if (user.type !== 'User') throw new Rejected('human_required');
    return {id:user.id,login:user.login,type:user.type}; // User and refresh tokens are never persisted.
  }
  #authContext():string {
    return JSON.stringify([this.c.appId,this.c.installationId,this.c.repositoryId,this.c.repository,this.c.appSlug]);
  }
  async #signingKey(material:string):Promise<CryptoKey> {
    let cached=this.#appSigningKey;
    if(!cached || cached.material!==material) {
      cached={material,promise:importAppSigningKey(material)};
      this.#appSigningKey=cached;
    }
    try { return await cached.promise; }
    catch(error) {
      if(this.#appSigningKey===cached)this.#appSigningKey=undefined;
      throw error;
    }
  }
  async #validAppJwt(material:string,appId:number,now:number,context:string):Promise<{jwt:string;cache:AppJwtCache}> {
    if(!Number.isSafeInteger(now))throw new Rejected('app_jwt_clock_invalid');
    let cached=this.#appJwtCache;
    if(!cached || cached.material!==material || cached.context!==context || now<cached.mintedAt || now>=cached.expiresAt-60) {
      // App JWT carries issuer/time claims, not installation-token permissions.
      // Single flight is scoped to this invocation's GitHub instance; token exchanges and scope checks remain per call.
      const promise=this.#signingKey(material).then(key=>appJwtWithKey(key,appId,now));
      cached={material,context,mintedAt:now,expiresAt:now+300,promise};
      this.#appJwtCache=cached;
    }
    try {
      const jwt=await cached.promise,observed=this.now();
      if(!Number.isSafeInteger(observed) || observed<cached.mintedAt || observed>=cached.expiresAt-60 ||
         this.secrets.pkcs8!==material || this.#authContext()!==context)
        throw new Rejected('app_jwt_context_or_expiry_changed');
      return {jwt,cache:cached};
    } catch(error) {
      // No automatic retry. A later existing business operation may mint anew after failure.
      if(this.#appJwtCache===cached)this.#appJwtCache=undefined;
      throw error;
    }
  }
  private async installationToken(write = false): Promise<string> {
    this.budget?.checkWork();
    if (!this.c.githubEnabled || write && !this.c.checkPublishingEnabled || !this.secrets.pkcs8)
      throw new Rejected('publication_disabled');
    const material=this.secrets.pkcs8, appId=this.c.appId, now=this.now(),context=this.#authContext();
    const authorization=await this.#validAppJwt(material,appId,now,context);
    const unchanged=()=>{
      const observed=this.now(),cached=authorization.cache;
      if(!Number.isSafeInteger(observed) || observed<cached.mintedAt || observed>=cached.expiresAt-60 ||
         this.secrets.pkcs8!==material || this.#authContext()!==context) {
        if(this.#appJwtCache===cached)this.#appJwtCache=undefined;
        throw new Rejected('app_jwt_context_or_expiry_changed');
      }
    };
    unchanged();const jwt=authorization.jwt;
    const installation = await this.json(`https://api.github.com/repos/${this.c.repository}/installation`,'GET',jwt);
    unchanged();
    if (installation.id !== this.c.installationId || installation.app_id !== this.c.appId ||
        installation.app_slug !== this.c.appSlug || installation.suspended_at !== null)
      throw new Rejected('installation_mismatch');
    const permissions = {contents:'read',pull_requests:'read',checks:write ? 'write' : 'read'};
    const data = await this.json(`https://api.github.com/app/installations/${this.c.installationId}/access_tokens`,'POST',jwt,
      {repository_ids:[this.c.repositoryId],permissions});
    unchanged();
    // Installation tokens are opaque, including stateless JWT-shaped tokens.
    // 8192 visible ASCII bytes is this service's resource/header safety bound,
    // not a claim about GitHub token syntax. Never parse a prefix or JWT payload.
    if (typeof data.token !== 'string' || data.token.length===0 || data.token.length>8192 || /[^\x21-\x7e]/.test(data.token) ||
        !Number.isFinite(Date.parse(data.expires_at)) || Date.parse(data.expires_at) <= this.now()*1000 ||
        !Array.isArray(data.repositories) || data.repositories.length !== 1 || data.repositories[0]?.id !== this.c.repositoryId ||
        Object.entries(permissions).some(([k,v]) => data.permissions?.[k] !== v) ||
        Object.keys(data.permissions ?? {}).some(k => k !== 'metadata' && !(k in permissions)) ||
        data.permissions?.metadata !== 'read') throw new Rejected('token_scope_mismatch');
    return data.token;
  }
  private async api(path: string, token: string): Promise<Json> { return this.json('https://api.github.com'+path,'GET',token); }
  async resolveLogin(login: string): Promise<Identity> {
    if (!/^[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?$/.test(login)) throw new Rejected('identity_unresolved');
    const user = identity(await this.api('/users/'+encodeURIComponent(login),await this.installationToken()) as Identity);
    if (user.login.toLowerCase() !== login.toLowerCase()) throw new Rejected('identity_unresolved'); return user;
  }
  private async content(path: string, ref: string, token: string): Promise<string> {
    if (!/^([A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+$/.test(path) || path.split('/').includes('..')) throw new Rejected('policy_path_invalid');
    const data = await this.api(`/repos/${this.c.repository}/contents/${path}?ref=${encodeURIComponent(ref)}`,token);
    if (data.type !== 'file' || data.encoding !== 'base64' || data.path !== path || typeof data.content !== 'string')
      throw new Rejected('policy_unavailable');
    try { return new TextDecoder('utf-8',{fatal:true}).decode(unbase64(data.content.replace(/\s/g,''))); }
    catch(error) {if(error instanceof BudgetDeferred)throw error;throw new Rejected('policy_unavailable'); }
  }
  async agreement(kind:'individual'|'corporate'):Promise<Document> {
    const a=this.c.policy.agreements[kind],token=await this.installationToken(),repo=`/repos/${this.c.repository}`;
    const metadata=await this.api(repo,token);
    if(metadata.id!==this.c.repositoryId || metadata.full_name!==this.c.repository || typeof metadata.default_branch!=='string')throw new Rejected('repository_mismatch');
    const branch=await this.api(repo+'/commits/'+encodeURIComponent(metadata.default_branch),token);
    if(!validSha(branch.sha))throw new Rejected('policy_unavailable');
    const policy=JSON.parse(await this.content('.github/cla-policy.json',branch.sha,token));
    if(await policyDigest(policy)!==await policyDigest(this.c.policy) || canonical(policy.provider)!==canonical(this.c.policy.provider))throw new Rejected('trusted_policy_changed');
    const content=await this.content(a.path,branch.sha,token);
    if(await sha256(content)!==a.sha256 || (await this.api(repo+'/commits/'+encodeURIComponent(metadata.default_branch),token)).sha!==branch.sha)
      throw new Rejected('agreement_hash_mismatch');
    return {digest:a.sha256,kind,version:a.version,source_sha:branch.sha,content};
  }
  async snapshot(target: Target): Promise<Candidate> {
    if (target.kind !== 'pr' || !validId(target.number)) throw new Rejected('merge_queue_not_implemented');
    const token = await this.installationToken(), repo = `/repos/${this.c.repository}`;
    const metadata = await this.api(repo,token);
    if (metadata.id !== this.c.repositoryId || metadata.full_name !== this.c.repository || typeof metadata.default_branch !== 'string')
      throw new Rejected('repository_mismatch');
    const trusted = await this.api(repo+'/commits/'+encodeURIComponent(metadata.default_branch),token);
    if (!validSha(trusted.sha)) throw new Rejected('policy_unavailable');
    let policy: Policy; try { policy = JSON.parse(await this.content('.github/cla-policy.json',trusted.sha,token)); }
    catch(error) {if(error instanceof BudgetDeferred)throw error;throw new Rejected('policy_unavailable'); }
    if (await policyDigest(policy) !== await policyDigest(this.c.policy) ||
        canonical(policy.provider) !== canonical(this.c.policy.provider)) throw new Rejected('trusted_policy_changed');
    for (const [kind,agreement] of Object.entries(this.c.policy.agreements)) {
      if (agreement.path !== (kind === 'individual' ? 'docs/legal/CLA_INDIVIDUAL.md' : 'docs/legal/CLA_CORPORATE.md') ||
          await sha256(await this.content(agreement.path,trusted.sha,token)) !== agreement.sha256)
        throw new Rejected('agreement_hash_mismatch');
    }
    const pr=await this.prSnapshot(target,token,repo);
    const branchAfter=await this.api(repo+'/commits/'+encodeURIComponent(metadata.default_branch),token);
    if(branchAfter.sha!==trusted.sha)throw new Rejected('state_changed');
    return {kind:'pr',repositoryId:this.c.repositoryId,headSha:pr.headSha,baseSha:pr.baseSha,policy,members:[],prs:[pr]};
  }
  private async prSnapshot(target:{number:number},token:string,repo:string,baseRef?:string):Promise<PrSnapshot> {
    const pr = await this.api(repo+'/pulls/'+target.number,token);
    if (pr.number !== target.number || pr.state !== 'open' || pr.base?.repo?.id !== this.c.repositoryId ||
        !validSha(pr.head?.sha) || !validSha(pr.base?.sha) || !Number.isSafeInteger(pr.commits) || pr.commits < 1 || pr.commits > 1000)
      throw new Rejected('pr_mismatch');
    const commits = []; let total = -1;
    for (let page = 1; page <= Math.ceil(pr.commits/100); page++) {
      const data = await this.api(`${repo}/compare/${pr.base.sha}...${pr.head.sha}?per_page=100&page=${page}`,token);
      if (data.total_commits !== pr.commits || !Array.isArray(data.commits) || data.commits.length < 1 || data.commits.length > 100 ||
          data.base_commit?.sha !== pr.base.sha) throw new Rejected('commits_incomplete');
      total = data.total_commits;
      for (const item of data.commits) {
        if (!validSha(item.sha) || typeof item.commit?.message !== 'string' || typeof item.commit?.author?.email !== 'string')
          throw new Rejected('commit_invalid');
        commits.push({sha:item.sha,author:item.author === null ? null : identity(item.author),
          email:item.commit.author.email,message:item.commit.message});
      }
    }
    if (total !== commits.length || new Set(commits.map(x=>x.sha)).size !== commits.length || !commits.some(x=>x.sha === pr.head.sha))
      throw new Rejected('commits_incomplete');
    const after = await this.api(repo+'/pulls/'+target.number,token);
    if (after.state !== 'open' || after.number !== pr.number || after.head?.sha !== pr.head.sha || after.base?.sha !== pr.base.sha ||
        after.commits !== pr.commits || after.user?.id !== pr.user?.id || after.base?.repo?.id !== this.c.repositoryId) throw new Rejected('state_changed');
    if(baseRef!==undefined && (pr.base?.ref!==baseRef || after.base?.ref!==baseRef || after.user?.login!==pr.user?.login || after.user?.type!==pr.user?.type))throw new Rejected('state_changed');
    return {number:pr.number,state:'open',headSha:pr.head.sha,baseSha:pr.base.sha,author:identity(pr.user),expectedCommits:pr.commits,commits};
  }

  async mergePrefixSnapshot(input:MergePrefixTarget):Promise<MergePrefixSnapshot> {
    if(this.c.mergePrefixAssessmentEnabled!==true)throw new Rejected('merge_prefix_disabled');
    const target=structuredClone(input);
    if(!target || Object.keys(target).sort().join(',')!=='baseRef,baseSha,headSha' || target.baseRef!=='refs/heads/main' ||
      !validSha(target.headSha) || !validSha(target.baseSha))throw new Rejected('merge_prefix_target_invalid');
    const token=await this.installationToken(),repo=`/repos/${this.c.repository}`;
    const metadata=await this.api(repo,token);
    if(metadata.id!==this.c.repositoryId || metadata.full_name!==this.c.repository || metadata.default_branch!=='main')throw new Rejected('repository_mismatch');
    const trusted=await this.api(repo+'/commits/main',token);
    if(!validSha(trusted.sha))throw new Rejected('policy_unavailable');
    let policy:Policy;try{policy=JSON.parse(await this.content('.github/cla-policy.json',trusted.sha,token));}
    catch(error){if(error instanceof BudgetDeferred)throw error;throw new Rejected('policy_unavailable');}
    if(await policyDigest(policy)!==await policyDigest(this.c.policy) || canonical(policy.provider)!==canonical(this.c.policy.provider))throw new Rejected('trusted_policy_changed');
    for(const [kind,agreement]of Object.entries(this.c.policy.agreements)) {
      if(agreement.path!==(kind==='individual'?'docs/legal/CLA_INDIVIDUAL.md':'docs/legal/CLA_CORPORATE.md') ||
        await sha256(await this.content(agreement.path,trusted.sha,token))!==agreement.sha256)throw new Rejected('agreement_hash_mismatch');
    }
    const queue=()=>readQueue(this.c.repository,this.c.repositoryId,(query,variables)=>this.json('https://api.github.com/graphql','POST',token,{query,variables}));
    const entries=await queue(),prefix=prefixFor(entries,target),prs:PrSnapshot[]=[];
    for(const entry of prefix) {
      const pr=await this.prSnapshot({number:entry.pullRequest.number},token,repo,'main');
      if(pr.headSha!==entry.pullRequest.headRefOid)throw new Rejected('merge_prefix_changed');prs.push(pr);
    }
    if(canonical(entries)!==canonical(await queue()) || (await this.api(repo+'/commits/main',token)).sha!==trusted.sha)
      throw new Rejected('merge_prefix_changed');
    return {kind:'merge_group',repositoryId:this.c.repositoryId,headSha:target.headSha,baseSha:target.baseSha,policy,
      members:prefix.map(entry=>({pr:entry.pullRequest.number,headSha:entry.pullRequest.headRefOid})),prs,queueEntries:entries,trustedSourceSha:trusted.sha};
  }
  private receipt(data: Json, check: CheckProposal): CheckReceipt {
    if (!validId(data.id) || data.app?.id !== this.c.appId || data.app?.slug !== this.c.appSlug ||
        data.name !== check.name || data.head_sha !== check.headSha) throw new Rejected('check_source_mismatch');
    return {id:data.id,appId:data.app.id,appSlug:data.app.slug,name:data.name,headSha:data.head_sha};
  }
  private async runs(head:string,token:string):Promise<Json[]> {
    const repo=`/repos/${this.c.repository}`,runs:Json[]=[];let total=-1;
    for(let page=1;page<=10;page++) {
      const data=await this.api(`${repo}/commits/${head}/check-runs?per_page=100&page=${page}&filter=all&check_name=${encodeURIComponent(this.c.checkName)}`,token);
      if(!Number.isSafeInteger(data.total_count) || data.total_count<0 || data.total_count>1000 || !Array.isArray(data.check_runs) ||
        data.check_runs.length>100 || total>=0 && total!==data.total_count)throw new Rejected('checks_incomplete');
      total=data.total_count;runs.push(...data.check_runs);if(runs.length>=total)break;if(data.check_runs.length===0)throw new Rejected('checks_incomplete');
    }
    if(runs.length!==total || new Set(runs.map(x=>x.id)).size!==total)throw new Rejected('checks_incomplete');return runs;
  }
  private own(data:Json,p:Publication):void {
    if(!validId(data.id) || data.app?.id!==this.c.appId || data.app?.slug!==this.c.appSlug || data.name!==this.c.checkName ||
      data.head_sha!==p.head_sha || data.external_id!==p.id || data.status!=='completed' || !['success','failure'].includes(data.conclusion))
      throw new Rejected('check_source_mismatch');
    if(data.conclusion==='success' && (p.conclusion!=='success' || data.output?.summary!==p.proof_text))throw new Rejected('check_source_mismatch');
  }
  #negativeWitness(p:Publication,run:number,context:NegativeContext|undefined,began:number,source:NegativeRelease['source']):NegativeWitness {
    const now=this.now(),job=context?.job,revision=context?.revision;
    if(!this.budget || !job || !Number.isSafeInteger(revision) || revision!<0 || !validId(run) ||
      p.confirmation_slot!==1 || p.conclusion!=='success' || !/^[A-Za-z0-9_-]{43}$/.test(p.id) ||
      job.target_key!==p.target_key || !p.target_key.startsWith('pr:'+this.c.repositoryId+':') ||
      !/^[A-Za-z0-9_-]{43}$/.test(job.lease) || !validId(job.generation) || !Number.isSafeInteger(job.lease_until) ||
      !Number.isSafeInteger(began) || !Number.isSafeInteger(now) || now<began || now>=began+10 || now>=job.lease_until)
      throw new Rejected('fresh_negative_witness_unavailable');
    const witness:NegativeWitness=Object.freeze({protocol:'cla-negative-witness-v1'});
    this.#negativeWitnesses.set(witness,{budget:this.budget,data:{protocol:'cla-negative-release-v1',repository_id:this.c.repositoryId,
      attempt:p.id,target_key:p.target_key,head:p.head_sha,original_revision:p.revision,expires:p.expires,proof_text:p.proof_text,
      old_run_id:p.run_id,run_id:run,observed_phase:p.phase,recovery_revision:revision!,lease:job.lease,generation:job.generation,
      observed_at:now,valid_until:Math.min(began+10,job.lease_until),source}});return witness;
  }
  consumeNegativeWitness(witness:NegativeWitness,p:Publication,job:Job,revision:number):string {
    const entry=this.#negativeWitnesses.get(witness);this.#negativeWitnesses.delete(witness);
    const d=entry?.data,now=this.now();
    if(!d || entry!.budget!==this.budget || !Number.isSafeInteger(now) || now<d.observed_at || now>=d.valid_until ||
      d.repository_id!==this.c.repositoryId || d.attempt!==p.id || d.target_key!==p.target_key || d.head!==p.head_sha ||
      d.original_revision!==p.revision || d.expires!==p.expires || d.proof_text!==p.proof_text || d.old_run_id!==p.run_id ||
      d.observed_phase!==p.phase || d.recovery_revision!==revision || d.target_key!==job.target_key || d.lease!==job.lease ||
      d.generation!==job.generation || p.confirmation_slot!==1)throw new Rejected('fresh_negative_witness_unavailable');
    return canonical(d);
  }
  async observe(input:Publication,negativeContext?:NegativeContext):Promise<{id:number;conclusion:'success'|'failure';negativeWitness?:NegativeWitness}|null> {
    const p=structuredClone(input),context=negativeContext?structuredClone(negativeContext):undefined;
    const token=await this.installationToken(),runs=await this.runs(p.head_sha,token);
    const own=runs.filter(x=>x.app?.id===this.c.appId && x.app?.slug===this.c.appSlug && x.name===this.c.checkName && x.head_sha===p.head_sha);
    if(own.length===0)return null;
    if(own.length!==1 || own[0]?.external_id!==p.id || p.run_id!==null && own[0]?.id!==p.run_id)throw new Rejected('ambiguous_own_check');
    this.own(own[0]!,p);
    if(p.confirmation_slot===1 && own[0]!.conclusion==='failure'){
      const began=this.now(),fresh=await this.api(`/repos/${this.c.repository}/check-runs/${own[0]!.id}`,token);
      this.own(fresh,p);if(fresh.id!==own[0]!.id || fresh.conclusion!=='failure')throw new Rejected('negative_observation_changed');
      return {id:fresh.id,conclusion:'failure',negativeWitness:this.#negativeWitness(p,fresh.id,context,began,'observed_get')};
    }
    return {id:own[0]!.id,conclusion:own[0]!.conclusion};
  }
  async readPublication(p:Publication):Promise<void> {
    if(!validId(p.run_id))throw new Rejected('proof_not_confirmed');
    const data=await this.api(`/repos/${this.c.repository}/check-runs/${p.run_id}`,await this.installationToken());
    this.own(data,p);if(data.id!==p.run_id || data.conclusion!=='success')throw new Rejected('qualification_changed');
  }
  async withdraw(input:Publication,run:number,revision:number,negativeContext?:NegativeContext):Promise<NegativeWitness|undefined> {
    const p=structuredClone(input),context=negativeContext?structuredClone(negativeContext):undefined;
    if(!this.c.checkPublishingEnabled)throw new Rejected('publication_disabled');
    const token=await this.installationToken(true),path=`/repos/${this.c.repository}/check-runs/${run}`;
    let began=this.now();const before=await this.api(path,token);this.own(before,p);if(before.id!==run)throw new Rejected('check_source_mismatch');
    if(before.conclusion==='failure')return p.confirmation_slot===1?this.#negativeWitness(p,run,context,began,'withdrawal_get'):undefined;
    const data=await this.json('https://api.github.com'+path,'PATCH',token,{name:this.c.checkName,status:'completed',conclusion:'failure',external_id:p.id,
      completed_at:new Date(this.now()*1000).toISOString(),output:{title:this.c.checkName,summary:'tavotto-cla-revocation-v1\n'+canonical({
        repository_id:this.c.repositoryId,head_sha:p.head_sha,attempt:p.id,revision,reason:'eligibility_requires_review'})}});
    this.own(data,p);if(data.id!==run || data.conclusion!=='failure')throw new Rejected('check_source_mismatch');
    began=this.now();const after=await this.api(path,token);this.own(after,p);
    if(after.id!==run || after.conclusion!=='failure')throw new Rejected('check_source_mismatch');
    return p.confirmation_slot===1?this.#negativeWitness(p,run,context,began,'withdrawal_get'):undefined;
  }
  // Refresh only an exactly observed, durably resolved same-head predecessor.
  // Authentication and all remote pre-reads finish before the journal callback;
  // after it commits, there is one mutation and no automatic retry.
  async publishFresh(check:CheckProposal,predecessor:Publication,beforeSend:()=>Promise<string>):Promise<CheckReceipt> {
    if(!this.c.checkPublishingEnabled)throw new Rejected('publication_disabled');
    if(check.appId!==this.c.appId || check.appSlug!==this.c.appSlug || check.name!==this.c.checkName ||
      check.proof.repository_id!==this.c.repositoryId || check.proof.kind!=='pr' || !validSha(check.headSha) ||
      check.headSha!==check.proof.head_sha || predecessor.head_sha!==check.headSha ||
      predecessor.phase!=='resolved' || !validId(predecessor.run_id) || !['success','failure'].includes(check.conclusion))
      throw new Rejected('refresh_predecessor_unavailable');
    const token=await this.installationToken(true),runs=await this.runs(check.headSha,token);
    const own=runs.filter(x=>x.app?.id===this.c.appId && x.app?.slug===this.c.appSlug && x.name===this.c.checkName && x.head_sha===check.headSha);
    if(own.length!==1)throw new Rejected('ambiguous_own_check');
    const old=own[0]!;this.own(old,predecessor);
    if(old.id!==predecessor.run_id || old.conclusion!=='failure')throw new Rejected('refresh_predecessor_unavailable');
    let recordedFailure=predecessor.conclusion==='failure' && old.output?.summary===predecessor.proof_text;
    if(typeof old.output?.summary==='string' && old.output.summary.startsWith('tavotto-cla-revocation-v1\n')) {
      try {const revoked=JSON.parse(old.output.summary.slice('tavotto-cla-revocation-v1\n'.length));
        recordedFailure=canonical(revoked)===old.output.summary.slice('tavotto-cla-revocation-v1\n'.length) &&
          revoked.repository_id===this.c.repositoryId && revoked.head_sha===predecessor.head_sha && revoked.attempt===predecessor.id &&
          revoked.reason==='eligibility_requires_review' && Number.isSafeInteger(revoked.revision) &&
          revoked.revision>=predecessor.revision && revoked.revision<=check.proof.revision;
      }catch{recordedFailure=false;}
    }
    if(!recordedFailure)throw new Rejected('refresh_predecessor_unavailable');
    const attempt=await beforeSend();
    const data=await this.json(`https://api.github.com/repos/${this.c.repository}/check-runs/${old.id}`,'PATCH',token,
      {name:check.name,status:'completed',conclusion:check.conclusion,external_id:attempt,
        completed_at:new Date(this.now()*1000).toISOString(),output:{title:check.name,summary:check.text}});
    if(data.id!==old.id || data.external_id!==attempt || data.status!=='completed' || data.conclusion!==check.conclusion ||
      data.output?.summary!==check.text)throw new Rejected('check_source_mismatch');
    return this.receipt(data,check);
  }
  // New heads only. All authentication and reads precede the durable send intent.
  // Existing/ambiguous owned runs require reconciliation, never a second POST.
  async publishNew(check:CheckProposal,beforeSend:()=>Promise<string>):Promise<CheckReceipt> {
    if(!this.c.automaticPublishingEnabled || !this.c.checkPublishingEnabled)throw new Rejected('publication_disabled');
    if(check.appId!==this.c.appId || check.appSlug!==this.c.appSlug || check.name!==this.c.checkName ||
      check.proof.repository_id!==this.c.repositoryId || check.proof.kind!=='pr' || !validSha(check.headSha) ||
      check.headSha!==check.proof.head_sha || !['success','failure'].includes(check.conclusion))throw new Rejected('check_invalid');
    const token=await this.installationToken(true),runs=await this.runs(check.headSha,token);
    if(runs.some(run=>run.app?.id===this.c.appId && run.app?.slug===this.c.appSlug && run.name===this.c.checkName && run.head_sha===check.headSha))
      throw new Rejected('new_head_run_already_exists');
    const attempt=await beforeSend();
    const data=await this.json(`https://api.github.com/repos/${this.c.repository}/check-runs`,'POST',token,
      {name:check.name,head_sha:check.headSha,status:'completed',conclusion:check.conclusion,external_id:attempt,
        completed_at:new Date(this.now()*1000).toISOString(),output:{title:check.name,summary:check.text}});
    if(data.external_id!==attempt || data.status!=='completed' || data.conclusion!==check.conclusion || data.output?.summary!==check.text)throw new Rejected('check_source_mismatch');
    return this.receipt(data,check);
  }
  async publish(check: CheckProposal,externalId?:string): Promise<CheckReceipt> {
    if (!this.c.checkPublishingEnabled) throw new Rejected('publication_disabled');
    if (check.appId !== this.c.appId || check.appSlug !== this.c.appSlug || check.name !== this.c.checkName ||
        !validSha(check.headSha) || check.headSha !== check.proof.head_sha ||
        check.proof.repository_id !== this.c.repositoryId || check.proof.kind !== 'pr' ||
        !['success','failure'].includes(check.conclusion)) throw new Rejected('check_invalid');
    const token = await this.installationToken(true), repo = `/repos/${this.c.repository}`;
    const runs=await this.runs(check.headSha,token);
    const own = runs.filter(x=>x.app?.id === this.c.appId && x.app?.slug === this.c.appSlug &&
      x.name === check.name && x.head_sha === check.headSha);
    if (own.length > 1) throw new Rejected('ambiguous_own_check');
    if (own[0]) this.receipt(own[0],check);
    const id = own[0]?.id, url = 'https://api.github.com'+repo+'/check-runs'+(id ? '/'+id : '');
    const body = {name:check.name,...(id ? {} : {head_sha:check.headSha}),...(externalId?{external_id:externalId}:{}),status:'completed',conclusion:check.conclusion,
      completed_at:new Date(this.now()*1000).toISOString(),output:{title:check.name,summary:check.text}};
    const data = await this.json(url,id ? 'PATCH' : 'POST',token,body);
    if (data.status !== 'completed' || data.conclusion !== check.conclusion) throw new Rejected('check_source_mismatch');
    if(externalId && data.external_id!==externalId)throw new Rejected('check_source_mismatch');
    return this.receipt(data,check);
  }
}
