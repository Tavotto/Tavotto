import type { ReactNode } from 'react'
import { RotateCcw, TextAlignCenter, TextAlignEnd, TextAlignStart } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
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

/**
 * 字形三态图标按钮（加粗 / 斜体）。
 *
 * **单选与多选是同一个控件**：多选后退化成 `normal / bold` 文字下拉是本轮
 * 要修掉的分叉。三态用 `aria-pressed="mixed"`（ARIA 认这个值），并且
 * **不只靠颜色**——mixed 时图标下多一条短横，屏幕阅读器名字里也带
 * 「多个值」。
 *
 * 点击语义：mixed → 全开；全开 → 全关；全关 → 全开。没有「点回 mixed」，
 * mixed 是当前事实的描述，不是用户能选的目标状态。
 */
export function StyleToggle({
  state,
  label,
  hint,
  onClick,
  children,
}: {
  state: 'on' | 'off' | 'mixed'
  /** 按钮说的是它干什么（加粗），不是属性叫什么（字重） */
  label: string
  /** 悬停时补一句当前值——图标按下与否在小尺寸下不总是一眼可辨 */
  hint?: string
  onClick: () => void
  children: ReactNode
}) {
  const mixedText = translate('element.mixedValues', { ns: 'inspector' })
  const name = state === 'mixed' ? `${label} · ${mixedText}` : label
  return (
    <Tip label={hint ?? name}>
      <Button
        size="icon-sm"
        active={state === 'on'}
        aria-pressed={state === 'mixed' ? 'mixed' : state === 'on'}
        aria-label={name}
        onClick={onClick}
        // 宽度不随状态变：mixed 的提示画在按钮内部，不挤走后面的控件
        className="relative"
      >
        {children}
        {state === 'mixed' && (
          <span
            aria-hidden
            className={cn(
              'pointer-events-none absolute inset-x-1 bottom-[3px] h-[2px] rounded-full bg-ink-2',
            )}
          />
        )}
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
  mixed,
  unavailable = [],
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
  optionLabelOf: (v: string) => string
  /** 多选且字体不一致：显示「多个值」占位，绝不谎报其中某一个的字体 */
  mixed?: boolean
  /**
   * 选项里**这个运行时画不出来**的那几个（脚本写死了一个没装的字体）。
   * 名字仍然显示——把它换掉再改文档是最坏的处置；旁边给一条 warning，
   * 用户于是知道图上那行字实际是别的字体画的。
   */
  unavailable?: readonly string[]
}) {
  const label = tc('font')
  const missing = new Set(unavailable)
  return (
    <>
      <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
        <Select
          className="min-w-0 flex-1"
          ariaLabel={label}
          // Radix 的 value 必须是选项之一才会显示；mixed 传空串走 placeholder
          value={mixed ? '' : value}
          placeholder={mixed ? translate('element.mixedValues', { ns: 'inspector' }) : undefined}
          onChange={onChange}
          options={options.map((o) => ({
            value: o,
            // Aa 预览：选项文字用它自己的字体栈显示；写入值仍是原始选项串
            label: (
              <span style={{ fontFamily: fontStackOf(o) }}>
                {optionLabelOf(o)}
                {missing.has(o) && (
                  <span className="ml-1 font-sans text-ink-3">{tc('fontMissingTag')}</span>
                )}
              </span>
            ),
          }))}
        />
        {overridden && onReset && <ResetChip label={label} onReset={onReset} />}
      </Row>
      {!mixed && missing.has(value) && (
        <p className="pl-1 text-xs leading-relaxed text-warn">{tc('fontMissingHint')}</p>
      )}
    </>
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
        dataProp="fontsize"
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
  mixed,
}: {
  value: string
  onChange: (v: string) => void
  onGestureEnd?: () => void
  labelWidth?: number
  overridden?: boolean
  onReset?: () => void
  /** 多选且颜色不一致：色块旁明说「多个值」，不把其中一个当成公共色 */
  mixed?: boolean
}) {
  const label = tc('color')
  const mixedText = translate('element.mixedValues', { ns: 'inspector' })
  return (
    <Row label={labeledWithState(label, overridden)} labelWidth={labelWidth}>
      <ColorField value={value} onChange={onChange} onGestureEnd={onGestureEnd} />
      {mixed && <span className="shrink-0 text-xs text-ink-3">{mixedText}</span>}
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
