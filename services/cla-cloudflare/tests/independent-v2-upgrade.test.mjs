import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {nativeSQL} from '../src/native/store.ts';
import {SOURCE_REF,TARGET_REF,prepareSyntheticV2Upgrade,applySyntheticV2Upgrade} from '../scripts/v2-upgrade-offline.mjs';

let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('UPGRADE_NETWORK_FORBIDDEN');};
const P='cla_native_v2_',q=s=>'"'+s.replaceAll('"','""')+'"';
const apply=(db,plan,options={})=>applySyntheticV2Upgrade(db,plan,{execute:true,syntheticFixture:true,...options});
const all=(db,sql)=>{const statement=db.prepare(sql);statement.setReadBigInts(true);return statement.all().map(row=>({...row}));};
function specs(db,name='main'){
  return all(db,`SELECT name FROM ${q(name)}.sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*' ORDER BY name`)
    .map(({name:table})=>({name:table,columns:db.prepare(`PRAGMA ${q(name)}.table_xinfo(${q(table)})`).all().map(row=>row.name)}));
}
// Independent comparison includes every storage value, rowid and raw TEXT/BLOB
// bytes. Invalid UTF-8 must not collapse to its decoded replacement character.
function rows(db,tables,name='main'){
  return tables.map(table=>[table.name,all(db,`SELECT rowid AS captured_rowid,${table.columns.map((column,i)=>
    `${q(column)} AS value_${i},typeof(${q(column)}) AS type_${i},hex(CAST(${q(column)} AS BLOB)) AS bytes_${i}`).join(',')}
    FROM ${q(name)}.${q(table.name)} ORDER BY rowid`)]);
}
function snapshot(db){
  db.prepare('SELECT count(*) FROM temp.sqlite_schema').get();
  return db.prepare('PRAGMA database_list').all().map(({name})=>({name,
    schema:all(db,`SELECT type,name,tbl_name,rootpage,sql FROM ${q(name)}.sqlite_schema ORDER BY type,name`),rows:rows(db,specs(db,name),name)}));
}
function deniedUnchanged(db,action,reason){const before=snapshot(db);assert.throws(action,reason);assert.deepEqual(snapshot(db),before);}
function source(){
  const db=new DatabaseSync(':memory:');
  for(const name of ['base.sql','native-schema.sql'])db.exec(nativeSQL(fs.readFileSync(new URL('../migrations/v2-main-77e2cab7/'+name,import.meta.url),'utf8')));
  return db;
}
function fixture(){
  const db=source(),a='a'.repeat(64),b='b'.repeat(64),head='c'.repeat(40);
  db.exec(`INSERT INTO ${P}authority VALUES(1,1);
    INSERT INTO ${P}agreements VALUES('individual','1.0','${a}'),('corporate','1.0','${b}');
    UPDATE ${P}state SET revision=7;
    INSERT INTO ${P}sessions VALUES('SYNTHETIC-SESSION',2,'合成🧪\n\t','SYNTHETIC-CSRF',999999);
    INSERT INTO ${P}oauth_states VALUES('SYNTHETIC-OAUTH','SYNTHETIC-BROWSER',X'00FF80',999999,1);
    INSERT INTO ${P}packets VALUES('SYNTHETIC-INDIVIDUAL',2,'individual','1.0','${a}',100,200),
      ('SYNTHETIC-CORPORATE',3,'corporate','1.0','${b}',100,200),('SYNTHETIC-HELD',4,'individual','1.0','${a}',100,200);
    INSERT INTO ${P}intents VALUES('SYNTHETIC-INTENT','SYNTHETIC-SESSION','SYNTHETIC-INDIVIDUAL','contributor','${a}',190,1);
    INSERT INTO ${P}signatures VALUES('SYNTHETIC-INDIVIDUAL','contributor',2,X'00FF800D0A','SYNTHETIC-HASH',NULL,150),
      ('SYNTHETIC-INDIVIDUAL','holder',1,'SYNTHETIC CIPHER 合成','SYNTHETIC-HASH','',151),
      ('SYNTHETIC-CORPORATE','contributor',3,'SYNTHETIC CORPORATE','SYNTHETIC-HASH',NULL,152);
    INSERT INTO ${P}eligibility_blocks VALUES('SYNTHETIC-HELD',4,153,'party_requested_review');
    INSERT INTO ${P}audit VALUES(9007199254740993,'SYNTHETIC-INDIVIDUAL',2,'signed:contributor',150),
      (9007199254740994,'SYNTHETIC-INDIVIDUAL',1,'signed:holder',151),(9007199254740995,'SYNTHETIC-HELD',4,'hold',153);
    INSERT INTO ${P}targets VALUES('SYNTHETIC-TARGET','{"kind":"pr","number":9}'),('SYNTHETIC-OTHER','{"kind":"pr","number":10}');
    INSERT INTO ${P}outbox(target_key,wanted_revision,generation,due,attempts) VALUES('SYNTHETIC-TARGET',7,5,123,3);
    INSERT INTO ${P}deliveries VALUES('SYNTHETIC-DELIVERY','SYNTHETIC-BODY','SYNTHETIC-TARGET',154);
    INSERT INTO ${P}published_checks VALUES('SYNTHETIC-TARGET',77,'${head}','success',7,154);
    INSERT INTO ${P}publication_attempts(id,target_key,head_sha,revision,expires,phase,run_id,created,updated,conclusion,proof_text,observation,reconcile_due,reconcile_attempts,observed_generation)
      VALUES('SYNTHETIC-CONFIRMED','SYNTHETIC-TARGET','${head}',6,200,'confirmed',77,100,150,'success','SYNTHETIC PROOF','historic observation',200,0,5),
      ('SYNTHETIC-RECONCILE','SYNTHETIC-OTHER','${head}',6,200,'reconcile',NULL,100,150,'success','SYNTHETIC PROOF','',0,1,4),
      ('SYNTHETIC-RESOLVED','SYNTHETIC-OTHER','${head}',5,200,'resolved',78,100,150,'failure','SYNTHETIC NEGATIVE','negative observation',NULL,0,3),
      ('SYNTHETIC-MANUAL','SYNTHETIC-OTHER','${head}',5,200,'manual',NULL,100,150,'success','SYNTHETIC PROOF','operator required',NULL,3,3);
    INSERT INTO ${P}reconciliation_progress VALUES('SYNTHETIC-TARGET',2,44);
    INSERT INTO ${P}refresh_requests VALUES('SYNTHETIC-TARGET','SYNTHETIC-MANUAL',7,'manual',3,456,'SYNTHETIC MANUAL');
    INSERT INTO ${P}agreement_documents VALUES('${a}','individual','1.0','${head}','SYNTHETIC 正文\n'),('${b}','corporate','1.0','${head}',X'0080FF');
    UPDATE ${P}sessions SET rowid=9223372036854775806 WHERE token_hash='SYNTHETIC-SESSION';`);
  return db;
}
const using=(fn,create=fixture)=>{const db=create();try{return fn(db);}finally{if(db.isTransaction)db.exec('ROLLBACK');db.close();}};
function intercept(db,handler){const exec=db.exec.bind(db);db.exec=sql=>handler(sql,exec);return ()=>{db.exec=exec;};}

