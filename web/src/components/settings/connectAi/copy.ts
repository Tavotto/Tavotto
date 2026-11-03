import { t as translate } from '@/i18n'

/** 本节文案在 dialogs:settings.connectAi.* 下 */
export const cx = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.connectAi.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 本节文案在 dialogs:settings.agents.* 下（厂商目录名 / Key 提示） */
export const ag = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.agents.${key}`, { ns: 'dialogs', ...(values ?? {}) })
