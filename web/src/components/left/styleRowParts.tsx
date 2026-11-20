import { createContext, useCallback, useRef, useState, type ReactNode } from 'react'
import { Bold, Italic } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import type { EditableField } from '@/lib/api'
import { nextToggle, toggleStateOf, type TypographyValue } from '@/lib/typography'
import { fontFamilyOptionLabel } from '../inspector/roles/registry'
import type { ControlValue } from '../inspector/textStyleModel'
import { StyleToggle } from '../inspector/controls/textRows'
import { NumberField } from '../ui/Input'
import { Select } from '../ui/Select'

/**
 * 样式面板两层（图层 / 画布层）共用的行零件：行骨架、两列网格、字体下拉、页面 pt 数字框、
 * 粗 / 斜体开关、「还没落定的写入」。从 `StylePanel.tsx` 原样搬出来（2026-11-20），画布层
 * （`CanvasStyleLayer`）与图层（`StylePanel`）各用一份，不抄第二套。
 */

/** 本面板的文案在 workspace:stylePanel.* 下 */
export const sp = (key: string, values?: Record<string, unknown>) =>
  translate(`stylePanel.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 行名写成字面量表：模板拼出来的键死键门禁看不见 */
export const ROW_LABEL: Record<string, () => string> = {
  title: () => sp('row.title'),
  axis_label: () => sp('row.axisLabel'),
  ticks: () => sp('row.ticks'),
  legend: () => sp('row.legend'),
  annotation: () => sp('row.annotation'),
  dataLine: () => sp('row.dataLine'),
  frame: () => sp('row.frame'),
  tickDirection: () => sp('row.tickDirection'),
  tickLength: () => sp('row.tickLength'),
  tickWidth: () => sp('row.tickWidth'),
}

export type PendingWrites = ReturnType<typeof usePendingWrites>
/** 画布级行的挂起值：挂在画布范围上，不跟着图级子树（`FigureStyle`）重挂 */
export const CanvasPending = createContext<PendingWrites | null>(null)
export function CanvasPendingScope({ children }: { children: ReactNode }) {
  const pw = usePendingWrites()
  return <CanvasPending.Provider value={pw}>{children}</CanvasPending.Provider>
}

/**
 * 一行的骨架：**两列固定网格**（2026-09-26 用户反馈：各行的控件左缘、宽度对不齐）。
 *
 * * 标签列 `4rem`：行名，放不下时截断、悬停看全名；
 * * 控件列弹性：一行或几行控件（`ControlLine`；文字行 = 字体一行 + 「字号 · 粗体 · 斜体」一行）。
 *   每一行里「值」那一格（字号 / 线宽 / 刻度方向）都是同一个宽度 `VALUE_W`、都从控件列的左缘
 *   起排，所以上下各行的数字框在同一条竖线上。
 *
 * 没有状态列：问题只在「问题」面板里看（见 `StylePanel` 的说明）。
 */
export function StyleRow({ id, children }: { id: string; children: ReactNode }) {
  const label = ROW_LABEL[id]()
  return (
    <div data-style-row={id} className={cn(STYLE_ROW_GRID, WIDE_COLS, NARROW_COLS, 'items-start py-0.5')}>
      <span className="h-7 truncate text-xs leading-7 text-ink-2" title={label}>
        {label}
      </span>
      <div className={cn('flex min-w-0 flex-col gap-1', WIDE_FLATTEN)}>{children}</div>
    </div>
  )
}

/**
 * 行网格与属性栏**同一副**（2026-10-07 设计审计 §9.2 / §10.3：此前标签列是自己的 4rem）：标签列
 * `--insp-label`（属性栏那一期在 `index.css` 定义；还没定义时退回审计给的同一个 clamp），列间 8。
 * 本地常量，等属性栏的变量落地后两边读的是同一个数。
 */
export const STYLE_ROW_GRID = 'grid grid-cols-[var(--insp-label,clamp(88px,28%,112px))_minmax(0,1fr)] gap-x-2'

/**
 * 样式面板按**侧栏自己的宽度**分三档（容器查询，不跟窗口；2026-10-31 UI 审计 C3）：
 *
 * * 宽（≥ 350px）：一行 `标签 | 字体 | 字号 | B | I`。线条行的数值框落在字号那一列，没有 B / I 的行那两格留空，各行等高；
 * * 中：标签一列，字体一行、字号 + B I 在下一行，各行的字号框从控件列左缘起排，在同一条竖线上；
 * * 窄（< 262px）：标签放到上面，控件占满一行宽。
 *
 * 宽档的阈值按真实列宽算：标签 4rem + 字体至少 ≈100 + 字号 5.5rem + B / I 各 1.75rem + 列间 4。
 */
export const STYLE_CONTAINER = '@container'
/* Tailwind 只认完整类名：阈值（宽 350 / 窄 262）写死在下面每个字面量里，不拼接 */
/** 宽档里「控件列 / 控件行」不再是盒子，控件直接落在行网格的列上 */
export const WIDE_FLATTEN = '@min-[350px]:contents'
/** 宽档的列：标签 | 字体 | 字号 | 粗 | 斜 */
export const WIDE_COLS = '@min-[350px]:grid-cols-[4rem_minmax(0,1fr)_5.5rem_1.75rem_1.75rem] @min-[350px]:gap-x-1'
/** 窄档：标签在上，控件占满一行 */
export const NARROW_COLS = '@max-[262px]:grid-cols-1'
/** 宽档里字体格所在的列（数值格的列写在 `VALUE_W` 里） */
export const WIDE_FAMILY_COL = '@min-[350px]:col-start-2'

/** 控件列里的一行：高 28，控件从左缘起排 */
export function ControlLine({ line, children }: { line: string; children: ReactNode }) {
  return (
    <div data-style-line={line} className={cn('flex h-7 min-w-0 items-center gap-1', WIDE_FLATTEN)}>
      {children}
    </div>
  )
}

/**
 * 「值」那一格的宽度：字号 / 线宽的数字框与刻度方向的下拉**同一个宽**。放得下「多个值 pt」
 * 与英文的「Outward」；280 px 的最窄侧栏里「值 + 粗体 + 斜体」仍排得下一行。
 */
export const VALUE_W = 'w-[5.5rem] shrink-0 @min-[350px]:col-start-3'

/**
 * 一格的读数：图内那侧是 `ControlValue`（uniform / mixed / unavailable），画布标注那侧是
 * 属性能力层的 `TypographyValue`（多一档 inherit——没设过、显示继承来的值）。两边都不压扁。
 */
export type CellValue = ControlValue | TypographyValue


/** 字体下拉：mixed 时空值走占位（「多个值」），不谎报其中某一个 */
export function FamilySelect({
  label,
  value,
  options,
  labels,
  onChange,
  locked,
}: {
  label: string
  value: CellValue
  options: readonly string[]
  /** 字体的中文显示名（manifest 顶层 `font_family_names`），只影响显示 */
  labels?: Readonly<Record<string, string>>
  onChange: (v: string) => void
  /** 此刻不许写（见 `FigureRowProps.locked`）：置灰并说原因 */
  locked?: boolean
}) {
  const current =
    value.kind === 'uniform' || value.kind === 'inherit' ? String(value.value ?? '') : ''
  // 当前值不在选项里（脚本写了一个引擎首选表之外的族）时照样显示它：把它换掉再
  // 改文档是最坏的处置（typography 能力层「装不上的字体保留名字」同一条）
  const opts = current && !options.includes(current) ? [current, ...options] : [...options]
  return (
    <Select
      className="min-w-0 flex-1"
      ariaLabel={label}
      value={value.kind === 'mixed' ? '' : current}
      placeholder={value.kind === 'mixed' ? translate('element.mixedValues', { ns: 'inspector' }) : undefined}
      onChange={onChange}
      disabled={locked}
      title={locked ? sp('waitingRender') : undefined}
      options={opts.map((o) => ({ value: o, label: fontFamilyOptionLabel(o, labels, opts) }))}
    />
  )
}

/**
 * 页面 pt 的数字框：mixed 留空 + 占位「多个值」；两位小数（0.75 不显示成 0.8）。
 *
 * 宽度是 `VALUE_W`、输入框铺满（`fill`）：此前框按 4 个等宽字符定宽，占位「多个值」被裁成
 * 「多个僮」。mixed 时悬停说明这是什么意思、输入一个数会怎样。
 */
export function PtField({
  label,
  value,
  field,
  step,
  onChange,
  locked,
}: {
  label: string
  value: CellValue
  /** 值域——写入器给的，已经换到页面上（`pagePtLens.field`） */
  field?: Pick<EditableField, 'min' | 'max'>
  step: number
  onChange: (v: number) => void
  locked?: boolean
}) {
  const shown = value.kind === 'uniform' || value.kind === 'inherit' ? value.value : undefined
  const mixed = value.kind === 'mixed'
  return (
    <NumberField
      ariaLabel={label}
      value={mixed ? NaN : Number(shown ?? NaN)}
      mixed={mixed}
      min={field?.min}
      max={field?.max}
      step={step}
      precision={2}
      disabled={locked}
      title={locked ? sp('waitingRender') : mixed ? sp('mixedHint') : undefined}
      unit="pt"
      fill
      className={VALUE_W}
      onChange={onChange}
    />
  )
}

/**
 * 粗体 / 斜体两颗开关（与属性页 `TypographyControls` 同一个 `StyleToggle`、同一套三态：
 * mixed 画成按钮底部一道短线，不拿其中一个冒充全部）。某一条不支持（`null`）就不摆那一颗。
 */
export function FaceToggles({
  row,
  weight,
  style,
  onWeight,
  onStyle,
  locked,
}: {
  row: string
  weight: TypographyValue | null
  style: TypographyValue | null
  onWeight: (next: string) => void
  onStyle: (next: string) => void
  locked?: boolean
}) {
  const mixedText = translate('element.mixedValues', { ns: 'inspector' })
  const tb = (key: string, values?: Record<string, unknown>) =>
    translate(`textBar.${key}`, { ns: 'inspector', ...(values ?? {}) })
  const hint = (state: 'on' | 'off' | 'mixed', key: string, on: string, off: string) =>
    locked ? sp('waitingRender') : tb(key, { value: state === 'mixed' ? mixedText : tb(state === 'on' ? on : off) })
  const boldState = weight ? toggleStateOf(weight, 'bold') : 'off'
  const italicState = style ? toggleStateOf(style, 'italic') : 'off'
  return (
    <>
      {weight && (
        <span className="contents" data-style-face={`${row}.weight`}>
          <StyleToggle
            state={boldState}
            label={sp('boldOf', { row: ROW_LABEL[row]() })}
            hint={hint(boldState, 'boldWeight', 'weightBold', 'weightNormal')}
            disabled={locked}
            onClick={() => onWeight(nextToggle(weight, 'bold', 'normal'))}
          >
            <Bold size={ICON_SIZE.sm} />
          </StyleToggle>
        </span>
      )}
      {style && (
        <span className="contents" data-style-face={`${row}.style`}>
          <StyleToggle
            state={italicState}
            label={sp('italicOf', { row: ROW_LABEL[row]() })}
            hint={hint(italicState, 'italicStyle', 'styleItalic', 'styleNormal')}
            disabled={locked}
            onClick={() => onStyle(nextToggle(style, 'italic', 'normal'))}
          >
            <Italic size={ICON_SIZE.sm} />
          </StyleToggle>
        </span>
      )}
    </>
  )
}

/** 图内写入器的读数 → 开关用的三态读数；`unavailable`（有元素不暴露这条）= 不摆开关 */
export const faceValue = (v: ControlValue): TypographyValue | null => (v.kind === 'unavailable' ? null : v)

/**
 * 绑定样式时的写入是**排队存库**的（`editBoundStyle` 返回 Promise，存完才改画布），这段时间里
 * 控件读到的还是改之前的值。「在当前值上做」的动作（粗体 / 斜体开关、数字框的 ↑↓ 步进）要是
 * 按那个旧值算，连点两下会算出同一个值：正体上连点两下加粗，排进去的是两次 `bold`，停在粗体上
 * （Codex #662 P2）。所以每一格记住**最后一次排进去、还没落定的值**，显示与下一次计算都按它；
 * 那一笔落定（存成功改了画布，或失败什么都没改）就放掉，回到控件真实读到的值。没绑样式时写入
 * 是同步的一次 commit，不经这里。
 */
export function usePendingWrites() {
  const [pending, setPending] = useState<Record<string, { value: unknown; token: number }>>({})
  const seq = useRef(0)
  /** 这一格此刻该显示的读数：有没落定的写入就是它 */
  const shown = useCallback(
    <V extends CellValue>(key: string, v: V): V | { kind: 'uniform'; value: unknown } =>
      key in pending ? { kind: 'uniform', value: pending[key].value } : v,
    [pending],
  )
  /** 写一格；`run` 返回 Promise（排队存库）时记下它，落定后放掉 */
  const write = useCallback((key: string, value: unknown, run: () => unknown) => {
    const done = run()
    if (!(done instanceof Promise)) return
    const token = ++seq.current
    setPending((p) => ({ ...p, [key]: { value, token } }))
    void done.finally(() =>
      setPending((p) => {
        if (p[key]?.token !== token) return p
        const { [key]: _settled, ...rest } = p
        return rest
      }),
    )
  }, [])
  return { shown, write }
}