// All databases in this file are new in-memory synthetic fixtures. Evidence
// fields are opaque synthetic storage values, not real signatures or consent.
test('v2 upgrade preserves every old typed cell rowid tuple and manual cursor with31 DDL',()=>using(db=>{
  const original=specs(db),before=rows(db,original);let plan,result;
  assert.doesNotThrow(()=>{plan=prepareSyntheticV2Upgrade(db);result=apply(db,plan);});
  assert.equal(SOURCE_REF,'77e2cab77597318859f874d9319c47f15da0e1dd');assert.equal(TARGET_REF,'dc2431f725798bd78c40a3b07e0a8054690fe82c');
  assert.equal(result.status,'upgraded_synthetic_only');assert.equal(result.ddl_statements,31);assert.equal(plan.statements.length,31);
  assert.deepEqual(rows(db,original),before);assert.equal(db.prepare(`SELECT revision FROM ${P}state`).get().revision,7);
  assert.equal(specs(db).length,26);assert.equal(plan.new_tables.length,7);
  for(const name of plan.new_tables)assert.equal(db.prepare('SELECT count(*) n FROM '+q(name)).get().n,0);
  assert.equal(db.prepare(`SELECT count(*) n FROM ${P}publication_attempts WHERE send_until IS NOT 0`).get().n,0);
  assert.equal(db.prepare(`SELECT status FROM ${P}refresh_requests`).get().status,'manual');
  assert.deepEqual({...db.prepare(`SELECT cursor,horizon FROM ${P}reconciliation_progress`).get()},{cursor:2,horizon:44});
  assert.equal(db.isTransaction,false);assert.equal(result.activation_performed,false);assert.equal(result.production_authorized,false);
}));

