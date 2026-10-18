import {parentPort,workerData} from 'node:worker_threads';
import {DatabaseSync} from 'node:sqlite';
import {LiveStore} from '../src/native/store.ts';
const sql=new DatabaseSync(workerData.file);sql.exec('PRAGMA busy_timeout=5000');sql.function('independent_now',()=>workerData.now);
const prepare=(q,args=[])=>{const exec=()=>{const s=sql.prepare(q.replaceAll("strftime('%s','now')",'independent_now()'));const r=s.columns().length?s.all(...args):s.run(...args);return {success:true,results:Array.isArray(r)?r:[],meta:{changes:Number(sql.prepare('SELECT changes() n').get().n)}};};return {bind:(...a)=>prepare(q,a),first:async()=>exec().results[0]??null,all:async()=>exec(),run:async()=>exec(),exec};};
const db={prepare,batch:async ss=>{sql.exec('BEGIN IMMEDIATE');try{const r=ss.map(s=>s.exec());sql.exec('COMMIT');return r;}catch(e){sql.exec('ROLLBACK');throw e;}}};
const store=new LiveStore(db),gate=new Int32Array(workerData.gate);parentPort.postMessage({ready:true});Atomics.wait(gate,0,0);
try{const packet=await store.createAuthenticatedPacket(workerData.session,workerData.packet);parentPort.postMessage({packet:packet.id});}catch(e){parentPort.postMessage({error:String(e)});}finally{sql.close();}
