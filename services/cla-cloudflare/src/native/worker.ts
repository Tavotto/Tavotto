import type { D1Database } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { importAes, unbase64, sha256 } from '../core/crypto.ts';
import type { LiveConfig, Secrets } from './config.ts';
import { validateLive } from './config.ts';
import { GitHub } from './github.ts';
import { LiveStore } from './store.ts';
import { LiveService } from './service.ts';
import { uiHTML, uiCSS, uiScript } from './ui.ts';
import { reconcileOne } from './background.ts';
import { verification } from './verification.ts';
import { InvocationBudget } from './budget.ts';
import { validId, validSha } from '../core/policy.ts';
import {admitHead,verifyHeadOne,AUTOMATIC_CRON} from './automatic.ts';

export interface Env {
  CLA_VERIFICATION_LIMITER?:{limit(input:{key:string}):Promise<{success:boolean}>};
  CLA_OAUTH_LIMITER?:{limit(input:{key:string}):Promise<{success:boolean}>};
  CLA_PUBLIC_AGREEMENT?:{fetch(request:Request):Promise<Response>};
  DB:D1Database; CLA_CONFIG?:string; CLA_EVIDENCE_KEY?:string; CLA_CLIENT_SECRET?:string; CLA_APP_PKCS8?:string;
  CLA_GITHUB_ENABLED?:string; CLA_OAUTH_ENABLED?:string; CLA_SIGNING_ENABLED?:string; CLA_CHECK_PUBLISHING_ENABLED?:string;
  CLA_UI_ENABLED?:string; CLA_BACKGROUND_ENABLED?:string;
  CLA_CI_VERIFICATION_ENABLED?:string;
  CLA_DELEGATED_COUNTERSIGNING_ENABLED?:string;
  CLA_AUTOMATIC_VERIFICATION_ENABLED?:string;
  CLA_AUTOMATIC_PUBLISHING_ENABLED?:string;
  CLA_AGREEMENT_ACTIVATION_ENABLED?:string;
  CLA_CONFIRMATION_SLOT_ENABLED?:string;
  CLA_MERGE_PREFIX_ASSESSMENT_ENABLED?:string;
  CLA_MERGE_PREFIX_READ_SESSION_ENABLED?:string;
  CLA_WEBHOOK_SECRET?:string;
  CLA_WEBHOOK_LIMITER?:{limit(input:{key:string}):Promise<{success:boolean}>};
}
const headers = {'cache-control':'no-store','referrer-policy':'no-referrer','x-content-type-options':'nosniff',
  'content-security-policy':"default-src 'none'; frame-ancestors 'none'"};
