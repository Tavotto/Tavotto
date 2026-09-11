import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { clearOverride } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { NumberField } from '../../ui/Input'
import { useElementWriter } from '../elementWrite'
import { fieldVisible } from '../presentation/registry'
import { propLabel } from '../roles/registry'
import { ResetChip, labeledWithState } from './textRows'

/**
 * 图例的「排版详情」（审计 T17）：示意线长度、线与文字间距、行距、列距、内边距。
 *
 * 这五条是 matplotlib 按**字号的倍数**（em）计的，引擎发 `unit: "em"`；它们
 * 不是选中图例后最先要改的东西，所以收进一个默认折叠的小节，位置 / 列数 /
 * 边框留在首屏。折叠段里标签独占一列、**不截断**——「线与文字间距」在 72px
 * 的标签列里只剩「线与文字间…」，正是审计点名的那一条。
 *
 * 用户改过任何一条时小节自动展开（override 不因折叠而不可发现，与「更多」
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
  const open = openPref || modified > 0

  if (!props.length) return null

  return (
    <div className="mt-1.5 border-t border-border pt-1.5" data-legend-spacing>
      <button
        type="button"
        onClick={() => setOpenPref(!open)}
        aria-expanded={open}
        className="flex h-6 w-full items-center gap-1 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
      >
        <ChevronRight size={ICON_SIZE.xs} aria-hidden className={cn('shrink-0 transition-transform', open && 'rotate-90')} />
        <span className="font-medium">{lg('layoutDetails')}</span>
        {!open && modified > 0 && (
          <span className="ml-auto shrink-0 text-xs text-ink-3">
            {translate('element.modifiedCount', { ns: 'inspector', count: modified })}
          </span>
        )}
      </button>
      {open && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          <p className="text-xs leading-snug text-ink-3">{lg('spacingUnitNote')}</p>
          {shown.map((prop) => {
            const field = w.fieldOf(prop)
            if (!field) return null
            const label = propLabel(prop, element.role)
            return (
              <div key={prop} data-prop={prop} data-gid={element.gid} className="flex min-h-6 items-center gap-2">
                <span className="min-w-0 flex-1 text-xs text-ink-2">
                  {labeledWithState(label, overridden(prop))}
                </span>
                <NumberField
                  className="w-[120px] shrink-0"
                  ariaLabel={label}
                  value={Number(w.read(prop) ?? 0)}
                  min={field.min}
                  max={field.max}
                  step={field.step ?? 0.1}
                  precision={2}
                  suffix={field.unit ?? SPACING_UNIT}
                  onChange={(v) => w.write(prop, v)}
                  onScrubStart={() => w.beginGesture()}
                  onScrubEnd={w.endGesture}
                />
                {overridden(prop) && (
                  <ResetChip label={label} onReset={() => clearOverride(panel.id, element.gid, prop)} />
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
