import { useMemo } from 'react'
import { Bold, Italic } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { LineStylePicker } from '@/components/inspector/controls/LineStylePicker'
import { LegendPositionPicker } from '@/components/inspector/controls/LegendPositionPicker'
import { useElementWriter } from '@/components/inspector/elementWrite'
import { hasTextStyleBar } from '@/components/inspector/TextStyleBar'
import { fontStackOf } from '@/components/inspector/controls/fontStack'
import { FIGURE_TEXT_SINGLE_PROPS, useFigureTypography } from '@/components/inspector/typographyAdapter'
import { FALLBACK_MIN_FONT_SIZE_PT } from '@/lib/profile'
import { displayValueOf, nextToggle, toggleStateOf } from '@/lib/typography'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { optionLabel, propLabel } from '@/components/inspector/roles/registry'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Popover } from '@/components/ui/Popover'
import { Select } from '@/components/ui/Select'
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
    return <TextElementActions panel={panel} element={element} />
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

/**
 * 图内文字（matplotlib `Text`）的浮动快捷编辑。
 *
 * **与属性页、右键快捷编辑读同一个适配器、写同一条路**（`useFigureTypography`，
 * ADR 0032）。这里以前是第三份实现：绕过适配器直接 `setOverride`，只有字号 /
 * 加粗 / 颜色，加粗按 `weight === 'bold'` 两态读——mixed / inherit 无从表达，
 * 字号的显示回退 `?? 9` 也与别处的 `?? 8` 不一致。现在四档取值、斜体、字体
 * 都与属性页同一份；布局仍按上下文（这里没有标签列），共享的是数据与 action。
 */
function TextElementActions({ panel, element }: { panel: PanelObject; element: ManifestElement }) {
  const elements = useMemo(() => [element], [element])
  const a = useFigureTypography(panel, elements, FIGURE_TEXT_SINGLE_PROPS)
  const family = a.fieldOf('fontFamily')
  const size = a.fieldOf('sizePt')
  const boldState = toggleStateOf(a.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(a.valueOf('style'), 'italic')
  return (
    <>
      {family && (family.options?.length ?? 0) > 0 && (
        <Select
          className="w-[112px] shrink-0"
          ariaLabel={translate('textControls.font', { ns: 'inspector' })}
          value={String(displayValueOf(a.valueOf('fontFamily')) ?? '')}
          onChange={(v) => a.writeOnce('fontFamily', v)}
          options={(family.options ?? []).map((o) => ({
            value: o,
            label: <span style={{ fontFamily: fontStackOf(o) }}>{optionLabel('fontfamily', o)}</span>,
          }))}
        />
      )}
      {size && (
        <NumberField
          className="w-[64px] shrink-0"
          value={Number(displayValueOf(a.valueOf('sizePt')) ?? FALLBACK_MIN_FONT_SIZE_PT)}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          precision={1}
          suffix={size.unit}
          title={translate('textControls.size', { ns: 'inspector' })}
          onChange={(v) => a.write('sizePt', v)}
          onScrubStart={a.beginGesture}
          onScrubEnd={a.endGesture}
        />
      )}
      {a.fieldOf('weight') && (
        <StyleToggle
          state={boldState}
          label={translate('textBar.bold', { ns: 'inspector' })}
          onClick={() => a.writeOnce('weight', nextToggle(a.valueOf('weight'), 'bold', 'normal'))}
        >
          <Bold size={12} />
        </StyleToggle>
      )}
      {a.fieldOf('style') && (
        <StyleToggle
          state={italicState}
          label={translate('textBar.italic', { ns: 'inspector' })}
          onClick={() => a.writeOnce('style', nextToggle(a.valueOf('style'), 'italic', 'normal'))}
        >
          <Italic size={12} />
        </StyleToggle>
      )}
      {a.fieldOf('color') && (
        <ColorField
          className="w-[86px] shrink-0"
          value={String(displayValueOf(a.valueOf('color')) ?? '#000000')}
          onChange={(v) => a.write('color', v, true)}
          onGestureEnd={a.endGesture}
        />
      )}
      <Sep />
    </>
  )
}
