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
 * 常规。保留的是「语言」「恢复默认布局」「快捷键速查表」三个真动作；
 * 「什么时候生效」「自动保存怎么实现」「重置具体影响什么」三段解释进问号。
 *
 * 快捷键那一行从独立分区并进来（ADR 0038）：一个分区只有一个按钮，是导航
 * 上的噪音；它本身不是设置，是一个入口。
 */
export function GeneralSettings({ close }: { close: () => void }) {
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
      <SettingRow label={st('shortcuts.label')} help={st('shortcuts.hint')}>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setShortcutHelpOpen(true)
          }}
        >
          {st('shortcuts.open')}
        </Button>
      </SettingRow>
    </SettingSection>
  )
}
