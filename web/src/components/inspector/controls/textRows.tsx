import type { ReactNode } from 'react'
import { RotateCcw, TextAlignCenter, TextAlignEnd, TextAlignStart } from 'lucide-react'
import { t as translate } from '@/i18n'
import { Button } from '../../ui/Button'
import { Row } from '../../ui/Field'
import { ColorField, NumberField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'
import { Select } from '../../ui/Select'
import { Tip } from '../../ui/Tooltip'
import { fontStackOf } from './fontStack'

/**
 * 文字属性的共享行组件：**「字体」「字号」是可见文字标签**，不再只有
 * aria-label（审计 P2）。图内文字（TextStyleBar / ElementWriter）与画布
 * 标注文字（TextSection / updateObjects）共用同一套视觉结构，写入各走
 * 各的 writer——界面语言一致，数据通道不混。
 *
 * 能力有无由调用方决定：画布文字没有字体族（文档字体统一走 --font-doc），
 * 就不渲染 FontFamilyRow——不摆假控件。
 */

const tc = (key: string) => translate(`textControls.${key}`, { ns: 'inspector' })

/** 已修改状态：标签前的点 + 行尾恢复按钮（与 FieldRow 同一套表达） */
export function labeledWithState(label: string, overridden?: boolean): ReactNode {
  return (
    <span
      className="flex min-w-0 items-center gap-1"
      title={overridden ? `${label} · ${translate('element.modified', { ns: 'inspector' })}` : label}
    >
      {overridden && <span aria-hidden className="h-1 w-1 shrink-0 rounded-full bg-accent" />}
      <span className="min-w-0 truncate">{label}</span>
      {overridden && (
        <span className="sr-only">{translate('element.modified', { ns: 'inspector' })}</span>
      )}
    </span>
  )
}

export function ResetChip({ label, onReset }: { label: string; onReset: () => void }) {
  const text = translate('element.resetProp', { ns: 'inspector', label })
  return (
    <Tip label={translate('element.backToScript', { ns: 'inspector' })} side="left">
      <Button size="icon-sm" className="shrink-0" aria-label={text} onClick={onReset}>
        <RotateCcw size={11} className="text-ink-3" />
      </Button>
    </Tip>
  )
}

export function FontFamilyRow({
  value,
  options,
  onChange,
  labelWidth,
  overridden,
  onReset,
  optionLabelOf,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
  optionLabelOf: (v: string) => string
}) {
  const label = tc('font')
  return (
    <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
      <Select
        className="min-w-0 flex-1"
        ariaLabel={label}
        value={value}
        onChange={onChange}
        options={options.map((o) => ({
          value: o,
          // Aa 预览：选项文字用它自己的字体栈显示；写入值仍是原始选项串
          label: <span style={{ fontFamily: fontStackOf(o) }}>{optionLabelOf(o)}</span>,
        }))}
      />
      {overridden && onReset && <ResetChip label={label} onReset={onReset} />}
    </Row>
  )
}

export function FontSizeRow({
  value,
  min,
  max,
  step = 0.5,
  suffix = 'pt',
  mixed,
  onChange,
  onScrubStart,
  onScrubEnd,
  labelWidth,
  overridden,
  onReset,
  children,
}: {
  value: number
  min?: number
  max?: number
  step?: number
  suffix?: string
  mixed?: boolean
  onChange: (v: number) => void
  onScrubStart?: () => void
  onScrubEnd?: () => void
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
  /** 字形按钮（B / I / U / 上下标）跟在字号后面 */
  children?: ReactNode
}) {
  const label = tc('size')
  return (
    <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
      <NumberField
        className="w-[74px] shrink-0"
        ariaLabel={label}
        value={value}
        mixed={mixed}
        min={min}
        max={max}
        step={step}
        precision={1}
        suffix={suffix}
        onChange={onChange}
        onScrubStart={onScrubStart}
        onScrubEnd={onScrubEnd}
      />
      {children}
      {overridden && onReset && <ResetChip label={label} onReset={onReset} />}
    </Row>
  )
}

export function TextColorRow({
  value,
  onChange,
  onGestureEnd,
  labelWidth,
  overridden,
  onReset,
}: {
  value: string
  onChange: (v: string) => void
  onGestureEnd?: () => void
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
}) {
  const label = tc('color')
  return (
    <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
      <ColorField value={value} onChange={onChange} onGestureEnd={onGestureEnd} />
      {overridden && onReset && <ResetChip label={label} onReset={onReset} />}
    </Row>
  )
}

export const alignmentItems = (labels: { left: string; center: string; right: string }) => [
  { value: 'left' as const, icon: <TextAlignStart size={12} />, tip: labels.left },
  { value: 'center' as const, icon: <TextAlignCenter size={12} />, tip: labels.center },
  { value: 'right' as const, icon: <TextAlignEnd size={12} />, tip: labels.right },
]

export function AlignmentRow({
  value,
  onChange,
  labels,
  labelWidth,
  overridden,
  onReset,
}: {
  value: 'left' | 'center' | 'right' | null
  onChange: (v: 'left' | 'center' | 'right') => void
  labels: { left: string; center: string; right: string }
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
}) {
  const label = tc('align')
  return (
    <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
      <Segmented
        tone="quiet"
        className="w-full"
        value={value}
        onChange={onChange}
        items={alignmentItems(labels)}
      />
      {overridden && onReset && <ResetChip label={label} onReset={onReset} />}
    </Row>
  )
}