test('v2 upgrade default off never inspects a database or a plan',()=>{
  let touches=0;const poison=new Proxy({},{get(){touches++;throw Error('SYNTHETIC DATABASE MUST NOT BE TOUCHED');}});
  for(const options of [undefined,{execute:true},{syntheticFixture:true},{execute:1,syntheticFixture:true},{execute:true,syntheticFixture:'true'}]){
    let result;assert.doesNotThrow(()=>{result=applySyntheticV2Upgrade(poison,poison,options);});assert.equal(result.status,'disabled');
  }
  assert.equal(touches,0);
});

test('v2 upgrade exact source rejects sqliteX names instead of treating underscore as wildcard',()=>using(db=>{
  db.exec('CREATE TABLE sqliteXspoof(value TEXT)');
  deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/unknown_schema/);
}));

for(const sql of ['CREATE VIEW external_view AS SELECT 1 AS v','CREATE INDEX external_index ON '+P+'sessions(login)',
  'CREATE TRIGGER external_trigger AFTER INSERT ON '+P+'sessions BEGIN SELECT 1; END',
  'CREATE TABLE cla_native_v1_unreviewed(value TEXT)',
  'ALTER TABLE '+P+'publication_attempts ADD COLUMN send_until INTEGER NOT NULL DEFAULT 0 CHECK(send_until>=0)']){
  test('v2 upgrade rejects unknown or partial schema '+sql.split(' ').slice(0,3).join(' '),()=>using(db=>{
    db.exec(sql);deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/unknown_schema/);
  }));
}

for(const sql of ['CREATE TEMP TABLE '+P+'_guards(id TEXT,ok INTEGER)',
  'CREATE TEMP TRIGGER temporary_trigger AFTER INSERT ON main.'+P+'sessions BEGIN SELECT 1; END',
  "ATTACH ':memory:' AS unreviewed"]){
  test('v2 upgrade rejects temporary or attached objects '+sql.split(' ').slice(0,4).join(' '),()=>using(db=>{
    db.exec(sql);deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/attached_or_temporary/);
  }));
}

test('v2 upgrade refuses a relabeled frozen v1 instead of testing the wrong source',()=>using(db=>{
  deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/unknown_schema/);
},()=>{const db=new DatabaseSync(':memory:');for(const name of ['v1-base.sql','v1-native-schema.sql'])db.exec(nativeSQL(fs.readFileSync(new URL('../migrations/'+name,import.meta.url),'utf8')));return db;}));

for(const phase of ['issued','unknown'])for(const conclusion of ['success','failure'])test('v2 upgrade quiescence rejects '+phase+' '+conclusion,()=>using(db=>{
  db.prepare(`UPDATE ${P}publication_attempts SET phase=?,conclusion=? WHERE id='SYNTHETIC-CONFIRMED'`).run(phase,conclusion);
  deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/source_not_quiescent/);
}));
for(const [lease,until] of [['SYNTHETIC-EXPIRED',1],['SYNTHETIC-NO-DEADLINE',0],['',999999]])test('v2 upgrade quiescence rejects mismatched or stale lease '+lease+' '+until,()=>using(db=>{
  db.prepare(`UPDATE ${P}outbox SET lease=?,lease_until=?`).run(lease,until);
  deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/source_not_quiescent/);
}));
test('v2 upgrade quiescence rejects arbitrary guards without cleaning them',()=>using(db=>{
  db.exec(`INSERT INTO ${P}_guards VALUES('SYNTHETIC-STRAY',1)`);deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/source_not_quiescent/);
}));

