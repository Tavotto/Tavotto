import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Bold, Italic } from 'lucide-react'
import type { ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { clearOverride } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'
import {
  AlignmentRow,
  FontFamilyRow,
  FontSizeRow,
  TextColorRow,
} from './controls/textRows'
import { useElementWriter } from './elementWrite'
import { optionLabel } from './roles/registry'

/**
 * 图内文字的高频样式：**带可见标签的行**（字体 / 字号 / 颜色 / 对齐），
 * 不再是一排无标签控件 + 多层弹层（审计 P2 / 嵌套弹层）。
 *
 * 行距 / 旋转 / 垂直对齐 / 背景 / 描边 / 层级不再压进齿轮弹层——它们经
 * 展示注册表落进「更多」，与所有别的元素同一套折叠模型。
 *
 * 属性页与右键快捷编辑共用这一份；控件严格按 manifest 里真有的字段出。
 * 画布标注文字（TextSection）用同一组行组件——两种「文字」一个操作语言。
 */

/** 工具条覆盖掉的属性——平铺列表与分组要把它们让出来，避免出现两套控件 */
export const TEXT_BAR_PROPS = new Set([
  'fontfamily', 'fontsize', 'weight', 'style', 'color', 'ha',
])

/**
 * 该不该给这个元素画文字样式行。判据是「它是不是一个 matplotlib Text」：
 * 三条都有才算——图例只有 fontsize、刻度标签只有 text，都不该套进来。
 */
export const hasTextStyleBar = (el: ManifestElement) =>
  ['fontsize', 'color', 'weight'].every((p) => el.editable.some((f) => f.prop === p))

/** 本组文案在 inspector:textBar.* 下 */
const tb = (key: string, values?: Record<string, unknown>) =>
  translate(`textBar.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function TextStyleBar({
  panel,
  element,
  className,
  labelWidth = 72,
}: {
  panel: PanelObject
  element: ManifestElement
  className?: string
  /** 标签列宽：属性页 72（与 FieldRow 对齐），快捷编辑弹层可传 44 */
  labelWidth?: number
}) {
  useTranslation('inspector')
  const w = useElementWriter(panel, element)
  const family = w.fieldOf('fontfamily')
  const size = w.fieldOf('fontsize')
  const gid = element.gid

  const bold = w.read('weight') === 'bold'
  const italic = w.read('style') === 'italic'
  const overridden = (prop: string) =>
    panel.overrides.some((o) => o.gid === gid && o.prop === prop)
  const reset = (prop: string) => () => clearOverride(panel.id, gid, prop)

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {family && (
        <FontFamilyRow
          labelWidth={labelWidth}
          value={String(w.read('fontfamily') ?? '')}
          options={family.options ?? []}
          onChange={(v) => w.writeOnce('fontfamily', v)}
          optionLabelOf={(o) => optionLabel('fontfamily', o)}
          overridden={overridden('fontfamily')}
          onReset={reset('fontfamily')}
        />
      )}
      {size && (
        <FontSizeRow
          labelWidth={labelWidth}
          value={Number(w.read('fontsize') ?? 9)}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          suffix={size.unit}
          onChange={(v) => w.write('fontsize', v)}
          onScrubStart={w.beginGesture}
          onScrubEnd={w.endGesture}
          overridden={overridden('fontsize')}
          onReset={reset('fontsize')}
        >
          {w.has('weight') && (
            <IconToggle
              on={bold}
              label={tb('bold')}
              hint={tb('boldWeight', { value: tb(bold ? 'weightBold' : 'weightNormal') })}
              onClick={() => w.writeOnce('weight', bold ? 'normal' : 'bold')}
            >
              <Bold size={12} />
            </IconToggle>
          )}
          {w.has('style') && (
            <IconToggle
              on={italic}
              label={tb('italic')}
              hint={tb('italicStyle', { value: tb(italic ? 'styleItalic' : 'styleNormal') })}
              onClick={() => w.writeOnce('style', italic ? 'normal' : 'italic')}
            >
              <Italic size={12} />
            </IconToggle>
          )}
        </FontSizeRow>
      )}
      {w.has('color') && (
        <TextColorRow
          labelWidth={labelWidth}
          value={String(w.read('color') ?? '#000000')}
          onChange={(v) => w.write('color', v, true)}
          onGestureEnd={w.endGesture}
          overridden={overridden('color')}
          onReset={reset('color')}
        />
      )}
      {w.has('ha') && (
        <AlignmentRow
          labelWidth={labelWidth}
          value={(String(w.read('ha') ?? 'center') as 'left' | 'center' | 'right') ?? null}
          onChange={(v) => w.writeOnce('ha', v)}
          labels={{ left: tb('alignLeft'), center: tb('alignCenter'), right: tb('alignRight') }}
          overridden={overridden('ha')}
          onReset={reset('ha')}
        />
      )}
    </div>
  )
}

function IconToggle({
  on,
  label,
  hint,
  onClick,
  children,
}: {
  on: boolean
  /** 按钮说的是它干什么（加粗），不是属性叫什么（字重） */
  label: string
  /** 悬停时补一句当前值——图标按下与否在小尺寸下不总是一眼可辨 */
  hint?: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Tip label={hint ?? label}>
      <Button size="icon-sm" active={on} aria-pressed={on} aria-label={label} onClick={onClick}>
        {children}
      </Button>
    </Tip>
  )
}
