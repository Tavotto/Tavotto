import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import {spawnSync} from 'node:child_process';
import {deploymentPlan} from '../scripts/deployment-plan.mjs';
const template=JSON.parse(fs.readFileSync(new URL('../deployment/wrangler.disabled.json',import.meta.url)));
test('deployment template remains disabled with explicit placeholders and no auto-provisioning',()=>{
  const result=deploymentPlan(template);assert.equal(result.ready_to_deploy,false);assert.equal(result.network_operations,0);assert.equal(result.resources_created,0);
  assert.match(template.d1_databases[0].database_id,/REPLACE/);assert.equal(result.required_secret_names.length,4);
  for(const value of Object.values(template.vars))assert.ok(value==='false'||value==='REPLACE_WITH_REVIEWED_CONFIG_JSON');
});
for(const change of [c=>c.vars.CLA_AUTOMATIC_PUBLISHING_ENABLED='true',c=>c.routes.push('real.example/*'),c=>c.triggers.crons.push('* * * * *'),c=>c.workers_dev=true,c=>c.preview_urls=true,c=>c.vars.CLA_EVIDENCE_KEY='FORBIDDEN_TEST_VALUE',c=>delete c.d1_databases[0].database_id,c=>c.ratelimits[1].namespace_id=c.ratelimits[0].namespace_id])
  test('offline deployment checker rejects active routes credentials or incomplete bindings '+String(change),()=>{const c=structuredClone(template);change(c);assert.throws(()=>deploymentPlan(c));});
test('offline deployment CLI executes and prints only the disabled plan',()=>{
  const r=spawnSync(process.execPath,['scripts/deployment-plan.mjs','deployment/wrangler.disabled.json'],{cwd:new URL('../',import.meta.url),encoding:'utf8'});
  assert.equal(r.status,0);assert.equal(JSON.parse(r.stdout).ready_to_deploy,false);assert.ok(!r.stdout.includes('FORBIDDEN_TEST_VALUE'));
});
