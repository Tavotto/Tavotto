// OFFLINE DATA VALIDATION ONLY. No Wrangler execution, network, D1 or secret writes.
import fs from 'node:fs';import {fileURLToPath} from 'node:url';import {createHash} from 'node:crypto';
const flags=['CLA_GITHUB_ENABLED','CLA_OAUTH_ENABLED','CLA_SIGNING_ENABLED','CLA_CHECK_PUBLISHING_ENABLED','CLA_UI_ENABLED','CLA_BACKGROUND_ENABLED','CLA_CI_VERIFICATION_ENABLED','CLA_DELEGATED_COUNTERSIGNING_ENABLED','CLA_AUTOMATIC_VERIFICATION_ENABLED','CLA_AUTOMATIC_PUBLISHING_ENABLED','CLA_AGREEMENT_ACTIVATION_ENABLED','CLA_CONFIRMATION_SLOT_ENABLED'];
export function deploymentPlan(c){
  if(!c || c.workers_dev!==false || c.preview_urls!==false || !Array.isArray(c.routes) || c.routes.length || !Array.isArray(c.triggers?.crons) || c.triggers.crons.length ||
    flags.some(flag=>c.vars?.[flag]!=='false') || Object.keys(c.vars??{}).some(key=>!flags.includes(key)&&key!=='CLA_CONFIG'))throw Error('disabled_manifest_required');
  if(c.d1_databases?.length!==1 || c.d1_databases[0].binding!=='DB' || !c.d1_databases[0].database_id ||
    c.assets?.binding!=='CLA_PUBLIC_AGREEMENT' || c.assets.run_worker_first!==true)throw Error('explicit_existing_bindings_required');
  const names=['CLA_OAUTH_LIMITER','CLA_VERIFICATION_LIMITER','CLA_WEBHOOK_LIMITER'];
  if(c.ratelimits?.length!==3 || names.some(name=>c.ratelimits.filter(row=>row.name===name).length!==1) ||
    new Set(c.ratelimits.map(row=>row.namespace_id)).size!==3 || c.ratelimits.some(row=>!row.namespace_id || row.simple?.period!==60 || !Number.isSafeInteger(row.simple.limit) || row.simple.limit<1))throw Error('distinct_limiter_bindings_required');
  return {protocol:'cla-offline-deployment-plan-v1',ready_to_deploy:false,network_operations:0,resources_created:0,
    manifest_sha256:createHash('sha256').update(JSON.stringify(c)).digest('hex'),
    required_secret_names:['CLA_EVIDENCE_KEY','CLA_CLIENT_SECRET','CLA_APP_PKCS8','CLA_WEBHOOK_SECRET'],
    proposed_separate_crons:{recovery:'*/2 * * * *',head_observation_or_dispatch:'7-59/15 * * * *'},
    pending:['independently review existing resource IDs, limits, plan quota and origin','register actual holder identity and public key outside this tool','obtain real limited grant and enable control outside this tool',
      'validate reviewed config, schema and exact pinned agreement asset','verify D1 batch/backup/single-authority migration','complete independent live acceptance including excluded publication scenario','separate trusted-main CI/merge-group rollout approval']};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  try{if(process.argv.length!==3)throw Error('usage: node scripts/deployment-plan.mjs deployment/wrangler.disabled.json');
    console.log(JSON.stringify(deploymentPlan(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
