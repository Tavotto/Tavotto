import test from 'node:test';import assert from 'node:assert/strict';
import {iso,fixture} from './delegation-fixture.mjs';
import {randomToken,sha256} from '../src/core/crypto.ts';
import {LiveStore} from '../src/native/store.ts';import {LiveService} from '../src/native/service.ts';
import {InvocationBudget} from '../src/native/budget.ts';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const snapshot=f=>Object.fromEntries(['state','delegated_grants','delegated_controls','delegated_receipts','packets','signatures','agreement_documents','outbox','head_requests'].map(t=>[t,f.sql.prepare('SELECT * FROM cla_native_v2_'+t+' ORDER BY rowid').all().map(r=>({...r}))]));
const rotate=f=>{
  const c={...structuredClone(f.c),holderAuthorizationId:randomToken(),holderAuthorizationKey:randomToken(),delegatedCountersigningEnabled:false,signingEnabled:false};
  return new LiveService(c,new LiveStore(f.db,undefined,c),f.gh,f.key);
};
async function deniedUnchanged(f,fn,code){const before=snapshot(f);await assert.rejects(fn,code?e=>e.code===code:undefined);assert.deepEqual(snapshot(f),before);}
async function rewriteGrant(f,change){
  const grant={...f.grant,...change},record=await f.signed(grant);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_grants_update');
  await f.store.stmt('UPDATE delegated_grants SET body=?,signature=?,hash=?',record.body,record.signature,await sha256(record.body)).run();
  return grant;
}
test('historical revoke uses original registered key after ID and key rotation with production readiness',()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);const s=rotate(f),before=snapshot(f),revision=await f.store.revision();
  const revoke=await f.control('revoke');await assert.rejects(s.delegation.append(revoke));
  await s.delegation.appendHistoricalRevocation(f.grant.id,revoke);
  assert.equal(await f.store.revision(),revision+1);const after=snapshot(f);
  assert.deepEqual(after.delegated_grants,before.delegated_grants);assert.deepEqual(after.delegated_controls.slice(0,-1),before.delegated_controls);
  for(const table of ['delegated_receipts','packets','signatures','agreement_documents'])assert.deepEqual(after[table],before[table]);
  assert.equal(after.delegated_controls.at(-1).action,'revoke');
  await assert.rejects(f.s.delegation.qualified(p,(await f.store.signatures(p.id))[0],f.now()));
  const a=await s.audit(f.args('contributor')[0],p.id);assert.equal(a.delegated.current,false);assert.equal(a.delegated.state,'revoked');
  await f.sign(p,'holder');assert.equal((await f.s.evaluate(9)).conclusion,'success');
}));
for(const mode of ['enabled-rotated','disabled-rotated','held-rotated','expired-current','expired-disabled-rotated'])
test('historical terminal revocation remains visible in current caller audit '+mode,()=>iso(async f=>{
  await f.install();const p=await f.create();await f.sign(p);
  const s=mode==='expired-current'?f.s:rotate(f);
  if(mode==='enabled-rotated')Object.assign(s.c,{signingEnabled:true,delegatedCountersigningEnabled:true});
  if(mode==='held-rotated')await f.s.hold(...f.args('contributor'),p.id,await f.s.intent(...f.args('contributor'),p.id,'hold'));
  await s.delegation.appendHistoricalRevocation(f.grant.id,await f.control('revoke'));
  if(mode.startsWith('expired-'))f.advance(f.grant.expires_at-f.now());
  const a=await s.audit(f.args('contributor')[0],p.id);
  assert.equal(a.delegated.state,'revoked');assert.equal(a.delegated.current,false);
  assert.equal(JSON.parse(a.delegated.controls.at(-1).body).action,'revoke');
  if(mode==='held-rotated')assert.equal((await s.status(f.args('contributor')[0],p.id)).blocked,true);
}));
for(const action of ['enable','suspend'])test('historical path refuses nonterminal '+action,()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control(action);
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_terminal_revocation_required');
}));
for(const mode of ['expired','not-started','disabled'])test('historical revoke remains possible when '+mode,()=>iso(async f=>{
  if(mode==='expired')f.grant.expires_at=f.now()+1;
  if(mode==='not-started')f.grant.not_before=f.now()+10;
  await f.install();if(mode==='expired')f.advance(1);
  const s=rotate(f);await s.delegation.appendHistoricalRevocation(f.grant.id,await f.control('revoke'));
  assert.equal((await f.store.stmt('SELECT action FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first()).action,'revoke');
}));
test('historical terminal revoke rejects replay and all later controls without revision change',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke');await s.delegation.appendHistoricalRevocation(f.grant.id,record);
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record));
  for(const action of ['revoke','enable','suspend']){
    const next=await f.control(action);await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,next));
    await deniedUnchanged(f,()=>f.s.delegation.append(next));
  }
}));
test('historical revoke concurrency consumes one predecessor and increments revision once',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke'),revision=await f.store.revision();
  const result=await Promise.allSettled(Array.from({length:8},()=>s.delegation.appendHistoricalRevocation(f.grant.id,record)));
  assert.equal(result.filter(r=>r.status==='fulfilled').length,1);assert.equal(await f.store.revision(),revision+1);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_controls').first()).n,2);
}));
for(const id of ['',"' OR 1=1 --",'x'.repeat(44),randomToken()])test('historical revoke rejects unknown or malformed id '+JSON.stringify(id),()=>iso(async f=>{
  await f.install();const record=await f.control('revoke');await deniedUnchanged(f,()=>rotate(f).delegation.appendHistoricalRevocation(id,record),'authorization_missing');
}));
for(const changes of [{sequence:3},{previous_sha256:'0'.repeat(64)},{issued_at:1800000001},{issued_at:1799999999},{authorization_id:randomToken()},{grant_sha256:'0'.repeat(64)}])
  test('historical revoke rejects control binding '+JSON.stringify(changes),()=>iso(async f=>{
    await f.install();const s=rotate(f),record=await f.control('revoke',changes);
    await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record));
  }));
