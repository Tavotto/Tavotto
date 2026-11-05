// Worker routes for the signed-PDF assets, and the committed staging assets they serve.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import worker from '../src/native/worker.ts';
import {deploymentPlan} from '../scripts/deployment-plan.mjs';
import {PDFDocument} from 'pdf-lib';
import {HASH_KEYWORD} from '../scripts/render-agreement-pdf.mjs';
import {sha} from './pdf-fixture.mjs';
import {ensureFont,FONT_SHA256} from '../scripts/fetch-pdf-font.mjs';
let outbound=0;globalThis.fetch=async()=>{outbound++;throw Error('OUTBOUND_FORBIDDEN');};
const staging=name=>new URL('../deployment/staging/'+name,import.meta.url);
const config=JSON.parse(JSON.parse(fs.readFileSync(staging('wrangler.staging.json'),'utf8')).vars.CLA_CONFIG);
let dbCalls=0;const deniedDB={prepare(){dbCalls++;throw Error('DB_FORBIDDEN');},batch(){dbCalls++;throw Error('DB_FORBIDDEN');}};
const JS='console.log("synthetic bundle")',PDF='%PDF-1.7 synthetic',TTF='synthetic-font',OFL='SIL Open Font License synthetic';
const FIXED={'/cla-pdf.js':[JS,'text/javascript'],'/CLA_INDIVIDUAL.pdf':[PDF,'application/pdf'],'/cla-pdf-font.ttf':[TTF,'font/ttf'],'/cla-pdf-font-OFL.txt':[OFL,'text/plain; charset=utf-8'],
  ...Object.fromEntries(['signature','pen','hand'].flatMap(s=>[[`/cla-sig-${s}.ttf`,[TTF+s,'font/ttf']],[`/cla-sig-${s}-${s==='pen'?'LICENSE':'OFL'}.txt`,[OFL+s,'text/plain; charset=utf-8']]]))};
function assets(table=FIXED){const seen=[];return {seen,fetch:async req=>{
  seen.push([new URL(req.url).pathname,req.redirect,req.method]);const hit=table[new URL(req.url).pathname];
  return hit?new Response(hit[0],{headers:{'content-type':hit[1]}}):new Response('missing',{status:404});}};}
const env=(binding,extra={})=>({DB:deniedDB,CLA_CONFIG:JSON.stringify(config),CLA_UI_ENABLED:'true',CLA_EVIDENCE_KEY:'INVALID',CLA_PUBLIC_AGREEMENT:binding,...extra});
const get=(p,e,init)=>worker.fetch(new Request(config.origin+p,init),e);

test('signed-pdf routes serve exactly the fixed assets with the right types and the standard security headers',async()=>{
  const a=assets();
  const expected=[['/cla-pdf.js','text/javascript; charset=utf-8',JS,'/cla-pdf.js'],['/agreement.pdf','application/pdf',PDF,'/CLA_INDIVIDUAL.pdf'],
    ['/cla-pdf-font.ttf','font/ttf',TTF,'/cla-pdf-font.ttf'],['/cla-pdf-font-OFL.txt','text/plain; charset=utf-8',OFL,'/cla-pdf-font-OFL.txt'],
    ...['signature','pen','hand'].flatMap(s=>{const l=`/cla-sig-${s}-${s==='pen'?'LICENSE':'OFL'}.txt`;return [[`/cla-sig-${s}.ttf`,'font/ttf',TTF+s,`/cla-sig-${s}.ttf`],[l,'text/plain; charset=utf-8',OFL+s,l]];})];
  for(const [route,type,body,asset] of expected){
    a.seen.length=0;const r=await get(route,env(a));
    assert.equal(r.status,200,route);assert.equal(r.headers.get('content-type'),type);assert.equal(await r.text(),body);
    assert.equal(r.headers.get('cache-control'),'no-store');assert.equal(r.headers.get('x-content-type-options'),'nosniff');assert.equal(r.headers.get('referrer-policy'),'no-referrer');
    assert.equal(r.headers.get('content-security-policy'),"default-src 'none'; frame-ancestors 'none'");
    assert.deepEqual(a.seen,[[asset,'manual','GET']],'one fixed asset path, redirects not followed');
  }
  assert.equal(dbCalls,0);assert.equal(outbound,0);
});

test('signed-pdf: any other path or method yields nothing and never reaches the asset binding',async()=>{
  const a=assets();
  for(const [p,init] of [['/cla-pdf.js?x=1'],['/agreement.pdf?packet=x'],['/CLA_INDIVIDUAL.pdf'],['/CLA_INDIVIDUAL.md'],['/cla-pdf.js.map'],['/cla-pdf.js/'],['/CLA-PDF.JS'],['/fonts/cla-pdf-font.ttf'],
    ['/..%2fCLA_INDIVIDUAL.pdf'],['/cla-pdf-font.ttf%00'],['/assets/cla-pdf.js'],['/cla-pdf.js',{method:'POST',body:'{}',headers:{'content-type':'application/json'}}],['/agreement.pdf',{method:'HEAD'}]]) {
    const r=await get(p,env(a),init);assert.notEqual(r.status,200,p);assert.ok(!['application/pdf','font/ttf'].includes(r.headers.get('content-type')),p);
    assert.equal(r.headers.get('content-security-policy'),"default-src 'none'; frame-ancestors 'none'",p);
  }
  assert.deepEqual(a.seen,[],'asset binding untouched');assert.equal(dbCalls,0);
});

