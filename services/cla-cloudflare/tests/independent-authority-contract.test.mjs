import test from 'node:test';
import assert from 'node:assert/strict';
import {iso} from './delegation-fixture.mjs';
import {base64url,canonical,open,seal,sha256} from '../src/core/crypto.ts';
globalThis.fetch=async()=>{throw Error('INDEPENDENT_OUTBOUND_FORBIDDEN');};
test('independent valid-format forged grant signature is rejected',()=>iso(async f=>{
 await assert.rejects(f.s.delegation.register({body:canonical(f.grant),signature:base64url(new Uint8Array(64))}));
 assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_grants').first()).n,0);
}));
test('independent valid-format forged control signature is rejected',()=>iso(async f=>{
 await f.s.delegation.register(await f.signed(f.grant));const control=await f.control();
 await assert.rejects(f.s.delegation.append({...control,signature:base64url(new Uint8Array(64))}));
 assert.equal((await f.store.stmt('SELECT count(*) n FROM delegated_controls').first()).n,0);
}));
test('independent direct delegated operation rejects disabled capability',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);f.c.delegatedCountersigningEnabled=false;
 await assert.rejects(f.s.delegation.countersign(p,(await f.store.signatures(p.id))[0]));
}));
test('independent encrypted receipt with incorrect stored digest is rejected',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_receipts_update');
 await f.store.stmt('UPDATE delegated_receipts SET hash=?','0'.repeat(64)).run();
 await assert.rejects(f.s.evaluate(9));
}));
test('independent correctly rehashed encrypted receipt with wrong holder is rejected',()=>iso(async f=>{
 await f.install();const p=await f.create();await f.sign(p);const r=await f.store.stmt('SELECT * FROM delegated_receipts').first();
 const aad=`delegated:${p.id}:${r.control_hash}`,data=JSON.parse(await open(f.key,r.cipher,aad));data.holder_id=999;
 const raw=canonical(data),cipher=await seal(f.key,raw,aad);f.sql.exec('DROP TRIGGER cla_native_v2_delegated_receipts_update');
 await f.store.stmt('UPDATE delegated_receipts SET cipher=?,hash=?',cipher,await sha256(raw)).run();
 await assert.rejects(f.s.evaluate(9));
}));
