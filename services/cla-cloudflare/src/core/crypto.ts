import { Rejected } from './types.ts';
const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', { fatal: true });
export function canonical(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (typeof value === 'object' && value) {
    const obj = value as Record<string, unknown>;
    return '{' + Object.keys(obj).sort().map(k => JSON.stringify(k) + ':' + canonical(obj[k])).join(',') + '}';
  }
  throw new Rejected('invalid_canonical_value');
}
export const bytes = (text: string): Uint8Array<ArrayBuffer> => enc.encode(text);
export function base64url(raw: Uint8Array): string {
  return btoa(String.fromCharCode(...raw)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
export function unbase64(text: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
}
export function randomToken(): string { return base64url(crypto.getRandomValues(new Uint8Array(32))); }
export async function sha256(input: string | Uint8Array<ArrayBuffer>): Promise<string> {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', typeof input === 'string' ? bytes(input) : input));
  return [...hash].map(b => b.toString(16).padStart(2, '0')).join('');
}
export async function importAes(key: Uint8Array<ArrayBuffer>): Promise<CryptoKey> {
  if (key.length !== 32) throw new Rejected('encryption_key_invalid');
  return crypto.subtle.importKey('raw', key, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
export async function seal(key: CryptoKey, plaintext: string, aad: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cipher = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: bytes(aad) }, key, bytes(plaintext));
  return canonical({ v: 1, iv: base64url(iv), ciphertext: base64url(new Uint8Array(cipher)) });
}
export async function open(key: CryptoKey, envelope: string, aad: string): Promise<string> {
  try {
    const e = JSON.parse(envelope);
    if (e.v !== 1) throw new Error();
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unbase64(e.iv), additionalData: bytes(aad) }, key, unbase64(e.ciphertext));
    return dec.decode(plain);
  } catch { throw new Rejected('evidence_unavailable'); }
}
export async function validWebhook(secret: Uint8Array<ArrayBuffer>, raw: Uint8Array<ArrayBuffer>, signature: string): Promise<boolean> {
  if (!/^sha256=[a-f0-9]{64}$/.test(signature)) return false;
  const key = await crypto.subtle.importKey('raw', secret, { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
  const sig = Uint8Array.from(signature.slice(7).match(/../g)!, s => parseInt(s, 16));
  return crypto.subtle.verify('HMAC', key, sig, raw);
}
// Synthetic App token helper only. Offline fixture generation is outside request handlers.
export function importAppSigningKey(pkcs8: string): Promise<CryptoKey> {
  return crypto.subtle.importKey('pkcs8', unbase64(pkcs8), { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']);
}
export async function appJwt(pkcs8: string, appId: number, now: number): Promise<string> {
  return appJwtWithKey(await importAppSigningKey(pkcs8), appId, now);
}
export async function appJwtWithKey(key: CryptoKey, appId: number, now: number): Promise<string> {
  const body = base64url(bytes(canonical({ alg: 'RS256', typ: 'JWT' }))) + '.' +
    base64url(bytes(canonical({ iss: String(appId), iat: now - 60, exp: now + 300 })));
  return body + '.' + base64url(new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, bytes(body))));
}
