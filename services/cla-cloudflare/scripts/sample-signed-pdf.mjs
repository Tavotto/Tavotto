// Dev tool: builds sample signed PDFs from SYNTHETIC data inside a real headless Chrome, so the real canvas
// fallback and the real bundle are exercised (Node tests use a fake rasteriser).
//   node scripts/sample-signed-pdf.mjs <out-dir> [agreement.md]    (default agreement: docs/legal/CLA_INDIVIDUAL.md)
// Writes <out-dir>/sample-latin.pdf and <out-dir>/sample-chinese.pdf. Chrome runs with a throw-away profile.
import {createHash,randomBytes} from 'node:crypto';
import {execFile} from 'node:child_process';
import {mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {promisify} from 'node:util';
import {buildPdfClient} from './build-pdf-client.mjs';
import {renderAgreementPdf} from './render-agreement-pdf.mjs';
const run=promisify(execFile),root=fileURLToPath(new URL('../',import.meta.url));
const [outDir,mdArg]=process.argv.slice(2);
if(!outDir){console.error('usage: sample-signed-pdf.mjs <out-dir> [agreement.md]');process.exit(2);}
const md=path.resolve(mdArg??path.join(root,'../../docs/legal/CLA_INDIVIDUAL.md')),sha=createHash('sha256').update(await readFile(md)).digest('hex');
const work=await mkdtemp(path.join(os.tmpdir(),'cla-sample-pdf-'));
try{
  await mkdir(outDir,{recursive:true});
  const agreementPdf=path.join(work,'agreement.pdf');await renderAgreementPdf(md,agreementPdf,{title:'Tavotto Individual Contributor License Agreement',publishedUrl:'https://github.com/example/example/blob/main/docs/legal/CLA_INDIVIDUAL.md',attribution:'Derived from the Harmony Individual CLA 1.0, \u00a9 2011 Project Harmony, CC BY 3.0'});
  const b64=async f=>(await readFile(f)).toString('base64');
  const hex=n=>randomBytes(n).toString('hex'),packet='pkt_'+randomBytes(24).toString('base64url');
  const ev=(role,o)=>({protocol:'cla-signature-v1',packet,role,agreement_kind:'individual',agreement_version:'1.0',agreement_sha256:sha,consent_ui_version:'1',evidence_sha256:hex(32),
    attestations:Object.fromEntries((role==='contributor'?['personal','electronic','read','identity','adult','own_rights','independent']:['personal','electronic','read','identity','reviewed']).map(k=>[k,true])),...o});
  const holder=ev('holder',{name:'Jiaqi Wan',signature:'Jiaqi Wan',address:'1 Example Holder Street, Example City',github_id:88193520,github_login_at_signing:'synthetic-holder',signed_at_unix:1793798407});
  const samples={latin:ev('contributor',{name:'Wan Jiaqi',signature:'Wan Jiaqi',address:'12 Example Road, Fuzhou, Fujian, China',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361}),
    chinese:ev('contributor',{name:'王嘉琪',signature:'王嘉琪',address:'福建省福州市仓山区示例路 12 号 3 单元 501 室',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361})};
  const bundle=(await buildPdfClient()).code.replaceAll('</script','<\\/script');
  for(const [name,contributor] of Object.entries(samples)){
    const page=`<!doctype html><meta charset="utf-8"><pre id="out">pending</pre><script>${bundle}</script><script>
const bytes=b=>Uint8Array.from(atob(b),c=>c.charCodeAt(0));
TavottoClaPdf.build({agreement:{kind:'individual',version:'1.0',sha256:${JSON.stringify(sha)}},
 contributor:${JSON.stringify(contributor)},holder:${JSON.stringify(holder)},
 overrides:{agreementPdf:bytes(${JSON.stringify(await b64(agreementPdf))}),font:bytes(${JSON.stringify(await b64(path.join(root,'deployment/staging/assets/cla-pdf-font.ttf')))}),serviceHost:'cla.example.test'}})
 .then(r=>{let s='';for(let i=0;i<r.length;i+=8192)s+=String.fromCharCode(...r.subarray(i,i+8192));document.getElementById('out').textContent='DONE:'+btoa(s);})
 .catch(e=>{document.getElementById('out').textContent='ERROR:'+e.message;});</script>`;
    const file=path.join(work,name+'.html');await writeFile(file,page);
    const {stdout}=await run(process.env.CHROME_BIN??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--disable-gpu','--no-first-run',
      `--user-data-dir=${path.join(work,'profile-'+name)}`,'--virtual-time-budget=30000','--dump-dom',pathToFileURL(file).href],{timeout:120000,maxBuffer:128*1024*1024});
    const m=/<pre id="out">(DONE:[A-Za-z0-9+/=]+|ERROR:[^<]*|pending)<\/pre>/.exec(stdout);
    if(!m||!m[1].startsWith('DONE:'))throw new Error(`${name}: ${m?m[1]:'no output'}`);
    const out=path.join(outDir,`sample-${name}.pdf`);await writeFile(out,Buffer.from(m[1].slice(5),'base64'));console.log('Wrote '+out);
  }
}finally{await rm(work,{recursive:true,force:true});}
