import test from 'node:test';
import assert from 'node:assert/strict';
import {iso} from './activation-fixture.mjs';
import {LiveStore} from '../src/native/store.ts';
import {InvocationBudget} from '../src/native/budget.ts';
import {config as workerConfig} from '../src/native/worker.ts';
import {prepareAgreementActivation,applyAgreementActivation} from '../src/native/agreement-activation.ts';
import {verifyActivationRecord} from '../src/native/activation-record.ts';
import {randomToken,sha256} from '../src/core/crypto.ts';

let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('ACTIVATION_NETWORK_FORBIDDEN');};

// Compare every application table, including row identities and all immutable
// evidence. A rejected call must not leave a partly committed activation.
function snapshot(f){
  return f.sql.prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'cla_native_v2_%' ORDER BY name").all()
    .map(({name})=>[name,f.sql.prepare('SELECT rowid,* FROM '+name+' ORDER BY rowid').all().map(row=>({...row}))]);
}
async function deniedUnchanged(f,action){
  const before=snapshot(f);await assert.rejects(action);assert.deepEqual(snapshot(f),before);
}
// Frozen pre-activation binary readiness; this deliberately does not call the
// new implementation's selection helper or accept an anchor as a substitute.
function legacyBinaryReady(f){
  return f.sql.prepare(`SELECT (SELECT count(*) FROM cla_native_v2_authority)=1
    AND EXISTS(SELECT 1 FROM cla_native_v2_authority WHERE id=1 AND holder=?)
    AND (SELECT count(*) FROM cla_native_v2_agreements)=2
    AND EXISTS(SELECT 1 FROM cla_native_v2_agreements WHERE kind='individual' AND version=? AND digest=?)
    AND EXISTS(SELECT 1 FROM cla_native_v2_agreements WHERE kind='corporate' AND version=? AND digest=?) AS ready`)
    .get(f.c.holderId,f.c.policy.agreements.individual.version,f.c.policy.agreements.individual.sha256,
      f.c.policy.agreements.corporate.version,f.c.policy.agreements.corporate.sha256).ready;
}
async function newSubject(f){
  const session={token_hash:await sha256(randomToken()),actor:3,login:'syntheticother',csrf_hash:await sha256(randomToken()),expires:f.now()+900};
  await f.store.addSession(session);return session;
}
async function pausedPersonalSignature(f,packet){
  const original=f.s.store.signAtomic.bind(f.s.store);let captured;
  f.s.store.signAtomic=async input=>{captured=input;throw Error('SYNTHETIC_PAUSE_BEFORE_COMMIT');};
  try{await assert.rejects(f.sign(packet),/SYNTHETIC_PAUSE_BEFORE_COMMIT/);}finally{f.s.store.signAtomic=original;}
  assert.ok(captured,'real authenticated consent path reached its final transaction');return captured;
}
function simultaneousBatches(f){
  const original=f.store.batch.bind(f.store);let count=0,release;
  const both=new Promise(resolve=>{release=resolve;});
  f.store.batch=async statements=>{if(++count===2)release();await both;return original(statements);};
  return ()=>{f.store.batch=original;assert.equal(count,2,'both proposals reached their actual transaction');};
}

