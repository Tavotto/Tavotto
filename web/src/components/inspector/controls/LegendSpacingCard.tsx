import { useState } from 'react'
import { Reveal, Row } from '../../ui/Field'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { clearOverride } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { NumberField } from '../../ui/Input'
import { GroupToggle } from '../GroupToggle'
import { INSPECTOR_LABEL_W } from '../layout'
import { useElementWriter } from '../elementWrite'
import { fieldVisible } from '../presentation/registry'
import { propLabel } from '../roles/registry'
import { ResetChip, labeledWithState } from './textRows'

/**
 * 图例的「排版详情」（审计 T17）：示意线长度、线与文字间距、行距、列距、内边距。
 *
 * 这五条是 matplotlib 按**字号的倍数**（em）计的，引擎发 `unit: "em"`。小节
 * **默认展开**（2026-09-26 用户反馈：默认折叠时找不到列距 / 间距在哪改——
 * 「排版详情」这个小字链接不像入口）；仍可收起，收起只在这次选中里有效，
 * 不存偏好：换个图例再选中又是展开的。段里标签独占一列、**不截断**——「线与
 * 文字间距」在 72px 的标签列里只剩「线与文字间…」，正是审计点名的那一条。
 *
 * 用户改过任何一条时小节必定展开（override 不因折叠而不可发现，与「更多」
 * 同一条纪律）。列距只在多列时出现（`fieldVisible`，与通用列表同一份判据）。
 */
/**
 * 这五条 matplotlib 都按**字号的倍数**计（`handlelength` 等在官方文档里写的是
 * “in font-size units”），引擎不发 `unit`——单位是 matplotlib 的语义常识，
 * 不是它从 artist 上量出来的值。界面把它写成 `em`（排版学里就是「一个字号」）
 * 并在小节顶上说明一次；引擎哪天真发了 `unit`，那份优先。
 */
const SPACING_UNIT = 'em'

export const LEGEND_SPACING_PROPS = [
  'handlelength',
  'handletextpad',
  'labelspacing',
  'columnspacing',
  'borderpad',
] as const

const lg = (key: string, values?: Record<string, unknown>) =>
  translate(`legend.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function LegendSpacingCard({ panel, element }: { panel: PanelObject; element: ManifestElement }) {
  const w = useElementWriter(panel, element)
  const overridden = (prop: string) =>
    panel.overrides.some((o) => o.gid === element.gid && o.prop === prop)
  const props = LEGEND_SPACING_PROPS.filter((p) => w.has(p))
  const shown = props.filter((p) =>
    fieldVisible(element.role, p, { isOverridden: overridden, read: w.read }),
  )
  const modified = props.filter(overridden).length
  const [openPref, setOpenPref] = useState(true)
  const open = openPref || modified > 0

  if (!props.length) return null

  return (
    /* 组内折叠 = 文字链接，上方不画 hairline（打磨 L4：组间靠留白分层） */
    <div data-legend-spacing>
      <GroupToggle
        open={open}
        onToggle={() => setOpenPref(!open)}
        summary={
          modified > 0
            ? translate('element.modifiedCount', { ns: 'inspector', count: modified })
            : undefined
        }
      >
        {lg('layoutDetails')}
      </GroupToggle>
      <Reveal open={open}>
        <div className="mt-1.5 flex flex-col gap-1.5">
          {/* 「1 em = 一个图例字号」那句删了（打磨 L8）：单位 em 已经在框里，
              解释放 NumberField 的 title，不常驻 */}
          {shown.map((prop) => {
            const field = w.fieldOf(prop)
            if (!field) return null
            const label = propLabel(prop, element.role)
            return (
              <div key={prop} data-prop={prop} data-gid={element.gid}>
              <Row
                label={labeledWithState(label, overridden(prop))}
                labelWidth={INSPECTOR_LABEL_W}
              >
                {/* 与同页其它行同一条控件竖线、同一档框宽（打磨 E4 / L3）：
                    此前标签 flex-1、112 宽的框贴右缘，是页内第三种行语法 */}
                <NumberField
                  ariaLabel={label}
                  title={lg('spacingUnitTitle')}
                  value={Number(w.read(prop) ?? 0)}
                  min={field.min}
                  max={field.max}
                  step={field.step ?? 0.1}
                  precision={2}
                  unit={field.unit ?? SPACING_UNIT}
                  onChange={(v) => w.write(prop, v)}
                  onScrubStart={() => w.beginGesture()}
                  onScrubEnd={w.endGesture}
                />
                {overridden(prop) && (
                  <ResetChip label={label} onReset={() => clearOverride(panel.id, element.gid, prop)} />
                )}
              </Row>
              </div>
            )
          })}
        </div>
      </Reveal>
    </div>
  )
}
