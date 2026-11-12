// How a typed signature is drawn. Typing the full name is what signs; the style is only its look, chosen on the
// signing page and stored in the signed evidence so every later PDF copy draws it the same way.
export const SIGNATURE_STYLES=['signature','formal','pen','hand'] as const;
export type SignatureStyle=typeof SIGNATURE_STYLES[number];
/** Records signed before styles existed were drawn in Great Vibes. */
export const DEFAULT_SIGNATURE_STYLE:SignatureStyle='formal';
export const isSignatureStyle=(v:unknown):v is SignatureStyle=>typeof v==='string' && (SIGNATURE_STYLES as readonly string[]).includes(v);
/** Same-origin fixed font paths served by the Worker (worker.ts PDF_ASSETS). */
export const SIGNATURE_FONT_PATHS:Record<SignatureStyle,string>={
  signature:'/cla-sig-signature.ttf',formal:'/cla-pdf-font.ttf',pen:'/cla-sig-pen.ttf',hand:'/cla-sig-hand.ttf'};
export const styleOf=(v:unknown):SignatureStyle=>isSignatureStyle(v)?v:DEFAULT_SIGNATURE_STYLE;
