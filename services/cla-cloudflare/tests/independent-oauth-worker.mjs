import {parentPort,workerData} from 'node:worker_threads';
import {fixture} from './independent-oauth-db.mjs';
const f=fixture(workerData.file,false),gate=new Int32Array(workerData.gate);parentPort.postMessage({ready:true});Atomics.wait(gate,0,0);
let accepted=0,rejected=0;
try{for(let i=0;i<10;i++){try{await f.store.addOAuth(workerData.id+'-'+i,'browser','synthetic-cipher',0);accepted++;}catch(e){if(e.code!=='oauth_busy')throw e;rejected++;}}parentPort.postMessage({accepted,rejected});}catch(e){parentPort.postMessage({error:String(e)});}finally{f.close();}
