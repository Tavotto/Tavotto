import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useUiStore } from '@/store/uiStore'
import { Toggle } from '../ui/Toggle'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 侧栏行为。两个开关保留；断点与自动收起规则进问号。 */
export function SidebarSettings() {
  useTranslation('dialogs')
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  return (
    <SettingSection>
      <SettingRow
        label={st('sidebars.leftPinned')}
        help={
          <>
            <p>{st('sidebars.leftPinnedHint')}</p>
            <p>{st('sidebars.breakpoints')}</p>
          </>
        }
      >
        <Toggle
          checked={leftPinned}
          onChange={(v) => useUiStore.getState().setLeftPinned(v)}
          aria-label={st('sidebars.leftPinned')}
        />
      </SettingRow>
      <SettingRow
        label={st('sidebars.rightPinned')}
        help={
          <>
            <p>{st('sidebars.rightPinnedHint')}</p>
            <p>{st('sidebars.breakpoints')}</p>
          </>
        }
      >
        <Toggle
          checked={rightPinned}
          onChange={(v) => useUiStore.getState().setRightPinned(v)}
          aria-label={st('sidebars.rightPinned')}
        />
      </SettingRow>
    </SettingSection>
  )
}
