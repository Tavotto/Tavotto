// Offline helpers for render-agreement-pdf.mjs: turn the agreement's own blank signature block (the fenced blocks after
// **You (...)** and **Us**) into a printed form, and find where each underlined line landed in the produced PDF.
// Positions come from the PDF itself (pdfjs-dist reads the rule rectangles Chrome painted and the marker words), never from CSS arithmetic.
import {getDocument,OPS} from 'pdfjs-dist/legacy/build/pdf.mjs';
const FIELDS={contributor:['signature','name','address','date','github'],holder:['signature','name','title','address','date']};
const LABEL=/^(Signature|Name|Title|Address|Date|GitHub):\s*(.*)$/;
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const blank=s=>/^[_\s]*$/.test(s);
/** One fenced block -> [{field,lines:[printed|'' ...]}], or null when it is not exactly the expected form for the role. */
function parseBlock(text,role){
  const rows=[];
  for(const line of text.split('\n')){
    const m=LABEL.exec(line);
    if(m){rows.push({field:m[1].toLowerCase(),lines:[blank(m[2])?'':m[2].trim()]});continue;}
    if(line.trim()==='')continue;
    const last=rows.at(-1);if(!last||!/^\s+\S/.test(line))return null;last.lines.push(blank(line)?'':line.trim());
  }
  const names=rows.map(r=>r.field);
  if(names.join()!==FIELDS[role].join())return null;
  if(rows.some(r=>r.lines.length>3||(r.field!=='address'&&r.lines.length!==1)))return null;
  return rows;
}
/** Replaces the two heading+code pairs by form HTML. Returns {tokens,parties} or null (leave the Markdown untouched). */
export function formifySignatureBlocks(tokens,{markers}){
  const out=[...tokens],found={};
  for(let i=0;i<out.length;i++){
    const t=out[i];if(t.type!=='paragraph')continue;
    const h=/^\*\*(You\b[^*]*|Us)\*\*$/.exec(t.text.trim());if(!h)continue;
    let j=i+1;while(out[j]?.type==='space')j++;
    const code=out[j];if(code?.type!=='code')continue;
    const role=h[1]==='Us'?'holder':'contributor';
    if(found[role])return null;
    const rows=parseBlock(code.text,role);if(!rows)return null;
    found[role]={role,heading:h[1],rows,at:i,end:j};
  }
  if(!found.contributor||!found.holder)return null;
  const parties=[found.contributor,found.holder];let n=0;
  for(const p of parties.sort((a,b)=>a.at-b.at))for(const r of p.rows)r.marks=r.lines.map(()=>`TVTF${String(n++).padStart(4,'0')}`);
  for(const p of [...parties].sort((a,b)=>b.at-a.at)){
    const rows=p.rows.map(r=>r.lines.map((v,k)=>`<div class="sf-r ${r.field==='signature'?'sf-s':''}"><span class="sf-k">${k===0?esc(r.field==='github'?'GitHub:':r.field[0].toUpperCase()+r.field.slice(1)+':'):''}</span><span class="sf-v">${markers?`<i class="sf-m">${r.marks[k]}</i>`:''}${v?`<b>${esc(v)}</b>`:''}</span></div>`).join('')).join('');
    out.splice(p.at,p.end-p.at+1,{type:'html',block:true,raw:'',text:`<div class="sf"><p class="sf-h"><strong>${esc(p.heading)}</strong></p>${rows}</div>\n`});
  }
  return {tokens:out,parties};
}
export const SIGNATURE_CSS=`.sf{break-inside:avoid;margin:0 0 20pt}.sf-h{margin:0 0 4pt;break-after:avoid}
.sf-r{display:flex;align-items:flex-end;height:25pt}.sf-s{height:40pt}
.sf-k{flex:none;width:68pt;font:9pt ui-monospace,Menlo,monospace;padding-bottom:1.5pt}
.sf-v{position:relative;flex:1;height:100%;border-bottom:.6pt solid #1b1b18;display:flex;align-items:flex-end}
.sf-v b{font-weight:400;font-size:11pt;padding:0 0 2pt 2pt}
.sf-m{position:absolute;left:0;bottom:0;font:5px Helvetica;font-style:normal}`;