test('v2 upgrade rejects foreign keys disabled without altering caller state',()=>using(db=>{
  db.exec('PRAGMA foreign_keys=OFF');deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/foreign_keys_must_be_enabled/);
}));
test('v2 upgrade rejects damaged foreign keys rather than treating schema as evidence',()=>using(db=>{
  db.exec(`PRAGMA foreign_keys=OFF;INSERT INTO ${P}published_checks VALUES('SYNTHETIC-MISSING',999,'c','failure',7,1);PRAGMA foreign_keys=ON;`);
  deniedUnchanged(db,()=>prepareSyntheticV2Upgrade(db),/database_integrity_failed/);
}));

test('v2 upgrade stale prepared data is rechecked inside its actual transaction',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);db.exec(`UPDATE ${P}outbox SET due=due+1`);
  deniedUnchanged(db,()=>apply(db,plan),/prepared_plan_changed/);assert.equal(db.isTransaction,false);
}));
test('v2 upgrade differing invalid UTF8 bytes cannot collide in a stale plan',()=>using(db=>{
  db.exec(`UPDATE ${P}sessions SET login=CAST(X'80' AS TEXT)`);const plan=prepareSyntheticV2Upgrade(db);
  db.exec(`UPDATE ${P}sessions SET login=CAST(X'81' AS TEXT)`);
  assert.equal(db.prepare(`SELECT login FROM ${P}sessions`).get().login,'�');
  deniedUnchanged(db,()=>apply(db,plan),/prepared_plan_changed/);
}));
test('v2 upgrade identical bytes with a different SQLite storage type invalidate a plan',()=>using(db=>{
  db.exec(`UPDATE ${P}sessions SET login=X'31'`);const plan=prepareSyntheticV2Upgrade(db);
  db.exec(`UPDATE ${P}sessions SET login='1'`);
  deniedUnchanged(db,()=>apply(db,plan),/prepared_plan_changed/);
}));

for(const changed of ['source_ref','target_ref','statements','new_tables','data'])test('v2 upgrade rejects tampered plan '+changed,()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);
  if(changed==='source_ref'||changed==='target_ref')plan[changed]='0'.repeat(40);
  if(changed==='statements')plan.statements[0]='SELECT 1';
  if(changed==='new_tables')plan.new_tables=[];
  if(changed==='data')plan.data[P+'sessions'].sha256='0'.repeat(64);
  deniedUnchanged(db,()=>apply(db,plan),/prepared_plan_changed/);
}));

test('v2 upgrade rejects a new active lease that arrives immediately before BEGIN',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let intercepted=false;
  const restore=intercept(db,(sql,exec)=>{
    if(sql==='BEGIN IMMEDIATE'){intercepted=true;exec(`UPDATE ${P}outbox SET lease='SYNTHETIC-RACING-WRITER',lease_until=999999`);}
    return exec(sql);
  });
  try{assert.throws(()=>apply(db,plan),/source_not_quiescent/);}finally{restore();}
  assert.equal(intercepted,true);assert.equal(db.prepare(`SELECT lease FROM ${P}outbox`).get().lease,'SYNTHETIC-RACING-WRITER');
  assert.equal(specs(db).length,19);assert.equal(db.isTransaction,false);
}));

test('v2 upgrade never owns or rolls back a caller transaction',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);db.exec('BEGIN IMMEDIATE');db.exec(`UPDATE ${P}outbox SET due=999`);
  deniedUnchanged(db,()=>apply(db,plan),/caller_transaction_must_remain_untouched/);
  assert.equal(db.isTransaction,true);assert.equal(db.prepare(`SELECT due FROM ${P}outbox`).get().due,999);db.exec('ROLLBACK');
}));

