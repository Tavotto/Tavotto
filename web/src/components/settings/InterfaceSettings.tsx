import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 界面。原来的「侧栏行为」与「画布与编辑」两个分区合成一页（ADR 0038）：
 * 它们都是「工作台长什么样、怎么响应你」的偏好，各自只有两行、一行，
 * 单独占导航上一格是噪音。解释仍然全在问号里。
 */
export function InterfaceSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  const withCompanions = useUiStore((s) => s.dragAxesWithCompanions)
  return (
    <div className="flex flex-col gap-4">
      <SettingSection title={st('section.sidebars')}>
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

      <SettingSection title={st('section.canvas')}>
        <SettingRow
          label={st('canvas.dragCompanions')}
          help={st('canvas.companionsExplain')}
          status={withCompanions ? st('canvas.dragCompanionsHint') : undefined}
        >
          <Toggle
            checked={withCompanions}
            onChange={(v) => useUiStore.getState().setCanvasPref({ dragAxesWithCompanions: v })}
            aria-label={st('canvas.dragCompanionsAria')}
          />
        </SettingRow>
        <SettingRow label={st('canvas.more')} help={st('canvas.elsewhere')}>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              close()
              useUiStore.getState().setRightTab('canvas')
            }}
          >
            {st('canvas.openCanvasSettings')}
          </Button>
        </SettingRow>
      </SettingSection>
    </div>
  )
}
