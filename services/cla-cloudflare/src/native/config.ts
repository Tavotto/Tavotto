import type { Config } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { validateConfig, validId } from '../core/policy.ts';

export interface LiveConfig extends Config {
  repository: string; installationId: number; clientId: string; holderName: string;
  githubEnabled: boolean; oauthEnabled: boolean; signingEnabled: boolean; checkPublishingEnabled: boolean;
  backgroundEnabled?: boolean;
}
export interface Secrets { clientSecret: string; pkcs8: string; evidenceKey: string }
export function validateLive(c: LiveConfig): void {
  validateConfig(c);
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(c.repository) ||
      !validId(c.installationId) || !/^[A-Za-z0-9_]{1,100}$/.test(c.clientId) ||
      typeof c.holderName !== 'string' || !c.holderName.trim() || c.holderName.length > 1000 ||
      c.syntheticSigning !== false || c.mockCheckPublishing !== false ||
      ![c.githubEnabled,c.oauthEnabled,c.signingEnabled,c.checkPublishingEnabled].every(v => typeof v === 'boolean'))
    throw new Rejected('live_configuration_invalid');
  if ((c.oauthEnabled || c.checkPublishingEnabled) && !c.githubEnabled) throw new Rejected('network_disabled');
  if (c.backgroundEnabled===true && !c.githubEnabled) throw new Rejected('network_disabled');
  if ((c.signingEnabled || c.checkPublishingEnabled) &&
      (c.policy.provider.configured !== true || c.policy.provider.app_id !== c.appId ||
       c.policy.provider.app_slug !== c.appSlug || c.policy.provider.check_name !== c.checkName ||
       c.policy.provider.protocol !== 'tavotto-cla-v1')) throw new Rejected('provider_unconfigured');
}
export const attestations = (role: 'contributor'|'holder') =>
  ['personal','electronic','read','identity',...(role === 'contributor' ? ['adult','own_rights','independent'] : ['reviewed'])];
