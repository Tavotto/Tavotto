import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 快捷键。本轮**保留在设置里**（迁到 Help 会动导航结构，超出本轮范围——
 * 记在 docs/ux/UX_CONSISTENCY_PASS.md 的延后项里）。原来那句无信息量的
 * 「全部快捷键见速查表」换成一行动作 + 问号里的「按 ? 随时打开」。
 */
export function ShortcutSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  return (
    <SettingSection>
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