test('v2 upgrade lost BEGIN reply rolls back its newly acquired transaction',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let acquired=false;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(sql==='BEGIN IMMEDIATE'){
    acquired=true;assert.equal(db.isTransaction,true);throw Error('SYNTHETIC LOST BEGIN REPLY');
  }return result;});
  try{deniedUnchanged(db,()=>apply(db,plan),/SYNTHETIC LOST BEGIN REPLY/);}finally{restore();}
  assert.equal(acquired,true);assert.equal(db.isTransaction,false,'the upgrade must clean up the transaction it acquired before throwing');
}));

test('v2 upgrade lost COMMIT reply is recognized without repeating any DDL',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db),original=specs(db),before=rows(db,original);let committed=false;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(sql==='COMMIT'){
    committed=true;assert.equal(db.isTransaction,false);throw Error('SYNTHETIC LOST COMMIT REPLY');
  }return result;});
  try{assert.throws(()=>apply(db,plan),/SYNTHETIC LOST COMMIT REPLY/);}finally{restore();}
  assert.equal(committed,true);assert.equal(db.isTransaction,false);assert.equal(specs(db).length,26);assert.deepEqual(rows(db,original),before);
  const executed=[],restoreRetry=intercept(db,(sql,exec)=>{executed.push(sql);return exec(sql);});let result;
  try{assert.doesNotThrow(()=>{result=apply(db,plan);});}finally{restoreRetry();}
  assert.equal(result.status,'already_applied');assert.deepEqual(executed,['BEGIN IMMEDIATE','COMMIT']);
}));

for(const seeded of [false,true])test('v2 upgrade old source plan rejects unrelated fresh target '+(seeded?'matching synthetic rows':'empty'),()=>using(db=>{
  const fresh=new DatabaseSync(':memory:');
  try{
    for(const name of ['base.sql','native/schema.sql'])fresh.exec(nativeSQL(fs.readFileSync(new URL('../src/'+name,import.meta.url),'utf8')));
    if(seeded)for(const target of [db,fresh])target.exec(`INSERT INTO ${P}sessions VALUES('SYNTHETIC-INDEPENDENT',2,'SYNTHETIC rebuilt target',X'0080FF',999999)`);
    const plan=prepareSyntheticV2Upgrade(db),freshPlan=prepareSyntheticV2Upgrade(fresh),before=snapshot(fresh);
    assert.equal(plan.status,'prepared');assert.equal(freshPlan.status,'already_target');
    assert.equal(freshPlan.schema_sha256,freshPlan.fresh_target_schema_sha256);
    assert.notEqual(freshPlan.schema_sha256,plan.target_schema_sha256);
    for(const [name,original] of Object.entries(plan.data))assert.deepEqual(freshPlan.data[name],original,'old rows match despite unrelated schema provenance');
    const executed=[],restore=intercept(fresh,(sql,exec)=>{executed.push(sql);return exec(sql);});
    try{deniedUnchanged(fresh,()=>apply(fresh,plan),/uncertain_commit_schema_mismatch/);}finally{restore();}
    assert.equal(fresh.isTransaction,false);assert.deepEqual(executed,['BEGIN IMMEDIATE','ROLLBACK']);assert.deepEqual(snapshot(fresh),before);
    const ownExecuted=[],restoreOwn=intercept(fresh,(sql,exec)=>{ownExecuted.push(sql);return exec(sql);});let own;
    try{own=apply(fresh,freshPlan);}finally{restoreOwn();}
    assert.equal(own.status,'already_target');assert.equal(own.writes,0);assert.equal(own.data_preserved,undefined);
    assert.deepEqual(ownExecuted,['BEGIN IMMEDIATE','COMMIT']);assert.deepEqual(snapshot(fresh),before);
  }finally{if(fresh.isTransaction)fresh.exec('ROLLBACK');fresh.close();}
},source));

