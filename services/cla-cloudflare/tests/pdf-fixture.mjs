// Synthetic inputs for the signed-PDF tests and sample generator. No real identity or evidence.
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {deflateSync} from 'node:zlib';
import {PDFDocument} from 'pdf-lib';
import {HASH_KEYWORD} from '../scripts/render-agreement-pdf.mjs';
import {ensureFont} from '../scripts/fetch-pdf-font.mjs';
export const root=new URL('../',import.meta.url);
export const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export const stagingMd=readFileSync(new URL('deployment/staging/assets/CLA_INDIVIDUAL.md',root));
/** The committed staging agreement PDF as shipped (real text, signature-block `fields` in its metadata). */
export const stagingPdfFull=new Uint8Array(readFileSync(new URL('deployment/staging/assets/CLA_INDIVIDUAL.pdf',root)));
/** One page of it with the same hash keyword and only `publishedUrl` in its metadata: the separate-Signatures-page
 * (fallback) path, with the agreement on page 1, the Signatures page 2 and the certificate page 3. */
export const stagingPdf=await (async()=>{
  const full=await PDFDocument.load(stagingPdfFull),d=await PDFDocument.create();
  const [page]=await d.copyPages(full,[0]);d.addPage(page);d.setKeywords([full.getKeywords()??'']);
  const meta=JSON.parse((full.getSubject()??'').replace(/^tavotto-agreement-meta:/,''));
  d.setSubject('tavotto-agreement-meta:'+JSON.stringify({publishedUrl:meta.publishedUrl}));return new Uint8Array(await d.save());
})();
// Not committed: fetched once by hash (see scripts/fetch-pdf-font.mjs). Without the file and without network this fails loudly.
export const font=new Uint8Array(readFileSync(await ensureFont()));
export const AGREEMENT_SHA=sha(stagingMd);
export async function pdfWithKeyword(keyword,pages=1){
  const d=await PDFDocument.create();for(let i=0;i<pages;i++)d.addPage([612,792]);
  if(keyword!==null)d.setKeywords([keyword]);return new Uint8Array(await d.save());
}
export const agreementPdf=async(hash=AGREEMENT_SHA,pages=1)=>pdfWithKeyword(HASH_KEYWORD+hash,pages);
const ATT={contributor:['personal','electronic','read','identity','adult','own_rights','independent'],holder:['personal','electronic','read','identity','reviewed']};
export function evidence(role,over={}){
  const base=role==='contributor'
    ?{name:'Wan Jiaqi',address:'12 Example Road, Fuzhou, Fujian, China',github_id:261183172,github_login_at_signing:'synthetic-contributor',signed_at_unix:1793797361}
    :{name:'Jiaqi Wan',address:'1 Holder Street, Example City',github_id:88193520,github_login_at_signing:'synthetic-holder',signed_at_unix:1793798407};
  const e={protocol:'cla-signature-v1',packet:'pkt_7Qm2xRsyntheticPacketIdentifier000000',agreement_kind:'individual',agreement_version:'1.0',agreement_sha256:AGREEMENT_SHA,
    role,consent_ui_version:'1',signature:base.name,attestations:Object.fromEntries(ATT[role].map(k=>[k,true])),...base,...over};
  e.evidence_sha256=over.evidence_sha256??sha(role+JSON.stringify(e));return e;
}
export const agreement=(over={})=>({kind:'individual',version:'1.0',sha256:AGREEMENT_SHA,publishedUrl:'https://github.com/example/example/blob/main/docs/legal/CLA_INDIVIDUAL.md',...over});
export const input=async(over={})=>{const {overrides,...rest}=over;
  return {agreement:agreement(),contributor:evidence('contributor'),holder:evidence('holder'),...rest,
    overrides:{agreementPdf:stagingPdf,font,serviceHost:'cla.example.test',now:new Date('2026-11-04T13:30:00Z'),...overrides}};};
function crc(buf){let c,t=crc.t??=Array.from({length:256},(_,n)=>{c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0;});let r=~0;for(const b of buf)r=t[(r^b)&255]^(r>>>8);return ~r>>>0;}
export function png(w,h){
  const chunk=(type,data)=>{const b=Buffer.alloc(12+data.length);b.writeUInt32BE(data.length,0);b.write(type,4,'latin1');data.copy(b,8);b.writeUInt32BE(crc(b.subarray(4,8+data.length)),8+data.length);return b;};
  const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(w,0);ihdr.writeUInt32BE(h,4);ihdr[8]=8;ihdr[9]=0;
  const raw=Buffer.alloc((w+1)*h,0x40);for(let y=0;y<h;y++)raw[y*(w+1)]=0;
  return new Uint8Array(Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]));
}
/** Fake canvas: 1 em per CJK char, 0.5 em otherwise; records every call so tests can see what was rasterised. */
export function fakeRasterizer(){
  const calls=[];const w=(t,s)=>Array.from(t).reduce((n,ch)=>n+(ch.codePointAt(0)>0x2e80?1:.5)*s,0);
  return {calls,measure:(t,style,s)=>w(t,s),render:async(t,style,s)=>{calls.push({text:t,style});return {png:png(Math.ceil(w(t,s)*4)+1,Math.ceil(s*6)),width:w(t,s)+2,height:s*1.5,descent:s*.35};}};
}
