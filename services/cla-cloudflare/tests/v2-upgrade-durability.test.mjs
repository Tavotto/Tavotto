import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {DatabaseSync} from 'node:sqlite';import {nativeSQL} from '../src/native/store.ts';
import {prepareSyntheticV2Upgrade,applySyntheticV2Upgrade} from '../scripts/v2-upgrade-offline.mjs';
const enabled={execute:true,syntheticFixture:true};
function fixture(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cla-v2-synthetic-wal-')),file=path.join(dir,'synthetic.sqlite');let db=new DatabaseSync(file);
 db.exec('PRAGMA journal_mode=WAL');for(const name of ['base.sql','native-schema.sql'])db.exec(nativeSQL(fs.readFileSync(new URL('../migrations/v2-main-77e2cab7/'+name,import.meta.url),'utf8')));
 db.exec("UPDATE cla_native_v2_state SET revision=9007199254740993; INSERT INTO cla_native_v2_sessions(rowid,token_hash,actor,login,csrf_hash,expires) VALUES(9223372036854775806,'SYNTHETIC',2,CAST(X'80FF' AS TEXT),X'0080FF',0)");
 return {get db(){return db;},reopen(){db.close();db=new DatabaseSync(file);db.exec('PRAGMA foreign_keys=ON');return db;},close(){db.close();fs.rmSync(dir,{recursive:true,force:true});}};
}
// These files are created here, contain only synthetic values, and are deleted.
// No existing file path or external database is accepted by the CLI or tests.
test('v2 upgrade synthetic WAL commit survives close reopen and old-plan recognition without DDL',()=>{
 const f=fixture();try{
  const plan=prepareSyntheticV2Upgrade(f.db);assert.equal(applySyntheticV2Upgrade(f.db,plan,enabled).status,'upgraded_synthetic_only');
  const db=f.reopen();assert.equal(prepareSyntheticV2Upgrade(db).status,'already_target');
  const exec=db.exec.bind(db),seen=[];db.exec=sql=>{seen.push(sql);return exec(sql);};
  assert.equal(applySyntheticV2Upgrade(db,plan,enabled).status,'already_applied');assert.deepEqual(seen,['BEGIN IMMEDIATE','COMMIT']);
  const s=db.prepare('SELECT rowid,hex(login) login_bytes,hex(csrf_hash) csrf_bytes FROM cla_native_v2_sessions');s.setReadBigInts(true);const row=s.get();
  assert.equal(row.rowid,9223372036854775806n);assert.equal(row.login_bytes,'80FF');assert.equal(row.csrf_bytes,'0080FF');
  const r=db.prepare('SELECT revision FROM cla_native_v2_state');r.setReadBigInts(true);assert.equal(r.get().revision,9007199254740993n);
 }finally{f.close();}
});
test('v2 upgrade synthetic WAL late DDL failure survives close reopen with original source data and schema',()=>{
 const f=fixture();try{
  const plan=prepareSyntheticV2Upgrade(f.db),exec=f.db.exec.bind(f.db);let ddl=0;
  f.db.exec=sql=>{const result=exec(sql);if(/^(ALTER|CREATE)/.test(sql) && ++ddl===31)throw Error('SYNTHETIC LAST DDL FAILED');return result;};
  assert.throws(()=>applySyntheticV2Upgrade(f.db,plan,enabled),/LAST DDL FAILED/);assert.equal(f.db.isTransaction,false);
  const db=f.reopen(),after=prepareSyntheticV2Upgrade(db);assert.deepEqual(after,plan);assert.equal(after.status,'prepared');
 }finally{f.close();}
});
