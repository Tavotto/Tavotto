import {PDFDocument,StandardFonts,rgb} from 'pdf-lib';
import type {PDFPage} from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import {canvasRasterizer} from './raster.ts';
import type {Rasterizer} from './raster.ts';
import {Typesetter,clean,customFace,standardFace} from './text.ts';
import type {Color,Face} from './text.ts';
import {SIGNATURE_FONT_PATHS} from './styles.ts';
import type {SignatureStyle} from './styles.ts';
import {KINDS,checkInput,isoTime,longDate,longTime,validFields,validMeta} from './evidence.ts';
import type {BuildInput,FieldBox,Party,SignatureFields} from './evidence.ts';

export type {BuildInput,Evidence,AgreementInfo} from './evidence.ts';
/** Same-origin fixed paths served by the Worker (see worker.ts); never derived from input. */
export const AGREEMENT_PDF_PATH='/agreement.pdf';
export const FONT_PATH=SIGNATURE_FONT_PATHS.formal;
const STYLE_SCALE:Record<SignatureStyle,number>={signature:1.25,formal:1,pen:.72,hand:.95};
/** The offline renderer writes the Markdown's SHA-256 into the PDF keywords under this prefix. */
export const HASH_KEYWORD='tavotto-agreement-sha256:';
/** PDF Subject written by the renderer: this prefix followed by JSON {publishedUrl?,attribution?,fields?}. */
export const META_SUBJECT='tavotto-agreement-meta:';
const PRODUCT='Tavotto';
const W=612,H=792,MX=68.4,CW=W-2*MX,TOP=64.8,LIMIT=78,THANKS_TOP=104;
const INK:Color=[.106,.106,.094],MUTED:Color=[.424,.424,.4],RULE:Color=[.851,.851,.831],ROW:Color=[.902,.902,.886],NOTE:Color=[.29,.29,.27],FOOT:Color=[.608,.608,.58];
const SIGNED_FOOT='Signed electronically · see the certificate on the next page';
const THANKS='Thank you for contributing to Tavotto. Every improvement you send makes the tool better for researchers everywhere.';
const CERT_NOTE='The signed record is kept by the Tavotto CLA service. This PDF is a copy for your records: if it ever disagrees with the service record, the record governs. To check this copy, compare the agreement fingerprint above with the published text.';

async function fetchAsset(path:string,max:number):Promise<Uint8Array> {
  const response=await fetch(path,{credentials:'same-origin',redirect:'error',cache:'no-store'});
  if(!response.ok)throw new Error('asset_unavailable');
  const bytes=new Uint8Array(await response.arrayBuffer());
  if(bytes.length===0 || bytes.length>max)throw new Error('asset_unavailable');
  return bytes;
}
/** The agreement pages are only usable if the PDF says it was rendered from exactly the pinned text. */
export function assertAgreementPdf(doc:PDFDocument,sha256:string):void {
  const claimed=(doc.getKeywords()??'').split(/\s+/).filter(x=>x.startsWith(HASH_KEYWORD)).map(x=>x.slice(HASH_KEYWORD.length));
  if(claimed.length!==1 || claimed[0]!==sha256)throw new Error('agreement_pdf_hash_mismatch');
  if(doc.getPageCount()<1)throw new Error('agreement_pdf_invalid');
}
const roundRect=(w:number,h:number,r:number)=>`M ${r},0 H ${w-r} A ${r},${r} 0 0 1 ${w},${r} V ${h-r} A ${r},${r} 0 0 1 ${w-r},${h} H ${r} A ${r},${r} 0 0 1 0,${h-r} V ${r} A ${r},${r} 0 0 1 ${r},0 Z`;

