// Bundles src/pdf/client.ts (pdf-lib + fontkit included) into one browser script. No CDN, no eval.
// Usage: node scripts/build-pdf-client.mjs [out-file]   default: deployment/staging/assets/cla-pdf.js
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const root=fileURLToPath(new URL('../',import.meta.url));
export async function buildPdfClient(){
  const result=await build({absWorkingDir:root,entryPoints:['src/pdf/client.ts'],bundle:true,format:'iife',platform:'browser',target:'es2022',
    minify:true,legalComments:'none',keepNames:false,write:false,logLevel:'silent',metafile:true,
    banner:{js:'/* Tavotto CLA signed-PDF builder. Bundles pdf-lib (MIT) and @pdf-lib/fontkit (MIT). */'}});
  return {code:result.outputFiles[0].text,inputs:Object.keys(result.metafile.inputs)};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  const out=path.resolve(process.argv[2]??path.join(root,'deployment/staging/assets/cla-pdf.js'));
  const {code}=await buildPdfClient();
  await mkdir(path.dirname(out),{recursive:true});await writeFile(out,code);
  console.log(`Wrote ${out} (${code.length} bytes)`);
}
