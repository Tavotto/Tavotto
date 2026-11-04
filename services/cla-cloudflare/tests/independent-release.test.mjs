import test from 'node:test';
import assert from 'node:assert/strict';
import {iso,deny} from './delegation-fixture.mjs';

// A release is the holder's append-only answer to a hold: the block and every signature stay, and only packets
// created strictly after the release stop being blocked by it.
const hold=async(f,p,role='contributor')=>f.s.hold(...f.args(role),p.id,await f.s.intent(...f.args(role),p.id,'hold'));
const release=(f,p,role='holder')=>f.s.release(...f.args(role),p.id);
const code=(promise,expected)=>assert.rejects(promise,e=>e.code===expected);
const success=async f=>(await f.s.evaluate(9)).conclusion==='success';
const held=async f=>{const p=await f.create();await f.sign(p,'contributor');await f.sign(p,'holder');await hold(f,p);return p;};
const status=(f,p,role='holder')=>f.s.status(f.args(role)[0],p.id);

test('release: an unreleased hold still blocks creation, signing, qualification and every status',()=>iso(async f=>{
  const p=await held(f);assert.equal(await f.store.subjectBlocked(2),true);
  await code(f.create(),'packet_ineligible');await code(f.s.intent(...f.args('contributor'),p.id,'hold'),'transaction_rejected');
  assert.equal(await success(f),false);
  for(const role of ['holder','contributor']){const s=await status(f,p,role);assert.equal(s.blocked,true);assert.equal(s.released,false);assert.equal(s.canSign,false);}
  assert.equal((await status(f,p,'holder')).canRelease,true);assert.equal((await status(f,p,'contributor')).canRelease,false);
}));

test('release: only the holder, only a held packet, only once; CSRF and origin are checked',()=>iso(async f=>{
  const p=await held(f);
  await code(release(f,p,'contributor'),'release_rejected');
  await code(f.s.release(f.args('holder')[0],'x'.repeat(43),f.c.origin,p.id),'csrf_rejected');
  await code(f.s.release(f.args('holder')[0],f.args('holder')[1],'https://wrong.invalid',p.id),'origin_rejected');
  await code(f.s.release(...f.args('holder'),'not-a-packet'),'packet_unavailable');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM eligibility_releases').first()).n,0);
  const before=await f.store.revision(),r=await release(f,p);assert.equal(r.revision,before+1);assert.equal(await f.store.revision(),before+1);
  await code(release(f,p),'release_not_held');
  const other=await (async()=>{f.advance(1);return f.create();})();await code(release(f,other),'release_not_held');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM eligibility_releases').first()).n,1);
}));

test('release: block, signatures and release rows are immutable',()=>iso(async f=>{
  const p=await held(f);await release(f,p);
  for(const q of ['UPDATE eligibility_releases SET actor=3','DELETE FROM eligibility_releases','UPDATE eligibility_blocks SET created=0','DELETE FROM eligibility_blocks'])
    await assert.rejects(f.store.stmt(q).run(),/immutable/);
  assert.equal((await f.store.signatures(p.id)).length,2);
}));

test('release: held packet and old signatures stay ineligible; nothing qualifies until a new packet is signed',()=>iso(async f=>{
  const p=await held(f);f.advance(5);await release(f,p);
  assert.equal(await f.store.subjectBlocked(2),false);assert.equal(await success(f),false);
  for(const role of ['holder','contributor']){const s=await status(f,p,role);assert.equal(s.blocked,true);assert.equal(s.released,true);assert.equal(s.canSign,false);assert.equal(s.canHold,false);}
  assert.equal((await status(f,p,'holder')).canRelease,false);
  for(const [role,as] of [['contributor','contributor'],['holder','holder'],['contributor','hold']])await code(f.s.intent(...f.args(role),p.id,as),'transaction_rejected');
  assert.equal(await f.store.qualification(2,p.digest),null);
  f.advance(7*86400);assert.equal(await success(f),false);
}));

test('release: the contributor signs a new packet, the holder countersigns and it qualifies; the old packet is never reused',()=>iso(async f=>{
  const old=await held(f);f.advance(2);await release(f,old);f.advance(1);
  const p=await f.create();assert.notEqual(p.id,old.id);assert.equal((await status(f,p,'contributor')).blocked,false);
  assert.equal(await success(f),false);await f.sign(p,'contributor');assert.equal(await success(f),false);await f.sign(p,'holder');
  assert.equal(await success(f),true);assert.equal((await f.create()).id,p.id);
  assert.equal((await f.store.qualification(2,p.digest)).id,p.id);assert.equal((await status(f,old,'contributor')).blocked,true);
  assert.equal((await f.store.stmt('SELECT count(*) n FROM packets').first()).n,2);
}));

test('release: a packet created in the same second as the release is refused, so it can never become a dead packet',()=>iso(async f=>{
  const p=await held(f);await release(f,p);await code(f.create(),'transaction_rejected');
  assert.equal((await f.store.stmt('SELECT count(*) n FROM packets').first()).n,1);f.advance(1);assert.ok((await f.create()).id);
}));

test('release: a later hold on the new packet blocks again and can itself be released',()=>iso(async f=>{
  const first=await held(f);f.advance(2);await release(f,first);f.advance(1);
  const second=await f.create();await f.sign(second,'contributor');await f.sign(second,'holder');assert.equal(await success(f),true);
  f.advance(1);await hold(f,second);assert.equal(await success(f),false);assert.equal(await f.store.subjectBlocked(2),true);await code(f.create(),'packet_ineligible');
  const s=await status(f,second);assert.equal(s.blocked,true);assert.equal(s.released,false);assert.equal(s.block.packet,second.id);
  // The first packet's release does not cover the second hold, and viewing the first packet points at the open one.
  const view=await status(f,first);assert.equal(view.blocked,true);assert.equal(view.released,false);assert.equal(view.block.packet,second.id);assert.equal(view.canRelease,true);
  f.advance(2);await release(f,second);f.advance(1);
  const third=await f.create();assert.ok(![first.id,second.id].includes(third.id));await f.sign(third,'contributor');await f.sign(third,'holder');
  assert.equal(await success(f),true);
}));

test('release: a hold on a packet that an older release does not cover is allowed only on the newer packet',()=>iso(async f=>{
  const first=await held(f);f.advance(2);await release(f,first);f.advance(1);const second=await f.create();
  await code(f.s.intent(...f.args('contributor'),first.id,'hold'),'transaction_rejected');
  await hold(f,second);assert.equal((await f.store.stmt('SELECT count(*) n FROM eligibility_blocks').first()).n,2);
}));

test('release: the atomic write itself refuses a non-holder session, an unheld packet and a stale revision',()=>iso(async f=>{
  const p=await held(f),contributor=await f.s.session(f.args('contributor')[0]),holder=await f.s.session(f.args('holder')[0]),revision=await f.store.revision();
  await assert.rejects(f.store.releaseAtomic(contributor,p,revision,f.now()));
  await assert.rejects(f.store.releaseAtomic(holder,p,revision-1,f.now()));
  assert.equal((await f.store.stmt('SELECT count(*) n FROM eligibility_releases').first()).n,0);
  assert.equal(await f.store.revision(),revision);
  assert.equal(await f.store.releaseAtomic(holder,p,revision,f.now()),revision+1);
  await assert.rejects(f.store.releaseAtomic(holder,p,revision+1,f.now()));
}));
