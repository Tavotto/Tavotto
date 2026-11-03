// Fetches the signature typeface (Great Vibes, SIL OFL 1.1) for the signed-PDF builder. Font binaries are never
// committed (repository invariant tests/test_font_provenance.py); like scripts/fetch_fonts.py this has one pinned
// source URL and one pinned SHA-256: download to `.part`, verify, rename only on success; on a mismatch delete and fail,
// never retry. Pure node standard library.
//
//   node scripts/fetch-pdf-font.mjs [--dest DIR] [--check]
//
// Default DIR: deployment/staging/assets (for production pass the assets-reviewed directory). --check only verifies.
import {createHash} from 'node:crypto';
import {mkdir,readFile,rename,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
export const FONT_FILE='cla-pdf-font.ttf';
// google/fonts, ofl/greatvibes/GreatVibes-Regular.ttf, pinned to the commit that last touched it.
export const FONT_URL='https://raw.githubusercontent.com/google/fonts/a6039f387a790a092e417b4e8dbdd5b57fe4d6d4/ofl/greatvibes/GreatVibes-Regular.ttf';
export const FONT_SHA256='8d509802186f1b51572531ecf313e8098f9a5bfdfaca93f0c9b34467f9982d15';
const MAX_BYTES=2*1024*1024;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export const defaultDest=fileURLToPath(new URL('../deployment/staging/assets/',import.meta.url));
// Stable error codes (the `code` property, and the first word of the CLI message):
//   pdf_font_network_unreachable  the download could not be made at all (offline, DNS, TLS, redirect refused)
//   pdf_font_http_status          the server answered with something other than 200
//   pdf_font_hash_mismatch        bytes on disk or downloaded are not the pinned file (deleted, never retried)
//   pdf_font_missing              --check only: the file is not there
const SENTENCE={
  pdf_font_network_unreachable:'The font could not be downloaded because the network request failed.',
  pdf_font_http_status:'The font server did not answer with HTTP 200.',
  pdf_font_hash_mismatch:'The font bytes are not the pinned Great Vibes file; they were deleted and will not be retried.',
  pdf_font_missing:'The font file is not present; run node scripts/fetch-pdf-font.mjs.',
};
export class FontError extends Error {
  constructor(code,detail){super(`${code}: ${SENTENCE[code]}${detail?` (${detail})`:''}`);this.code=code;}
}
/** Returns the font path. With checkOnly nothing is written. Rejects (never skips) when it cannot be verified. */
export async function ensureFont(dest=defaultDest,{checkOnly=false,fetchImpl=fetch,url=FONT_URL,sha256=FONT_SHA256}={}) {
  const target=path.join(dest,FONT_FILE);
  let have=null;try{have=await readFile(target);}catch{}
  if(have){
    if(sha(have)===sha256)return target;
    await rm(target,{force:true});
    throw new FontError('pdf_font_hash_mismatch',`removed ${target}; expected ${sha256}`);
  }
  if(checkOnly)throw new FontError('pdf_font_missing',target);
  await mkdir(dest,{recursive:true});
  const part=target+'.part';
  try{
    let response;
    try{response=await fetchImpl(url,{redirect:'error'});}catch(e){throw new FontError('pdf_font_network_unreachable',`${url}: ${e&&e.cause&&e.cause.code||e&&e.message||e}`);}
    if(response.status!==200)throw new FontError('pdf_font_http_status',`HTTP ${response.status} from ${url}`);
    let bytes;
    try{bytes=new Uint8Array(await response.arrayBuffer());}catch(e){throw new FontError('pdf_font_network_unreachable',`${url}: body read failed`);}
    await writeFile(part,bytes);
    if(bytes.length===0||bytes.length>MAX_BYTES||sha(bytes)!==sha256)throw new FontError('pdf_font_hash_mismatch',`download ${sha(bytes)}; expected ${sha256}`);
    await rename(part,target);
    return target;
  }finally{await rm(part,{force:true});}
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2),i=args.indexOf('--dest');
  try{console.log('OK '+await ensureFont(i>=0?path.resolve(args[i+1]):defaultDest,{checkOnly:args.includes('--check')}));}
  catch(e){console.error(e.message);process.exit(1);}
}
