import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import worker,{loggedRun} from '../src/native/worker.ts';
import {AUTOMATIC_CRON} from '../src/native/automatic.ts';
import {rejectionCode} from '../src/native/background.ts';
import {Rejected} from '../src/core/types.ts';

const SECRET='ghs_SYNTHETIC_TOKEN_must_never_be_logged';
async function captured(fn){
 const lines=[],original=console.log;console.log=(...a)=>lines.push(a.join(' '));
 try{return {lines,error:await fn().then(()=>undefined,e=>e)};}finally{console.log=original;}
}

test('rejection codes are a closed vocabulary',()=>{
 assert.equal(rejectionCode(new Rejected('merge_prefix_changed')),'merge_prefix_changed');
 for(const e of [new Error('token '+SECRET),new Rejected('Bearer '+SECRET),new Rejected('a'.repeat(65)),'merge_prefix_changed',undefined])assert.equal(rejectionCode(e),'unexpected');
});

test('a finished run logs exactly one line with disposition, budget and fixed reason',async()=>{
 const lines=[];
 await loggedRun('reconcile',async()=>({disposition:'target_retry_pending_or_exhausted',reason:'group_resource_capacity_exhausted',budget:{sql:9,external:35,extra:SECRET}}),l=>lines.push(l));
 assert.deepEqual(lines.map(l=>JSON.parse(l)),[{run:'reconcile',disposition:'target_retry_pending_or_exhausted',budget:{sql:9,external:35},reason:'group_resource_capacity_exhausted'}]);
 lines.length=0;await loggedRun('verify_head',async()=>'idle',l=>lines.push(l));
 assert.deepEqual(lines.map(l=>JSON.parse(l)),[{run:'verify_head',disposition:'idle'}]);
 lines.length=0;await loggedRun('reconcile',async()=>({disposition:'x',reason:'Bearer '+SECRET}),l=>lines.push(l));
 assert.equal(JSON.parse(lines[0]).reason,'unexpected');assert.ok(!lines[0].includes(SECRET));
});

test('a thrown run logs only the code and rethrows the same error',async()=>{
 for(const [error,reason] of [[new Rejected('github_unavailable'),'github_unavailable'],[new Error('fetch '+SECRET),'unexpected'],[new TypeError(SECRET),'unexpected']]){
  const lines=[];
  await assert.rejects(loggedRun('reconcile',async()=>{throw error;},l=>lines.push(l)),e=>e===error);
  assert.deepEqual(lines.map(l=>JSON.parse(l)),[{run:'reconcile',disposition:'thrown',reason}]);
  assert.ok(!lines[0].includes(SECRET));
 }
});

test('the scheduled handler itself emits one line per enabled run and none when disabled',async()=>{
 // No CLA_CONFIG: service() rejects with configuration_required before any I/O.
 let r=await captured(()=>worker.scheduled({cron:'*/2 * * * *'},{CLA_BACKGROUND_ENABLED:'true'}));
 assert.equal(r.error?.code,'configuration_required');
 assert.deepEqual(r.lines.map(l=>JSON.parse(l)),[{run:'reconcile',disposition:'thrown',reason:'configuration_required'}]);
 r=await captured(()=>worker.scheduled({cron:AUTOMATIC_CRON},{CLA_AUTOMATIC_VERIFICATION_ENABLED:'true'}));
 assert.equal(r.error?.code,'configuration_required');
 assert.deepEqual(r.lines.map(l=>JSON.parse(l)),[{run:'verify_head',disposition:'thrown',reason:'configuration_required'}]);
 for(const [cron,env] of [['*/2 * * * *',{}],[AUTOMATIC_CRON,{CLA_BACKGROUND_ENABLED:'true'}]]){
  r=await captured(()=>worker.scheduled({cron},env));assert.equal(r.error,undefined);assert.deepEqual(r.lines,[]);
 }
});

test('staging enables Workers Logs',()=>{
 const w=JSON.parse(readFileSync(new URL('../deployment/staging/wrangler.staging.json',import.meta.url),'utf8'));
 assert.deepEqual(w.observability,{enabled:true,head_sampling_rate:1});
});