const r2=n=>Math.round(n*100)/100;
/** Reads the PDF: marker words (page, x, baseline) and the thin filled rectangles Chrome paints for the underlines. */
async function scan(bytes){
  const doc=await getDocument({data:new Uint8Array(bytes),useSystemFonts:false,verbosity:0}).promise;
  const markers=new Map(),rules=[];
  for(let pn=1;pn<=doc.numPages;pn++){
    const page=await doc.getPage(pn);
    for(const it of (await page.getTextContent()).items){const m=/TVTF\d{4}/.exec(it.str);if(m)markers.set(m[0],{page:pn-1,x:it.transform[4],y:it.transform[5]});}
    const ol=await page.getOperatorList();let ctm=[1,0,0,1,0,0];const stack=[],pending=[];
    const mul=(a,b)=>[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
    ol.fnArray.forEach((fn,i)=>{
      const a=ol.argsArray[i];
      if(fn===OPS.save)stack.push(ctm);else if(fn===OPS.restore)ctm=stack.pop()??ctm;
      else if(fn===OPS.transform)ctm=mul(ctm,a);
      else if(fn===OPS.constructPath&&a[0].length===1&&a[0][0]===OPS.rectangle){const [x,y,w,h]=a[1];const p=(px,py)=>[ctm[0]*px+ctm[2]*py+ctm[4],ctm[1]*px+ctm[3]*py+ctm[5]];
        const c1=p(x,y),c2=p(x+w,y+h);pending.push({x:Math.min(c1[0],c2[0]),w:Math.abs(c2[0]-c1[0]),top:Math.max(c1[1],c2[1]),h:Math.abs(c2[1]-c1[1])});}
      else if(fn===OPS.fill&&pending.length){const r=pending.pop();pending.length=0;if(r.h<1.2&&r.w>20)rules.push({page:pn-1,...r});}
      else if(fn===OPS.endPath||fn===OPS.stroke)pending.length=0;
    });
  }
  await doc.destroy();
  return {markers,rules};
}
const same=(a,b)=>a.length===b.length&&a.every((r,i)=>r.page===b[i].page&&Math.abs(r.x-b[i].x)<.02&&Math.abs(r.top-b[i].top)<.02&&Math.abs(r.w-b[i].w)<.02);
/** Pass 1 (with markers) gives the geometry; pass 2 (shipped, no markers) must paint exactly the same rules. */
export async function locateFields(markedPdf,shippedPdf,parties){
  const a=await scan(markedPdf),b=await scan(shippedPdf);
  if(!same(a.rules,b.rules))throw new Error('signature_block_layout_changed_between_passes');
  const used=new Set(),fields={};
  for(const p of parties){
    const o={};
    for(const r of p.rows){
      const boxes=r.marks.map(id=>{
        const m=a.markers.get(id);if(!m)throw new Error('signature_marker_missing:'+id);
        let best=null;a.rules.forEach((u,i)=>{if(u.page!==m.page||used.has(i)||Math.abs(u.x-m.x)>1.5)return;const d=Math.abs(u.top-m.y);if(d<8&&(!best||d<best.d))best={d,i,u};});
        if(!best)throw new Error('signature_rule_missing:'+id);used.add(best.i);
        return {page:best.u.page,x:r2(best.u.x),y:r2(best.u.top),w:r2(best.u.w),...(r.lines[r.marks.indexOf(id)]?{printed:true}:{})};
      });
      o[r.field]=r.field==='address'?boxes:boxes[0];
    }
    fields[p.role]=o;
  }
  return fields;
}
