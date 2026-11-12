// Offline, dev-time: render the pinned agreement Markdown to a US Letter PDF with headless Chrome,
// and write the Markdown's SHA-256 into the PDF keywords. The signed-PDF builder refuses any PDF
// whose keywords do not carry the same SHA-256 as the agreement being signed.
//
//   node scripts/render-agreement-pdf.mjs <agreement.md> <out.pdf> [--title "..."] [--published-url https://...] [--attribution "..."] [--banner "..."]
// --published-url and --attribution are written to the PDF Subject (tavotto-agreement-meta:{json}); the signed-PDF
// builder shows them on the certificate / Signatures page when the signing page does not pass its own.
// When the Markdown ends with the usual **You (...)** / **Us** signature blocks they are rendered as a form and the
// position of every underlined line is stored in the same meta as `fields` (see README, signed-PDF section).
//
// Chrome: the macOS "Google Chrome" app, or CHROME_BIN. It runs with a throw-away profile
// directory (never the user's profile) and no network access is needed.
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {promisify} from 'node:util';
import {marked} from 'marked';
import {SIGNATURE_CSS,formifySignatureBlocks,locateFields} from './signature-block.mjs';
import {PDFDocument,StandardFonts,rgb} from 'pdf-lib';
const run=promisify(execFile);
export const HASH_KEYWORD='tavotto-agreement-sha256:';
export const META_SUBJECT='tavotto-agreement-meta:';
const CSS=`@page{size:Letter;margin:1.45in .95in .95in}
*{box-sizing:border-box}
body{margin:0;font:10.5pt/1.5 Charter,'Iowan Old Style',Georgia,'Times New Roman',serif;color:#1b1b18;overflow-wrap:break-word}
h1{font-size:19pt;line-height:1.25;margin:0 0 14pt}
h2{font-size:13.5pt;margin:20pt 0 6pt;break-after:avoid}h3{font-size:11.5pt;margin:14pt 0 4pt;break-after:avoid}h4{font-size:10.5pt;margin:10pt 0 3pt;break-after:avoid}
p{margin:0 0 8pt;orphans:3;widows:3}ul,ol{margin:0 0 8pt;padding-left:20pt}li{margin:0 0 3pt}
blockquote{margin:0 0 10pt;padding:2pt 0 2pt 12pt;border-left:2pt solid #d9d9d4;color:#4a4a45}
code{font:9pt ui-monospace,Menlo,monospace}
a{color:inherit;text-decoration:underline;text-decoration-color:#9b9b94}
table{border-collapse:collapse;width:100%;margin:0 0 10pt;font-size:9.5pt}th,td{border:.5pt solid #d9d9d4;padding:4pt 6pt;vertical-align:top;text-align:left}th{background:#f4f4f1}
tr{break-inside:avoid}hr{border:0;border-top:.5pt solid #d9d9d4;margin:14pt 0}
${SIGNATURE_CSS}`;
// --banner: one line drawn at the top of every page (0.5 in from the edge), above the signed copy's own header, after
// Chrome has laid the text out, so the signature-block positions are unaffected. Staging uses it so a staging copy can
// never pass for the real agreement. WinAnsi text only (Helvetica).
const drawBanner=async(doc,banner)=>{
  const font=await doc.embedFont(StandardFonts.HelveticaBold),size=7.5;
  for(const page of doc.getPages()){const {width,height}=page.getSize(),w=font.widthOfTextAtSize(banner,size);
    page.drawText(banner,{x:(width-w)/2,y:height-36,size,font,color:rgb(.70,.15,.12)});}
};
export async function renderAgreementPdf(markdownPath,outPath,{title,publishedUrl,attribution,banner}={}){
  const bytes=await readFile(markdownPath),sha256=createHash('sha256').update(bytes).digest('hex');
  const chrome=process.env.CHROME_BIN??'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if(!existsSync(chrome))throw new Error(`Chrome not found at ${chrome}; set CHROME_BIN`);
  const work=await mkdtemp(path.join(os.tmpdir(),'cla-agreement-pdf-'));
  try{
    const text=bytes.toString('utf8'),page=(body)=>`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title??'Agreement'}</title><style>${CSS}</style></head><body>${body}</body></html>`;
    const print=async(body,tag)=>{
      const file=path.join(work,tag+'.html'),raw=path.join(work,tag+'.pdf');await writeFile(file,page(body));
      // Chrome sometimes exits non-zero from its teardown watchdog after the PDF is already written; the file is what counts.
      await run(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',`--user-data-dir=${path.join(work,'profile-'+tag)}`,
        '--no-pdf-header-footer','--allow-pdf-data-url-js=false',`--print-to-pdf=${raw}`,pathToFileURL(file).href],{timeout:90000}).catch(e=>{if(!existsSync(raw))throw e;});
      return readFile(raw);
    };
    // The agreement's own blank signature block becomes a printed form. Pass 1 carries invisible marker words so the
    // underlines can be found in the PDF; pass 2 (the shipped file) has none and must paint identical underlines.
    const lexed=()=>marked.lexer(text,{gfm:true}),form=formifySignatureBlocks(lexed(),{markers:true});
    let raw,fields;
    if(form){
      const marked1=await print(marked.parser(form.tokens),'marked'),shipped=formifySignatureBlocks(lexed(),{markers:false});
      raw=await print(marked.parser(shipped.tokens),'shipped');fields=await locateFields(marked1,raw,form.parties);
    }else raw=await print(marked.parser(lexed()),'plain');
    const doc=await PDFDocument.load(raw);
    if(banner)await drawBanner(doc,banner);
    doc.setTitle(title??'Agreement');doc.setKeywords([`${HASH_KEYWORD}${sha256}`]);
    if(publishedUrl&&!/^https:\/\//.test(publishedUrl))throw new Error('published_url_must_be_https');
    doc.setSubject(META_SUBJECT+JSON.stringify({publishedUrl:publishedUrl||undefined,attribution:attribution||undefined,fields}));doc.setProducer('Tavotto CLA agreement renderer (headless Chrome)');doc.setCreator('Tavotto CLA');
    await writeFile(outPath,await doc.save());
    return {sha256,pages:doc.getPageCount()};
  }finally{await rm(work,{recursive:true,force:true});}
}
if(process.argv[1]===new URL(import.meta.url).pathname){
  const [md,out,...rest]=process.argv.slice(2);
  if(!md||!out){console.error('usage: render-agreement-pdf.mjs <agreement.md> <out.pdf> [--title "..."]');process.exit(2);}
  const opt=name=>{const i=rest.indexOf(name);return i>=0?rest[i+1]:undefined;};
  const r=await renderAgreementPdf(md,out,{title:opt('--title'),publishedUrl:opt('--published-url'),attribution:opt('--attribution'),banner:opt('--banner')});
  console.log(`Wrote ${out}: ${r.pages} page(s), keyword ${HASH_KEYWORD}${r.sha256}`);
}
