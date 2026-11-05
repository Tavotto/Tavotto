import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {rejectionCode} from '../src/native/background.ts';
import {Rejected} from '../src/core/types.ts';
test('scheduled diagnostics expose only fixed rejection codes',()=>{
 assert.equal(rejectionCode(new Rejected('merge_prefix_changed')),'merge_prefix_changed');
 for(const e of [new Error('token ghs_SECRET leaked'),new Rejected('Bearer ghs_x'),new Rejected('a'.repeat(65)),'merge_prefix_changed',undefined])assert.equal(rejectionCode(e),'unexpected');
});
test('scheduled handler logs one line per run and staging enables Workers Logs',()=>{
 const src=readFileSync(new URL('../src/native/worker.ts',import.meta.url),'utf8');
 assert.equal((src.match(/console\.log\(/g)||[]).length,4);assert.ok(!/console\.(error|warn|info|debug)/.test(src));
 const w=JSON.parse(readFileSync(new URL('../deployment/staging/wrangler.staging.json',import.meta.url),'utf8'));
 assert.deepEqual(w.observability,{enabled:true,head_sampling_rate:1});
});
