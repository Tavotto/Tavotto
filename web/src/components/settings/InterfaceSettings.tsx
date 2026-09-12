import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import { CompanionDiagram } from './CompanionDiagram'
import { SettingRow, SettingSection, settingRowLabelId } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 界面。原来的「侧栏行为」与「画布与编辑」两个分区合成一页（ADR 0038）。
 *
 * 三个开关都改成**结果式名称**（审计 T39）：「左抽屉常驻」「右栏常驻」
 * 「拖动子图联动」是我们内部叫法，用户读不出开了之后会怎样。现在标签说结果，
 * 开关统一落在 `SettingRow` 的控件列（Session 5 的标准行：标题列弹性、控件列定宽）。
 *
 * **像素断点不再写在界面上。** 改动前两个侧栏各挂一段「窗口 ≥1440px 时……」
 * ——那串数字既是开发口径（真实断点在 `uiStore` 是 WIDE=1280 / MEDIUM=1024，
 * 文案里的 1440 早就不对了），又要用户拿窗口宽度去做心算。现在只在**当前窗口
 * 真的限制了它**的时候，就近说一句现在能做到什么。
 */
export function InterfaceSettings({ close }: { close: () => void }) {
  useTranslation('dialogs')
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  const layout = useUiStore((s) => s.layout)
  const withCompanions = useUiStore((s) => s.dragAxesWithCompanions)
  // 'wide' 两侧都能固定，没有限制可说；medium 互斥、narrow 是覆盖层。
  // 状态取自布局状态本身，不在这里第二次判窗口宽度
  const pinLimit =
    layout === 'narrow'
      ? st('sidebars.pinLimitedNarrow')
      : layout === 'medium'
        ? st('sidebars.pinLimitedMedium')
        : undefined
  // 分区之间的间距由外壳的内容容器统一给，这里不再自带一层 gap
  return (
    <>
      <SettingSection title={st('section.sidebars')}>
        <SettingRow
          label={st('sidebars.leftPinned')}
          controlId="setting-left-pinned"
          status={pinLimit}
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-left-pinned')}
            id="setting-left-pinned"
            checked={leftPinned}
            onChange={(v) => useUiStore.getState().setLeftPinned(v)}
          />
        </SettingRow>
        <SettingRow
          label={st('sidebars.rightPinned')}
          controlId="setting-right-pinned"
          status={pinLimit}
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-right-pinned')}
            id="setting-right-pinned"
            checked={rightPinned}
            onChange={(v) => useUiStore.getState().setRightPinned(v)}
          />
        </SettingRow>
      </SettingSection>

      <SettingSection title={st('section.canvas')}>
        {/* 全页唯一的问号：figure 坐标、twinx 孪生轴这类实现词确实解释了
            「为什么偏偏是这几个对象」，但那是技术帮助，不该挡在动作前面。
            界面这一层交给示意图——空间关系用图讲，比两行字快 */}
        <SettingRow
          label={st('canvas.dragCompanions')}
          description={st('canvas.dragCompanionsDesc')}
          help={st('canvas.companionsExplain')}
          controlId="setting-drag-companions"
          // 示意图是说明的一部分，坐在标题列的说明下方（Session 6）；此前它与开关、
          // 问号挤在控件列同一条基线上，读起来像一个奇怪的大图标
          illustration={<CompanionDiagram on={withCompanions} />}
        >
          <Toggle
            aria-labelledby={settingRowLabelId('setting-drag-companions')}
            id="setting-drag-companions"
            checked={withCompanions}
            onChange={(v) => useUiStore.getState().setCanvasPref({ dragAxesWithCompanions: v })}
          />
        </SettingRow>
        <SettingRow label={st('canvas.more')}>
          <Button
            variant="secondary"
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
    </>
  )
}
