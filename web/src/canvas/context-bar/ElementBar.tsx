import { useMemo } from 'react'
import { Bold, Italic } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { LineStylePicker } from '@/components/inspector/controls/LineStylePicker'
import { PickerTrigger } from '@/components/inspector/controls/PickerTrigger'
import { LegendPositionPicker } from '@/components/inspector/controls/LegendPositionPicker'
import { useElementWriter } from '@/components/inspector/elementWrite'
import { LEGEND_ANCHOR_PROP, legendAnchorRange, outsidePresetOf, toLegendAnchor } from '@/lib/legendModel'
import { setLegendPlacement } from '@/store/actions'
import { hasTextStyleBar } from '@/components/inspector/TextStyleBar'
import { fontStackOf } from '@/components/inspector/controls/fontStack'
import { FIGURE_TEXT_SINGLE_PROPS, useFigureTypography } from '@/components/inspector/typographyAdapter'
import { FALLBACK_MIN_FONT_SIZE_PT } from '@/lib/profile'
import { displayValueOf, nextToggle, toggleStateOf } from '@/lib/typography'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { optionLabel, propLabel } from '@/components/inspector/roles/registry'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Popover } from '@/components/ui/Popover'
import { Select } from '@/components/ui/Select'
import { usePanelDisplayManifest } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import { Sep } from './shared'

/* ------------------------------- 图内元素 --------------------------------- */

export function ElementQuickActions({
  panel,
  gid,
  compact = false,
}: {
  panel: PanelObject
  gid: string
  /** 停靠的属性页正开着：文字元素只留字号 / 加粗 / 斜体（见 ContextBar） */
  compact?: boolean
}) {
  const manifest = usePanelDisplayManifest(panel)
  const el = manifest?.elements.find((e) => e.gid === gid)
  if (!el || !el.editable.length) return null
  return <ElementQuickInner panel={panel} element={el} compact={compact} />
}

