import { useTranslation } from 'react-i18next'
import { msg, setLocale, SUPPORTED_LOCALES, LOCALE_LABELS, t as translate } from '@/i18n'
import { useLocale } from '@/i18n/react'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Select } from '../ui/Select'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 常规。保留的是「语言」与「恢复默认布局」两个真动作；
 * 「什么时候生效」「自动保存怎么实现」「重置具体影响什么」三段解释进问号。
 */
export function GeneralSettings() {
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  const locale = useLocale()
  return (
    <SettingSection>
      {/*
        语言：选完立刻生效（i18next 的 languageChanged 会让整棵树重渲染），
        偏好写在独立的 tavotto.locale 里，不进任何文档或项目数据。
      */}
      <SettingRow label={st('general.language')} help={st('general.languageHint')}>
        <Select
          className="w-[160px]"
          ariaLabel={st('general.language')}
          value={locale}
          onChange={(v) => void setLocale(v as (typeof SUPPORTED_LOCALES)[number])}
          options={SUPPORTED_LOCALES.map((l) => ({ value: l, label: LOCALE_LABELS[l] }))}
        />
      </SettingRow>
      <SettingRow label={st('general.autosave')} help={st('general.autosaveHint')}>
        <span className="text-xs text-ink-3">{st('general.autosaveState')}</span>
      </SettingRow>
      <SettingRow label={st('general.layout')} help={st('general.resetLayoutHint')}>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            try {
              localStorage.removeItem('tavotto.ui')
            } catch {
              /* 忽略 */
            }
            setStatus(msg('settings.general.layoutReset', undefined, 'dialogs'))
          }}
        >
          {st('general.resetLayout')}
        </Button>
      </SettingRow>
    </SettingSection>
  )
}
