import {fixture} from './publication-lifecycle-fixture.mjs';
import {randomToken,sha256} from '../src/core/crypto.ts';
export async function multi(delegated=true){
 const f=await fixture({group:true,delegated});
 const token=randomToken(),csrf=randomToken();await f.store.addSession({token_hash:await sha256(token),actor:3,login:'user3',csrf_hash:await sha256(csrf),expires:f.now()+86400});
 const args=[token,csrf,f.c.origin],packet=await f.s.packet(...args,'individual');
 await f.s.sign(...args,packet.id,await f.s.intent(...args,packet.id,'contributor'),'contributor',f.evidence('contributor'));
 if(!delegated)await f.sign(packet,'holder');
 f.members.get(11).user={id:3,login:'user3',type:'User'};f.second={packet,args};return f;
}
export async function isolated(fn,delegated=true){const f=await multi(delegated);try{await fn(f);}finally{f.close();}}
