import type { Config, Policy, Packet } from './types.ts';
import { Rejected } from './types.ts';
import { canonical, sha256 } from './crypto.ts';
export const policyDigest = (p: Policy) => sha256(canonical({ agreements: p.agreements, exemptions: p.exemptions }));
export function validId(id: unknown): id is number { return Number.isSafeInteger(id) && (id as number) > 0; }
export const validSha = (sha: unknown): sha is string => typeof sha === 'string' && /^[0-9a-f]{40}$/.test(sha);
export function validateConfig(c: Config): void {
  const u = new URL(c.origin);
  if (u.origin !== c.origin || u.protocol !== 'https:' || u.username || u.password) throw new Rejected('origin_invalid');
  if (![c.repositoryId, c.holderId, c.appId].every(validId) || !validSha(c.referenceSha)) throw new Rejected('identity_config_invalid');
  if (c.appId === 15368 || c.appSlug === 'github-actions' || !c.appSlug || !c.checkName || c.autoHolder !== false) throw new Rejected('independent_app_required');
  const names = c.policy.exemptions.map(e => e.login);
  if (canonical([...names].sort()) !== canonical(Object.keys(c.exemptionIds).sort())) throw new Rejected('exemptions_unpinned');
  const ids = Object.values(c.exemptionIds);
  if (!ids.every(validId) || new Set(ids).size !== ids.length || c.exemptionIds.erwanjun !== c.holderId) throw new Rejected('exemptions_invalid');
  if(canonical(Object.keys(c.policy.agreements).sort())!==canonical(['corporate','individual']))throw new Rejected('agreement_invalid');
  for (const a of Object.values(c.policy.agreements)) {
    if (a.version !== '1.0' || !/^[a-f0-9]{64}$/.test(a.sha256)) throw new Rejected('agreement_invalid');
  }
}
export function packetAgreementMatches(c: Config, p: Packet): boolean {
  const a = c.policy.agreements[p.kind];
  return !!a && p.version === a.version && p.digest === a.sha256;
}
// Deadline applies only to collecting new signatures, never to completed eligibility.
export function currentPacket(c: Config, p: Packet, now: number): boolean {
  return packetAgreementMatches(c,p) && p.valid_until > now;
}
export function autoHolderProposal(): { enabled: false; disposition: 'personal_review_required' } {
  return { enabled: false, disposition: 'personal_review_required' };
}