// Documents, identities and consents in this file are synthetic local inputs.
// No Worker fetch, HTTP, publishing or successful-publication expiry transition.
test('activation first real version fences legacy binaries within13 SQL',()=>iso(async f=>{
  const plan=await f.plan(),budget=new InvocationBudget(),store=new LiveStore(f.db,budget);
  assert.equal(legacyBinaryReady(f),1);
  let result;await assert.doesNotReject(async()=>{result=await plan.apply(store);});
  assert.equal(result.status,'applied');assert.equal(budget.snapshot().sql,13);assert.equal(budget.snapshot().external,0);
  assert.equal(legacyBinaryReady(f),0);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM agreements').first()).n,3);
  assert.equal((await f.store.stmt('SELECT version FROM agreements WHERE digest=?',plan.config.policy.agreements.individual.sha256).first()).version,'2.0');
  await assert.doesNotReject(f.service(plan.config,plan.content).store.assertReady());
  await assert.rejects(f.s.store.assertReady());await assert.rejects(f.s.store.revision());
}));
test('activation cannot replace a real version transition with source only',()=>iso(async f=>{
  const plan=await f.plan('1.0');await deniedUnchanged(f,()=>plan.apply());
  assert.equal(legacyBinaryReady(f),1);
}));
test('activation capability requires an environment flag and an explicit hash pin',()=>iso(async f=>{
  const plan=await f.plan();
  assert.equal(workerConfig({CLA_CONFIG:JSON.stringify(plan.config)}).agreementActivationEnabled,false);
  assert.equal(workerConfig({CLA_CONFIG:JSON.stringify(plan.config),CLA_AGREEMENT_ACTIVATION_ENABLED:'true'}).agreementActivationEnabled,true);
  const unpinned={...plan.config};delete unpinned.agreementActivationHash;
  assert.throws(()=>f.service(unpinned,plan.content));
  assert.throws(()=>f.service({...plan.config,agreementActivationEnabled:false},plan.content));
}));
test('activation legacy mode rejects a journal even with two registry rows',()=>iso(async f=>{
  // Synthetic partial-state falsification isolates NOT EXISTS(anchor) from the
  // independent real-third-agreement fence. This is not an accepted migration.
  const {proposal:{record:r}}=await f.plan('1.0');await f.store.batch(f.store.cas(await f.store.revision()));
  await f.store.stmt(`INSERT INTO agreement_activations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    r.generation,r.hash,r.previous_hash,r.activation_revision,r.repository_id,r.holder_id,r.reference_sha,r.policy_json,
    r.individual_version,r.individual_digest,r.corporate_version,r.corporate_digest,r.observation_json,r.body,f.now()).run();
  assert.equal(legacyBinaryReady(f),1);await assert.rejects(f.s.store.assertReady());await assert.rejects(f.s.store.revision());
}));
for(const field of ['hash','repository','holder','reference','policy','agreement'])test('activation current config binds '+field,()=>iso(async f=>{
  const plan=await f.plan();await plan.apply();const row=await f.store.stmt('SELECT * FROM agreement_activations').first(),c=structuredClone(plan.config);
  if(field==='hash')c.agreementActivationHash='f'.repeat(64);
  if(field==='repository')c.repositoryId++;
  if(field==='holder')c.holderId++;
  if(field==='reference')c.referenceSha='b'.repeat(40);
  if(field==='policy')c.policy.exemptions[0].reason='SYNTHETIC CHANGED POLICY';
  if(field==='agreement')c.policy.agreements.individual.version='3.0';
  await assert.rejects(verifyActivationRecord(row,c));
  const store=new LiveStore(f.db,undefined,c);await assert.rejects(store.assertReady());await assert.rejects(store.revision());
}));
test('activation runtime verifies stored canonical bytes beyond mirrored columns',()=>iso(async f=>{
  const plan=await f.plan();await plan.apply();f.sql.exec('DROP TRIGGER cla_native_v2_activations_update');
  await f.store.stmt('UPDATE agreement_activations SET body=?',plan.proposal.record.body+' ').run();
  const s=f.service(plan.config,plan.content);await assert.rejects(s.store.assertReady());await assert.rejects(s.store.revision());
}));
test('activation rejects altered fresh individual bytes',()=>iso(async f=>{
  const plan=await f.plan(),proposal=structuredClone(plan.proposal);
  proposal.documents.find(d=>d.kind==='individual').content+='SYNTHETIC UNAPPROVED BYTE CHANGE';
  await deniedUnchanged(f,()=>applyAgreementActivation(f.store,plan.config,proposal));
}));
test('activation requires a version marker matching the selected new agreement',()=>iso(async f=>{
  const plan=await f.plan(),c=structuredClone(plan.config),documents=structuredClone(plan.documents);delete c.agreementActivationHash;
  const individual=documents.find(d=>d.kind==='individual');individual.content=individual.content.replace('CLA_VERSION: 2.0','CLA_VERSION: 1.0');
  individual.digest=await sha256(individual.content);c.policy.agreements.individual.sha256=individual.digest;
  await assert.rejects(prepareAgreementActivation(c,documents,plan.expected));
}));
test('activation rejects a modified prepared proposal without persistent changes',()=>iso(async f=>{
  const plan=await f.plan();
  for(const mutate of [p=>p.record.generation++,p=>p.record.activation_revision++,p=>p.record.previous_hash='f'.repeat(64),
    p=>p.record.hash='0'.repeat(64),p=>p.record.body+=' ',p=>p.record.observation_json='{}',p=>p.record.created=1]){
    const proposal=structuredClone(plan.proposal);mutate(proposal);await deniedUnchanged(f,()=>applyAgreementActivation(f.store,plan.config,proposal));
  }
}));
test('activation replay is an exact read only noop',()=>iso(async f=>{
  const plan=await f.plan();await plan.apply();const before=snapshot(f);let result;
  await assert.doesNotReject(async()=>{result=await plan.apply();});assert.equal(result.status,'already_active');assert.deepEqual(snapshot(f),before);
}));
test('activation identical concurrent proposals recover the committed winner without another write',()=>iso(async f=>{
  const plan=await f.plan(),restore=simultaneousBatches(f);let result;
  try{await assert.doesNotReject(async()=>{result=await Promise.all([plan.apply(),plan.apply()]);});}finally{restore();}
  assert.deepEqual(result.map(r=>r.status).sort(),['already_active','applied']);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM agreement_activations').first()).n,1);assert.equal(await f.store.revision(),1);
}));
test('activation different concurrent proposals select exactly one atomic winner',()=>iso(async f=>{
  const a=await f.plan('2.0','A'),b=await f.plan('2.0','B'),restore=simultaneousBatches(f);let result;
  try{result=await Promise.allSettled([a.apply(),b.apply()]);}finally{restore();}
  assert.equal(result.filter(r=>r.status==='fulfilled').length,1);
  const rows=(await f.store.stmt('SELECT * FROM agreement_activations').all()).results;assert.equal(rows.length,1);
  const winner=rows[0].hash===a.proposal.record.hash?a:b;assert.equal(rows[0].hash,winner.proposal.record.hash);
  await assert.doesNotReject(f.service(winner.config,winner.content).store.assertReady());
  assert.equal(await f.store.revision(),1);assert.equal((await f.store.stmt('SELECT count(*) n FROM agreements').first()).n,3);
}));
test('activation superseded proposal and configuration cannot become current again',()=>iso(async f=>{
  const a=await f.plan();await a.apply();const b=await f.plan('3.0');await b.apply();
  await deniedUnchanged(f,()=>a.apply());await assert.rejects(f.service(a.config,a.content).store.assertReady());
  await assert.doesNotReject(f.service(b.config,b.content).store.assertReady());
}));
test('activation cannot consume an unrelated later evidence revision',()=>iso(async f=>{
  const old=await f.create(),plan=await f.plan();
  await f.s.hold(...f.args('contributor'),old.id,await f.s.intent(...f.args('contributor'),old.id,'hold'));
  await deniedUnchanged(f,()=>plan.apply());
}));
test('activation final insert failure rolls back every row and invalidation',()=>iso(async f=>{
  await f.install();const old=await f.create();await f.sign(old);
  await f.s.hold(...f.args('contributor'),old.id,await f.s.intent(...f.args('contributor'),old.id,'hold'));
  const key=await f.store.track(707,{kind:'pr',number:9});await f.store.enqueue(key);
  for(const state of ['done','running','manual','dispatched'])await f.store.stmt('INSERT INTO head_requests VALUES(?,9,?,?,?,120,1,?)',
    state,'c'.repeat(40),state,'SYNTHETIC OLD ATTEMPT','SYNTHETIC OBSERVATION').run();
  const plan=await f.plan();f.sql.exec("CREATE TRIGGER reject_activation BEFORE INSERT ON cla_native_v2_agreement_activations BEGIN SELECT RAISE(ABORT,'SYNTHETIC LATE FAILURE'); END");
  await deniedUnchanged(f,()=>plan.apply());
}));
test('activation preserves original document sources personal evidence and delegated receipts',()=>iso(async f=>{
  await f.install();const old=await f.create();await f.sign(old);
  const signatures=await f.store.signatures(old.id),receipt=await f.store.stmt('SELECT * FROM delegated_receipts').first(),
    documents=(await f.store.stmt('SELECT * FROM agreement_documents ORDER BY digest').all()).results,plan=await f.plan();
  await plan.apply();for(const document of documents)assert.deepEqual(await f.store.document(document.digest),document);
  assert.deepEqual(await f.store.signatures(old.id),signatures);assert.deepEqual(await f.store.stmt('SELECT * FROM delegated_receipts').first(),receipt);
  const sources=JSON.parse(plan.proposal.record.observation_json);assert.equal(sources.corporate,plan.config.referenceSha);
  assert.notEqual((await f.store.document(f.c.policy.agreements.corporate.sha256)).source_sha,sources.corporate);
  const s=f.service({...plan.config,holderAuthorizationId:randomToken(),holderAuthorizationKey:randomToken()},plan.content);
  const audit=await s.audit(f.args('contributor')[0],old.id);assert.equal(audit.agreement.content,f.texts.individual);
  assert.equal(audit.delegated.current,false);assert.equal(audit.delegated.state,'superseded');
  await s.delegation.appendHistoricalRevocation(f.grant.id,await f.control('revoke'));
  const revoked=await s.audit(f.args('contributor')[0],old.id);assert.equal(revoked.delegated.current,false);
  assert.equal(JSON.parse(revoked.delegated.controls.at(-1).body).action,'revoke');
}));
test('activation current store rejects old tuple for a new subject before commit',()=>iso(async f=>{
  const old=await f.create(),plan=await f.plan();await plan.apply();const s=f.service(plan.config,plan.content),session=await newSubject(f);
  await deniedUnchanged(f,()=>s.store.createAuthenticatedPacket(session,{...old,id:randomToken(),subject:session.actor}));
}));
test('activation current store rejects old signing intent',()=>iso(async f=>{
  const old=await f.create(),plan=await f.plan();await plan.apply();const s=f.service(plan.config,plan.content),session=await s.session(f.args('contributor')[0]),hash=await sha256(randomToken());
  await deniedUnchanged(f,()=>s.store.addAuthenticatedIntent(hash,session,old,'contributor',f.now()+300));
}));
test('activation current store rejects old signature even with a fresh revision',()=>iso(async f=>{
  const old=await f.create(),input=await pausedPersonalSignature(f,old),plan=await f.plan();await plan.apply();const s=f.service(plan.config,plan.content),revision=await s.store.revision();
  await deniedUnchanged(f,()=>s.store.signAtomic({...input,revision}));
}));
test('activation stale signer cannot commit a preactivation revision',()=>iso(async f=>{
  const old=await f.create(),input=await pausedPersonalSignature(f,old),plan=await f.plan();await plan.apply();
  await deniedUnchanged(f,()=>f.service(plan.config,plan.content).store.signAtomic(input));
}));
test('activation after packet or intent preread rejects the final stale write',async()=>{
  for(const operation of ['packet','intent'])await iso(async f=>{
    const old=await f.create(),session=operation==='packet'?await newSubject(f):await f.s.session(f.args('contributor')[0]),plan=await f.plan(),batch=f.s.store.batch.bind(f.s.store);let raced=false;
    f.s.store.batch=async statements=>{if(!raced){raced=true;await plan.apply();}return batch(statements);};
    const beforePackets=(await f.store.stmt('SELECT count(*) n FROM packets').first()).n,beforeIntents=(await f.store.stmt('SELECT count(*) n FROM intents').first()).n;
    const action=operation==='packet'?()=>f.s.store.createAuthenticatedPacket(session,{...old,id:randomToken(),subject:session.actor}):
      ()=>f.s.store.addAuthenticatedIntent('0'.repeat(64),session,old,'contributor',f.now()+300);
    await assert.rejects(action);assert.equal(raced,true);
    assert.equal((await f.store.stmt('SELECT count(*) n FROM packets').first()).n,beforePackets);
    assert.equal((await f.store.stmt('SELECT count(*) n FROM intents').first()).n,beforeIntents);
    assert.equal((await f.store.stmt('SELECT count(*) n FROM agreement_activations').first()).n,1);
  });
});
test('activation historic hold remains available and later evidence revisions stay ready',()=>iso(async f=>{
  const old=await f.create(),plan=await f.plan();await plan.apply();const s=f.service(plan.config,plan.content);
  await assert.doesNotReject(s.hold(...f.args('contributor'),old.id,await s.intent(...f.args('contributor'),old.id,'hold')));
  assert.equal(await s.store.subjectBlocked(old.subject),true);assert.equal(await f.store.revision(),plan.proposal.record.activation_revision+1);
  await assert.doesNotReject(s.store.assertReady());await assert.doesNotReject(s.store.revision());
}));
test('activation new agreement needs fresh personal contributor and holder consent',()=>iso(async f=>{
  const old=await f.create();await f.sign(old);await f.sign(old,'holder');const oldSignatures=await f.store.signatures(old.id),plan=await f.plan();await plan.apply();
  const s=f.service(plan.config,plan.content);assert.equal((await s.evaluate(9)).conclusion,'failure');
  const fresh=await f.create(s);assert.notEqual(fresh.id,old.id);assert.equal(fresh.version,'2.0');assert.equal((await f.store.signatures(fresh.id)).length,0);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_receipts WHERE packet=?',fresh.id).first()).n,0);
  await assert.doesNotReject(f.sign(fresh,'contributor',s));assert.equal((await s.evaluate(9)).conclusion,'failure');
  await assert.doesNotReject(f.sign(fresh,'holder',s));assert.equal((await s.evaluate(9)).conclusion,'success');
  const audit=await s.audit(f.args('contributor')[0],fresh.id);assert.equal(audit.personal_signatures.length,2);
  assert.ok(audit.personal_signatures.every(row=>row.agreement_version==='2.0' && row.agreement_sha256===fresh.digest));
  assert.deepEqual(await f.store.signatures(old.id),oldSignatures);
}));
test('activation suite performs zero outbound requests',()=>assert.equal(outbound,0));
