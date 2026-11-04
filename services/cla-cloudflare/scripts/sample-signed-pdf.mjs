// Dev tool: builds sample signed PDFs from SYNTHETIC data inside a real headless Chrome, so the real canvas
// fallback and the real bundle are exercised (Node tests use a fake rasteriser).
//   node scripts/sample-signed-pdf.mjs <out-dir> [agreement.md] [--runs N] [--virtual]
// The page is driven over the DevTools protocol in REAL time (Runtime.evaluate with awaitPromise), so the build is awaited
// until it settles. --runs N repeats each sample N times and prints result + milliseconds per run (nothing is written);
// --virtual uses the old --virtual-time-budget/--dump-dom mode, kept only to reproduce its false 'pending' results.
// Writes <out-dir>/sample-latin.pdf, sample-style-{signature,pen,hand}.pdf and sample-chinese.pdf. Chrome runs with a throw-away profile.
import {createHash,randomBytes} from 'node:crypto';
import {execFile,spawn} from 'node:child_process';
import {mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {promisify} from 'node:util';
import {buildPdfClient} from './build-pdf-client.mjs';
import {renderAgreementPdf} from './render-agreement-pdf.mjs';
const run=promisify(execFile),root=fileURLToPath(new URL('../',import.meta.url));
const argv=process.argv.slice(2),flag=n=>{const i=argv.indexOf(n);if(i<0)return undefined;return argv.splice(i,n==='--virtual'?1:2)[n==='--virtual'?0:1]??true;};
const runs=Number(flag('--runs')??0),virtual=flag('--virtual')!==undefined;const [outDir,mdArg]=argv;
if(!outDir){console.error('usage: sample-signed-pdf.mjs <out-dir> [agreement.md]');process.exit(2);}
const md=path.resolve(mdArg??path.join(root,'../../docs/legal/CLA_INDIVIDUAL.md')),sha=createHash('sha256').update(await readFile(md)).digest('hex');
const work=await mkdtemp(path.join(os.tmpdir(),'cla-sample-pdf-'));
const chromeBin=process.env.CHROME_BIN??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
async function viaDumpDom(file,tag){
  const {stdout}=await run(chromeBin,['--headless=new','--disable-gpu','--no-first-run',`--user-data-dir=${path.join(work,'profile-'+tag)}`,'--virtual-time-budget=30000','--dump-dom',pathToFileURL(file).href],{timeout:120000,maxBuffer:128*1024*1024});
  const m=/<pre id="out">(DONE:[A-Za-z0-9+/=]+|ERROR:[^<]*|pending)<\/pre>/.exec(stdout);return m?m[1]:'no output';
}
// Real-time driver: start Chrome with a DevTools port, open the page, await its promise, close Chrome.
async function viaDevTools(file,tag){
  const profile=path.join(work,'profile-'+tag),child=spawn(chromeBin,['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'});
  try{
    let port;for(let i=0;i<200&&!port;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];}catch{await new Promise(r=>setTimeout(r,50));}}
    if(!port)throw new Error('chrome_did_not_start');
    const target=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');
    const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=()=>rej(new Error('devtools_connect_failed'));});
    let id=0;const waiting=new Map(),events=[];ws.onmessage=m=>{const d=JSON.parse(m.data);if(d.id&&waiting.has(d.id)){waiting.get(d.id)(d);waiting.delete(d.id);}else events.push(d);};
    const send=(method,params={})=>new Promise(res=>{waiting.set(++id,res);ws.send(JSON.stringify({id,method,params}));});
    await send('Page.enable');await send('Page.navigate',{url:pathToFileURL(file).href});
    for(let i=0;i<600&&!events.some(e=>e.method==='Page.loadEventFired');i++)await new Promise(r=>setTimeout(r,50));
    // The page already started the build on load; wait for whatever it wrote (no fixed sleep: poll the marker in real time).
    const deadline=Date.now()+60000;
    for(;;){
      const r=await send('Runtime.evaluate',{expression:"document.getElementById('out').textContent",returnByValue:true});
      const v=r.result?.result?.value??'';if(v!=='pending')return v;
      if(Date.now()>deadline)return 'pending';await new Promise(r=>setTimeout(r,100));
    }
  }finally{const gone=new Promise(r=>child.once('exit',r));child.kill();await Promise.race([gone,new Promise(r=>setTimeout(r,5000))]);}
}
try{
  await mkdir(outDir,{recursive:true});
  const agreementPdf=path.join(work,'agreement.pdf');await renderAgreementPdf(md,agreementPdf,{title:'Tavotto Individual Contributor License Agreement',banner:process.env.SAMPLE_BANNER,publishedUrl:'https://github.com/example/example/blob/main/docs/legal/CLA_INDIVIDUAL.md',attribution:'Derived from the Harmony Individual CLA 1.0, \u00a9 2011 Project Harmony, CC BY 3.0'});
  const b64=async f=>(await readFile(f)).toString('base64');
  const hex=n=>randomBytes(n).toString('hex'),packet='pkt_'+randomBytes(24).toString('base64url');
  const ev=(role,o)=>({protocol:'cla-signature-v1',packet,role,agreement_kind:'individual',agreement_version:'1.0',agreement_sha256:sha,consent_ui_version:'1',evidence_sha256:hex(32),
    attestations:Object.fromEntries((role==='contributor'?['personal','electronic','read','identity','adult','own_rights','independent']:['personal','electronic','read','identity','reviewed']).map(k=>[k,true])),...o});
  const holder=ev('holder',{name:'Jiaqi Wan',signature:'Jiaqi Wan',address:'1 Example Holder Street, Example City',github_id:88193520,github_login_at_signing:'synthetic-holder',signed_at_unix:1793798407});
  const samples={latin:ev('contributor',{name:'Wan Jiaqi',signature:'Wan Jiaqi',address:'12 Example Road, Fuzhou, Fujian, China',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361}),
    ...Object.fromEntries(['signature','pen','hand'].map(style=>['style-'+style,ev('contributor',{name:'Shang Moqi',signature:'Shang Moqi',signature_style:style,address:'Fuzhou University, No. 2 Xueyuan Road, Fuzhou, Fujian 350108, China',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361})])),
    chinese:ev('contributor',{name:'王嘉琪',signature:'王嘉琪',address:'福建省福州市仓山区示例路 12 号 3 单元 501 室',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361})};
  const bundle=(await buildPdfClient()).code.replaceAll('</script','<\\/script');
  for(const [name,contributor] of Object.entries(samples)){
    const page=`<!doctype html><meta charset="utf-8"><pre id="out">pending</pre><script>${bundle}</script><script>
const bytes=b=>Uint8Array.from(atob(b),c=>c.charCodeAt(0));
TavottoClaPdf.build({agreement:{kind:'individual',version:'1.0',sha256:${JSON.stringify(sha)}},
 contributor:${JSON.stringify(contributor)},holder:${JSON.stringify(holder)},
 overrides:{agreementPdf:bytes(${JSON.stringify(await b64(agreementPdf))}),font:bytes(${JSON.stringify(await b64(path.join(root,'deployment/staging/assets/cla-pdf-font.ttf')))}),fonts:{${(await Promise.all(['signature','pen','hand'].map(async st=>`${st}:bytes(${JSON.stringify(await b64(path.join(root,`deployment/staging/assets/cla-sig-${st}.ttf`)))})`))).join(',')}},serviceHost:'cla.example.test'}})
 .then(r=>{let s='';for(let i=0;i<r.length;i+=8192)s+=String.fromCharCode(...r.subarray(i,i+8192));document.getElementById('out').textContent='DONE:'+btoa(s);})
 .catch(e=>{document.getElementById('out').textContent='ERROR:'+e.message;});</script>`;
    const file=path.join(work,name+'.html');await writeFile(file,page);
    for(let n=0;n<Math.max(1,runs);n++){
      const t0=Date.now();let result;
      try{result=virtual?await viaDumpDom(file,name+n):await viaDevTools(file,name+n);}catch(e){result='ERROR:'+e.message;}
      const ms=Date.now()-t0;
      if(runs){console.log(`${name} run ${n+1}: ${result.startsWith('DONE:')?'ok':result.slice(0,80)} ${ms} ms`);continue;}
      if(!result.startsWith('DONE:'))throw new Error(`${name}: ${result}`);
      const out=path.join(outDir,`sample-${name}.pdf`);await writeFile(out,Buffer.from(result.slice(5),'base64'));console.log('Wrote '+out);
    }
  }
}finally{await rm(work,{recursive:true,force:true,maxRetries:5,retryDelay:200});}
