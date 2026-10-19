import test from 'node:test';import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';
import {LiveService} from '../src/native/service.ts';import {LiveStore} from '../src/native/store.ts';import {InvocationBudget,qualificationSQL} from '../src/native/budget.ts';
import {sha256,randomToken} from '../src/core/crypto.ts';
globalThis.fetch=async()=>{throw Error('INDEPENDENT_OUTBOUND_FORBIDDEN');};
const metered=f=>{const budget=new InvocationBudget();return {budget,s:new LiveService(f.c,new LiveStore(f.db,budget,f.c),f.gh,f.key)};};
test('independent optimized fallback uses three SELECTs and original cryptographic evidence',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const {budget,s}=metered(f);assert.equal((await s.evaluate(9,3)).conclusion,'success');assert.equal(budget.snapshot().sql,6);assert.equal(qualificationSQL(f.c),3);
}));
test('independent two-person optimized fallback uses nine total reads and each real consent',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);
 const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:3,login:'other',csrf_hash:await sha256(csrf),expires:f.now()+86400});
 const args=[token,csrf,f.c.origin],q=await f.s.packet(...args,'individual');await f.s.sign(...args,q.id,await f.s.intent(...args,q.id,'contributor'),'contributor',f.evidence('contributor'));
 f.gh.extra=[{sha:'f'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic second person'}];
 const {budget,s}=metered(f);assert.equal((await s.evaluate(9,3)).conclusion,'success');assert.equal(budget.snapshot().sql,9);
}));
test('independent personal holder route remains usable after delegated revocation',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);await f.sign(p,'holder');await f.s.delegation.append(await f.control('revoke'));
 const {budget,s}=metered(f);assert.equal((await s.evaluate(9)).conclusion,'success');assert.equal(budget.snapshot().sql,4);
}));
test('independent hold between candidate snapshot and evidence verification fails closed',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const {s}=metered(f),original=s.store.delegatedCandidate.bind(s.store);let raced=false;
 s.store.delegatedCandidate=async(...args)=>{const candidate=await original(...args);if(!raced){raced=true;await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));}return candidate;};
 await assert.rejects(s.evaluate(9));assert.equal(raced,true);
}));
test('independent optimized chain preserves 100 signed events and refuses a 101st raw row',()=>iso(async f=>{
 await f.install();for(let n=2;n<=100;n++)await f.s.delegation.append(await f.control('enable'));
 const p=await f.create();await f.sign(p);const {budget,s}=metered(f);assert.equal((await s.evaluate(9)).conclusion,'success');assert.equal(budget.snapshot().sql,6);
 const record=await f.control('enable',{sequence:101}),body=JSON.parse(record.body);f.sql.exec('PRAGMA ignore_check_constraints=ON');
 await f.store.stmt('INSERT INTO delegated_controls VALUES(?,?,?,?,?,?,?)',f.grant.id,101,record.body,record.signature,await sha256(record.body),body.action,body.issued_at).run();
 await assert.rejects(f.s.evaluate(9),e=>e.code==='authorization_history_limit');
}));
test('independent control corruption in combined JSON snapshot cannot qualify',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_controls_update');
 await f.store.stmt('UPDATE delegated_controls SET signature=?','A'.repeat(86)).run();await assert.rejects(f.s.evaluate(9));
}));

import {reconcileOne} from '../src/native/background.ts';
async function seedPublication(f,phase,conclusion){
 const check=await f.s.evaluate(9),target=f.store.targetKey(f.c.repositoryId,{kind:'pr',number:9}),id=randomToken();
 await f.store.track(f.c.repositoryId,{kind:'pr',number:9});
 await f.store.stmt(`INSERT INTO publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text) VALUES(?,?,?,?,?,?,101,?,?,?,?)`,id,target,check.headSha,check.proof.revision,check.proof.expires_at,phase,f.now(),f.now(),conclusion,check.text).run();
 await f.store.enqueue(target);return {id,target,check};
}
test('independent one-person same-head dispatch fits work budget and preserves uncertain send journal',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const previous=await seedPublication(f,'resolved','failure');
 await f.store.stmt("INSERT INTO refresh_requests VALUES(?,?,?,'pending',0,?,'independent fixed-time failure')",previous.target,previous.id,previous.check.proof.revision,f.now()).run();
 f.c.githubEnabled=true;f.c.backgroundEnabled=true;f.c.checkPublishingEnabled=true;
 const {budget,s}=metered(f);f.gh.budget=budget;let reached=false,work;
 f.gh.publishFresh=async(check,source,beforeSend)=>{reached=true;await beforeSend();work=budget.snapshot().sql;throw Error('SYNTHETIC_FAILURE_NO_HTTP_SENT');};
 const result=await reconcileOne(s);assert.equal(result.disposition,'fresh_publication_unknown_no_replay');assert.equal(reached,true);assert.equal(work,16);assert.equal(budget.snapshot().sql,20);
 const journal=await f.store.stmt('SELECT phase,proof_text FROM publication_attempts WHERE id!=?',previous.id).first();assert.equal(journal.phase,'unknown');assert.equal(JSON.parse(journal.proof_text.split('\n')[1]).head_sha,f.gh.head);
}));
test('independent one-person same-head read-only confirmation uses seventeen work queries',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const previous=await seedPublication(f,'reconcile','success');
 f.c.githubEnabled=true;f.c.backgroundEnabled=true;const {budget,s}=metered(f);f.gh.budget=budget;f.gh.observe=async()=>({id:101,conclusion:'success'});
 const work=[],original=budget.settlement.bind(budget);budget.settlement=()=>{work.push(budget.snapshot().sql);original();};
 const result=await reconcileOne(s);assert.equal(result.disposition,'verified_read_only');assert.equal(work[0],17);assert.ok(budget.snapshot().sql<=40);
 assert.equal((await f.store.stmt('SELECT phase FROM publication_attempts WHERE id=?',previous.id).first()).phase,'confirmed');
}));

test('independent two-person same-head refresh stops before journal or send at unchanged ceiling',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const token=randomToken(),csrf=randomToken();
 await f.store.addSession({token_hash:await sha256(token),actor:3,login:'other',csrf_hash:await sha256(csrf),expires:f.now()+86400});
 const args=[token,csrf,f.c.origin],q=await f.s.packet(...args,'individual');await f.s.sign(...args,q.id,await f.s.intent(...args,q.id,'contributor'),'contributor',f.evidence('contributor'));
 f.gh.extra=[{sha:'f'.repeat(40),author:{id:3,login:'other',type:'User'},email:'',message:'synthetic second person'}];
 const previous=await seedPublication(f,'resolved','failure');await f.store.stmt("INSERT INTO refresh_requests VALUES(?,?,?,'pending',0,?,'independent two-person rejection')",previous.target,previous.id,previous.check.proof.revision,f.now()).run();
 f.c.githubEnabled=true;f.c.backgroundEnabled=true;f.c.checkPublishingEnabled=true;const {budget,s}=metered(f);f.gh.budget=budget;let sent=false;
 f.gh.publishFresh=async()=>{sent=true;throw Error('MUST_NOT_ATTEMPT_SEND');};
 const result=await reconcileOne(s);assert.equal(result.disposition,'refresh_retry_pending_or_manual');assert.equal(sent,false);assert.ok(budget.snapshot().sql<=40);
 assert.equal((await f.store.stmt('SELECT count(*) n FROM publication_attempts').first()).n,1);
}));
