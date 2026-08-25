import { t as translate } from '@/i18n'

/** playground 这一屏的文案都在 `dialogs:playground.*` 下——所有组件共用这一个短助手 */
export const pg = (key: string, values?: Record<string, unknown>) =>
  translate(`playground.${key}`, { ns: 'dialogs', ...(values ?? {}) })
