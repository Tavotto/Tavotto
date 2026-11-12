// Signature styles in the signed PDF: each party's stored style picks its typeface; records without one keep Great Vibes.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {PDFDict,PDFDocument,PDFName} from 'pdf-lib';
import {build} from '../src/pdf/build.ts';
import {SIGNATURE_FONT_PATHS,SIGNATURE_STYLES,styleOf} from '../src/pdf/styles.ts';
import {ensureSignatureFonts,SIGNATURE_FONTS} from '../scripts/fetch-pdf-font.mjs';
import {evidence,fakeRasterizer,input} from './pdf-fixture.mjs';
import {SIGNATURE_STYLE_IDS} from '../src/native/service.ts';

const paths=await ensureSignatureFonts();
const fonts=Object.fromEntries(SIGNATURE_FONTS.map((f,i)=>[f.style,new Uint8Array(readFileSync(paths[i]))]));
const NAMES={signature:/MrsSaintDelafield/,formal:/GreatVibes/,pen:/HomemadeApple/,hand:/PatrickHand/};
async function baseFonts(bytes){
  const doc=await PDFDocument.load(bytes),out=[];
  for(const [,obj] of doc.context.enumerateIndirectObjects())if(obj instanceof PDFDict && obj.get(PDFName.of('Type'))===PDFName.of('Font'))out.push(String(obj.get(PDFName.of('BaseFont'))));
  return out.join(' ');
}
test('the style list, its fixed font paths and the fetch table agree',()=>{
  assert.deepEqual([...SIGNATURE_STYLES].sort(),SIGNATURE_FONTS.map(f=>f.style).sort());
  assert.deepEqual([...SIGNATURE_STYLE_IDS],[...SIGNATURE_STYLES],'service validation list');
  const ui=readFileSync(new URL('../src/native/ui-client.ts',import.meta.url),'utf8');
  assert.match(ui,new RegExp("const STYLES=\\["+SIGNATURE_STYLES.map(s=>`'${s}'`).join(',')+"\\]"),'signing page list');
  for(const style of SIGNATURE_STYLES)assert.match(SIGNATURE_FONT_PATHS[style],/^\/cla-(pdf-font|sig-[a-z]+)\.ttf$/);
  assert.equal(styleOf(undefined),'formal');assert.equal(styleOf('comic'),'formal');assert.equal(styleOf('pen'),'pen');
});
for(const style of SIGNATURE_STYLES)test('contributor style '+style+' is drawn in its own typeface; the unstyled holder stays in Great Vibes',async()=>{
  const bytes=await build(await input({contributor:evidence('contributor',{signature_style:style}),overrides:{fonts,rasterizer:fakeRasterizer()}}));
  const names=await baseFonts(bytes);assert.match(names,NAMES[style]);assert.match(names,/GreatVibes/);
  for(const other of SIGNATURE_STYLES)if(other!==style && other!=='formal')assert.doesNotMatch(names,NAMES[other]);
});
test('an unknown stored style is refused before anything is drawn',async()=>{
  await assert.rejects(build(await input({contributor:evidence('contributor',{signature_style:'comic'}),overrides:{fonts}})),/evidence_invalid/);
});
test('only the fonts of the styles in use are fetched, from their fixed same-origin paths',async()=>{
  const seen=[],real=globalThis.fetch;
  globalThis.fetch=async p=>{seen.push(p);const style=Object.entries(SIGNATURE_FONT_PATHS).find(([,v])=>v===p)?.[0];
    return style?new Response(fonts[style]):new Response('missing',{status:404});};
  try{
    const i=await input({contributor:evidence('contributor',{signature_style:'pen'}),holder:evidence('holder',{signature_style:'hand'}),overrides:{rasterizer:fakeRasterizer()}});
    delete i.overrides.font;await build(i);
  }finally{globalThis.fetch=real;}
  assert.deepEqual(seen.sort(),['/cla-sig-hand.ttf','/cla-sig-pen.ttf']);
});
