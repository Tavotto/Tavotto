import { useState } from 'react'
import { Row } from '../../ui/Field'
import { SummaryRow } from '../../ui/SummaryRow'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { clearOverride } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { NumberField } from '../../ui/Input'
import { INSPECTOR_LABEL_W } from '../layout'
import { useElementWriter } from '../elementWrite'
import { fieldVisible } from '../presentation/registry'
import { propLabel } from '../roles/registry'
import { ResetChip, labeledWithState } from './textRows'

/**
 * 图例的「排版详情」（审计 T17）：示意线长度、线与文字间距、行距、列距、内边距。
 *
 * 这五条是 matplotlib 按**字号的倍数**（em）计的，引擎发 `unit: "em"`。
 * 收成一行摘要行「间距」（2026-10-01，设计稿 A4）：右边只写当前值（默认 / 已调整），
 * 点开是原来的几行。**默认收起**——2026-09-26 那次「默认展开」是因为旧的小字链接
 * 不像入口，摘要行是整行可点的入口，这个理由不再成立；收起只在这次选中里有效，不存
 * 偏好：调用方按图例 gid 取 key，组件不跨图例复用。段里标签独占一列、**不截断**——
 * 「线与文字间距」在 72px 的标签列里只剩「线与文字间…」，正是审计点名的那一条。
 *
 * 用户改过任何一条时，收起的右值说「已调整」（override 不因折叠而不可发现，与「更多」
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
  const [openPref, setOpenPref] = useState(false)
  const open = openPref

  if (!props.length) return null

  return (
    /* 摘要行「间距」（设计稿 A4）：右边只写当前值——默认 / 已调整 */
    <div data-legend-spacing className="mt-3">
      <SummaryRow
        className="mx-0"
        data-fold="spacing"
        label={lg('layoutDetails')}
        value={modified > 0 ? lg('spacingAdjusted') : lg('spacingDefault')}
        open={open}
        onToggle={() => setOpenPref(!open)}
      >
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
                status={overridden(prop) ? <ResetChip label={label} onReset={() => clearOverride(panel.id, element.gid, prop)} /> : undefined}
              >
                {/* 与同页其它行同一条控件竖线、同一档框宽（打磨 E4 / L3）：
                    此前标签 flex-1、112 宽的框贴右缘，是页内第三种行语法 */}
                <NumberField
                  half
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
              </Row>
              </div>
            )
          })}
        </div>
      </SummaryRow>
    </div>
  )
}
