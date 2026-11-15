// OFFLINE DATA VALIDATION ONLY. No Wrangler execution, network, D1 or secret writes.
import fs from 'node:fs';import {fileURLToPath} from 'node:url';import {createHash} from 'node:crypto';
const flags=['CLA_GITHUB_ENABLED','CLA_OAUTH_ENABLED','CLA_SIGNING_ENABLED','CLA_CHECK_PUBLISHING_ENABLED','CLA_UI_ENABLED','CLA_BACKGROUND_ENABLED','CLA_CI_VERIFICATION_ENABLED','CLA_DELEGATED_COUNTERSIGNING_ENABLED','CLA_AUTOMATIC_VERIFICATION_ENABLED','CLA_AUTOMATIC_PUBLISHING_ENABLED','CLA_AGREEMENT_ACTIVATION_ENABLED','CLA_CONFIRMATION_SLOT_ENABLED'];
// Staging and production are the only profiles that may carry a route; only staging may
// carry crons. Capability flags stay false in the file; each deploy turns them on with --var.
export const STAGING_ORIGIN='https://cla-staging.tavotto.com';
// Production runs the live-eligibility protocol only (ADR 0172): CI asks the service
// directly, so there is no check publishing and no scheduled work — zero crons.
export const PRODUCTION_ORIGIN='https://cla.tavotto.com';
const STAGING_CRONS=['*/2 * * * *','7-59/15 * * * *'];
function stagingShape(c){
  const host=new URL(STAGING_ORIGIN).host;
  if(!c || typeof c.name!=='string' || !/^tavotto-cla-staging$/.test(c.name) || !Array.isArray(c.routes) || c.routes.length!==1 ||
    canonicalJSON(c.routes[0])!==canonicalJSON({custom_domain:true,pattern:host}) || !Array.isArray(c.triggers?.crons) ||
    c.triggers.crons.some(cron=>!STAGING_CRONS.includes(cron)) || new Set(c.triggers.crons).size!==c.triggers.crons.length)throw Error('staging_route_or_cron_invalid');
  let config;try{config=JSON.parse(c.vars?.CLA_CONFIG);}catch{throw Error('staging_config_invalid');}
  if(config?.origin!==STAGING_ORIGIN)throw Error('staging_origin_mismatch');
  return {...c,routes:[],triggers:{crons:[]}};
}
function productionShape(c){
  const host=new URL(PRODUCTION_ORIGIN).host;
  if(!c || c.name!=='tavotto-cla' || !Array.isArray(c.routes) || c.routes.length!==1 ||
    canonicalJSON(c.routes[0])!==canonicalJSON({custom_domain:true,pattern:host}) || !Array.isArray(c.triggers?.crons) ||
    c.triggers.crons.length!==0)throw Error('production_route_or_cron_invalid');
  let config;try{config=JSON.parse(c.vars?.CLA_CONFIG);}catch{throw Error('production_config_invalid');}
  if(config?.origin!==PRODUCTION_ORIGIN)throw Error('production_origin_mismatch');
  if(c.d1_databases?.[0]?.database_name!=='tavotto-cla')throw Error('production_database_invalid');
  return {...c,routes:[]};
}
const canonicalJSON=v=>JSON.stringify(v,Object.keys(v??{}).sort());
export function deploymentPlan(input,{profile='disabled'}={}){
  if(!['disabled','staging','production'].includes(profile))throw Error('profile_invalid');
  const c=profile==='staging'?stagingShape(input):profile==='production'?productionShape(input):input;
  // The signing page serves client.toString() as /ui.js. Wrangler's default keep_names wraps
  // functions in an __name() helper that is not shipped with that string, so the page's script
  // throws on load and no button works.
  if(c?.keep_names!==false)throw Error('keep_names_false_required');
  if(!c || c.workers_dev!==false || c.preview_urls!==false || !Array.isArray(c.routes) || c.routes.length || !Array.isArray(c.triggers?.crons) || c.triggers.crons.length ||
    flags.some(flag=>c.vars?.[flag]!=='false') || Object.keys(c.vars??{}).some(key=>!flags.includes(key)&&key!=='CLA_CONFIG'))throw Error('disabled_manifest_required');
  if(c.d1_databases?.length!==1 || c.d1_databases[0].binding!=='DB' || !c.d1_databases[0].database_id ||
    c.assets?.binding!=='CLA_PUBLIC_AGREEMENT' || c.assets.run_worker_first!==true)throw Error('explicit_existing_bindings_required');
  const names=['CLA_OAUTH_LIMITER','CLA_VERIFICATION_LIMITER','CLA_WEBHOOK_LIMITER'];
  if(c.ratelimits?.length!==3 || names.some(name=>c.ratelimits.filter(row=>row.name===name).length!==1) ||
    new Set(c.ratelimits.map(row=>row.namespace_id)).size!==3 || c.ratelimits.some(row=>!row.namespace_id || row.simple?.period!==60 || !Number.isSafeInteger(row.simple.limit) || row.simple.limit<1))throw Error('distinct_limiter_bindings_required');
  return {protocol:'cla-offline-deployment-plan-v1',profile,ready_to_deploy:false,network_operations:0,resources_created:0,
    manifest_sha256:createHash('sha256').update(JSON.stringify(input)).digest('hex'),
    // Production never enables the webhook route (CLA_AUTOMATIC_VERIFICATION_ENABLED stays off), so it has no webhook secret.
    required_secret_names:profile==='production'?['CLA_EVIDENCE_KEY','CLA_CLIENT_SECRET','CLA_APP_PKCS8']:['CLA_EVIDENCE_KEY','CLA_CLIENT_SECRET','CLA_APP_PKCS8','CLA_WEBHOOK_SECRET'],
    proposed_separate_crons:profile==='production'?{}:{recovery:'*/2 * * * *',head_observation_or_dispatch:'7-59/15 * * * *'},
    pending:['independently review existing resource IDs, limits, plan quota and origin','register actual holder identity and public key outside this tool','obtain real limited grant and enable control outside this tool',
      'validate reviewed config, schema and exact pinned agreement asset','verify D1 batch/backup/single-authority migration','complete independent live acceptance including excluded publication scenario','separate trusted-main CI/merge-group rollout approval']};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  try{const args=process.argv.slice(2),flag=['--staging','--production'].includes(args[0])?args.shift():null;
    if(args.length!==1)throw Error('usage: node scripts/deployment-plan.mjs [--staging|--production] MANIFEST.json');
    console.log(JSON.stringify(deploymentPlan(JSON.parse(fs.readFileSync(args[0],'utf8')),{profile:flag?flag.slice(2):'disabled'}),null,2));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
