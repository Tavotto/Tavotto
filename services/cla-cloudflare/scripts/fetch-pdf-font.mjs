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
/** Returns the font path. With checkOnly nothing is written. Rejects (never skips) when it cannot be verified. */
export async function ensureFont(dest=defaultDest,{checkOnly=false,fetchImpl=fetch,url=FONT_URL,sha256=FONT_SHA256}={}) {
  const target=path.join(dest,FONT_FILE);
  let have=null;try{have=await readFile(target);}catch{}
  if(have){
    if(sha(have)===sha256)return target;
    await rm(target,{force:true});
    throw new Error(`pdf_font_hash_mismatch: removed ${target} (expected ${sha256})`);
  }
  if(checkOnly)throw new Error(`pdf_font_missing: ${target}; run node scripts/fetch-pdf-font.mjs`);
  await mkdir(dest,{recursive:true});
  const part=target+'.part';
  try{
    const response=await fetchImpl(url,{redirect:'error'});
    if(!response.ok)throw new Error(`pdf_font_download_failed: HTTP ${response.status} (network required once: ${url})`);
    const bytes=new Uint8Array(await response.arrayBuffer());
    if(bytes.length===0||bytes.length>MAX_BYTES)throw new Error('pdf_font_download_failed: unexpected size');
    await writeFile(part,bytes);
    if(sha(bytes)!==sha256)throw new Error(`pdf_font_hash_mismatch: download is not the pinned file (expected ${sha256})`);
    await rename(part,target);
    return target;
  }catch(e){
    await rm(part,{force:true});
    throw e instanceof Error&&e.message.startsWith('pdf_font_')?e:new Error('pdf_font_download_failed: '+(e&&e.message||e));
  }
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2),i=args.indexOf('--dest');
  try{console.log('OK '+await ensureFont(i>=0?path.resolve(args[i+1]):defaultDest,{checkOnly:args.includes('--check')}));}
  catch(e){console.error(e.message);process.exit(1);}
}
