// Signed PDF copy (src/pdf): interface, agreement-hash refusal, page order, text content, non-Latin names.
// Synthetic identities only; assets come from deployment/staging/assets; nothing leaves the process.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import vm from 'node:vm';
import {PDFArray,PDFDocument,PDFName,decodePDFRawStream} from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import {build} from '../src/pdf/build.ts';
import {buildPdfClient} from '../scripts/build-pdf-client.mjs';
import {AGREEMENT_SHA,agreementPdf,evidence,fakeRasterizer,font,input,pdfWithKeyword,stagingPdf,stagingPdfFull} from './pdf-fixture.mjs';

async function pages(bytes){
  const doc=await PDFDocument.load(bytes);
  return doc.getPages().map(page=>{
    const ctx=page.node.context,c=page.node.Contents();
    const streams=(c instanceof PDFArray?c.asArray().map(r=>ctx.lookup(r)):[c]);
    const content=streams.map(s=>Buffer.from(decodePDFRawStream(s).decode()).toString('latin1')).join('\n');
    const lines=[...content.matchAll(/<([0-9A-Fa-f]+)>\s*Tj/g)].map(m=>Buffer.from(m[1],'hex').toString('latin1'));
    const xo=page.node.Resources()?.lookup(PDFName.of('XObject'));
    return {text:lines.join('\n'),flat:lines.join('').replace(/\s+/g,''),images:xo?xo.keys().length:0};
  });
}
const rejects=(promise,code)=>assert.rejects(promise,e=>{assert.equal(e.message,code);return true;});

