import { Bold } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { LineStylePicker } from '@/components/inspector/controls/LineStylePicker'
import { LegendPositionPicker } from '@/components/inspector/controls/LegendPositionPicker'
import { useElementWriter } from '@/components/inspector/elementWrite'
import { hasTextStyleBar } from '@/components/inspector/TextStyleBar'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { optionLabel, propLabel } from '@/components/inspector/roles/registry'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Popover } from '@/components/ui/Popover'
import { usePanelDisplayManifest } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import { Sep } from './shared'

/* ------------------------------- 图内元素 --------------------------------- */

export function ElementQuickActions({ panel, gid }: { panel: PanelObject; gid: string }) {
  const manifest = usePanelDisplayManifest(panel)
  const el = manifest?.elements.find((e) => e.gid === gid)
  if (!el || !el.editable.length) return null
  return <ElementQuickInner panel={panel} element={el} />
}

function ElementQuickInner({
  panel,
  element,
}: {
  panel: PanelObject
  element: ManifestElement
}) {
  const w = useElementWriter(panel, element)
  const role = element.role

  if (hasTextStyleBar(element)) {
    const size = w.fieldOf('fontsize')
    const bold = w.read('weight') === 'bold'
    return (
      <>
        {size && (
          <NumberField
            className="w-[64px] shrink-0"
            value={Number(w.read('fontsize') ?? 9)}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            precision={1}
            suffix={size.unit}
            title={translate('textControls.size', { ns: 'inspector' })}
            onChange={(v) => w.write('fontsize', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        {/* 与属性页、批量样式**同一个**三态图标按钮：这里曾经是一份自己写的
            <Button active={bold}>，长得一样但是第二份实现——同形的两份实现
            迟早分叉，本轮那些「多选就退化」的缺陷正是这么来的 */}
        {w.has('weight') && (
          <StyleToggle
            state={bold ? 'on' : 'off'}
            label={translate('textBar.bold', { ns: 'inspector' })}
            onClick={() => w.writeOnce('weight', bold ? 'normal' : 'bold')}
          >
            <Bold size={12} />
          </StyleToggle>
        )}
        {w.has('color') && (
          <ColorField
            className="w-[86px] shrink-0"
            value={String(w.read('color') ?? '#000000')}
            onChange={(v) => w.write('color', v, true)}
            onGestureEnd={w.endGesture}
          />
        )}
        <Sep />
      </>
    )
  }

  if (role === 'line' || role === 'linecoll') {
    const ls = w.fieldOf('linestyle')
    return (
      <>
        {w.has('color') && (
          <ColorField
            className="w-[86px] shrink-0"
            value={String(w.read('color') ?? '#000000')}
            onChange={(v) => w.write('color', v, true)}
            onGestureEnd={w.endGesture}
          />
        )}
        {w.has('linewidth') && (
          <NumberField
            className="w-[70px] shrink-0"
            value={Number(w.read('linewidth') ?? 1)}
            min={0.1}
            max={12}
            step={0.1}
            precision={2}
            suffix="pt"
            title={propLabel('linewidth', role)}
            onChange={(v) => w.write('linewidth', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        {ls && (
          <Popover
            width={190}
            align="start"
            trigger={
              <Button size="sm" className="px-1.5 font-mono" aria-label={propLabel('linestyle', role)}>
                {optionLabel('linestyle', String(w.read('linestyle') ?? '-'))}
              </Button>
            }
          >
            <LineStylePicker
              value={String(w.read('linestyle') ?? '-')}
              options={ls.options ?? []}
              onChange={(v) => w.writeOnce('linestyle', v)}
              ariaLabel={propLabel('linestyle', role)}
            />
          </Popover>
        )}
        <Sep />
      </>
    )
  }

  if (role === 'legend') {
    const loc = w.fieldOf('loc')
    const size = w.fieldOf('fontsize')
    return (
      <>
        {loc && (
          <Popover
            width={196}
            align="start"
            trigger={
              <Button size="sm" className="px-1.5" aria-label={propLabel('loc', role)}>
                {optionLabel('loc', String(w.read('loc') ?? 'best'))}
              </Button>
            }
          >
            <LegendPositionPicker
              value={String(w.read('loc') ?? 'best')}
              options={loc.options ?? []}
              onChange={(v) => w.writeOnce('loc', v)}
              ariaLabel={propLabel('loc', role)}
            />
          </Popover>
        )}
        {size && (
          <NumberField
            className="w-[64px] shrink-0"
            value={Number(w.read('fontsize') ?? 8)}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            precision={1}
            suffix={size.unit}
            title={propLabel('fontsize', role)}
            onChange={(v) => w.write('fontsize', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        <Sep />
      </>
    )
  }

  return null
}