/** Whole-build ceiling. A canvas callback or fetch that never answers must end in a stable error, not an eternal pending. */
export const BUILD_TIMEOUT_MS=20000;
export async function build(input:BuildInput):Promise<Uint8Array> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  const limit=input?.overrides?.timeoutMs??BUILD_TIMEOUT_MS;
  try {
    return await Promise.race([buildPdf(input),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('build_timeout')),limit);})]);
  } finally {clearTimeout(timer);}
}
async function buildPdf(input:BuildInput):Promise<Uint8Array> {
  const v=checkInput(input),over=input.overrides??{},kind=KINDS[v.agreement.kind]!,now=over.now??new Date();
  const agreementBytes=over.agreementPdf??await fetchAsset(AGREEMENT_PDF_PATH,8*1024*1024);
  const doc=await PDFDocument.load(agreementBytes);
  assertAgreementPdf(doc,v.agreement.sha256);
  // Hash-verified PDF metadata fills in what the caller did not pass; the caller's values win.
  const subject=doc.getSubject()??'';let meta:{publishedUrl?:string;attribution?:string}={},fields:SignatureFields|undefined;
  if(subject.startsWith(META_SUBJECT)){try{const j=JSON.parse(subject.slice(META_SUBJECT.length));meta=validMeta(j?.publishedUrl,j?.attribution);fields=validFields(j?.fields,doc.getPages().map(p=>p.getSize()));}catch{meta={};fields=undefined;}}
  const publishedUrl=v.agreement.publishedUrl??meta.publishedUrl,attribution=v.agreement.attribution??meta.attribution;
  doc.registerFontkit(fontkit);
  const raster:Rasterizer=over.rasterizer??canvasRasterizer();
  const ts=new Typesetter(doc,raster);
  const sans=standardFace('sans',await doc.embedFont(StandardFonts.Helvetica)),sansB=standardFace('sansBold',await doc.embedFont(StandardFonts.HelveticaBold));
  const serifB=standardFace('serifBold',await doc.embedFont(StandardFonts.TimesRomanBold)),serifI=standardFace('serifItalic',await doc.embedFont(StandardFonts.TimesRomanItalic));
  const mono=standardFace('mono',await doc.embedFont(StandardFonts.Courier)),serif=standardFace('serif',await doc.embedFont(StandardFonts.TimesRoman));
  // One script face per signature style actually used (src/pdf/styles.ts); fetched from its fixed same-origin path.
  const scriptFaces=new Map<SignatureStyle,Face>();
  for(const style of new Set([v.contributor.style,v.holder.style])){
    const bytes=over.fonts?.[style]??(style==='formal'?over.font:undefined)??await fetchAsset(SIGNATURE_FONT_PATHS[style],2*1024*1024);
    scriptFaces.set(style,customFace('script',await doc.embedFont(bytes,{subset:true})));
  }
  const faceOf=(p:Party)=>scriptFaces.get(p.style)!;
  // The typefaces differ in how large a given point size looks; this evens them out.
  const scaleOf=(face:Face)=>{for(const [style,f] of scriptFaces)if(f===face)return STYLE_SCALE[style];return 1;};
  const host=over.serviceHost??(typeof location!=='undefined'?location.host:'');
  const footLeft=new Map<PDFPage,string>();
  const heading=`${kind.title} · version ${v.agreement.version}`;

  const header=async(page:PDFPage,right:string)=>{
    const base=page.getHeight()-TOP-8.5;
    await ts.draw(page,sansB,PRODUCT,MX,base,8.5,INK);
    await ts.draw(page,sans,right,page.getWidth()-MX,base,8.5,MUTED,'right');
    page.drawLine({start:{x:MX,y:base-10},end:{x:page.getWidth()-MX,y:base-10},thickness:.5,color:rgb(...RULE)});
  };
  for(const page of doc.getPages())await header(page,heading);

  // Flow layout: blocks are placed top-down; a block that does not fit moves to a continuation page.
  let page!:PDFPage,y=0,right='';
  const begin=async(rightText:string,left?:string)=>{
    right=rightText;page=doc.addPage([W,H]);if(left)footLeft.set(page,left);
    await header(page,right);y=H-TOP-8.5-10-30;
  };
  const cont=async()=>{const left=footLeft.get(page);await begin(right,left);};
  const ensure=async(height:number)=>{if(y-height<LIMIT)await cont();};
  const h1=async(title:string,sub:string)=>{
    await ts.draw(page,serifB,title,MX,y-19*.78,19,INK);y-=19*1.25+6;
    const lines=ts.wrap(sans,sub,9.5,CW);
    for(const line of lines){await ts.draw(page,sans,line,MX,y-9.5*.85,9.5,MUTED);y-=14.25;}
    y-=26-4;
  };

  // Great Vibes' space glyph is too narrow to read as a word gap, so signature words are placed one by one.
  // Text the face cannot encode stays one raster run. The face is a parameter so the signature style can be swapped.
  const sigWords=(face:Face,t:string)=>face.covers(t)?clean(t).split(/ +/).filter(Boolean):[t];
  const sigGap=(size:number)=>size*.35;
  const sigWidth=(face:Face,t:string,size:number)=>{const w=sigWords(face,t);return w.reduce((n,x)=>n+ts.width(face,x,size),0)+sigGap(size)*(w.length-1);};
  const drawSig=async(page:PDFPage,face:Face,t:string,x:number,y:number,size:number)=>{
    for(const word of sigWords(face,t))x+=await ts.draw(page,face,word,x,y,size,INK)+sigGap(size);
  };
  // Signature sitting on a field line of width w: natural size, shrunk to fit; null when even the smallest size does not fit.
  const signature=(face:Face,t:string,box:FieldBox)=>{
    const base=26*scaleOf(face),natural=sigWidth(face,t,base),size=natural>box.w?base*box.w/natural:base;
    return size<12*scaleOf(face)?null:(pg:PDFPage)=>drawSig(pg,face,t,box.x,box.y+3,size);
  };
  // One line of ordinary text on a field line: 11pt, shrunk down to 7pt to fit; null when it still does not fit.
  const onLine=(t:string,box:FieldBox)=>{
    let size=11;const natural=ts.width(serif,t,size);if(natural>box.w)size=Math.floor(11*box.w/natural*10)/10;
    return size<7?null:async(pg:PDFPage)=>{await ts.draw(pg,serif,t,box.x,box.y+3,size,INK);};
  };
  // Address wrapped over the form's address lines at one common size (11pt down to 7pt).
  const onLines=(t:string,boxes:FieldBox[])=>{
    for(let size=11;size>=7;size-=.5){
      const lines=ts.wrap(serif,t,size,Math.min(...boxes.map(b=>b.w)));
      if(lines.length<=boxes.length)return async(pg:PDFPage)=>{for(const [i,line] of lines.entries())await ts.draw(pg,serif,line,boxes[i]!.x,boxes[i]!.y+3,size,INK);};
    }
    return null;
  };
  // Fills the agreement's own signature block. All-or-nothing: every value is planned (and must fit) before anything is drawn.
  const fillInPlace=async(f:SignatureFields):Promise<boolean>=>{
    const pgs=doc.getPages(),jobs:[number,(pg:PDFPage)=>Promise<void>][]=[];
    const add=(box:FieldBox,job:((pg:PDFPage)=>Promise<void>)|null|undefined)=>{if(job===null)return false;if(job)jobs.push([box.page,job]);return true;};
    const text=(box:FieldBox,t:string)=>box.printed?undefined:onLine(t,box);
    const party=(p:Party,b:typeof f.contributor)=>add(b.signature,signature(faceOf(p),p.signature,b.signature)) && add(b.name,text(b.name,p.name)) &&
      add(b.address[0]!,b.address.some(a=>a.printed)?undefined:onLines(p.address,b.address)) && add(b.date,text(b.date,longDate(new Date(p.signedAt*1000))));
    const ok=party(v.contributor,f.contributor) && add(f.contributor.github!,text(f.contributor.github!,`@${v.contributor.login}`)) &&
      party(v.holder,f.holder) && add(f.holder.title!,text(f.holder.title!,'Rights holder'));
    if(!ok)return false;
    for(const [i,job] of jobs)await job(pgs[i]!);
    footLeft.set(pgs[f.holder.signature.page]!,SIGNED_FOOT);
    return true;
  };

  // A party box is a list of units (the header with signature plus the first row line, then one unit per further line).
  // Units are placed top-down; when the next one cannot fit above the page's usable area the box's border is closed on
  // this page and the box continues, with a fresh border, on a continuation page. No unit is ever drawn below LIMIT.
  const partyBox=async(label:string,party:Party,rows:[string,string][])=>{
    const padX=20,padT=18,inner=CW-2*padX,labelW=92,gap=12,valueW=inner-labelW-gap,reserve=16,contTop=12;
    const script=faceOf(party),k=scaleOf(script);let size=27*k;const natural=sigWidth(script,party.signature,size);if(natural>inner)size=Math.max(14*k,27*k*inner/natural);
    const sigH=Math.max(34,size*1.25),x0=MX+padX;
    const lineUnit=(k:string|null,line:string,last:boolean)=>({h:14+(last?5:0),draw:async(cy:number)=>{
      if(k!==null)await ts.draw(page,sans,k,x0,cy-9.5*.85,9.5,MUTED);
      await ts.draw(page,sans,line,x0+labelW+gap,cy-9.5*.85,9.5,INK);}});
    const units:{h:number;draw:(cy:number)=>Promise<void>}[]=[];
    rows.forEach(([k,val],r)=>{const lines=ts.wrap(sans,val,9.5,valueW);lines.forEach((line,n)=>units.push(lineUnit(n===0?k:null,line,n===lines.length-1)));void r;});
    const first=units.shift()!;
    const headH=padT+8.5+14+sigH+4+12;
    units.unshift({h:headH+first.h,draw:async(cy:number)=>{
      let cx=x0;for(const ch of label.toUpperCase())cx+=await ts.draw(page,sansB,ch,cx,cy-padT-8.5*.8,8.5,MUTED)+.68;
      const sy=cy-padT-8.5-14;
      await drawSig(page,script,party.signature,x0,sy-sigH+size*.32+4,size);
      page.drawLine({start:{x:x0,y:sy-sigH+4-3},end:{x:x0+inner,y:sy-sigH+4-3},thickness:.6,color:rgb(...INK)});
      await first.draw(cy-headH);}});
    await ensure(units[0]!.h+reserve+18);
    let pg=page,top=y,cy=y,atStart=true;
    const close=(pad:number)=>pg.drawSvgPath(roundRect(CW,top-(cy-pad),6),{x:MX,y:top,borderColor:rgb(...RULE),borderWidth:.6});
    for(const u of units){
      if(!atStart && cy-u.h-reserve<LIMIT){close(8);await cont();pg=page;top=y;cy=y-contTop;atStart=true;}
      await u.draw(cy);cy-=u.h;atStart=false;
    }
    close(reserve);y=cy-reserve-18;
  };

  const youLabel=kind.you,inPlace=fields?await fillInPlace(fields):false;
  if(!inPlace){
  await begin(heading,attribution);
  await h1('Signatures','This page completes the agreement on the preceding pages. Both parties signed electronically through the Tavotto CLA service.');
  const identity=(p:Party)=>`@${p.login} · ID ${p.githubId}`;
  await partyBox(youLabel,v.contributor,[['Name',v.contributor.name],['Address',v.contributor.address],['GitHub',identity(v.contributor)],['Signed',longTime(v.contributor.signedAt)]]);
  await partyBox('Us',v.holder,[['Name',v.holder.name],['Title','Rights holder'],['Address',v.holder.address],['GitHub',identity(v.holder)],['Signed',longTime(v.holder.signedAt)]]);
  if(y<THANKS_TOP+16)await cont();
  page.drawLine({start:{x:MX,y:THANKS_TOP},end:{x:W-MX,y:THANKS_TOP},thickness:.5,color:rgb(...RULE)});
  let ty=THANKS_TOP-14-10.5*.8;
  for(const line of ts.wrap(serifI,THANKS,10.5,CW)){await ts.draw(page,serifI,line,MX,ty,10.5,NOTE);ty-=15;}
  }

  // Certificate
  await begin('Electronic signature certificate',`Generated ${longDate(now)}${host?` · ${host}`:''}`);
  await h1('Electronic signature certificate','How this agreement was signed, with the fingerprints needed to verify it.');
  const labelW=150;
  const table=async(rows:[string,string,Face?][])=>{
    for(const [k,val,face] of rows){
      const f=face??sans,size=f===mono?8:9,lines=ts.wrap(f,val,size,CW-labelW),height=lines.length*13+10;
      await ensure(height);
      await ts.draw(page,sans,k,MX,y-5-9*.85,9,MUTED);
      let ly=y-5;for(const line of lines){await ts.draw(page,f,line,MX+labelW,ly-size*.85,size,INK);ly-=13;}
      page.drawLine({start:{x:MX,y:y-height},end:{x:W-MX,y:y-height},thickness:.5,color:rgb(...ROW)});
      y-=height;
    }
  };
  const h3=async(title:string)=>{await ensure(60);y-=14;await ts.draw(page,sansB,title,MX,y-10*.85,10,INK);y-=10*1.3+6;};
  await table([['Record',v.packet,mono],['Agreement',`${kind.short}, version ${v.agreement.version}`],['Agreement fingerprint (SHA-256)',v.agreement.sha256,mono],...(publishedUrl?[['Published text',publishedUrl,mono] as [string,string,Face]]:[])]);
  for(const [title,p] of [['Contributor',v.contributor],['Rights holder',v.holder]] as const){
    await h3(title);
    await table([['Identity',`Signed in with GitHub as @${p.login} (ID ${p.githubId})`],['Confirmations',p.confirmations.join(' · ')],...(p.consent?[['Consent wording version',p.consent] as [string,string]]:[]),...(p.fingerprint?[['Evidence fingerprint',p.fingerprint,mono] as [string,string,Face]]:[]),['Time',isoTime(p.signedAt)]]);
  }
  y-=18;
  for(const line of ts.wrap(sans,CERT_NOTE,8.5,CW)){await ensure(14);await ts.draw(page,sans,line,MX,y-8.5*.85,8.5,NOTE);y-=13.2;}

  if(inPlace){
    // The thanks line closes the certificate: a quiet bordered box, italic.
    const lines=ts.wrap(serifI,THANKS,10.5,CW-32),h=lines.length*14+18;
    y-=12;if(y-h<54)await cont();
    page.drawSvgPath(roundRect(CW,h,6),{x:MX,y,borderColor:rgb(...RULE),borderWidth:.6});
    let ty=y-9-10.5*.8;for(const line of lines){await ts.draw(page,serifI,line,MX+16,ty,10.5,NOTE);ty-=14;}
    y-=h;
  }

  // Footers last, when the total page count is known.
  const all=doc.getPages(),total=all.length;
  for(const [i,p] of all.entries()){
    const left=footLeft.get(p);
    if(left)await ts.draw(p,sans,clean(left),MX,32.4,7.5,FOOT);
    await ts.draw(p,sans,`Page ${i+1} of ${total}`,p.getWidth()-MX,32.4,7.5,FOOT,'right');
  }
  doc.setTitle(`${PRODUCT} ${kind.short} ${v.agreement.version}, signed copy`);
  doc.setSubject(`${kind.title}, version ${v.agreement.version}`);
  doc.setKeywords([`${HASH_KEYWORD}${v.agreement.sha256}`,`tavotto-packet:${v.packet}`]);
  doc.setProducer(`${PRODUCT} CLA`);doc.setCreator(`${PRODUCT} CLA`);doc.setCreationDate(now);doc.setModificationDate(now);
  return doc.save();
}
