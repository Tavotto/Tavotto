import {isSignatureStyle,styleOf} from './styles.ts';
import type {SignatureStyle} from './styles.ts';
// Input contract of TavottoClaPdf.build and its validation. Everything here is checked
// before a single page is drawn: a PDF is never produced from inconsistent records.
export interface AgreementInfo {kind:string;version:string;sha256:string;publishedUrl?:string;attribution?:string}
/** Exactly what GET /evidence?packet=…&role=… returns (plus evidence_sha256). */
export interface Evidence {
  protocol?:string;packet:string;role:'contributor'|'holder';name:string;address:string;signature:string;
  attestations:Record<string,boolean>;github_id:number;github_login_at_signing:string;signed_at_unix:number;
  agreement_kind:string;agreement_version:string;agreement_sha256:string;evidence_sha256?:string;[extra:string]:unknown;
}
export interface BuildInput {
  agreement:AgreementInfo;contributor:Evidence;holder:Evidence;contributorSha256?:string;holderSha256?:string;
  /** Offline/test hooks. The signing page does not pass these. */
  overrides?:{agreementPdf?:Uint8Array;font?:Uint8Array;fonts?:Partial<Record<SignatureStyle,Uint8Array>>;rasterizer?:import('./raster.ts').Rasterizer;now?:Date;serviceHost?:string;timeoutMs?:number};
}
export const KINDS:Record<string,{title:string;short:string;you:string}>={
  individual:{title:'Individual Contributor License Agreement',short:'Individual CLA',you:'You (Individual)'},
  corporate:{title:'Corporate Contributor License Agreement',short:'Corporate CLA',you:'You (Corporation)'},
};
// Order follows how the signing page presents them; unknown keys are kept, after these.
export const CONFIRMATIONS:Record<string,string>={
  read:'Read the agreement',personal:'Personal decision',electronic:'Electronic signature',identity:'Own identity',
  adult:'18 or older',own_rights:'Holds the rights',independent:'Signs as an individual',reviewed:'Reviewed as rights holder',
};
const HEX=/^[0-9a-f]{64}$/,LOGIN=/^[A-Za-z0-9][A-Za-z0-9-]*$/;
const fail=(code:string):never=>{throw new Error(code);};
const text=(v:unknown,max:number)=>typeof v==='string' && v.trim()!=='' && v.length<=max;
export interface Party {
  role:'contributor'|'holder';name:string;address:string;signature:string;login:string;githubId:number;signedAt:number;
  confirmations:string[];fingerprint?:string;consent?:string;style:SignatureStyle;
}
/** Only these two optional fields may come from the (hash-verified) agreement PDF metadata. */
export function validMeta(url:unknown,attribution:unknown):{publishedUrl?:string;attribution?:string} {
  const out:{publishedUrl?:string;attribution?:string}={};
  if(text(url,500) && /^https:\/\//.test(url as string))out.publishedUrl=url as string;
  if(text(attribution,200))out.attribution=attribution as string;
  return out;
}
/** One underlined line of the agreement's own signature block, in PDF points (origin bottom-left); y is the line's upper edge. */
export interface FieldBox {page:number;x:number;y:number;w:number;printed?:true}
export interface PartyFields {signature:FieldBox;name:FieldBox;date:FieldBox;address:FieldBox[];title?:FieldBox;github?:FieldBox}
export interface SignatureFields {contributor:PartyFields;holder:PartyFields}
const REQUIRED:Record<'contributor'|'holder',string[]>={contributor:['signature','name','address','date','github'],holder:['signature','name','title','address','date']};
/**
 * `fields` of the (hash-verified) agreement PDF meta, strictly checked against the real page sizes. Anything off
 * (missing key, unknown key, non-finite or out-of-page number, bad page index) returns undefined and the caller
 * falls back to the separate Signatures page: a position is never guessed or clamped.
 */
export function validFields(raw:unknown,pages:{width:number;height:number}[]):SignatureFields|undefined {
  const obj=(v:unknown):Record<string,unknown>|undefined=>v&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:undefined;
  const box=(v:unknown):FieldBox|undefined=>{
    const o=obj(v);if(!o)return undefined;
    if(Object.keys(o).some(k=>!['page','x','y','w','printed'].includes(k)) || (o.printed!==undefined && o.printed!==true))return undefined;
    const {page,x,y,w}=o;
    if(typeof page!=='number' || typeof x!=='number' || typeof y!=='number' || typeof w!=='number' || !Number.isInteger(page) || ![x,y,w].every(Number.isFinite))return undefined;
    const pg=pages[page];
    if(!pg || x<0 || y<=0 || w<20 || x+w>pg.width || y>=pg.height)return undefined;
    return o.printed?{page,x,y,w,printed:true}:{page,x,y,w};
  };
  const party=(role:'contributor'|'holder'):PartyFields|undefined=>{
    const o=obj(obj(raw)?.[role]);if(!o)return undefined;
    const need=REQUIRED[role];if(Object.keys(o).length!==need.length || need.some(k=>!(k in o)))return undefined;
    const out:Record<string,unknown>={};
    for(const k of need){
      if(k==='address'){const a=o.address;if(!Array.isArray(a) || a.length<1 || a.length>3)return undefined;const boxes=a.map(box);if(boxes.some(b=>!b))return undefined;out.address=boxes;}
      else{const b=box(o[k]);if(!b)return undefined;out[k]=b;}
    }
    return out as unknown as PartyFields;
  };
  const o=obj(raw);if(!o || Object.keys(o).length!==2)return undefined;
  const contributor=party('contributor'),holder=party('holder');
  return contributor && holder?{contributor,holder}:undefined;
}
export function checkInput(input:BuildInput):{agreement:AgreementInfo;packet:string;contributor:Party;holder:Party} {
  const a=input?.agreement;
  if(!a || typeof a!=='object' || !KINDS[a.kind] || !text(a.version,40) || !HEX.test(a.sha256) || (a.publishedUrl!==undefined && (!text(a.publishedUrl,500) || !/^https:\/\//.test(a.publishedUrl))) ||
     (a.attribution!==undefined && !text(a.attribution,200)))fail('agreement_input_invalid');
  const party=(role:'contributor'|'holder',e:Evidence,extra:string|undefined):Party=>{
    if(!e || typeof e!=='object' || e.role!==role)fail('evidence_role_mismatch');
    if(e.agreement_sha256!==a.sha256 || e.agreement_kind!==a.kind || e.agreement_version!==a.version)fail('evidence_agreement_mismatch');
    if(!text(e.packet,200) || !text(e.name,1000) || !text(e.address,1000) || e.signature!==e.name)fail('evidence_invalid');
    if(!Number.isSafeInteger(e.github_id) || e.github_id<=0 || typeof e.github_login_at_signing!=='string' || !LOGIN.test(e.github_login_at_signing) ||
       !Number.isSafeInteger(e.signed_at_unix) || e.signed_at_unix<=0)fail('evidence_invalid');
    const att=e.attestations;
    if(!att || typeof att!=='object' || Object.keys(att).length===0 || Object.values(att).some(v=>v!==true))fail('evidence_attestations_invalid');
    const fingerprint=e.evidence_sha256??extra;
    if(fingerprint!==undefined && (typeof fingerprint!=='string' || !HEX.test(fingerprint) || (e.evidence_sha256!==undefined && extra!==undefined && extra!==e.evidence_sha256)))fail('evidence_fingerprint_missing');
    if(e.signature_style!==undefined && !isSignatureStyle(e.signature_style))fail('evidence_invalid');
    const consent=typeof e.consent_ui_version==='string' && /^[A-Za-z0-9._-]{1,20}$/.test(e.consent_ui_version)?e.consent_ui_version:undefined;
    const keys=Object.keys(att),known=Object.keys(CONFIRMATIONS);
    const ordered=[...known.filter(k=>keys.includes(k)),...keys.filter(k=>!known.includes(k)).sort()];
    return {role,name:e.name,address:e.address,signature:e.signature,login:e.github_login_at_signing,githubId:e.github_id,signedAt:e.signed_at_unix,
      confirmations:ordered.map(k=>CONFIRMATIONS[k]??k),fingerprint,consent,style:styleOf(e.signature_style)};
  };
  const contributor=party('contributor',input.contributor,input.contributorSha256),holder=party('holder',input.holder,input.holderSha256);
  if(input.contributor.packet!==input.holder.packet)fail('evidence_packet_mismatch');
  return {agreement:a,packet:input.contributor.packet,contributor,holder};
}
const two=(n:number)=>String(n).padStart(2,'0');
const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
export const longDate=(d:Date)=>`${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
export const longTime=(unix:number)=>{const d=new Date(unix*1000);return `${longDate(d)}, ${two(d.getUTCHours())}:${two(d.getUTCMinutes())}:${two(d.getUTCSeconds())} UTC`;};
export const isoTime=(unix:number)=>{const d=new Date(unix*1000);return `${d.getUTCFullYear()}-${two(d.getUTCMonth()+1)}-${two(d.getUTCDate())} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())}:${two(d.getUTCSeconds())} UTC`;};
