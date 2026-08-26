import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { SettingRow, SettingSection } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 画布与编辑。「关联元素是什么」那一大段（原来常驻两行）进问号；
 * 「其他画布设置在右栏」从两段文字换成一个能点的按钮——一句解释都不需要，
 * 按钮本身就说明了去哪。
 */
export function CanvasSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  const withCompanions = useUiStore((s) => s.dragAxesWithCompanions)
  return (
    <SettingSection>
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
  )
}
