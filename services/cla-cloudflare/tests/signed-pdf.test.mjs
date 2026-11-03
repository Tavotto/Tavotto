// Signed PDF copy (src/pdf): interface, agreement-hash refusal, page order, text content, non-Latin names.
// Synthetic identities only; assets come from deployment/staging/assets; nothing leaves the process.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import vm from 'node:vm';
import {PDFArray,PDFDocument,PDFName,decodePDFRawStream} from 'pdf-lib';
import {build} from '../src/pdf/build.ts';
import {buildPdfClient} from '../scripts/build-pdf-client.mjs';
import {AGREEMENT_SHA,agreementPdf,evidence,fakeRasterizer,input,pdfWithKeyword,stagingPdf} from './pdf-fixture.mjs';

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
  assert.equal(r.calls.length,0);assert.ok(sig.text.includes(name.replace('é','\xe9').replace('ü','\xfc').replace('Å','\xc5')));
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
  const [,,cert]=await pages(await build({...base,agreement:a,contributor:c,holder:h}));
  assert.doesNotMatch(cert.text,/Published text/);assert.doesNotMatch(cert.text,/Evidence fingerprint/);
  assert.ok(cert.text.includes('Consent wording version'));assert.ok(cert.flat.includes('Consentwordingversion2'));
  assert.ok(cert.text.includes('Time')&&cert.text.includes(base.contributor.packet));
  const [,,full]=await pages(await build(base));assert.ok(full.text.includes('Published text')&&full.text.includes('Evidence fingerprint'));
});