function ElementQuickInner({
  panel,
  element,
  compact,
}: {
  panel: PanelObject
  element: ManifestElement
  compact: boolean
}) {
  const w = useElementWriter(panel, element)
  const role = element.role

  if (hasTextStyleBar(element)) {
    return <TextElementActions panel={panel} element={element} compact={compact} />
  }

  if (role === 'line' || role === 'linecoll') {
    const ls = w.fieldOf('linestyle')
    return (
      <span className="flex items-center gap-1">
        {w.has('color') && (
          <ColorField
            ariaLabel={propLabel('color', role)}
            value={String(w.read('color') ?? '#000000')}
            onChange={(v) => w.write('color', v, true)}
            onGestureEnd={w.endGesture}
          />
        )}
        {w.has('linewidth') && (
          <NumberField
            fill
            className="w-[76px] shrink-0"
            value={Number(w.read('linewidth') ?? 1)}
            min={0.1}
            max={12}
            step={0.1}
            precision={2}
            unit="pt"
            title={propLabel('linewidth', role)}
            onChange={(v) => w.write('linewidth', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        {ls && (
          /* 带样张的取值 = `Popover + PickerTrigger + OptionGrid`（宪法第五节），触发器与
             Select 同一副框。`LineStylePicker` 本身就是这一整套——此前它被再包一层 Popover +
             34×28 的 ghost 文字钮，点「实线」开出的弹层里还蹲着一个没展开的选择器
             （2026-09-15 打磨 F4） */
          <span className="w-32 shrink-0">
            <LineStylePicker
              value={String(w.read('linestyle') ?? '-')}
              options={ls.options ?? []}
              onChange={(v) => w.writeOnce('linestyle', v)}
              ariaLabel={propLabel('linestyle', role)}
            />
          </span>
        )}
        <Sep />
      </span>
    )
  }

  if (role === 'legend') {
    const loc = w.fieldOf('loc')
    const size = w.fieldOf('fontsize')
    const locValue = String(w.read('loc') ?? 'best')
    // 浮动栏上的位置名与选择器同一套词汇（2026-09-14 审计 A8，用户拍板）：放到子图外时
    // 说「右侧上」这种空间名，不说 `loc` 的角（「左上」是图例框自己的锚角，用户看到的却是
    // 图例在子图右上方）；`loc` 原值只在「排版详情」里出现
    const outside = w.has(LEGEND_ANCHOR_PROP)
      ? outsidePresetOf({ loc: locValue, anchor: toLegendAnchor(w.read(LEGEND_ANCHOR_PROP)) })
      : null
    const placeName = outside
      ? translate(`control.legendOutside.${outside}`, { ns: 'inspector' })
      : optionLabel('loc', locValue)
    return (
      <span className="flex items-center gap-1">
        {loc && (
          <Popover
            width={196}
            align="start"
            trigger={
              /* 与线型同一副框（F4）：内 / 外两带有五行高，进不了 36px 的浮条，
                 所以这里仍留一层 Popover，只把触发器换成 PickerTrigger。宽度写在它自己身上
                 ——外面套一层 span 的话 Radix 的 asChild 会把 data-state / aria-expanded
                 落到那层 span 上，钮就没有打开态了 */
              <PickerTrigger
                ariaLabel={propLabel('loc', role)}
                mixed={false}
                className="w-32 shrink-0"
              >
                {placeName}
              </PickerTrigger>
            }
          >
            {/* 内 / 外两带：浮动栏与属性页是同一个控件，少给一半就等于
                「选中图例时只能放在图内」 */}
            <LegendPositionPicker
              value={String(w.read('loc') ?? 'best')}
              options={loc.options ?? []}
              onChange={(v) => w.writeOnce('loc', v)}
              ariaLabel={propLabel('loc', role)}
              anchor={toLegendAnchor(w.read(LEGEND_ANCHOR_PROP))}
              anchorSupported={w.has(LEGEND_ANCHOR_PROP)}
              anchorRange={legendAnchorRange(element)}
              onPlace={(next) => setLegendPlacement(panel.id, [element], next)}
            />
          </Popover>
        )}
        {size && (
          <NumberField
            fill
            className="w-[68px] shrink-0"
            value={Number(w.read('fontsize') ?? 8)}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            precision={1}
            unit={size.unit}
            title={propLabel('fontsize', role)}
            onChange={(v) => w.write('fontsize', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        <Sep />
      </span>
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
function TextElementActions({
  panel,
  element,
  compact,
}: {
  panel: PanelObject
  element: ManifestElement
  /**
   * 属性页开着时的缩减档：只留字号 / 加粗 / 斜体。**不是第二份实现**——
   * 同一个适配器、同一批控件，只是少画两个（字体下拉、取色器），它们正在
   * 右栏里、带着标签、比这里更好用。
   */
  compact: boolean
}) {
  const elements = useMemo(() => [element], [element])
  const a = useFigureTypography(panel, elements, FIGURE_TEXT_SINGLE_PROPS)
  const family = a.fieldOf('fontFamily')
  const size = a.fieldOf('sizePt')
  const boldState = toggleStateOf(a.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(a.valueOf('style'), 'italic')
  return (
    <span className="flex items-center gap-1" data-text-quick={compact ? 'compact' : 'full'}>
      {!compact && family && (family.options?.length ?? 0) > 0 && (
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
          fill
          className="w-[68px] shrink-0"
          value={Number(displayValueOf(a.valueOf('sizePt')) ?? FALLBACK_MIN_FONT_SIZE_PT)}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          precision={1}
          unit={size.unit}
          title={translate('textControls.size', { ns: 'inspector' })}
          onChange={(v) => a.write('sizePt', v)}
          onScrubStart={a.beginGesture}
          onScrubEnd={a.endGesture}
        />
      )}
      {(a.fieldOf('weight') || a.fieldOf('style')) && (
        <span className="flex items-center gap-0.5">
          {a.fieldOf('weight') && (
            <StyleToggle
              state={boldState}
              label={translate('textBar.bold', { ns: 'inspector' })}
              onClick={() => a.writeOnce('weight', nextToggle(a.valueOf('weight'), 'bold', 'normal'))}
            >
              <Bold size={ICON_SIZE.sm} />
            </StyleToggle>
          )}
          {a.fieldOf('style') && (
            <StyleToggle
              state={italicState}
              label={translate('textBar.italic', { ns: 'inspector' })}
              onClick={() => a.writeOnce('style', nextToggle(a.valueOf('style'), 'italic', 'normal'))}
            >
              <Italic size={ICON_SIZE.sm} />
            </StyleToggle>
          )}
        </span>
      )}
      {!compact && a.fieldOf('color') && (
        <ColorField
          ariaLabel={translate('textBar.color', { ns: 'inspector' })}
          value={String(displayValueOf(a.valueOf('color')) ?? '#000000')}
          onChange={(v) => a.write('color', v, true)}
          onGestureEnd={a.endGesture}
        />
      )}
      <Sep />
    </span>
  )
}
