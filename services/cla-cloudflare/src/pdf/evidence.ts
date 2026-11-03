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
  overrides?:{agreementPdf?:Uint8Array;font?:Uint8Array;rasterizer?:import('./raster.ts').Rasterizer;now?:Date;serviceHost?:string};
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
  confirmations:string[];fingerprint?:string;consent?:string;
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
    const consent=typeof e.consent_ui_version==='string' && /^[A-Za-z0-9._-]{1,20}$/.test(e.consent_ui_version)?e.consent_ui_version:undefined;
    const keys=Object.keys(att),known=Object.keys(CONFIRMATIONS);
    const ordered=[...known.filter(k=>keys.includes(k)),...keys.filter(k=>!known.includes(k)).sort()];
    return {role,name:e.name,address:e.address,signature:e.signature,login:e.github_login_at_signing,githubId:e.github_id,signedAt:e.signed_at_unix,
      confirmations:ordered.map(k=>CONFIRMATIONS[k]??k),fingerprint,consent};
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
