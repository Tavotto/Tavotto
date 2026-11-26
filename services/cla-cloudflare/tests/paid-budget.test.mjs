import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {isolated} from './prefix-read-session-fixture.mjs';
import {FREE_BUDGET,PAID_EXTERNAL} from '../src/native/budget.ts';
import {ciBudget} from '../src/native/worker.ts';

const prod=name=>JSON.parse(fs.readFileSync(new URL('../deployment/production/'+name,import.meta.url),'utf8'));

// The Free profile is the default everywhere; the paid ceilings are an explicit, finite opt-in for the CI read routes.
test('paid budget: Free numbers stay the default and only the external ceilings differ',()=>{
  assert.deepEqual({e:FREE_BUDGET.external,w:FREE_BUDGET.externalWork,s:FREE_BUDGET.sql,sw:FREE_BUDGET.sqlWork},{e:40,w:35,s:40,sw:20});
  assert.deepEqual({...PAID_EXTERNAL},{external:500,externalWork:480});
  const drain=b=>{let n=0;try{for(;;){b.externalSignal();n++;}}catch(e){assert.equal(e.code,'budget_deferred');}return n;};
  const counts=[{},{CLA_WORKERS_PAID_BUDGET:'false'},{CLA_WORKERS_PAID_BUDGET:'TRUE'},{CLA_WORKERS_PAID_BUDGET:'true'}].map(env=>drain(ciBudget(env)));
  assert.deepEqual(counts,[35,35,35,480],'finite in both profiles: the drain stops with budget_deferred');
});
test('paid budget: a prefix that Free refuses at 35 completes, with the same answer shape',()=>isolated(async f=>{
  const result=await f.assess();
  assert.equal(result.eligible,true);assert.equal(result.protocol,'cla-merge-prefix-assessment-v1');assert.equal(result.publication,'not_attempted');
  assert.ok(f.calls.length>35&&f.calls.length<=480);
},{prefix:8,paid:true}));
test('paid budget: SQL ceilings do not follow the plan',()=>isolated(async f=>{
  await f.assess();assert.ok(f.budget.snapshot().sql<=FREE_BUDGET.sqlWork);
},{prefix:8,paid:true}));
test('paid budget: production manifest pins a platform backstop above the application cap and keeps every flag off',()=>{
  const m=prod('wrangler.production.json');
  assert.equal(m.limits.subrequests,1000);assert.ok(m.limits.subrequests>PAID_EXTERNAL.external);
  assert.equal(m.vars.CLA_WORKERS_PAID_BUDGET,undefined,'the profile is a deploy-time --var like every other flag');
  assert.match(fs.readFileSync(new URL('../deployment/production/README.md',import.meta.url),'utf8'),/CLA_WORKERS_PAID_BUDGET:true/);
});
