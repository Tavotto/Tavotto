// Signed PDF, in-place signing: the agreement's own signature block (positions in the PDF Subject meta `fields`) is filled
// instead of appending a Signatures page. Strict meta validation, fit/shrink rules, and the fallback to the Signatures page.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {PDFDocument} from 'pdf-lib';
import {getDocument} from 'pdfjs-dist/legacy/build/pdf.mjs';
import {build,META_SUBJECT,HASH_KEYWORD} from '../src/pdf/build.ts';
import {validFields} from '../src/pdf/evidence.ts';
import {AGREEMENT_SHA,evidence,fakeRasterizer,input} from './pdf-fixture.mjs';

const PAGES=[{width:612,height:792},{width:612,height:792}];
const box=(y,extra={})=>({page:1,x:136.5,y,w:407.25,...extra});
const contributor=()=>({signature:box(628.5),name:box(603.75),address:[box(579),box(553.5)],date:box(528.75),github:box(504)});
const holder=()=>({signature:box(423.75),name:box(399,{printed:true}),title:box(374.25),address:[box(348.75)],date:box(324)});
const fields=()=>({contributor:contributor(),holder:holder()});
async function agreementWith(f,pages=2){
  const d=await PDFDocument.create();for(let i=0;i<pages;i++)d.addPage([612,792]);
  d.setKeywords([HASH_KEYWORD+AGREEMENT_SHA]);d.setSubject(META_SUBJECT+JSON.stringify({fields:f}));
  return new Uint8Array(await d.save());
}
async function items(bytes,pageIndex){
  const doc=await getDocument({data:new Uint8Array(bytes),verbosity:0}).promise,page=await doc.getPage(pageIndex+1);
  return (await page.getTextContent()).items.filter(i=>i.str.trim()!=='').map(i=>({str:i.str,x:i.transform[4],y:i.transform[5],w:i.width,size:Math.abs(i.transform[3])}));
}
const onLine=(all,b)=>all.filter(i=>Math.abs(i.y-(b.y+3))<.01&&i.x>=b.x-.01);
const allText=async(bytes,n)=>{const out=[];for(let i=0;i<n;i++)out.push(...(await items(bytes,i)).map(x=>x.str));return out.join('\n');};

test('validFields accepts the renderer output shape and nothing else',()=>{
  assert.ok(validFields(fields(),PAGES));
  const bad=[];const mut=fn=>{const f=fields();fn(f);bad.push(f);};
  mut(f=>{f.contributor.name.page=2;});mut(f=>{f.contributor.name.page=0.5;});mut(f=>{f.contributor.name.x=-1;});mut(f=>{f.contributor.name.w=700;});
  mut(f=>{f.contributor.name.y=792;});mut(f=>{f.contributor.name.y=0;});mut(f=>{f.contributor.name.w=5;});mut(f=>{f.contributor.name.y=null;});mut(f=>{f.contributor.name.x='136';});
  mut(f=>{f.contributor.name.printed=false;});mut(f=>{f.contributor.name.extra=1;});mut(f=>{delete f.holder.title;});mut(f=>{f.holder.surprise=box(300);});
  mut(f=>{f.contributor.address=[box(1),box(2),box(3),box(4)];});mut(f=>{f.contributor.address=[];});mut(f=>{f.contributor.address=box(579);});mut(f=>{f.contributor.github=undefined;});
  for(const [i,f] of bad.entries())assert.equal(validFields(f,PAGES),undefined,'case '+i);
  for(const x of [undefined,null,[],'x',{contributor:contributor()},{...fields(),extra:1}])assert.equal(validFields(x,PAGES),undefined);
  assert.equal(validFields(fields(),[]),undefined);
});

test('in-place: values sit on the agreement\'s own lines; no Signatures page; certificate last with the thanks box',async()=>{
  const bytes=await build(await input({overrides:{agreementPdf:await agreementWith(fields())}}));
  const doc=await PDFDocument.load(bytes);assert.equal(doc.getPageCount(),3);   // 2 agreement pages + certificate
  const text=await allText(bytes,3);
  assert.doesNotMatch(text,/Signatures/);
  assert.match(text,/Electronic signature certificate/);assert.match(text,/Thank you for contributing to Tavotto/);
  assert.match(text,/Signed electronically · see the certificate on the next page/);assert.match(text,/Page 3 of 3/);
  const p=await items(bytes,1),f=fields(),c=evidence('contributor'),h=evidence('holder');
  assert.deepEqual(onLine(p,f.contributor.name).map(i=>i.str),[c.name]);
  assert.deepEqual(onLine(p,f.contributor.address[0]).map(i=>i.str),[c.address]);
  assert.deepEqual(onLine(p,f.contributor.date).map(i=>i.str),['4 November 2026']);
  assert.deepEqual(onLine(p,f.contributor.github).map(i=>i.str),['@synthetic-contributor']);
  assert.deepEqual(onLine(p,f.holder.title).map(i=>i.str),['Rights holder']);
  assert.deepEqual(onLine(p,f.holder.address[0]).map(i=>i.str),[h.address]);
  assert.deepEqual(onLine(p,f.holder.date).map(i=>i.str),['4 November 2026']);
  assert.equal(onLine(p,f.holder.name).length,0,'pre-printed holder name is not drawn again');
  assert.ok(onLine(p,f.contributor.signature).length>=2&&onLine(p,f.holder.signature).length>=2,'script signature, one run per word, baseline on the line');
  for(const i of p.filter(i=>i.y>300&&i.y<640&&i.x>=136))assert.ok(i.x+i.w<=136.5+407.25+.01,'within line width: '+i.str);
});

