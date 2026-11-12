// Rasterising fallback for text a PDF standard font cannot encode (for example a Chinese name).
// It runs only in the browser: the text is drawn with the viewer's own fonts onto a canvas at
// 4x scale and embedded as a PNG, so no glyph is dropped and no large CJK font has to be shipped.
export type RasterStyle='sans'|'sansBold'|'serif'|'serifBold'|'serifItalic'|'mono'|'script';
export interface RasterImage {png:Uint8Array;width:number;height:number;descent:number}
export interface Rasterizer {
  /** Width in points of `text` at `size` points. */
  measure(text:string,style:RasterStyle,size:number):number;
  /** PNG of `text`; width/height/descent are in points (descent = baseline height above the image bottom). */
  render(text:string,style:RasterStyle,size:number,color:[number,number,number]):Promise<RasterImage>;
}
const CJK='"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC","Noto Sans SC","Source Han Sans SC"';
const FAMILY:Record<RasterStyle,string>={
  sans:`-apple-system,"Helvetica Neue",Arial,${CJK},sans-serif`,
  sansBold:`-apple-system,"Helvetica Neue",Arial,${CJK},sans-serif`,
  serif:`Charter,Georgia,"Times New Roman",${CJK},serif`,
  serifBold:`Charter,Georgia,"Times New Roman",${CJK},serif`,
  serifItalic:`Charter,Georgia,"Times New Roman",${CJK},serif`,
  mono:`ui-monospace,Menlo,Consolas,${CJK},monospace`,
  script:`"Snell Roundhand","Apple Chancery","STKaiti","Kaiti SC","KaiTi","Noto Serif CJK SC","Songti SC","SimSun",cursive`,
};
const cssFont=(style:RasterStyle,px:number)=>
  `${style==='serifItalic'?'italic ':''}${style==='sansBold'||style==='serifBold'?'700 ':''}${px}px ${FAMILY[style]}`;
type Ctx=CanvasRenderingContext2D|OffscreenCanvasRenderingContext2D;
function canvas(w:number,h:number):{ctx:Ctx;toPng:()=>Promise<Uint8Array>} {
  if(typeof OffscreenCanvas!=='undefined') {
    const c=new OffscreenCanvas(w,h),ctx=c.getContext('2d');if(!ctx)throw new Error('canvas_unavailable');
    return {ctx,toPng:async()=>new Uint8Array(await (await c.convertToBlob({type:'image/png'})).arrayBuffer())};
  }
  if(typeof document==='undefined')throw new Error('canvas_unavailable');
  const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');if(!ctx)throw new Error('canvas_unavailable');
  return {ctx,toPng:()=>new Promise((resolve,reject)=>c.toBlob(b=>b?b.arrayBuffer().then(x=>resolve(new Uint8Array(x)),reject):reject(new Error('canvas_unavailable')),'image/png'))};
}
export function canvasRasterizer(scale=4):Rasterizer {
  let probe:Ctx|undefined;
  const measureCtx=()=>probe??=canvas(1,1).ctx;
  return {
    measure(text,style,size) {const ctx=measureCtx();ctx.font=cssFont(style,size);return ctx.measureText(text).width;},
    async render(text,style,size,color) {
      const probeCtx=measureCtx();probeCtx.font=cssFont(style,size);
      const width=probeCtx.measureText(text).width,pad=2,w=Math.max(1,Math.ceil((width+2*pad)*scale)),h=Math.ceil(size*1.5*scale),base=Math.round(size*1.15*scale);
      const {ctx,toPng}=canvas(w,h);ctx.font=cssFont(style,size*scale);ctx.textBaseline='alphabetic';
      ctx.fillStyle=`rgb(${color.map(x=>Math.round(x*255)).join(',')})`;ctx.fillText(text,pad*scale,base);
      return {png:await toPng(),width:w/scale,height:h/scale,descent:(h-base)/scale};
    },
  };
}
