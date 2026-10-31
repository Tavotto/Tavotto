import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {LiveStore,nativeSQL} from '../src/native/store.ts';
export function fixture(file=':memory:',initialize=true){
  let now=1800000000;const sql=new DatabaseSync(file);
  sql.exec('PRAGMA busy_timeout=5000');if(file!==':memory:')sql.exec('PRAGMA journal_mode=WAL');
  sql.function('independent_now',()=>now);
  const adapt=q=>q.replaceAll("strftime('%s','now')",'independent_now()');
  if(initialize)for(const f of ['base.sql','native/schema.sql'])sql.exec(adapt(nativeSQL(fs.readFileSync(new URL('../src/'+f,import.meta.url),'utf8'))));
  const prepare=(q,args=[])=>{const exec=()=>{const s=sql.prepare(adapt(q)),r=s.columns().length?s.all(...args):s.run(...args);return {success:true,results:Array.isArray(r)?r:[]};};return {bind:(...a)=>prepare(q,a),first:async()=>exec().results[0]??null,all:async()=>exec(),run:async()=>exec(),exec};};
  const db={prepare,batch:async ss=>{sql.exec('BEGIN IMMEDIATE');try{const r=ss.map(s=>s.exec());sql.exec('COMMIT');return r;}catch(e){sql.exec('ROLLBACK');throw e;}}};
  return {sql,db,store:new LiveStore(db),advance:n=>{now+=n;},now:()=>now,close:()=>sql.close()};
}