test('historical revoke rejects nonmonotonic control time after a later existing control',()=>iso(async f=>{
  await f.install();f.advance(2);await f.s.delegation.append(await f.control('suspend'));
  const record=await f.control('revoke',{issued_at:f.now()-1});await deniedUnchanged(f,()=>rotate(f).delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_control_order');
}));
test('historical revoke rejects a new control signed by another synthetic key',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke');
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,{...record,signature:'A'.repeat(86)}),'authorization_signature_invalid');
}));
test('historical revoke refuses the current rotated key signing an old grant control',()=>iso(async f=>{
  const other=await fixture();try{
    await f.install();const s=rotate(f);s.c.holderAuthorizationKey=other.c.holderAuthorizationKey;
    const record=await other.signed(JSON.parse((await f.control('revoke')).body));
    await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_signature_invalid');
  }finally{other.close();}
}));
for(const field of ['signature','public_key','hash'])test('historical revoke rejects tampered immutable registration '+field,()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke');f.sql.exec('DROP TRIGGER cla_native_v2_delegated_grants_update');
  await f.store.stmt('UPDATE delegated_grants SET '+field+'=?',field==='signature'?'A'.repeat(86):field==='hash'?'0'.repeat(64):randomToken()).run();
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record));
}));
for(const changes of [{agreement_version:'2.0'},{agreement_sha256:'0'.repeat(64)}])test('historical revoke requires exact historical agreement tuple '+JSON.stringify(changes),()=>iso(async f=>{
  await f.install();await rewriteGrant(f,changes);const record=await f.control('revoke');
  await deniedUnchanged(f,()=>rotate(f).delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_historical_agreement_missing');
}));
for(const changes of [{holderName:'OTHER SYNTHETIC HOLDER'},{repository:'other/synthetic'},{repositoryId:708}])test('historical revoke retains current identity boundary '+JSON.stringify(changes),()=>iso(async f=>{
  await f.install();const s=rotate(f);Object.assign(s.c,changes);const record=await f.control('revoke');
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_scope_invalid');
}));
test('historical revoke rejects mismatched production database readiness',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke');s.c.holderId=3;
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'database_configuration_uninitialized_or_mismatched');
}));
test('historical registry unit fixture can revoke its old exact tuple without accepting it as current',()=>iso(async f=>{
  // Deliberately unpinned fixture store: this tests historical tuple validation,
  // not production activation (which still rejects a third agreement row).
  f.grant.agreement_version='0.9';f.grant.agreement_sha256=await sha256('SYNTHETIC HISTORICAL TEXT');
  await f.store.stmt("INSERT INTO agreements VALUES('individual','0.9',?)",f.grant.agreement_sha256).run();
  const grant=await f.signed(f.grant);await f.store.stmt('INSERT INTO delegated_grants VALUES(?,?,?,?,?)',f.grant.id,grant.body,grant.signature,await sha256(grant.body),f.c.holderAuthorizationKey).run();
  const control=await f.control('enable');await f.store.stmt('INSERT INTO delegated_controls VALUES(?,?,?,?,?,?,?)',f.grant.id,1,control.body,control.signature,await sha256(control.body),'enable',f.now()).run();
  const revoke=await f.control('revoke');await assert.rejects(f.s.delegation.append(revoke),e=>e.code==='authorization_scope_invalid');
  await assert.rejects(new LiveStore(f.db,undefined,f.c).assertReady(),e=>e.code==='database_configuration_uninitialized_or_mismatched');
  await f.s.delegation.appendHistoricalRevocation(f.grant.id,revoke);
  assert.equal((await f.store.stmt('SELECT action FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first()).action,'revoke');
  assert.equal(f.c.policy.agreements.individual.version,'1.0');
}));
test('historical revoke stays within the unchanged invocation SQL budget',()=>iso(async f=>{
  await f.install();const c=rotate(f).c,budget=new InvocationBudget(),store=new LiveStore(f.db,budget,c),s=new LiveService(c,store,f.gh,f.key);
  await s.delegation.appendHistoricalRevocation(f.grant.id,await f.control('revoke'));
  assert.equal(budget.snapshot().sql,11);assert.equal(budget.snapshot().external,0);
}));
test('historical revoke authenticates every prior control signature',()=>iso(async f=>{
  await f.install();const record=await f.control('revoke');f.sql.exec('DROP TRIGGER cla_native_v2_delegated_controls_update');
  await f.store.stmt('UPDATE delegated_controls SET signature=?','A'.repeat(86)).run();
  await deniedUnchanged(f,()=>rotate(f).delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_signature_invalid');
}));
test('historical revoke rejects a prior canonical signed control with broken predecessor',()=>iso(async f=>{
  await f.install();const row=await f.store.stmt('SELECT * FROM delegated_controls').first(),record=await f.control('revoke');
  const altered=await f.signed({...JSON.parse(row.body),previous_sha256:'0'.repeat(64)});f.sql.exec('DROP TRIGGER cla_native_v2_delegated_controls_update');
  await f.store.stmt('UPDATE delegated_controls SET body=?,signature=?,hash=?',altered.body,altered.signature,await sha256(altered.body)).run();
  await deniedUnchanged(f,()=>rotate(f).delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_control_order');
}));
test('historical revoke fails transaction when evidence revision changes after validation',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke'),before=snapshot(f),batch=s.store.batch.bind(s.store);
  s.store.batch=async statements=>{f.sql.exec('UPDATE cla_native_v2_state SET revision=revision+1');return batch(statements);};
  await assert.rejects(s.delegation.appendHistoricalRevocation(f.grant.id,record),e=>e.code==='transaction_rejected');
  assert.deepEqual(snapshot(f).delegated_controls,before.delegated_controls);assert.equal(await f.store.revision(),before.state[0].revision+1);
}));
test('historical revoke rechecks database clock in the commit transaction',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke'),batch=s.store.batch.bind(s.store);
  s.store.batch=async statements=>{f.advance(-1);return batch(statements);};
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'transaction_rejected');
}));
test('historical revoke failed insert rolls back revision and all triggered invalidations',()=>iso(async f=>{
  await f.install();const s=rotate(f),record=await f.control('revoke');await f.store.track(707,{kind:'pr',number:9});
  f.sql.exec("INSERT INTO cla_native_v2_head_requests VALUES('synthetic',9,'"+'c'.repeat(40)+"','done','',0,1,'observed');");
  f.sql.exec("CREATE TRIGGER reject_historical_revoke BEFORE INSERT ON cla_native_v2_delegated_controls WHEN NEW.action='revoke' BEGIN SELECT RAISE(ABORT,'synthetic rejection'); END;");
  await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'transaction_rejected');
}));
test('historical revoke atomically invalidates old observations without releasing manual failures or send fences',()=>iso(async f=>{
  await f.install();await f.store.track(707,{kind:'pr',number:9});
  for(const status of ['done','running','manual','dispatched'])f.sql.prepare('INSERT INTO cla_native_v2_head_requests VALUES(?,9,?,?,?,120,1,?)').run(status,'c'.repeat(40),status,'old-attempt','old-result');
  f.sql.prepare("INSERT INTO cla_native_v2_publication_attempts(id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text,send_until) VALUES('old-issued','pr:707:9',?,1,1800000120,'issued',1,1,'success','SYNTHETIC PREEXISTING INPUT',1800000120)").run('c'.repeat(40));
  const s=rotate(f);await s.delegation.appendHistoricalRevocation(f.grant.id,await f.control('revoke'));
  const rows=f.sql.prepare('SELECT * FROM cla_native_v2_head_requests ORDER BY request_key').all();
  for(const row of rows)if(['done','running'].includes(row.request_key)){assert.equal(row.status,'pending');assert.equal(row.result,'');}else{assert.equal(row.status,row.request_key);assert.equal(row.result,'old-result');}
  assert.equal((await f.store.stmt('SELECT wanted_revision FROM outbox').first()).wanted_revision,await f.store.revision());
  assert.equal((await f.store.stmt('SELECT send_until FROM publication_attempts').first()).send_until,1800000120);
}));
test('historical revoke at control 100 succeeds',()=>iso(async f=>{
  await f.install();for(let i=2;i<=99;i++)await f.s.delegation.append(await f.control('enable'));
  const s=rotate(f),record=await f.control('revoke');await s.delegation.appendHistoricalRevocation(f.grant.id,record);
  assert.equal((await f.store.stmt('SELECT sequence FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first()).sequence,100);
}));
test('historical revoke cannot bypass the 100 control limit or report success without a write',()=>iso(async f=>{
  await f.install();for(let i=2;i<=100;i++)await f.s.delegation.append(await f.control('enable'));
  const s=rotate(f),record=await f.control('revoke');await deniedUnchanged(f,()=>s.delegation.appendHistoricalRevocation(f.grant.id,record),'authorization_control_invalid');
  assert.equal((await f.store.stmt('SELECT action FROM delegated_controls ORDER BY sequence DESC LIMIT 1').first()).action,'enable');
}));
test('historical revocation suite makes no outbound request or publication',()=>assert.equal(outbound,0));
