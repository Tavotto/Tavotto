import type { Config } from '../core/types.ts';
import { Rejected } from '../core/types.ts';
import { validateConfig, validId } from '../core/policy.ts';

export interface LiveConfig extends Config {
  repository: string; installationId: number; clientId: string; holderName: string;
  githubEnabled: boolean; oauthEnabled: boolean; signingEnabled: boolean; checkPublishingEnabled: boolean;
  backgroundEnabled?: boolean;
  delegatedCountersigningEnabled?: boolean;
  holderAuthorizationKey?: string;
  holderAuthorizationId?: string;
  automaticVerificationEnabled?: boolean;
  automaticPublishingEnabled?: boolean;
  agreementActivationEnabled?: boolean;
  agreementActivationHash?: string;
  confirmationSlotEnabled?: boolean;
  mergePrefixAssessmentEnabled?: boolean;
}
export interface Secrets { clientSecret: string; pkcs8: string; evidenceKey: string }
export function validateLive(c: LiveConfig): void {
  if(c.mergePrefixAssessmentEnabled!==undefined && typeof c.mergePrefixAssessmentEnabled!=='boolean')throw new Rejected('live_configuration_invalid');
  if(c.mergePrefixAssessmentEnabled===true && !c.githubEnabled)throw new Rejected('network_disabled');
  if(c.confirmationSlotEnabled!==undefined && typeof c.confirmationSlotEnabled!=='boolean')throw new Rejected('live_configuration_invalid');
  if(c.confirmationSlotEnabled===true && (!c.backgroundEnabled || !c.checkPublishingEnabled))throw new Rejected('confirmation_slot_recovery_required');
  if(c.agreementActivationEnabled!==undefined && typeof c.agreementActivationEnabled!=='boolean')throw new Rejected('live_configuration_invalid');
  if(c.agreementActivationEnabled===true && !/^[0-9a-f]{64}$/.test(c.agreementActivationHash ?? ''))throw new Rejected('agreement_activation_pin_required');
  validateConfig(c,c.agreementActivationEnabled===true);
  if(c.automaticPublishingEnabled!==undefined && typeof c.automaticPublishingEnabled!=='boolean')throw new Rejected('live_configuration_invalid');
  if(c.automaticPublishingEnabled===true && (!c.automaticVerificationEnabled || !c.checkPublishingEnabled || !c.backgroundEnabled))throw new Rejected('automatic_publication_recovery_required');
  if(c.delegatedCountersigningEnabled!==undefined && typeof c.delegatedCountersigningEnabled!=='boolean' ||
    c.automaticVerificationEnabled!==undefined && typeof c.automaticVerificationEnabled!=='boolean')throw new Rejected('live_configuration_invalid');
  if(c.delegatedCountersigningEnabled===true && (!c.signingEnabled ||
    !/^[A-Za-z0-9_-]{43}$/.test(c.holderAuthorizationKey ?? '') || !/^[A-Za-z0-9_-]{43}$/.test(c.holderAuthorizationId ?? '')))
    throw new Rejected('holder_authorization_configuration_required');
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(c.repository) ||
      !validId(c.installationId) || !/^[A-Za-z0-9_]{1,100}$/.test(c.clientId) ||
      typeof c.holderName !== 'string' || !c.holderName.trim() || c.holderName.length > 1000 ||
      c.syntheticSigning !== false || c.mockCheckPublishing !== false ||
      ![c.githubEnabled,c.oauthEnabled,c.signingEnabled,c.checkPublishingEnabled].every(v => typeof v === 'boolean'))
    throw new Rejected('live_configuration_invalid');
  if ((c.oauthEnabled || c.checkPublishingEnabled) && !c.githubEnabled) throw new Rejected('network_disabled');
  if (c.backgroundEnabled===true && !c.githubEnabled) throw new Rejected('network_disabled');
  if (c.automaticVerificationEnabled===true && !c.githubEnabled) throw new Rejected('network_disabled');
  if ((c.signingEnabled || c.checkPublishingEnabled) &&
      (c.policy.provider.configured !== true || c.policy.provider.app_id !== c.appId ||
       c.policy.provider.app_slug !== c.appSlug || c.policy.provider.check_name !== c.checkName ||
       c.policy.provider.protocol !== 'tavotto-cla-v1')) throw new Rejected('provider_unconfigured');
}
export const attestations = (role: 'contributor'|'holder') =>
  ['personal','electronic','read','identity',...(role === 'contributor' ? ['adult','own_rights','independent'] : ['reviewed'])];