test('signed-pdf routes fail closed: missing binding, wrong type, redirect, 404, thrown fetch, oversized',async()=>{
  assert.equal((await get('/cla-pdf.js',env(undefined))).status,503);
  for(const table of [{'/cla-pdf.js':[JS,'text/html']},{'/cla-pdf.js':[JS,'application/octet-stream']},{}])
    {const r=await get('/cla-pdf.js',env(assets(table)));assert.equal(r.status,503);assert.equal((await r.json()).error,'pdf_asset_unavailable');}
  const redirect={fetch:async()=>new Response(null,{status:302,headers:{location:'https://evil.invalid/x.js'}})};assert.equal((await get('/agreement.pdf',env(redirect))).status,503);
  assert.equal((await get('/cla-pdf-font.ttf',env({fetch:async()=>{throw Error('ASSET_DOWN');}}))).status,503);
  const big={fetch:async()=>new Response(PDF,{headers:{'content-type':'application/pdf','content-length':String(9*1024*1024)}})};assert.equal((await get('/agreement.pdf',env(big))).status,503);
  assert.equal(outbound,0);
});

test('staging deployment still routes the worker first and the committed PDF assets match the pinned agreement',async()=>{
  const manifest=JSON.parse(fs.readFileSync(staging('wrangler.staging.json'),'utf8'));
  assert.equal(manifest.assets.run_worker_first,true);deploymentPlan(manifest,{profile:'staging'});
  const md=fs.readFileSync(staging('assets/CLA_INDIVIDUAL.md'));
  assert.equal(sha(md),config.policy.agreements.individual.sha256);
  const bytes=fs.readFileSync(staging('assets/CLA_INDIVIDUAL.pdf')),doc=await PDFDocument.load(bytes);
  assert.deepEqual((doc.getKeywords()??'').split(/\s+/).filter(x=>x.startsWith(HASH_KEYWORD)),[HASH_KEYWORD+sha(md)],'PDF is stale: rerun npm run render:agreement-pdf');
  const meta=JSON.parse((doc.getSubject()??'').replace(/^tavotto-agreement-meta:/,''));
  assert.equal(meta.publishedUrl,'https://github.com/tavotto-cla-sandbox/cla-mq-sandbox/blob/main/docs/legal/CLA_INDIVIDUAL.md','staging metadata');
  assert.deepEqual(Object.keys(meta).sort(),['fields','publishedUrl'],'staging metadata');
  assert.deepEqual(Object.keys(meta.fields).sort(),['contributor','holder'],'the real text carries its own signature block');
  assert.ok(doc.getPageCount()>=1);for(const p of doc.getPages())assert.deepEqual([p.getWidth(),p.getHeight()],[612,792],'US Letter');
  assert.ok(bytes.length<8*1024*1024);
});

test('signature font is OFL with its licence file beside it',async()=>{
  const ofl=fs.readFileSync(staging('assets/cla-pdf-font-OFL.txt'),'utf8');
  assert.match(ofl,/SIL OPEN FONT LICENSE Version 1\.1/i);assert.match(ofl,/Great Vibes/);
  const font=fs.readFileSync(await ensureFont());assert.equal(sha(font),FONT_SHA256);assert.ok(font.length>10000&&font.length<2*1024*1024);
  assert.deepEqual([...font.subarray(0,4)],[0,1,0,0],'TrueType');
});

test('/capabilities reports signedPdf:true only while every PDF asset is really retrievable',async()=>{
  const caps=async binding=>await (await get('/capabilities',env(binding))).json();
  assert.equal((await caps(assets())).signedPdf,true);
  assert.equal('signedPdf' in await caps(undefined),false,'no binding');
  for(const missing of Object.keys(FIXED)){const table={...FIXED};delete table[missing];assert.equal('signedPdf' in await caps(assets(table)),false,missing+' missing');}
  assert.equal('signedPdf' in await caps(assets({...FIXED,'/cla-pdf.js':[JS,'text/html']})),false,'wrong type');
  assert.equal('signedPdf' in await caps({fetch:async()=>{throw Error('ASSET_DOWN');}}),false);
  const c=await caps(assets());assert.deepEqual(Object.keys(c).sort(),['checkPublishingEnabled','oauthEnabled','signedPdf','signingEnabled']);
  assert.equal(dbCalls,0);
});
