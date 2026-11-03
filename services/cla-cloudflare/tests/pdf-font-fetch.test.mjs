// scripts/fetch-pdf-font.mjs: pinned URL + SHA-256, .part then rename, delete and fail on mismatch, never retry.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {existsSync,mkdtempSync,readdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {FONT_FILE,FontError,FONT_SHA256,FONT_URL,ensureFont} from '../scripts/fetch-pdf-font.mjs';
const bytes=Buffer.from('synthetic font bytes'),good=createHash('sha256').update(bytes).digest('hex');
const tmp=()=>mkdtempSync(path.join(os.tmpdir(),'cla-font-'));
test('pinned source is a commit-pinned google/fonts raw URL with a full SHA-256',()=>{
  assert.match(FONT_URL,/^https:\/\/raw\.githubusercontent\.com\/google\/fonts\/[0-9a-f]{40}\/ofl\/greatvibes\/GreatVibes-Regular\.ttf$/);
  assert.match(FONT_SHA256,/^[0-9a-f]{64}$/);
});
test('download is verified, written via .part and renamed; second call needs no network',async()=>{
  const d=tmp();try{
    let calls=0;const f=async(url,init)=>{calls++;assert.equal(url,'https://x.invalid/f.ttf');assert.equal(init.redirect,'error');return new Response(bytes);};
    const p=await ensureFont(d,{fetchImpl:f,url:'https://x.invalid/f.ttf',sha256:good});
    assert.deepEqual(readFileSync(p),bytes);assert.deepEqual(readdirSync(d),[FONT_FILE]);
    await ensureFont(d,{fetchImpl:async()=>{throw Error('NO_NETWORK');},url:'https://x.invalid/f.ttf',sha256:good});assert.equal(calls,1);
  }finally{rmSync(d,{recursive:true,force:true});}
});
test('hash mismatch deletes everything, fails, and does not retry',async()=>{
  const d=tmp();try{
    let calls=0;await assert.rejects(ensureFont(d,{fetchImpl:async()=>{calls++;return new Response('tampered');},sha256:good}),/pdf_font_hash_mismatch/);
    assert.equal(calls,1);assert.deepEqual(readdirSync(d),[]);
    writeFileSync(path.join(d,FONT_FILE),'tampered on disk');
    await assert.rejects(ensureFont(d,{fetchImpl:async()=>{calls++;return new Response(bytes);},sha256:good}),/pdf_font_hash_mismatch/);
    assert.equal(existsSync(path.join(d,FONT_FILE)),false);assert.equal(calls,1);
  }finally{rmSync(d,{recursive:true,force:true});}
});
test('no network or HTTP error is a clear failure, never a silent skip; --check never downloads',async()=>{
  const d=tmp();try{
    await assert.rejects(ensureFont(d,{fetchImpl:async()=>{throw Error('offline');},sha256:good}),/pdf_font_network_unreachable/);
    await assert.rejects(ensureFont(d,{fetchImpl:async()=>new Response('x',{status:404}),sha256:good}),/pdf_font_http_status.*HTTP 404/);
    await assert.rejects(ensureFont(d,{checkOnly:true,fetchImpl:async()=>{throw Error('must not be called');},sha256:good}),/pdf_font_missing/);
    assert.deepEqual(readdirSync(d),[]);
  }finally{rmSync(d,{recursive:true,force:true});}
});

test('failures carry stable codes and a one-sentence reason; the CLI exits non-zero with the code first',async()=>{
  const d=tmp();try{
    for(const [f,code] of [[async()=>{throw Error('offline');},'pdf_font_network_unreachable'],[async()=>new Response('x',{status:503}),'pdf_font_http_status'],[async()=>new Response('tampered'),'pdf_font_hash_mismatch']])
      await assert.rejects(ensureFont(d,{fetchImpl:f,sha256:good}),e=>e instanceof FontError&&e.code===code&&e.message.startsWith(code+': ')&&!e.message.includes('\n'));
    const r=spawnSync(process.execPath,['scripts/fetch-pdf-font.mjs','--check','--dest',d],{cwd:path.join(import.meta.dirname,'..'),encoding:'utf8'});
    assert.equal(r.status,1);assert.match(r.stderr,/^pdf_font_missing: /);
  }finally{rmSync(d,{recursive:true,force:true});}
});