test('TavottoClaPdf interface: the bundle defines exactly one global with exactly build()',async()=>{
  const {code,inputs}=await buildPdfClient();
  assert.doesNotMatch(code,/\b__name\(/);assert.doesNotMatch(code,/\bimport\(|\beval\(|new Function\(/);
  assert.ok(inputs.every(i=>/^(src\/pdf\/[a-z]+\.ts|node_modules\/(pdf-lib|@pdf-lib\/fontkit|@pdf-lib\/standard-fonts|@pdf-lib\/upng|pako|tslib|brotli|restructure|fast-deep-equal|unicode-trie|tiny-inflate|dfa|clone)\b.*)$/.test(i)),'unexpected bundle input: '+inputs.find(i=>!/^src\/pdf|^node_modules\//.test(i)));
  const context={TextEncoder,TextDecoder,URL,setTimeout,clearTimeout,console};context.globalThis=context;
  vm.runInNewContext(code,context);
  assert.deepEqual(Object.keys(context.TavottoClaPdf),['build']);
  assert.equal(typeof context.TavottoClaPdf.build,'function');assert.ok(Object.isFrozen(context.TavottoClaPdf));
  // The bundled build() is the same behaviour: refuses before touching the network when inputs are wrong.
  await assert.rejects(context.TavottoClaPdf.build({}),e=>e.message==='agreement_input_invalid');
});

test('builder returns a PDF: agreement pages first, then Signatures, then the certificate, footers Page N of M',async()=>{
  for(const n of [1,5]){
    const bytes=await build(await input({overrides:{agreementPdf:await agreementPdf(AGREEMENT_SHA,n)}}));
    assert.equal(Buffer.from(bytes.subarray(0,5)).toString(),'%PDF-');
    const p=await pages(bytes);assert.equal(p.length,n+2);
    for(let i=0;i<n;i++){assert.match(p[i].text,/Individual Contributor License Agreement · version 1\.0/);assert.doesNotMatch(p[i].text,/Signatures|Electronic signature certificate/);}
    assert.match(p[n].text,/Signatures\n/);assert.match(p[n].text,/YOU \(INDIVIDUAL\)|Y\nO\nU/);assert.doesNotMatch(p[n].text,/Electronic signature certificate/);
    assert.match(p[n+1].text,/Electronic signature certificate\nElectronic signature certificate|Electronic signature certificate/);
    p.forEach((page,i)=>assert.match(page.text,new RegExp(`Page ${i+1} of ${n+2}$`,'m')));
  }
});

test('Signatures and certificate carry names, addresses, logins, ids, times and every fingerprint',async()=>{
  const i=await input(),c=i.contributor,h=i.holder,[,sig,cert]=await pages(await build(i));
  for(const e of [c,h]){for(const s of [e.name,e.address,`@${e.github_login_at_signing} · ID ${e.github_id}`])assert.ok(sig.text.includes(s),'signature page: '+s);}
  assert.ok(sig.text.includes('Rights holder'));assert.ok(sig.text.includes('4 November 2026, 13:02:41 UTC'));assert.ok(sig.text.includes('4 November 2026, 13:20:07 UTC'));
  assert.ok(sig.text.includes('Thank you for contributing to Tavotto. Every improvement you send makes the tool better for researchers everywhere.'.split(' ').slice(0,8).join(' ')));
  for(const s of [c.packet,AGREEMENT_SHA,i.agreement.publishedUrl,c.evidence_sha256,h.evidence_sha256])assert.ok(cert.flat.includes(s.replace(/\s+/g,'')),'certificate: '+s);
  for(const e of [c,h])for(const s of [`@${e.github_login_at_signing} (ID ${e.github_id})`])assert.ok(cert.text.includes(s));
  assert.ok(cert.text.includes('2026-11-04 13:02:41 UTC')&&cert.text.includes('2026-11-04 13:20:07 UTC'));
  assert.ok(cert.text.includes('Read the agreement · Personal decision · Electronic signature · Own identity · 18'));
  assert.ok(cert.flat.includes('Reviewedasrightsholder'));assert.ok(cert.flat.includes('Signsasanindividual'));
  assert.ok(cert.flat.includes('ifiteverdisagreeswiththeservicerecord,therecordgoverns.'));
  assert.ok(cert.text.includes('Generated 4 November 2026 · cla.example.test'));
  assert.equal(sig.images+cert.images,0,'plain Latin text needs no raster fallback');
});

test('builder refuses an agreement PDF whose hash does not equal the agreement being signed',async()=>{
  const other='0'.repeat(64);
  await rejects(build(await input({overrides:{agreementPdf:await agreementPdf(other)}})),'agreement_pdf_hash_mismatch');
  await rejects(build(await input({overrides:{agreementPdf:await pdfWithKeyword(null)}})),'agreement_pdf_hash_mismatch');
  await rejects(build(await input({overrides:{agreementPdf:await pdfWithKeyword('tavotto-agreement-sha256:'+AGREEMENT_SHA.toUpperCase())}})),'agreement_pdf_hash_mismatch');
  await rejects(build(await input({overrides:{agreementPdf:await pdfWithKeyword(`tavotto-agreement-sha256:${AGREEMENT_SHA} tavotto-agreement-sha256:${other}`)}})),'agreement_pdf_hash_mismatch');
  // The same PDF is refused when the caller's agreement hash is the other one, and accepted for its own.
  await rejects(build(await input({agreement:{...(await input()).agreement,sha256:other},contributor:evidence('contributor',{agreement_sha256:other}),holder:evidence('holder',{agreement_sha256:other})})),'agreement_pdf_hash_mismatch');
  assert.ok((await build(await input({overrides:{agreementPdf:stagingPdf}}))).length>1000);
});

test('builder refuses inconsistent evidence before drawing anything',async()=>{
  const base=await input(),bad=(patch,code)=>rejects(build({...base,...patch}),code);
  await bad({contributor:evidence('contributor',{agreement_sha256:'1'.repeat(64)})},'evidence_agreement_mismatch');
  await bad({holder:evidence('holder',{agreement_version:'2.0'})},'evidence_agreement_mismatch');
  await bad({holder:evidence('holder',{packet:'pkt_other'})},'evidence_packet_mismatch');
  await bad({contributor:evidence('holder')},'evidence_role_mismatch');
  await bad({contributor:evidence('contributor',{signature:'Someone Else'})},'evidence_invalid');
  await bad({contributor:evidence('contributor',{attestations:{personal:true,read:false}})},'evidence_attestations_invalid');
  await bad({contributor:evidence('contributor',{github_login_at_signing:'bad login'})},'evidence_invalid');
  await bad({agreement:{...base.agreement,kind:'weird'}},'agreement_input_invalid');
  await bad({agreement:{...base.agreement,publishedUrl:'javascript:alert(1)'}},'agreement_input_invalid');
  // A fingerprint may arrive beside the evidence instead of inside it, but never disagree with it.
  const c=evidence('contributor');const fp=c.evidence_sha256;delete c.evidence_sha256;
  assert.ok((await build({...base,contributor:c,contributorSha256:fp})).length>1000);
  await bad({contributor:{...c,evidence_sha256:fp},contributorSha256:'2'.repeat(64)},'evidence_fingerprint_missing');
  await bad({contributor:{...c,evidence_sha256:'xyz'}},'evidence_fingerprint_missing');
});

test('Chinese names and addresses are rasterised whole, never dropped or mangled; Latin text stays real text',async()=>{
  const r=fakeRasterizer(),name='王嘉琪',address='福建省福州市仓山区示例路12号';
  const i=await input({contributor:evidence('contributor',{name,signature:name,address}),overrides:{rasterizer:r}});
  const [,sig]=await pages(await build(i));
  const drawn=new Set(r.calls.map(c=>c.text));
  assert.ok(drawn.has(name),'name rasterised');assert.ok(drawn.has(address),'address rasterised');
  assert.ok(r.calls.some(c=>c.text===name&&c.style==='script'),'signature uses the script style');
  assert.ok(r.calls.some(c=>c.text===name&&c.style==='sans'),'printed name uses the sans style');
  assert.ok(sig.images>=3,'signature + name + address images');
  assert.ok(sig.text.includes('@synthetic-contributor · ID 261183172'),'ASCII rows are still text');
  assert.ok(sig.text.includes('Jiaqi Wan'),'Latin holder name stays text (script font covers it)');
  assert.ok(![...drawn].some(t=>t==='Jiaqi Wan'),'Latin holder name is not rasterised');
  // Mixed scripts stay one run, so nothing is split or reordered.
  const mixed='Wan 嘉琪',r2=fakeRasterizer();
  await build(await input({contributor:evidence('contributor',{name:mixed,signature:mixed}),overrides:{rasterizer:r2}}));
  assert.ok(r2.calls.some(c=>c.text===mixed));
});

test('without a canvas the builder fails loudly for non-Latin text instead of printing question marks',async()=>{
  const name='王嘉琪';
  await assert.rejects(build(await input({contributor:evidence('contributor',{name,signature:name})})),e=>e.message==='canvas_unavailable');
});

test('accented Latin names need no raster fallback',async()=>{
  const r=fakeRasterizer(),name='José Müller-Ångström';
  const [,sig]=await pages(await build(await input({contributor:evidence('contributor',{name,signature:name}),overrides:{rasterizer:r}})));
  assert.equal(r.calls.length,0);assert.ok(sig.text.includes(name));
});

test('very long addresses flow onto continuation pages; order and footers stay correct',async()=>{
  const address=('Building 7, Unit 3, 12 Very Long Example Boulevard, Industrial Park Phase II, ').repeat(13).slice(0,1000);
  const bytes=await build(await input({contributor:evidence('contributor',{address}),holder:evidence('holder',{address})}));
  const p=await pages(bytes);assert.ok(p.length>3);
  const sigAt=p.findIndex(x=>/Signatures\n/.test(x.text)),certAt=p.findIndex(x=>/Electronic signature certificate/.test(x.text.replace(/^[^\n]*\n[^\n]*\n/,'')));
  assert.equal(sigAt,1);assert.ok(certAt>sigAt);
  assert.equal(p[p.length-1].text.includes('Page '+p.length+' of '+p.length),true);
  assert.ok(p.at(-1).text.includes('the record governs')||p.at(-1).flat.includes('therecordgoverns'));
  assert.equal(p.filter(x=>x.flat.includes('Thankyoufor')).length,1);
});

test('publishedUrl and fingerprints are optional: their rows are simply omitted; consent wording version is printed',async()=>{
  const base=await input(),a={...base.agreement};delete a.publishedUrl;
  const c=evidence('contributor',{consent_ui_version:'2'}),h=evidence('holder');delete c.evidence_sha256;delete h.evidence_sha256;
  const [,,cert]=await pages(await build({...base,agreement:a,contributor:c,holder:h,overrides:{...base.overrides,agreementPdf:await agreementPdf()}}));
  assert.doesNotMatch(cert.text,/Published text/);assert.doesNotMatch(cert.text,/Evidence fingerprint/);
  assert.ok(cert.text.includes('Consent wording version'));assert.ok(cert.flat.includes('Consentwordingversion2'));
  assert.ok(cert.text.includes('Time')&&cert.text.includes(base.contributor.packet));
  const [,,full]=await pages(await build(base));assert.ok(full.text.includes('Published text')&&full.text.includes('Evidence fingerprint'));
});

test('multi-word signatures keep a visible word gap (Great Vibes space is too narrow)',async()=>{
  const bytes=await build(await input()),doc=await PDFDocument.load(bytes),page=doc.getPages()[1]; // Signatures page
  const ctx=page.node.context,c=page.node.Contents();
  const content=(c instanceof PDFArray?c.asArray().map(r=>ctx.lookup(r)):[c]).map(x=>Buffer.from(decodePDFRawStream(x).decode()).toString('latin1')).join('\n');
  // Each drawText is: ... /Font 27 Tf ... x y Tm <hex> Tj. Collect the size-27 runs (the two signatures) with their x.
  const runs=[...content.matchAll(/\/(\S+) 27 Tf[\s\S]*?1 0 0 1 ([\d.]+) ([\d.]+) Tm\s*<([0-9A-Fa-f]+)> Tj/g)].map(m=>({x:Number(m[2]),y:Number(m[3])}));
  assert.equal(runs.length,4,'Wan + Jiaqi, then Jiaqi + Wan');
  const probe=await PDFDocument.create();probe.registerFontkit(fontkit);const f=await probe.embedFont(font);
  const [w1,w2]=[runs[2],runs[3]];assert.equal(w1.y,w2.y);
  const first=f.widthOfTextAtSize('Jiaqi',27),gap=w2.x-(w1.x+first);
  assert.ok(gap>=27*.3,'gap between words is at least ~0.3em, got '+gap);
  assert.ok(gap<=27*.4);
});

test('agreement/publishedUrl come from the hash-verified PDF metadata unless the caller passes them',async()=>{
  const base=await input(),a={...base.agreement};delete a.publishedUrl;
  const [,,cert]=await pages(await build({...base,agreement:a}));   // staging PDF carries the sandbox URL
  assert.ok(cert.flat.includes('github.com/tavotto-cla-sandbox/cla-mq-sandbox/blob/main/docs/legal/CLA_INDIVIDUAL.md'));
  const [,,own]=await pages(await build(base));assert.ok(own.flat.includes('github.com/example/example'));assert.ok(!own.flat.includes('cla-e2e-sandbox'));
  // attribution: from metadata when the PDF has one, caller wins.
  const {PDFDocument:D}=await import('pdf-lib');
  const withMeta=async attribution=>{const d=await D.load(stagingPdf);d.setSubject('tavotto-agreement-meta:'+JSON.stringify({attribution}));return new Uint8Array(await d.save());};
  const sigText=async(i)=>(await pages(await build(i)))[1].text;
  assert.ok((await sigText(await input({overrides:{agreementPdf:await withMeta('Derived from the Example CLA')}}))).includes('Derived from the Example CLA'));
  assert.ok((await sigText(await input({agreement:{...a,attribution:'Caller wins'},overrides:{agreementPdf:await withMeta('Derived from the Example CLA')}}))).includes('Caller wins'));
  // Malformed or hostile metadata is ignored, never an error and never drawn as a link.
  for(const subject of ['tavotto-agreement-meta:{not json','tavotto-agreement-meta:{"publishedUrl":"javascript:alert(1)"}']){
    const d=await D.load(stagingPdf);d.setSubject(subject);const [,,c]=await pages(await build({...base,agreement:a,overrides:{...base.overrides,agreementPdf:new Uint8Array(await d.save())}}));
    assert.doesNotMatch(c.text,/Published text|javascript/);}
});

test('a rasteriser callback that never returns ends in build_timeout, not an eternal pending',async()=>{
  const name='王嘉琪',hang={measure:()=>10,render:()=>new Promise(()=>{})};
  const t0=Date.now(),guard=(p)=>Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new assert.AssertionError({message:'build hung past its own limit'})),5000).unref())]);
  await rejects(guard(build(await input({contributor:evidence('contributor',{name,signature:name}),overrides:{rasterizer:hang,timeoutMs:200}}))),'build_timeout');
  assert.ok(Date.now()-t0<3000);
  // A fetch that never answers is bounded the same way (the default path used in browsers).
  const real=globalThis.fetch;globalThis.fetch=()=>new Promise(()=>{});
  try{const i=await input({overrides:{timeoutMs:200}});delete i.overrides.agreementPdf;await rejects(guard(build(i)),'build_timeout');}finally{globalThis.fetch=real;}
  // Normal builds are unaffected by the limit and leave no timer behind (the test process would not exit otherwise).
  assert.ok((await build(await input({overrides:{timeoutMs:60000}}))).length>1000);
});

// Every drawn item (text baselines and raster images) on every page, in content order.
async function drawn(bytes){
  const doc=await PDFDocument.load(bytes);
  return doc.getPages().map(page=>{
    const ctx=page.node.context,c=page.node.Contents();
    const content=(c instanceof PDFArray?c.asArray().map(r=>ctx.lookup(r)):[c]).map(x=>Buffer.from(decodePDFRawStream(x).decode()).toString('latin1')).join('\n');
    const texts=[...content.matchAll(/BT[\s\S]*?\/\S+ ([\d.]+) Tf[\s\S]*?1 0 0 1 ([\d.-]+) ([\d.-]+) Tm\s*<([0-9A-Fa-f]+)> Tj[\s\S]*?ET/g)].map(m=>({size:Number(m[1]),x:Number(m[2]),y:Number(m[3]),text:Buffer.from(m[4],'hex').toString('latin1')}));
    const images=[...content.matchAll(/1 0 0 1 ([\d.e-]+) ([\d.e-]+) cm\s+1 0 0 1 0 0 cm\s+([\d.e-]+) 0 0 ([\d.e-]+) 0 0 cm[\s\S]*?Do/g)].map(m=>({x:Number(m[1]),y:Number(m[2]),w:Number(m[3]),h:Number(m[4])}));
    return {texts,images};
  });
}
const USABLE_BOTTOM=78,USABLE_TOP=792-64.8-8.5-10; // LIMIT in build.ts; below the header rule

for(const [label,mk] of [['Latin',n=>`Address line ${String(n).padStart(2,'0')}`],['Chinese',n=>`示例路第${String(n).padStart(2,'0')}行`]]) {
  test(`${label} 50-line addresses continue across pages: nothing drawn outside the usable area, order and footers correct`,async()=>{
    const lines=Array.from({length:50},(_,i)=>mk(i+1)),address=lines.join('\n'),r=fakeRasterizer();
    const bytes=await build(await input({contributor:evidence('contributor',{address}),holder:evidence('holder',{address}),overrides:{rasterizer:r}}));
    const p=await drawn(bytes),t=await pages(bytes);
    assert.ok(p.length>=5,'agreement + several signature pages + certificate, got '+p.length);
    p.forEach((page,i)=>assert.match(t[i].text,new RegExp(`Page ${i+1} of ${p.length}$`,'m')));
    assert.match(t.at(-1).text,/Electronic signature certificate/);
    // Every address line (size 9.5 text, or raster image) sits inside [LIMIT, header rule] on its page.
    for(const [i,page] of p.entries()){
      for(const x of page.texts.filter(x=>x.size===9.5))assert.ok(x.y>=USABLE_BOTTOM&&x.y<=USABLE_TOP,`page ${i+1} text baseline ${x.y} outside usable area`);
      for(const im of page.images)assert.ok(im.y>=USABLE_BOTTOM-6&&im.y+im.h<=USABLE_TOP+6,`page ${i+1} image at ${im.y} outside usable area`);
    }
    // Order: both parties' lines appear 1..50 in sequence, contributor first, holder second; signature page continues.
    if(label==='Latin'){
      const seq=t.flatMap(x=>x.text.split('\n')).filter(x=>/^Address line \d\d$/.test(x));
      assert.deepEqual(seq,[...lines,...lines]);
    } else {
      assert.deepEqual(r.calls.map(c=>c.text).filter(x=>/^示例路/.test(x)),[...lines,...lines]);
      assert.ok(p.reduce((n,x)=>n+x.images.length,0)>=100);
    }
    // The rows after the address (identity, time) are still drawn, on a page, inside the area.
    assert.equal(t.filter(x=>x.text.includes('@synthetic-contributor · ID')).length,1);
    assert.equal(t.filter(x=>x.text.includes('@synthetic-holder · ID')).length,1);
    assert.equal(t.filter(x=>x.text.includes('4 November 2026, 13:20:07 UTC')).length,1);
    assert.equal(t.filter(x=>x.text.includes('Thank you for')).length,1);
  });
}

test('the shipped staging agreement is filled in place: agreement pages plus the certificate, no separate Signatures page',async()=>{
  const out=await pages(await build(await input({overrides:{agreementPdf:stagingPdfFull,rasterizer:fakeRasterizer()}})));
  const n=(await PDFDocument.load(stagingPdfFull)).getPageCount();
  assert.equal(out.length,n+1);assert.ok(!out.some(p=>p.flat.includes('Signatures')),'no separate Signatures page');
  assert.ok(out[n-1].flat.includes('WanJiaqi')&&out[n-1].flat.includes('@synthetic-contributor')&&out[n-1].flat.includes('Rightsholder'));
  assert.ok(out[n].flat.includes('Electronicsignaturecertificate')&&out[n].flat.includes('Thankyouforcontributing'));
});