test('in-place: a long address shrinks and wraps over both lines; neither it nor a too-long name overflows',async()=>{
  const address='Building 7, Unit 3, 12 Very Long Example Boulevard, Industrial Park Phase II, Example City, Example Province, Examplestan 100000';
  const bytes=await build(await input({contributor:evidence('contributor',{address}),overrides:{agreementPdf:await agreementWith(fields())}}));
  assert.equal((await PDFDocument.load(bytes)).getPageCount(),3);
  const p=await items(bytes,1),f=fields(),l1=onLine(p,f.contributor.address[0]),l2=onLine(p,f.contributor.address[1]);
  assert.equal(l1.length,1);assert.equal(l2.length,1);assert.equal((l1[0].str+' '+l2[0].str).replace(/\s+/g,' '),address);
  for(const l of [l1[0],l2[0]]){assert.ok(l.w<=407.25+.01);assert.equal(l.size,l1[0].size);}
  // A name too wide for its line (signature below 12pt) also falls back instead of overflowing.
  const name='Wan Jiaqi Example-Longname Zhang Wei Ming Xiao Hong Li Si Wang Wu Zhao Liu Qian Sun Chen Yang Huang';
  const b2=await build(await input({contributor:evidence('contributor',{name,signature:name}),overrides:{agreementPdf:await agreementWith(fields())}}));
  assert.equal((await PDFDocument.load(b2)).getPageCount(),4);assert.equal(onLine(await items(b2,1),f.contributor.name).length,0);
});

test('in-place: an address that cannot fit even at the smallest size falls back to the Signatures page (nothing is cut)',async()=>{
  const address=('Building 7, Unit 3, 12 Very Long Example Boulevard, Industrial Park Phase II, ').repeat(13).slice(0,1000);
  const bytes=await build(await input({contributor:evidence('contributor',{address}),overrides:{agreementPdf:await agreementWith(fields())}}));
  const doc=await PDFDocument.load(bytes);assert.ok(doc.getPageCount()>3);
  assert.match(await allText(bytes,doc.getPageCount()),/Signatures/);
  assert.equal(onLine(await items(bytes,1),fields().contributor.name).length,0,'nothing was written onto the agreement page');
});

test('missing or invalid fields meta: the separate Signatures page, as before',async()=>{
  const broken=fields();broken.contributor.signature.page=9;
  const outside=fields();outside.holder.date.x=600;
  const unknown=fields();unknown.holder.extra=box(100);
  for(const f of [undefined,broken,outside,unknown,'nope',{}]){
    const bytes=await build(await input({overrides:{agreementPdf:await agreementWith(f)}}));
    const doc=await PDFDocument.load(bytes);assert.equal(doc.getPageCount(),4,'agreement x2 + Signatures + certificate');
    assert.match(await allText(bytes,4),/Signatures/);assert.equal(onLine(await items(bytes,1),fields().contributor.name).length,0);
  }
});

test('in-place: Chinese names go through the rasteriser; Latin parts stay real text',async()=>{
  const r=fakeRasterizer();
  const bytes=await build(await input({contributor:evidence('contributor',{name:'王嘉琪',signature:'王嘉琪',address:'福建省福州市仓山区示例路 12 号'}),overrides:{agreementPdf:await agreementWith(fields()),rasterizer:r}}));
  assert.equal((await PDFDocument.load(bytes)).getPageCount(),3);
  assert.ok(r.calls.some(c=>c.text==='王嘉琪'&&c.style==='script'));assert.ok(r.calls.some(c=>c.text==='王嘉琪'&&c.style==='serif'));assert.ok(r.calls.some(c=>/^福建省/.test(c.text)&&c.style==='serif'));
  assert.deepEqual(onLine(await items(bytes,1),fields().contributor.date).map(i=>i.str),['4 November 2026']);
});

test('the fields meta only counts from a hash-verified PDF',async()=>{
  const d=await PDFDocument.load(await agreementWith(fields()));d.setKeywords([HASH_KEYWORD+'0'.repeat(64)]);
  await assert.rejects(build(await input({overrides:{agreementPdf:new Uint8Array(await d.save())}})),e=>e.message==='agreement_pdf_hash_mismatch');
});
