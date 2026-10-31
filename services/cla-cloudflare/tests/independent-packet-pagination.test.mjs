import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import worker from '../src/native/worker.ts';
import {LiveStore,nativeSQL} from '../src/native/store.ts';
import {LiveService} from '../src/native/service.ts';
import {importAes,randomToken,sha256} from '../src/core/crypto.ts';

let outbound=0;
globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const NOW=1800000000;
async function fixture(){
  const sql=new DatabaseSync(':memory:');
  sql.function('pagination_now',()=>NOW);
  const adapt=q=>q.replaceAll("strftime('%s','now')",'pagination_now()');
  for(const file of ['base.sql','native/schema.sql'])sql.exec(adapt(nativeSQL(fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8'))));
  const queries=[];
  const execute=(q,args)=>{
    queries.push(q);const statement=sql.prepare(adapt(q));
    const result=statement.columns().length?statement.all(...args):statement.run(...args);
    return {success:true,results:Array.isArray(result)?result:[],meta:{changes:Number(sql.prepare('SELECT changes() n').get().n)}};
  };
  const prepare=(q,args=[])=>({bind:(...values)=>prepare(q,values),first:async()=>execute(q,args).results[0]??null,
    all:async()=>execute(q,args),run:async()=>execute(q,args),exec:()=>execute(q,args)});
  const db={prepare,batch:async statements=>{sql.exec('BEGIN IMMEDIATE');try{const result=statements.map(s=>s.exec());sql.exec('COMMIT');return result;}catch(error){sql.exec('ROLLBACK');throw error;}}};
  const c={origin:'https://cla.invalid',repositoryId:707,referenceSha:'a'.repeat(40),holderId:1,appId:123,appSlug:'synthetic-cla',checkName:'synthetic-cla',repository:'synthetic/repo',installationId:2,clientId:'synthetic',holderName:'SYNTHETIC HOLDER',syntheticSigning:false,mockCheckPublishing:false,autoHolder:false,exemptionIds:{erwanjun:1},githubEnabled:false,oauthEnabled:false,signingEnabled:true,checkPublishingEnabled:false,policy:{agreements:{individual:{path:'docs/legal/CLA_INDIVIDUAL.md',version:'1.0',sha256:'a'.repeat(64)},corporate:{path:'docs/legal/CLA_CORPORATE.md',version:'1.0',sha256:'b'.repeat(64)}},exemptions:[{login:'erwanjun',kind:'rights_holder',reason:'synthetic'}],provider:{configured:true,protocol:'tavotto-cla-v1',app_id:123,app_slug:'synthetic-cla',check_name:'synthetic-cla'}}};
  const store=new LiveStore(db);await store.initialize(c);
  const key=crypto.getRandomValues(new Uint8Array(32));
  const svc=new LiveService(c,store,{agreement:async()=>({digest:c.policy.agreements.individual.sha256,kind:'individual',version:'1.0',source_sha:c.referenceSha,content:'SYNTHETIC'})},await importAes(key));
  const users={};
  for(const actor of [1,2,3]){
    const token=randomToken(),csrf=randomToken();
    await store.addSession({token_hash:await sha256(token),actor,login:'synthetic-'+actor,csrf_hash:await sha256(csrf),expires:NOW+3600});users[actor]={token,csrf};
  }
  const packets=[];
  async function insert(subject,created=NOW-1000){
    const p={id:randomToken(),subject,kind:'individual',version:'1.0',digest:c.policy.agreements.individual.sha256,created,valid_until:created+500};
    await store.stmt('INSERT INTO packets VALUES(?,?,?,?,?,?,?)',p.id,p.subject,p.kind,p.version,p.digest,p.created,p.valid_until).run();packets.push(p);return p;
  }
  const env={DB:db,CLA_CONFIG:JSON.stringify(c),CLA_UI_ENABLED:'true',CLA_EVIDENCE_KEY:Buffer.from(key).toString('base64')};
  async function request(query='',actor=1){return worker.fetch(new Request(c.origin+'/session'+query,{headers:{cookie:'__Host-cla-session='+users[actor].token}}),env);}
  return {svc,store,users,insert,packets,queries,request,close:()=>sql.close(),c};
}
async function isolated(fn){const f=await fixture();try{await fn(f);}finally{f.close();}}
const newestFirst=(a,b)=>b.created-a.created || (a.id<b.id?1:a.id>b.id?-1:0);
async function collect(f,actor){
  let cursor;const ids=[],pages=[];
  do{const page=await f.svc.packets(f.users[actor].token,cursor);assert.ok(page.packets.length<=20);pages.push(page);ids.push(...page.packets.map(p=>p.id));cursor=page.nextCursor;}while(cursor);
  return {ids,pages};
}

test('holder discovers 45 packets, including same-second boundaries, once each in bounded pages',()=>isolated(async f=>{
  for(let i=0;i<45;i++)await f.insert(i%2+2,i<42?NOW-1000:NOW-2000);
  const {ids,pages}=await collect(f,1);
  assert.deepEqual(pages.map(p=>p.packets.length),[20,20,5]);
  assert.deepEqual(ids,[...f.packets].sort(newestFirst).map(p=>p.id));
  assert.equal(new Set(ids).size,45);
  assert.equal(pages.at(-1).nextCursor,null);
  for(const query of f.queries.filter(q=>q.includes('SELECT id FROM cla_native_v2_packets')))assert.match(query,/ORDER BY created DESC,id DESC LIMIT 21/);
}));

test('cursor traversal never exposes another contributor and foreign anchors reject',()=>isolated(async f=>{
  for(let i=0;i<46;i++)await f.insert(i%2+2);
  const {ids,pages}=await collect(f,2);
  assert.deepEqual(pages.map(p=>p.packets.length),[20,3]);
  assert.deepEqual(ids,f.packets.filter(p=>p.subject===2).sort(newestFirst).map(p=>p.id));
  const foreign=f.packets.find(p=>p.subject===3);
  await assert.rejects(f.svc.packets(f.users[2].token,foreign.id),e=>e.code==='packet_unavailable');
  await assert.rejects(f.svc.packets(f.users[2].token,'not-a-packet'),e=>e.code==='packet_unavailable');
  await assert.rejects(f.svc.packets(randomToken(),ids[0]),e=>e.code==='session_rejected');
}));

test('keyset pages do not duplicate or skip older records when a newer packet arrives',()=>isolated(async f=>{
  for(let i=0;i<25;i++)await f.insert(2);
  const expected=[...f.packets].sort(newestFirst).map(p=>p.id),first=await f.svc.packets(f.users[1].token);
  const fresh=await f.insert(3,NOW-500);
  const second=await f.svc.packets(f.users[1].token,first.nextCursor);
  assert.deepEqual([...first.packets,...second.packets].map(p=>p.id),expected);
  assert.equal(second.nextCursor,null);
  assert.equal((await f.svc.packets(f.users[1].token)).packets[0].id,fresh.id);
}));

test('20-row terminal page and empty history have no continuation',()=>isolated(async f=>{
  assert.deepEqual((await f.svc.packets(f.users[1].token)).packets,[]);
  assert.equal((await f.svc.packets(f.users[1].token)).nextCursor,null);
  for(let i=0;i<20;i++)await f.insert(2);
  const page=await f.svc.packets(f.users[2].token);
  assert.equal(page.packets.length,20);assert.equal(page.nextCursor,null);
}));

test('HTTP session accepts exactly one authorized cursor and rejects malformed query shapes',()=>isolated(async f=>{
  for(let i=0;i<21;i++)await f.insert(2);
  const first=await f.request();assert.equal(first.status,200);const page=await first.json();
  const second=await f.request('?cursor='+page.nextCursor);assert.equal(second.status,200);assert.equal((await second.json()).packets.length,1);
  for(const query of ['?cursor=', '?cursor=bad']){
    const response=await f.request(query);assert.equal(response.status,403);assert.equal((await response.json()).error,'packet_unavailable');
  }
  for(const query of ['?limit=1000','?cursor='+page.nextCursor+'&cursor='+page.nextCursor,'?cursor='+page.nextCursor+'&extra=1']){
    const response=await f.request(query);assert.equal(response.status,403);assert.equal((await response.json()).error,'form_rejected');
  }
  const foreign=await f.insert(3);const rejected=await f.request('?cursor='+foreign.id,2);
  assert.equal(rejected.status,403);assert.equal((await rejected.json()).error,'packet_unavailable');
}));

test('history pagination retains subject-wide holds and cannot create around an older held packet',()=>isolated(async f=>{
  for(let i=0;i<24;i++)await f.insert(2);
  const held=await f.insert(2,NOW-2000);
  await f.store.stmt('INSERT INTO eligibility_blocks VALUES(?,?,?,?)',held.id,2,NOW,'party_requested_review').run();
  const {ids}=await collect(f,2);assert.ok(ids.includes(held.id));
  const status=await f.svc.status(f.users[2].token,ids[0]);assert.equal(status.blocked,true);assert.equal(status.canSign,false);
  await assert.rejects(f.svc.packet(f.users[2].token,f.users[2].csrf,f.c.origin,'individual'));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM packets').first()).n,25);
}));

test('pagination tests never use outbound fetch',()=>assert.equal(outbound,0));
