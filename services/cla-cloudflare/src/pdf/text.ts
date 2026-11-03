import type {PDFDocument,PDFFont,PDFPage} from 'pdf-lib';
import {rgb} from 'pdf-lib';
import type {RasterStyle,Rasterizer} from './raster.ts';

export type Color=[number,number,number];
/** A typeface: the PDF font used when it can encode the text, and the canvas style used otherwise. */
export interface Face {style:RasterStyle;font:PDFFont;covers:(text:string)=>boolean}
export const clean=(text:string)=>text.replace(/\r\n?/g,'\n').replace(/\t/g,' ').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,' ');
export const standardFace=(style:RasterStyle,font:PDFFont):Face=>({style,font,covers:text=>{try{font.widthOfTextAtSize(text,1);return true;}catch{return false;}}});
export const customFace=(style:RasterStyle,font:PDFFont):Face=>{
  const set=new Set(font.getCharacterSet());
  return {style,font,covers:text=>Array.from(text).every(ch=>set.has(ch.codePointAt(0)!))};
};
export class Typesetter {
  readonly doc:PDFDocument;readonly raster:Rasterizer;
  constructor(doc:PDFDocument,raster:Rasterizer){this.doc=doc;this.raster=raster;}
  width(face:Face,text:string,size:number):number {
    return face.covers(text)?face.font.widthOfTextAtSize(text,size):this.raster.measure(text,face.style,size);
  }
  /** Greedy wrap: honours newlines, prefers spaces, and breaks inside a word (or CJK run) only when it must. */
  wrap(face:Face,text:string,size:number,maxWidth:number):string[] {
    const out:string[]=[];
    for(const paragraph of clean(text).split('\n')) {
      let line='';
      for(const ch of Array.from(paragraph)) {
        const candidate=line+ch;
        if(line==='' || this.width(face,candidate,size)<=maxWidth){line=candidate;continue;}
        const space=line.lastIndexOf(' ');
        if(space>0 && ch!==' '){out.push(line.slice(0,space).trimEnd());line=line.slice(space+1)+ch;}
        else{out.push(line.trimEnd());line=ch===' '?'':ch;}
      }
      out.push(line.trimEnd());
    }
    return out;
  }
  /** Draws one line with its baseline at y. Returns the drawn width. */
  async draw(page:PDFPage,face:Face,text:string,x:number,y:number,size:number,color:Color,align:'left'|'right'='left'):Promise<number> {
    if(text==='')return 0;
    const width=this.width(face,text,size),left=align==='right'?x-width:x;
    if(face.covers(text)){page.drawText(text,{x:left,y,size,font:face.font,color:rgb(...color)});return width;}
    const image=await this.raster.render(text,face.style,size,color);
    page.drawImage(await this.doc.embedPng(image.png),{x:left-2,y:y-image.descent,width:image.width,height:image.height});
    return width;
  }
}