const json = (data:unknown,status=200,extra:Record<string,string>={}) => Response.json(data,{status,headers:{...headers,...extra}});
const cookie = (name:string,value:string,age:number) => `${name}=${value}; Path=/; Max-Age=${age}; Secure; HttpOnly; SameSite=Lax`;
// GitHub's /.well-known/oauth-authorization-server/login/oauth declares this
// issuer and authorization_response_iss_parameter_supported=true. RFC 9207
// section 2.4 requires an issuer for that provider and exact decoded-string
// comparison; URL normalization or a missing-issuer fallback is not allowed.
const GITHUB_OAUTH_ISSUER='https://github.com/login/oauth';
function validOAuthCallbackQuery(url:URL):boolean {
  return [...url.searchParams.keys()].sort().join(',')==='code,iss,state' &&
    url.searchParams.get('iss')===GITHUB_OAUTH_ISSUER;
}
function cookies(req:Request):Map<string,string> {
  const result=new Map<string,string>();
  for (const piece of (req.headers.get('cookie') ?? '').split(';').filter(x=>x.trim())) {
    const match=/^\s*([^=\s]+)=([^;]*)$/.exec(piece); if (!match || result.has(match[1]!)) throw new Rejected('cookie_rejected');
    result.set(match[1]!,match[2]!);
  } return result;
}
async function body(req:Request,keys:string[]):Promise<Record<string,any>> {
  if (req.headers.get('content-type')?.split(';')[0] !== 'application/json') throw new Rejected('form_rejected');
  const reader=req.body?.getReader(); if (!reader) throw new Rejected('form_rejected');
  const chunks:Uint8Array[]=[];let size=0;
  for (;;) {const {done,value}=await reader.read();if(done)break;size+=value.length;
    if(size>32768){await reader.cancel();throw new Rejected('form_rejected');} chunks.push(value);}
  const all=new Uint8Array(size);let offset=0;for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.length;}
  let data;try{data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(all));}catch{throw new Rejected('form_rejected');}
  if(!data || typeof data!=='object' || Array.isArray(data) || Object.keys(data).sort().join(',')!==keys.sort().join(','))
    throw new Rejected('form_rejected');return data;
}
async function publicAgreement(env:Env,c:LiveConfig):Promise<Response> {
  if(!env.CLA_PUBLIC_AGREEMENT)return json({error:'agreement_configuration_required'},503);
  try {
    // This fixed asset path is never derived from caller input and never falls back to GitHub.
    const response=await env.CLA_PUBLIC_AGREEMENT.fetch(new Request('https://cla-assets.invalid/CLA_INDIVIDUAL.md',{redirect:'manual'}));
    if(response.status!==200 || response.redirected || !response.body ||
      !['text/plain','text/markdown'].includes(response.headers.get('content-type')?.split(';')[0]?.trim() ?? ''))
      throw new Rejected('agreement_asset_unavailable');
    const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
    for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;
      if(size>131072){await reader.cancel();throw new Rejected('agreement_asset_too_large');}chunks.push(value);}
    const data=new Uint8Array(size);let at=0;for(const chunk of chunks){data.set(chunk,at);at+=chunk.length;}
    const a=c.policy.agreements.individual;
    if(await sha256(data)!==a.sha256)throw new Rejected('agreement_digest_mismatch');
    const content=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(data);
    return json({digest:a.sha256,kind:'individual',version:a.version,content});
  }catch(error){return json({error:error instanceof Rejected?error.code:'agreement_asset_unavailable'},503);}
}
export function config(env:Env):LiveConfig {
  let c:LiveConfig;try{c=JSON.parse(env.CLA_CONFIG ?? '');}catch{throw new Rejected('configuration_required');}
  // A JSON config cannot silently turn capabilities on. Each binding is explicit and defaults false.
  return {...c,githubEnabled:env.CLA_GITHUB_ENABLED==='true',oauthEnabled:env.CLA_OAUTH_ENABLED==='true',
    signingEnabled:env.CLA_SIGNING_ENABLED==='true',checkPublishingEnabled:env.CLA_CHECK_PUBLISHING_ENABLED==='true',backgroundEnabled:env.CLA_BACKGROUND_ENABLED==='true',
    delegatedCountersigningEnabled:env.CLA_DELEGATED_COUNTERSIGNING_ENABLED==='true',automaticVerificationEnabled:env.CLA_AUTOMATIC_VERIFICATION_ENABLED==='true',
    automaticPublishingEnabled:env.CLA_AUTOMATIC_PUBLISHING_ENABLED==='true',agreementActivationEnabled:env.CLA_AGREEMENT_ACTIVATION_ENABLED==='true',
    confirmationSlotEnabled:env.CLA_CONFIRMATION_SLOT_ENABLED==='true',mergePrefixAssessmentEnabled:env.CLA_MERGE_PREFIX_ASSESSMENT_ENABLED==='true',mergePrefixReadSessionEnabled:env.CLA_MERGE_PREFIX_READ_SESSION_ENABLED==='true'};
}
export async function service(env:Env,budget?:InvocationBudget):Promise<LiveService> {
  const c=config(env),secrets:Secrets={clientSecret:env.CLA_CLIENT_SECRET ?? '',pkcs8:env.CLA_APP_PKCS8 ?? '',evidenceKey:env.CLA_EVIDENCE_KEY ?? ''};
  const key=await importAes(unbase64(secrets.evidenceKey));
  return new LiveService(c,new LiveStore(env.DB,budget,c),new GitHub(c,secrets,undefined,undefined,budget),key);
}
export default {async scheduled(controller:{cron?:string},env:Env):Promise<void> {
  if(controller.cron===AUTOMATIC_CRON) {
    if(env.CLA_AUTOMATIC_VERIFICATION_ENABLED==='true')await verifyHeadOne(await service(env,new InvocationBudget()));
    return;
  }
  if(env.CLA_BACKGROUND_ENABLED!=='true')return;
  await reconcileOne(await service(env,new InvocationBudget()));
},async fetch(req:Request,env:Env):Promise<Response> {
  try {
    const url=new URL(req.url),enabled=[env.CLA_GITHUB_ENABLED,env.CLA_OAUTH_ENABLED,env.CLA_SIGNING_ENABLED,env.CLA_CHECK_PUBLISHING_ENABLED,env.CLA_UI_ENABLED,env.CLA_CI_VERIFICATION_ENABLED].some(v=>v==='true');
    if (!enabled) return json({runtime:'workers-d1',enabled:false,signingEnabled:false,checkPublishingEnabled:false},503);
    const c=config(env);
    validateLive(c);
    if (url.origin!==c.origin) throw new Rejected('origin_rejected');
    if(req.method==='POST' && url.pathname==='/webhooks/github' && !url.search) {
      if(!c.automaticVerificationEnabled)throw new Rejected('automatic_verification_disabled');
      if(!env.CLA_WEBHOOK_LIMITER || req.headers.get('content-type')?.split(';')[0]!=='application/json')return json({error:'webhook_configuration_required'},503);
      try{if((await env.CLA_WEBHOOK_LIMITER.limit({key:'github-webhook'})).success!==true)return json({error:'webhook_busy'},429);}
      catch{return json({error:'webhook_abuse_control_unavailable'},503);}
      const reader=req.body?.getReader();if(!reader)throw new Rejected('webhook_rejected');const chunks:Uint8Array[]=[];let size=0;
      for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>262144){await reader.cancel();throw new Rejected('webhook_rejected');}chunks.push(value);}
      const raw=new Uint8Array(size);let at=0;for(const chunk of chunks){raw.set(chunk,at);at+=chunk.length;}
      await admitHead(await service(env,new InvocationBudget()),raw,req.headers.get('x-hub-signature-256') ?? '',req.headers.get('x-github-delivery') ?? '',req.headers.get('x-github-event') ?? '',env.CLA_WEBHOOK_SECRET ?? '');
      return json({accepted:true,publication:'not_attempted'},202);
    }
    if(env.CLA_UI_ENABLED==='true' && req.method==='GET') {
      const assets:Record<string,[string,string]>={'/':[uiHTML,'text/html; charset=utf-8'],'/ui.css':[uiCSS,'text/css; charset=utf-8'],'/ui.js':[uiScript(),'application/javascript; charset=utf-8']};
      const asset=assets[url.pathname];if(asset && (url.pathname==='/' || !url.search))return new Response(asset[0],{headers:{...headers,'content-type':asset[1],
        'content-security-policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"}});
    }
    if(req.method==='GET' && url.pathname==='/capabilities' && !url.search)return json({oauthEnabled:c.oauthEnabled,signingEnabled:c.signingEnabled,checkPublishingEnabled:c.checkPublishingEnabled});
    if (req.method==='GET' && url.pathname==='/health' && !url.search)
      return json({runtime:'workers-d1',signingEnabled:c.signingEnabled,checkPublishingEnabled:c.checkPublishingEnabled});
    if(req.method==='GET' && url.pathname==='/verification/group') {
      if(env.CLA_CI_VERIFICATION_ENABLED!=='true')throw new Rejected('ci_verification_disabled');
      if([...url.searchParams.keys()].sort().join(',')!=='attempt,base,head,run' || !/^[1-9][0-9]*$/.test(url.searchParams.get('run') ?? ''))throw new Rejected('verification_invalid');
      const head=url.searchParams.get('head')!,base=url.searchParams.get('base')!,run=Number(url.searchParams.get('run')),attempt=url.searchParams.get('attempt')!;
      if(!validSha(head) || !validSha(base) || !validId(run) || !/^[A-Za-z0-9_-]{43}$/.test(attempt))throw new Rejected('verification_invalid');
      if(!env.CLA_VERIFICATION_LIMITER)return json({error:'verification_abuse_control_required'},503);
      let admitted=false;try{admitted=(await env.CLA_VERIFICATION_LIMITER.limit({key:'verification-group'})).success===true;}catch{return json({error:'verification_abuse_control_unavailable'},503);}
      if(!admitted)return json({error:'verification_busy'},429,{'retry-after':'60'});
      return json(await verification(await service(env,new InvocationBudget()),{kind:'merge_group',headSha:head,baseSha:base},head,run,attempt));
    }
    if(req.method==='GET' && url.pathname==='/verification/pr') {
      if(env.CLA_CI_VERIFICATION_ENABLED!=='true')throw new Rejected('ci_verification_disabled');
      if([...url.searchParams.keys()].sort().join(',')!=='attempt,head,pr,run' || !/^[1-9][0-9]*$/.test(url.searchParams.get('pr') ?? '') ||
        !/^[1-9][0-9]*$/.test(url.searchParams.get('run') ?? ''))throw new Rejected('verification_invalid');
      const pr=Number(url.searchParams.get('pr')),run=Number(url.searchParams.get('run')),head=url.searchParams.get('head')!,attempt=url.searchParams.get('attempt')!;
      if(!validId(pr) || !validId(run) || !validSha(head) || !/^[A-Za-z0-9_-]{43}$/.test(attempt))throw new Rejected('verification_invalid');
      if(!env.CLA_VERIFICATION_LIMITER)return json({error:'verification_abuse_control_required'},503);
      let admitted=false;
      try{admitted=(await env.CLA_VERIFICATION_LIMITER.limit({key:'verification-pr'})).success===true;}
      catch{return json({error:'verification_abuse_control_unavailable'},503);}
      if(!admitted)return json({error:'verification_busy'},429,{'retry-after':'60'});
      return json(await verification(await service(env,new InvocationBudget()),pr,head,run,attempt));
    }
    if(req.method==='GET' && url.pathname==='/agreement' && !url.search)return publicAgreement(env,c);
    if(req.method==='POST' && url.pathname==='/oauth/start' && !url.search) {
      if(req.headers.get('origin')!==c.origin)throw new Rejected('origin_rejected');
      if(req.headers.get('content-type')?.split(';')[0]!=='application/json')throw new Rejected('form_rejected');
      if(!c.oauthEnabled)throw new Rejected('oauth_disabled');
      // A deployment-supplied edge binding must admit the request BEFORE body, key import, or D1.
      if(!env.CLA_OAUTH_LIMITER)return json({error:'oauth_abuse_control_required'},503);
      let admitted=false;
      try{admitted=(await env.CLA_OAUTH_LIMITER.limit({key:'oauth-start'})).success===true;}
      catch{return json({error:'oauth_abuse_control_unavailable'},503);}
      if(!admitted)return json({error:'oauth_busy'},429,{'retry-after':'60'});
      await body(req,[]);
      const flow=await (await service(env)).startOAuth(req.headers.get('origin'));
      return json({authorizationURL:flow.url},200,{'set-cookie':cookie('__Host-cla-oauth',flow.browser,300)});
    }
    const s=await service(env),jar=cookies(req),token=jar.get('__Host-cla-session') ?? '',csrf=req.headers.get('x-csrf-token') ?? '';
    if(req.method==='GET' && url.pathname==='/oauth/callback') {
      if(!validOAuthCallbackQuery(url)) throw new Rejected('oauth_rejected');
      const login=await s.completeOAuth(url.searchParams.get('state')!,jar.get('__Host-cla-oauth') ?? '',url.searchParams.get('code')!);
      const browser=req.headers.get('accept')?.includes('text/html');
      const response=browser?new Response(null,{status:303,headers:{...headers,location:'/'}}):json({csrf:login.csrf});response.headers.append('set-cookie',cookie('__Host-cla-session',login.token,900));
      response.headers.append('set-cookie',`__Host-cla-csrf=${login.csrf}; Path=/; Max-Age=900; Secure; SameSite=Strict`);
      response.headers.append('set-cookie',cookie('__Host-cla-oauth','',0));return response;
    }
    if(req.method==='GET' && url.pathname==='/session') {
      if(url.search && [...url.searchParams.keys()].join(',')!=='cursor')throw new Rejected('form_rejected');
      return json(await s.packets(token,url.searchParams.get('cursor') ?? undefined));
    }
    if(req.method==='GET' && url.pathname==='/status' && [...url.searchParams.keys()].join(',')==='packet')return json(await s.status(token,url.searchParams.get('packet')!));
    if(req.method==='GET' && url.pathname==='/audit' && [...url.searchParams.keys()].join(',')==='packet')
      return json(await s.audit(token,url.searchParams.get('packet')!),200,{'content-disposition':'attachment; filename="cla-private-audit.json"'});
    if(req.method==='GET' && url.pathname==='/agreement' && (!url.search || [...url.searchParams.keys()].join(',')==='packet'))return json(await s.document(token,url.searchParams.get('packet') ?? undefined));
    if(req.method==='GET' && url.pathname==='/evidence') {
      const query=[...url.searchParams.keys()];if(query.length!==2 || !query.includes('packet') || !query.includes('role')) throw new Rejected('form_rejected');
      return json(await s.readEvidence(token,url.searchParams.get('packet')!,url.searchParams.get('role')!));
    }
    if(req.method==='POST' && !url.search) {
      const origin=req.headers.get('origin');
      if(url.pathname==='/packets'){const b=await body(req,['kind']);return json(await s.packet(token,csrf,origin,b.kind));}
      if(url.pathname==='/intents'){const b=await body(req,['packet','role']);return json({intent:await s.intent(token,csrf,origin,b.packet,b.role)});}
      if(url.pathname==='/sign'){const b=await body(req,['packet','intent','role','evidence']);return json(await s.sign(token,csrf,origin,b.packet,b.intent,b.role,b.evidence));}
      if(url.pathname==='/hold'){const b=await body(req,['packet','intent']);return json(await s.hold(token,csrf,origin,b.packet,b.intent));}
      if(url.pathname==='/checks/pr'){
        await s.authorize(token,csrf,origin);const session=await s.session(token);
        if(session.actor!==c.holderId)throw new Rejected('holder_required');
        const b=await body(req,['pr']);return json(await s.check(b.pr));
      }
    }
    return json({error:'route_unavailable'},404);
  } catch(error) {
    // Never log upstream responses, OAuth URLs/codes, request data, cookies, evidence or secrets.
    if(error instanceof Rejected && error.code==='oauth_busy')return json({error:error.code},429,{'retry-after':'300'});
    return json({error:error instanceof Rejected ? error.code : 'request_unavailable'},error instanceof Rejected ? 403 : 503);
  }
}};
