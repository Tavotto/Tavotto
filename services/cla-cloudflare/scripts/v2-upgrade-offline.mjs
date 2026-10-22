// OFFLINE SYNTHETIC SQLite ONLY. No D1/HTTP/credentials/file-target writer.
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {nativeSQL} from '../src/native/store.ts';
import {canonical} from '../src/core/crypto.ts';

export const SOURCE_REF='77e2cab77597318859f874d9319c47f15da0e1dd';
export const TARGET_REF='dc2431f725798bd78c40a3b07e0a8054690fe82c';
const PREFIX='cla_native_v2_',PUBLICATIONS=PREFIX+'publication_attempts';
const ADD_SEND_FENCE=`ALTER TABLE ${PUBLICATIONS} ADD COLUMN send_until INTEGER NOT NULL DEFAULT 0 CHECK(send_until>=0)`;
const pins={
 'migrations/v2-main-77e2cab7/base.sql':'b7c158869acbd1eb43dcbfee72f85ebf1149716082a66f9140989c56a4927e69',
 'migrations/v2-main-77e2cab7/native-schema.sql':'5e2a63a7560aee08253f942c8ee34a2cf0c99e692ef14417014d6081e348392a',
 'src/base.sql':'b7c158869acbd1eb43dcbfee72f85ebf1149716082a66f9140989c56a4927e69',
 'src/native/schema.sql':'0e4cc1672c78837b219eba0c1f54de89fc86169c87ffd26277085a229dfe3a08',
};
const hash=value=>createHash('sha256').update(typeof value==='string'?value:canonical(value)).digest('hex');
const deny=reason=>{throw Error(reason);};
const identifier=name=>'"'+name.replaceAll('"','""')+'"';
const schema=db=>db.prepare("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT GLOB 'sqlite_*' ORDER BY type,name").all().map(r=>({...r}));
const columns=(db,name)=>db.prepare(`PRAGMA table_xinfo(${identifier(name)})`).all().map(r=>r.name);
const checkedFile=name=>{const text=fs.readFileSync(new URL('../'+name,import.meta.url),'utf8');if(hash(text)!==pins[name])deny('canonical_schema_pin_changed');return nativeSQL(text);};
const load=(db,paths)=>{for(const p of paths)db.exec(checkedFile(p));};
function shape(db,name){
 const rows=query=>db.prepare(query).all().map(r=>({...r}));
 return {columns:rows(`PRAGMA table_xinfo(${identifier(name)})`),foreign_keys:rows(`PRAGMA foreign_key_list(${identifier(name)})`),
  indices:rows(`PRAGMA index_list(${identifier(name)})`).map(index=>({...index,columns:rows(`PRAGMA index_xinfo(${identifier(index.name)})`)}))};
}
function models(){
 const source=new DatabaseSync(':memory:'),target=new DatabaseSync(':memory:');
 try{
  load(source,['migrations/v2-main-77e2cab7/base.sql','migrations/v2-main-77e2cab7/native-schema.sql']);
  load(target,['src/base.sql','src/native/schema.sql']);
  const before=schema(source),fresh=schema(target),names=new Set(before.map(r=>r.name));
  const additions=fresh.filter(r=>!names.has(r.name));
  if(additions.filter(r=>r.type==='table').length!==7 || additions.filter(r=>r.type==='trigger').length!==23 || additions.length!==30)deny('unreviewed_schema_delta');
  const originalTables=before.filter(r=>r.type==='table').map(r=>({name:r.name,columns:columns(source,r.name)}));
  for(const row of before){const expected=fresh.find(r=>r.name===row.name);if(!expected || row.name!==PUBLICATIONS && canonical(expected)!==canonical(row))deny('non_additive_schema_delta');}
  const statements=[ADD_SEND_FENCE,...additions.map(r=>r.sql)];
  for(const sql of statements)source.exec(sql);
  const upgraded=schema(source);
  // Keep ALTER's exact SQLite-generated CREATE text; never erase SQL differences.
  for(const row of fresh){const actual=upgraded.find(r=>r.name===row.name);if(!actual || row.name!==PUBLICATIONS && canonical(actual)!==canonical(row))deny('target_model_mismatch');}
  for(const row of fresh.filter(r=>r.type==='table'))if(canonical(shape(source,row.name))!==canonical(shape(target,row.name)))deny('target_table_shape_mismatch');
  return {before,fresh,upgraded,originalTables,newTables:additions.filter(r=>r.type==='table').map(r=>r.name),statements};
 }finally{source.close();target.close();}
}
const value=v=>v===null?['null']:typeof v==='bigint'?['integer',String(v)]:typeof v==='number'?['real',String(v)]:typeof v==='string'?['text',v]:v instanceof Uint8Array?['blob',Buffer.from(v).toString('hex')]:deny('unrecognized_sqlite_value');
function data(db,tables){
 return Object.fromEntries(tables.map(t=>{
  const raw=t.columns.map((c,i)=>`CASE WHEN typeof(${identifier(c)}) IN ('text','blob') THEN hex(${identifier(c)}) ELSE NULL END AS __migration_bytes_${i}`);
  const statement=db.prepare(`SELECT rowid AS __migration_rowid,${t.columns.map(identifier).join(',')},${raw.join(',')} FROM ${identifier(t.name)} ORDER BY rowid`);
  statement.setReadBigInts(true);const rows=statement.all();
  const encoded=rows.map(row=>[value(row.__migration_rowid),...t.columns.map((c,i)=>[value(row[c]),row['__migration_bytes_'+i]])]);
  return [t.name,{rows:rows.length,sha256:hash(encoded)}];
 }));
}
function integrity(db){
 if(db.prepare('PRAGMA database_list').all().some(row=>!['main','temp'].includes(row.name)) ||
  db.prepare('SELECT 1 FROM temp.sqlite_schema WHERE sql IS NOT NULL LIMIT 1').get())deny('attached_or_temporary_schema_requires_review');
 if(db.prepare('PRAGMA foreign_keys').get()?.foreign_keys!==1)deny('foreign_keys_must_be_enabled');
 const rows=db.prepare('PRAGMA integrity_check').all();
 if(rows.length!==1 || rows[0].integrity_check!=='ok' || db.prepare('PRAGMA foreign_key_check').all().length)deny('database_integrity_failed');
}
function quiet(db){
 if(db.prepare(`SELECT 1 FROM ${PREFIX}_guards LIMIT 1`).get() ||
  db.prepare(`SELECT 1 FROM ${PREFIX}outbox WHERE lease!='' OR lease_until!=0 LIMIT 1`).get() ||
  db.prepare(`SELECT 1 FROM ${PUBLICATIONS} WHERE phase IN ('issued','unknown') LIMIT 1`).get())deny('source_not_quiescent');
}
function classify(db,m){
 const current=canonical(schema(db));
 if(current===canonical(m.before))return 'source';
 if(current===canonical(m.upgraded) || current===canonical(m.fresh))return 'target';
 deny('unknown_schema_manual_mapping_required');
}
function defaultsAndEmpty(db,m){
 if(db.prepare(`SELECT 1 FROM ${PUBLICATIONS} WHERE send_until!=0 LIMIT 1`).get())deny('historical_send_fence_changed');
 for(const name of m.newTables)if(db.prepare(`SELECT 1 FROM ${identifier(name)} LIMIT 1`).get())deny('new_capability_data_must_remain_empty');
}
function prepare(db,m){
 const status=classify(db,m);integrity(db);if(status==='source')quiet(db);
 const tableList=status==='source'?m.originalTables:schema(db).filter(r=>r.type==='table').map(r=>({name:r.name,columns:columns(db,r.name)}));
 return {protocol:'cla-offline-v2-upgrade-v1',status:status==='source'?'prepared':'already_target',source_ref:SOURCE_REF,target_ref:TARGET_REF,
  offline_only:true,execution_enabled:false,production_authorized:false,activation_performed:false,
  schema_sha256:hash(schema(db)),data:data(db,tableList),target_schema_sha256:hash(m.upgraded),
  fresh_target_schema_sha256:hash(m.fresh),statements:status==='source'?m.statements:[],new_tables:m.newTables};
}
export function prepareSyntheticV2Upgrade(db){return prepare(db,models());}
export function applySyntheticV2Upgrade(db,plan,{execute=false,syntheticFixture=false}={}){
 // Default-off before even inspecting a caller database. The label is a caller
 // assertion for test use, not proof of provenance or production authorization.
 if(execute!==true || syntheticFixture!==true)return {status:'disabled',offline_only:true,production_authorized:false};
 if(db.isTransaction)deny('caller_transaction_must_remain_untouched');
 const m=models();let begun=false;
 try{
  begun=true;db.exec('BEGIN IMMEDIATE');
  const current=prepare(db,m);
  if(current.status==='already_target' && plan?.status==='prepared'){
   // An uncertain commit may only be recognized read-only against the complete
   // original data and still-empty additions. Never retry or repair partial DDL.
   // A fresh CREATE target cannot be the result of this exact ALTER sequence.
   if(current.schema_sha256!==hash(m.upgraded))deny('uncertain_commit_schema_mismatch');
   const original={...current,status:'prepared',schema_sha256:hash(m.before),statements:m.statements,data:data(db,m.originalTables)};
   if(canonical(plan)!==canonical(original))deny('prepared_plan_changed');
   quiet(db);defaultsAndEmpty(db,m);db.exec('COMMIT');begun=false;
   return {status:'already_applied',offline_only:true,production_authorized:false,data_preserved:true,activation_performed:false};
  }
  if(canonical(plan)!==canonical(current))deny('prepared_plan_changed');
  if(current.status==='already_target'){db.exec('COMMIT');begun=false;return {status:'already_target',offline_only:true,production_authorized:false,writes:0};}
  for(const sql of m.statements)db.exec(sql);
  if(canonical(schema(db))!==canonical(m.upgraded))deny('upgraded_schema_mismatch');
  if(canonical(data(db,m.originalTables))!==canonical(current.data))deny('original_rows_changed');
  defaultsAndEmpty(db,m);integrity(db);
  db.exec('COMMIT');begun=false;
  return {status:'upgraded_synthetic_only',offline_only:true,production_authorized:false,data_preserved:true,rowids_preserved:true,
   activation_performed:false,ddl_statements:m.statements.length,schema_sha256:hash(m.upgraded),data:current.data};
 }catch(error){if(begun && db.isTransaction)db.exec('ROLLBACK');throw error;}
}
export function syntheticSource(){const db=new DatabaseSync(':memory:');try{load(db,['migrations/v2-main-77e2cab7/base.sql','migrations/v2-main-77e2cab7/native-schema.sql']);return db;}catch(error){db.close();throw error;}}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 let db;try{
  if(process.argv.length>3 || process.argv[2]!==undefined && process.argv[2]!=='--plan')deny('usage: node scripts/v2-upgrade-offline.mjs [--plan]; no file target or execution option');
  db=syntheticSource();console.log(JSON.stringify(prepareSyntheticV2Upgrade(db),null,2));
 }catch(error){console.error(error.message);process.exitCode=1;}finally{db?.close();}
}
