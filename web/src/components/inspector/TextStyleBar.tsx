import type { ReactNode } from 'react'
import {
  Baseline,
  Bold,
  Italic,
  PaintBucket,
  PenLine,
  Settings2,
  TextAlignCenter,
  TextAlignEnd,
  TextAlignStart,
} from 'lucide-react'
import type { EditableField, ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { clearOverride } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { ColorField, NumberField } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { useElementWriter, type ElementWriter } from './elementWrite'
import { optionLabel, propLabel } from './roles/registry'

/**
 * 图内文字的样式工具条。
 *
 * 加粗 / 字形 / 颜色 / 背景 / 描边 / 排版收敛成图标——一行认得出、点得到，
 * 不用在十几条「标签 + 控件」里找。属性页与右键弹层**共用这一份**，两边
 * 不会各写一套然后慢慢飘。
 *
 * 控件严格按 manifest 里真有的字段出：这里不维护属性清单，引擎说有才画。
 */

/** 工具条覆盖掉的属性——属性页的平铺列表与分组要把它们让出来，避免出现两套控件 */
export const TEXT_BAR_PROPS = new Set([
  'fontfamily', 'fontsize', 'weight', 'style', 'color', 'alpha',
  'ha', 'va', 'rotation', 'linespacing', 'zorder',
  'bbox_visible', 'bbox_facecolor', 'bbox_alpha', 'bbox_edgecolor',
  'bbox_linewidth', 'bbox_pad', 'bbox_rounded',
  'stroke_enabled', 'stroke_color', 'stroke_width',
])

/**
 * 该不该给这个元素画工具条。判据是「它是不是一个 matplotlib Text」：
 * 三条都有才算——图例只有 fontsize、刻度标签只有 text，都不该套进来。
 */
export const hasTextStyleBar = (el: ManifestElement) =>
  ['fontsize', 'color', 'weight'].every((p) => el.editable.some((f) => f.prop === p))

const HA_ITEMS = [
  { value: 'left', icon: <TextAlignStart size={12} />, tip: '左对齐' },
  { value: 'center', icon: <TextAlignCenter size={12} />, tip: '居中' },
  { value: 'right', icon: <TextAlignEnd size={12} />, tip: '右对齐' },
]

/**
 * 弹层里的行标签改写。注册表给的是全局通名（bbox_visible = 「背景」），
 * 落在标题已经写着「背景」的弹层里就成了重复；这里只改称呼，不改属性。
 */
const ROW_LABEL: Record<string, string> = {
  bbox_visible: '显示',
  bbox_facecolor: '填充',
  bbox_alpha: '不透明度',
  bbox_edgecolor: '边框色',
  bbox_linewidth: '边框粗细',
  stroke_enabled: '启用',
  stroke_color: '颜色',
  stroke_width: '粗细',
  alpha: '不透明度',
  color: '颜色',
}

export function TextStyleBar({
  panel,
  element,
  className,
}: {
  panel: PanelObject
  element: ManifestElement
  className?: string
}) {
  const w = useElementWriter(panel, element)
  const family = w.fieldOf('fontfamily')
  const size = w.fieldOf('fontsize')

  const bold = w.read('weight') === 'bold'
  const italic = w.read('style') === 'italic'

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      {(family || size) && (
        <div className="flex items-center gap-1">
          {family && (
            <Select
              className="min-w-0 flex-1"
              ariaLabel={propLabel('fontfamily', element.role)}
              value={String(w.read('fontfamily') ?? '')}
              onChange={(v) => w.writeOnce('fontfamily', v)}
              options={(family.options ?? []).map((o) => ({
                value: o,
                label: optionLabel('fontfamily', o),
              }))}
            />
          )}
          {size && (
            <NumberField
              className="w-[74px] shrink-0"
              value={Number(w.read('fontsize') ?? 9)}
              min={size.min}
              max={size.max}
              step={size.step ?? 0.5}
              precision={1}
              suffix={size.unit}
              onChange={(v) => w.write('fontsize', v)}
              onScrubStart={w.beginGesture}
              onScrubEnd={w.endGesture}
            />
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1">
        {w.has('weight') && (
          <IconToggle
            on={bold}
            label="加粗"
            hint={`字重 · ${bold ? '加粗' : '常规'}`}
            onClick={() => w.writeOnce('weight', bold ? 'normal' : 'bold')}
          >
            <Bold size={12} />
          </IconToggle>
        )}
        {w.has('style') && (
          <IconToggle
            on={italic}
            label="斜体"
            hint={`字形 · ${italic ? '斜体' : '正体'}`}
            onClick={() => w.writeOnce('style', italic ? 'normal' : 'italic')}
          >
            <Italic size={12} />
          </IconToggle>
        )}

        <SwatchPopover
          panel={panel}
          element={element}
          w={w}
          label="文字颜色"
          icon={<Baseline size={12} />}
          swatch={String(w.read('color') ?? '#000000')}
          props={['color', 'alpha']}
        />
        <SwatchPopover
          panel={panel}
          element={element}
          w={w}
          label="背景"
          icon={<PaintBucket size={12} />}
          swatch={w.read('bbox_visible') === true ? String(w.read('bbox_facecolor') ?? '#ffffff') : null}
          props={[
            'bbox_visible', 'bbox_facecolor', 'bbox_alpha',
            'bbox_edgecolor', 'bbox_linewidth', 'bbox_pad', 'bbox_rounded',
          ]}
        />
        <SwatchPopover
          panel={panel}
          element={element}
          w={w}
          label="描边"
          icon={<PenLine size={12} />}
          swatch={w.read('stroke_enabled') === true ? String(w.read('stroke_color') ?? '#ffffff') : null}
          props={['stroke_enabled', 'stroke_color', 'stroke_width']}
        />
        {/* 对齐进弹层而不是摆在主行：matplotlib 的 ha/va 说的是「锚点落在
            文字的哪一侧」，跟段落对齐不是一回事，和旋转/行距/层级放一起才
            讲得通。腾出来的位置也让主行在 296px 的属性栏里排得下一行 */}
        <SwatchPopover
          panel={panel}
          element={element}
          w={w}
          label="排版与层级"
          icon={<Settings2 size={12} />}
          props={['ha', 'va', 'rotation', 'linespacing', 'zorder']}
        />
      </div>
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
  children: ReactNode
}) {
  return (
    <Tip label={hint ?? label}>
      <Button size="icon-sm" active={on} aria-pressed={on} aria-label={label} onClick={onClick}>
        {children}
      </Button>
    </Tip>
  )
}

/**
 * 一个图标按钮 + 一层弹出的细项。按钮下沿那条色带就是当前值——
 * 「颜色是什么」不用点开就知道。关掉的功能（没开背景 / 没开描边）色带留白、
 * 图标压暗：状态不只靠颜色区分。
 */
function SwatchPopover({
  panel,
  element,
  w,
  label,
  icon,
  swatch,
  props,
}: {
  panel: PanelObject
  element: ManifestElement
  w: ElementWriter
  label: string
  icon: ReactNode
  /** undefined = 这层跟颜色无关，不画色带；null = 有颜色但功能当前关着 */
  swatch?: string | null
  props: string[]
}) {
  const fields = props
    .map((p) => w.fieldOf(p))
    .filter((f): f is EditableField => !!f)
  if (!fields.length) return null

  return (
    <Popover
      align="start"
      width={214}
      // 弹层关掉 = 这一轮调完了：原生取色盘不保证发 blur，安静计时之外再兜一次
      onOpenChange={(open) => !open && w.endGesture()}
      trigger={
        <Button
          size="icon-sm"
          aria-label={label}
          title={label}
          // 关着的功能连图标一起压暗：色带空着已经说明问题，但只靠一条
          // 细线的有无区分状态，扫一眼是看不见的
          className={cn(swatch === null && 'text-ink-3')}
        >
          <span className="flex flex-col items-center gap-[2px] leading-none">
            {icon}
            {swatch !== undefined && (
              <span
                aria-hidden
                className={cn(
                  // 关着时留一条透明占位：有没有色带都占同样高度，切换时
                  // 图标不会上下跳。3px 的虚线在这个尺寸下只会糊成一排点，
                  // 「关着」交给图标压暗去说
                  'block h-[3px] w-3.5 rounded-[1px] border',
                  swatch ? 'border-border-strong' : 'border-transparent',
                )}
                style={swatch ? { background: swatch } : undefined}
              />
            )}
          </span>
        </Button>
      }
    >
      <div className="mb-1.5 text-xs text-ink-3">{label}</div>
      <div className="flex flex-col gap-1.5">
        {fields.map((f) => (
          <CompactRow key={f.prop} panel={panel} element={element} w={w} field={f} />
        ))}
      </div>
    </Popover>
  )
}

/** 弹层里的一行：标签窄一号，控件与属性页同一批（写入也走同一条 writer） */
function CompactRow({
  panel,
  element,
  w,
  field,
}: {
  panel: PanelObject
  element: ManifestElement
  w: ElementWriter
  field: EditableField
}) {
  const label = ROW_LABEL[field.prop] ?? propLabel(field.prop, element.role)
  const value = w.read(field.prop)
  const overridden = panel.overrides.some(
    (o) => o.gid === element.gid && o.prop === field.prop,
  )

  let control: ReactNode = null
  switch (field.type) {
    case 'bool':
      control = <Toggle checked={!!value} onChange={(v) => w.writeOnce(field.prop, v)} />
      break
    case 'color':
      control = (
        <ColorField
          className="min-w-0 flex-1"
          value={String(value ?? '#000000')}
          onChange={(v) => w.write(field.prop, v, true)}
          onGestureEnd={w.endGesture}
        />
      )
      break
    case 'number':
      control = (
        <NumberField
          className="min-w-0 flex-1"
          value={Number(value ?? 0)}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          precision={2}
          suffix={field.unit}
          onChange={(v) => w.write(field.prop, v)}
          onScrubStart={w.beginGesture}
          onScrubEnd={w.endGesture}
        />
      )
      break
    case 'enum':
      if (field.prop === 'ha') {
        control = (
          <Segmented
            tone="quiet"
            value={String(value ?? 'center')}
            onChange={(v) => w.writeOnce('ha', v)}
            items={HA_ITEMS}
          />
        )
        break
      }
      control = (
        <Select
          className="min-w-0 flex-1"
          ariaLabel={label}
          value={String(value ?? '')}
          onChange={(v) => w.writeOnce(field.prop, v)}
          options={(field.options ?? []).map((o) => ({
            value: o,
            label: optionLabel(field.prop, o),
          }))}
        />
      )
      break
    default:
      return null
  }

  return (
    <div>
      <label className="flex min-h-7 items-center gap-1.5">
        <span className="w-[62px] shrink-0 truncate text-xs text-ink-2" title={label}>
          {label}
        </span>
        {control}
      </label>
      {overridden && (
        <button
          onClick={() => clearOverride(panel.id, element.gid, field.prop)}
          className="pl-[68px] text-xs text-ink-3 hover:text-accent"
        >
          回到脚本值
        </button>
      )}
    </div>
  )
}
