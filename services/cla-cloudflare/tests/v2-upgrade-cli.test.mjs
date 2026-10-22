import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {fileURLToPath} from 'node:url';import {spawnSync} from 'node:child_process';
import {applySyntheticV2Upgrade} from '../scripts/v2-upgrade-offline.mjs';
const script=fileURLToPath(new URL('../scripts/v2-upgrade-offline.mjs',import.meta.url));
test('v2 upgrade default API does not inspect a database or enable execution',()=>{
 const db=new Proxy({},{get(){assert.fail('default-off path inspected database');}});
 for(const options of [undefined,{}, {execute:true},{syntheticFixture:true},{execute:'true',syntheticFixture:true},{execute:true,syntheticFixture:'true'}]){
  const r=applySyntheticV2Upgrade(db,{},options);assert.equal(r.status,'disabled');assert.equal(r.production_authorized,false);
 }
});
test('v2 upgrade CLI only emits a disabled synthetic schema plan and creates no files',()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'cla-v2-plan-'));
 try{const r=spawnSync(process.execPath,[script,'--plan'],{cwd:tmp,encoding:'utf8'});assert.equal(r.status,0,r.stderr);
  const p=JSON.parse(r.stdout);assert.equal(p.execution_enabled,false);assert.equal(p.activation_performed,false);assert.equal(p.production_authorized,false);
  assert.equal(p.statements.length,31);assert.equal(p.new_tables.length,7);assert.deepEqual(fs.readdirSync(tmp),[]);
 }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
test('v2 upgrade CLI refuses execution flags and database path arguments',()=>{
 for(const args of [['--execute'],['synthetic.sqlite'],['--plan','synthetic.sqlite'],['--execute','--synthetic']]){
  const r=spawnSync(process.execPath,[script,...args],{encoding:'utf8'});assert.equal(r.status,1);assert.match(r.stderr,/no file target or execution option/);assert.equal(r.stdout,'');
 }
});
