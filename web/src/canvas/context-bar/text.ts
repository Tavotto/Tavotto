import { t as translate } from '@/i18n'
import { msg, type UiMessage } from '@/i18n'

/** 本组文案在 workspace:contextBar.* 下 */
export const qb = (key: string, values?: Record<string, unknown>) =>
  translate(`contextBar.${key}`, { ns: 'workspace', ...(values ?? {}) })
export const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')