for(const stage of ['ALTER','TABLE','TRIGGER','COMMIT'])test('v2 upgrade late '+stage+' failure restores complete schema rows and revision',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let injected=false;
  const restore=intercept(db,(sql,exec)=>{
    if(stage==='COMMIT'&&sql==='COMMIT'){injected=true;throw Error('SYNTHETIC LATE DDL FAILURE');}
    const result=exec(sql);
    if(!injected&&((stage==='ALTER'&&sql.startsWith('ALTER TABLE'))||(stage==='TABLE'&&sql.startsWith('CREATE TABLE'))||(stage==='TRIGGER'&&sql.startsWith('CREATE TRIGGER')))){
      injected=true;throw Error('SYNTHETIC LATE DDL FAILURE');
    }
    return result;
  });
  try{deniedUnchanged(db,()=>apply(db,plan),/SYNTHETIC LATE DDL FAILURE/);}finally{restore();}
  assert.equal(injected,true);assert.equal(db.isTransaction,false);assert.equal(specs(db).length,19);
}));

test('v2 upgrade final verification detects unexpected old row writes and rolls them back',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let injected=false;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(!injected&&sql.startsWith('ALTER TABLE')){injected=true;exec(`UPDATE ${P}outbox SET due=888`);}return result;});
  try{deniedUnchanged(db,()=>apply(db,plan),/original_rows_changed/);}finally{restore();}
  assert.equal(injected,true);assert.equal(db.isTransaction,false);
}));

test('v2 upgrade final verification rejects an unexpected schema write during DDL',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let injected=false;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(!injected&&sql.startsWith('ALTER TABLE')){injected=true;exec('CREATE TABLE SYNTHETIC_UNREVIEWED(value TEXT)');}return result;});
  try{deniedUnchanged(db,()=>apply(db,plan),/upgraded_schema_mismatch/);}finally{restore();}
  assert.equal(injected,true);assert.equal(db.isTransaction,false);
}));

test('v2 upgrade final verification rejects a nonzero historical fence',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let injected=false;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(!injected&&sql.startsWith('ALTER TABLE')){injected=true;exec(`UPDATE ${P}publication_attempts SET send_until=1`);}return result;});
  try{deniedUnchanged(db,()=>apply(db,plan),/historical_send_fence_changed/);}finally{restore();}
}));

test('v2 upgrade final verification rejects synthesized capability data',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);let count=0;
  const restore=intercept(db,(sql,exec)=>{const result=exec(sql);if(++count===31)exec(`INSERT INTO ${P}head_requests VALUES('SYNTHETIC-NEW',9,'c','manual','',0,0,'')`);return result;});
  // BEGIN is also intercepted, so inject after the 30th DDL while all seven
  // tables exist. No statement creates an authorization or real activation.
  try{deniedUnchanged(db,()=>apply(db,plan),/new_capability_data_must_remain_empty/);}finally{restore();}
}));

test('v2 upgrade same prepared plan retry is a read-only exact applied recognition',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);assert.doesNotThrow(()=>apply(db,plan));const before=snapshot(db),executed=[];
  const restore=intercept(db,(sql,exec)=>{executed.push(sql);return exec(sql);});let result;
  try{assert.doesNotThrow(()=>{result=apply(db,plan);});}finally{restore();}
  assert.equal(result.status,'already_applied');assert.deepEqual(snapshot(db),before);assert.deepEqual(executed,['BEGIN IMMEDIATE','COMMIT']);
  const targetPlan=prepareSyntheticV2Upgrade(db);const again=apply(db,targetPlan);assert.equal(again.status,'already_target');assert.equal(again.writes,0);
}));

test('v2 upgrade uncertain commit retry rejects changed old data without repair',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);apply(db,plan);db.exec(`UPDATE ${P}outbox SET due=due+1`);
  deniedUnchanged(db,()=>apply(db,plan),/prepared_plan_changed/);
}));
test('v2 upgrade uncertain commit retry rejects new capability rows',()=>using(db=>{
  const plan=prepareSyntheticV2Upgrade(db);apply(db,plan);db.exec(`INSERT INTO ${P}head_requests VALUES('SYNTHETIC-NEW',9,'c','manual','',0,0,'')`);
  deniedUnchanged(db,()=>apply(db,plan),/new_capability_data_must_remain_empty/);
}));

test('v2 upgrade tests never made outbound requests',()=>assert.equal(outbound,0));
