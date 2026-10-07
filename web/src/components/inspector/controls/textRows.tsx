import { memo, useCallback, useLayoutEffect, useRef, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { PT_DECIMALS } from '@/lib/stylePresets'
import { RotateCcw, TextAlignCenter, TextAlignEnd, TextAlignStart } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { Button, IconButton } from '../../ui/Button'
import { Row, type RowLabelWidth } from '../../ui/Field'
import { Menu, MenuItem } from '../../ui/Menu'
import { ColorField, NumberField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'
import { Select } from '../../ui/Select'
import { Tip } from '../../ui/Tooltip'
import { fontStackOf } from './fontStack'
import { INSPECTOR_CONTROL_X } from '../layout'

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

/**
 * 修改状态：`true` / `'all'` = 这个值来自你的修改；`'some'` = 多选里只有一部分改过（部分修改）；
 * `false` / `'none'` / undefined = 脚本原样。
 */
export type ModifiedState = boolean | 'all' | 'some' | 'none' | undefined

const modState = (m: ModifiedState): 'all' | 'some' | null =>
  m === true || m === 'all' ? 'all' : m === 'some' ? 'some' : null

/**
 * 修改点（2026-10-07 设计审计 §9.2 P0-3）：**悬挂在标签左边 8px**（`absolute -left-2`），标签文字不右移；
 * 全部修改 = 实心点，部分修改 = 空心环（多选里只改了几个）——形状而非颜色区分。
 * 修改点在标签的定位盒里，所以调用方的标签外壳要 `relative`（`Row grid` 的标签格已是）。
 */
export function ModifiedDot({ state }: { state: ModifiedState }) {
  const m = modState(state)
  if (!m) return null
  return (
    <span
      aria-hidden
      data-modified-dot={m}
      className={cn(
        'pointer-events-none absolute -left-2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full',
        m === 'all' ? 'h-1 w-1 bg-ink' : 'h-1.5 w-1.5 border border-ink bg-transparent',
      )}
    />
  )
}

/** 已修改状态：悬挂的点 + 状态槽里的恢复按钮（与 FieldRow 同一套表达） */
export function labeledWithState(label: string, overridden?: ModifiedState): ReactElement {
  const m = modState(overridden)
  const said =
    m === 'all'
      ? translate('element.modified', { ns: 'inspector' })
      : m === 'some'
        ? translate('element.modifiedPartial', { ns: 'inspector' })
        : null
  return (
    <span className="relative flex min-w-0 items-center" title={said ? `${label} · ${said}` : label}>
      <ModifiedDot state={overridden} />
      {/* 折两行而不是截成省略号（与 FieldRow 同一条纪律）：「Major tick mode」在 88px 里
          放得下，放不下的也让用户看见整个词 */}
      <span className="line-clamp-2 min-w-0 leading-tight break-words">{label}</span>
      {said && <span className="sr-only">{said}</span>}
    </span>
  )
}

/**
 * 恢复到脚本：20px 的行内小钮，**住在行网格的状态槽里**（`Row status=`），
 * 出现与消失都不挪动控件（2026-10-07 设计审计 §9.2 P0-3）。
 */
export function ResetChip({ label, onReset, hint }: { label: string; onReset: () => void; hint?: string }) {
  const text = translate('element.resetProp', { ns: 'inspector', label })
  return (
    <Tip label={hint ?? translate('element.backToScript', { ns: 'inspector' })} side="left">
      <Button size="icon-xs" data-reset-prop className="shrink-0 text-ink-2" aria-label={text} onClick={onReset}>
        <RotateCcw size={ICON_SIZE.xs} />
      </Button>
    </Tip>
  )
}

/**
 * 一行两条字段、状态槽只有一格时的恢复钮（Codex #829 P2）：两条仍是**各自的** override，
 * 各自都要能单独回到脚本值——并成一行只是排版，不许把「恢复」也并成一刀。
 *
 * * 只有一条改过：就是那一条的 `ResetChip`，只清它（名字说的也是它）。
 * * 两条都改过：同一格里的钮打开一张小菜单——「恢复 A」「恢复 B」各清各的，
 *   「两项都恢复」一次清两条（一条历史，与 RestoreMenu 同一副菜单）。
 *
 * `fields` 只递**改过的**那几条；`onReset(props, label)` 由调用方写进一次 `clearOverrides`。
 */
export function ResetPairChip({
  label,
  fields,
  onReset,
}: {
  /** 行标题：菜单触发器与「两项都恢复」那条历史用它 */
  label: string
  fields: readonly { prop: string; label: string }[]
  onReset: (props: string[], label: string) => void
}) {
  if (fields.length === 0) return null
  if (fields.length === 1) {
    const [f] = fields
    return <ResetChip label={f.label} onReset={() => onReset([f.prop], f.label)} />
  }
  const resetText = (l: string) => translate('element.resetProp', { ns: 'inspector', label: l })
  return (
    <Menu
      align="end"
      width={176}
      trigger={
        <IconButton
          iconSize="xs"
          side="left"
          data-reset-prop
          data-reset-menu
          className="shrink-0 text-ink-2"
          label={resetText(label)}
        >
          <RotateCcw size={ICON_SIZE.xs} />
        </IconButton>
      }
    >
      {fields.map((f) => (
        <MenuItem key={f.prop} data-reset-field={f.prop} onSelect={() => onReset([f.prop], f.label)}>
          {resetText(f.label)}
        </MenuItem>
      ))}
      <MenuItem
        data-reset-field="*"
        onSelect={() =>
          onReset(
            fields.map((f) => f.prop),
            label,
          )
        }
      >
        {translate('element.resetPropBoth', { ns: 'inspector' })}
      </MenuItem>
    </Menu>
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
  disabled,
  mixedText,
  children,
}: {
  state: 'on' | 'off' | 'mixed'
  /**
   * 第三态叫什么（默认「多个值」）。设置 › 样式页编辑的是样式本身，第三态是「未设置」——
   * 同一副视觉（按钮下一道短横、`aria-pressed="mixed"`），读屏说的是它自己的意思
   */
  mixedText?: string
  /** 按钮说的是它干什么（加粗），不是属性叫什么（字重） */
  label: string
  /** 悬停时补一句当前值——图标按下与否在小尺寸下不总是一眼可辨 */
  hint?: string
  onClick: () => void
  /** 此刻不许写（左栏样式面板在这一版渲染回来之前）：置灰，气泡照样说原因 */
  disabled?: boolean
  children: ReactNode
}) {
  const third = mixedText ?? translate('element.mixedValues', { ns: 'inspector' })
  const name = state === 'mixed' ? `${label} · ${third}` : label
  return (
    <Tip label={hint ?? name}>
      <Button
        size="icon-sm"
        active={state === 'on'}
        aria-pressed={state === 'mixed' ? 'mixed' : state === 'on'}
        aria-label={name}
        disabled={disabled}
        onClick={onClick}
        className={cn(
          // 宽度不随状态变：mixed 的提示画在按钮内部，不挤走后面的控件
          'relative',
          // 按下时字形自己跟着变重：B 真的加粗。状态不只写在底色上——
          // 图标就是它所描述的那件事，低对比屏与色觉差异下也读得出来。
          // 文字子节点走 font-bold，lucide 图标走 stroke-width（CSS 覆盖
          // SVG 的表现属性），按钮尺寸由 icon-sm 固定，不会因此位移。
          state === 'on' && 'font-bold [&_svg]:[stroke-width:2.5]',
        )}
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

/**
 * 「这个运行时画不出来」的字体：选项后的小标记与当前值下面的 warning。属性页与设置 › 样式页
 * 共用这一副（`options_unavailable`；名字保留、不换掉）。
 */
export function FontMissingTag() {
  return <span className="ml-1 font-sans text-ink-3">{tc('fontMissingTag')}</span>
}

export function FontMissingHint({ className }: { className?: string } = {}) {
  return <p className={cn('pl-1 text-xs leading-relaxed text-warn-content', className)}>{tc('fontMissingHint')}</p>
}

function FontFamilyRowView({
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
  /** 只用来让语言切换时重画（选项标签随语言变）；memo 比较它，组件体不读 */
  lang?: string
  value: string
  options: string[]
  onChange: (v: string) => void
  labelWidth?: RowLabelWidth
  overridden?: ModifiedState
  onReset?: () => void
  /**
   * 选项的显示名。**在渲染里调用**，所以它必须是当次渲染的那一份（不经 ref 转发），
   * 而且只能依赖 memo 比较的数据（`options` / `optionLabels` / `lang`）——比较器不比它
   */
  optionLabelOf: (v: string) => string
  /**
   * 选项显示名表（`option_labels`，字体的中文名）。组件体不读它——显示走
   * `optionLabelOf`；它在这里只为让 memo 在显示名到达 / 变化时重画
   */
  optionLabels?: Readonly<Record<string, string>>
  /** 多选且字体不一致：显示「多个值」占位，绝不谎报其中某一个的字体 */
  mixed?: boolean
  /**
   * 选项里**这个运行时画不出来**的那几个（脚本写死了一个没装的字体）。
   * 名字仍然显示——把它换掉再改文档是最坏的处置；旁边给一条 warning，
   * 用户于是知道图上那行字实际是别的字体画的。
   */
  unavailable?: readonly string[]
}) {
  // 性能探针（ADR 0075）：它下面挂着本机字体并表的几百个选项，重画一次很贵
  perfCount('render.FontFamilyRow')
  const label = tc('font')
  const missing = new Set(unavailable)
  return (
    <>
      <Row
        label={labeledWithState(label, overridden)}
        labelWidth={labelWidth}
        status={overridden && overridden !== 'none' && onReset ? <ResetChip label={label} onReset={onReset} /> : undefined}
      >
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
                {missing.has(o) && <FontMissingTag />}
              </span>
            ),
          }))}
        />
      </Row>
      {!mixed && missing.has(value) && (
        <FontMissingHint className={labelWidth === 'grid' ? INSPECTOR_CONTROL_X : undefined} />
      )}
    </>
  )
}

type FontFamilyRowProps = Omit<Parameters<typeof FontFamilyRowView>[0], 'lang'>

const sameList = (a: readonly string[] = [], b: readonly string[] = []) =>
  a === b || (a.length === b.length && a.every((v, i) => v === b[i]))

const sameLabels = (
  a: Readonly<Record<string, string>> = {},
  b: Readonly<Record<string, string>> = {},
) => {
  if (a === b) return true
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k])
}

/**
 * 字体下拉的选项是本机字体并表（`withMachineFamilies`），常见四五百项；Radix Select
 * **收起时也把全部 item 渲进一个 DocumentFragment**（登记选项用），所以属性页每重渲染
 * 一次它就把几百项全部重建一遍。2026-09-23 的剖析里它占了松手 / 换图 / 按下那三次
 * React 提交的六成（性能探针 ADR 0075；M2 Pro 上松手那一帧 113ms 里 React 84ms）。
 *
 * 所以按**数据**比较：值 / 选项内容 / 显示名表 / 不可用项 / 修改状态 / 标签宽 / 语言都没变
 * 就不重画。选项数组与显示名表（每次渲染响应里的 `font_family_names` 都是新对象）常是
 * 新引用、内容相同，按内容比。
 */
const FontFamilyRowMemo = memo(
  FontFamilyRowView,
  (a, b) =>
    a.value === b.value &&
    a.mixed === b.mixed &&
    a.overridden === b.overridden &&
    a.labelWidth === b.labelWidth &&
    a.lang === b.lang &&
    sameLabels(a.optionLabels, b.optionLabels) &&
    !!a.onReset === !!b.onReset &&
    sameList(a.options, b.options) &&
    sameList(a.unavailable, b.unavailable),
)

export function FontFamilyRow(props: FontFamilyRowProps) {
  const { i18n } = useTranslation()
  // **事件回调始终指向最新一次渲染的那一份**：memo 跳过重画时，里面挂的还是上一次的
  // 回调。换选中另一个字体相同的元素，数据全等、组件不重画——这时要是用旧的 onChange，
  // 字体会写到**上一个**元素上。所以传进去的是稳定的转发器，转发给 ref 里最新的那个。
  // ref 在 layout effect 里才更新，所以**只许事件里读**（点击总在提交之后）；渲染里要
  // 调的 `optionLabelOf` 不走这里，原样传下去——走 ref 的话 memo 因显示名变了而重画时
  // 读到的是上一次渲染的它，画出来的仍是旧名字，之后内容相同的渲染又都被跳过
  const latest = useRef(props)
  useLayoutEffect(() => {
    latest.current = props
  })
  const onChange = useCallback((v: string) => latest.current.onChange(v), [])
  const onReset = useCallback(() => latest.current.onReset?.(), [])
  return (
    <FontFamilyRowMemo
      {...props}
      lang={i18n.language}
      onChange={onChange}
      onReset={props.onReset ? onReset : undefined}
    />
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
  labelWidth?: RowLabelWidth
  overridden?: ModifiedState
  onReset?: () => void
  /** 字形按钮（B / I / U / 上下标）跟在字号后面，坐在后半列 */
  children?: ReactNode
}) {
  const label = tc('size')
  return (
    <Row
      label={labeledWithState(label, overridden)}
      labelWidth={labelWidth}
      status={overridden && overridden !== 'none' && onReset ? <ResetChip label={label} onReset={onReset} /> : undefined}
    >
      {/* 字号 = 半列（行网格的 half 档），后半列给 B / I 这组字形钮
          （2026-10-07 设计审计 §9.2 拍板①：取消字号的行内标签，字号自己一行） */}
      <NumberField
        half={labelWidth === 'grid'}
        dataProp="fontsize"
        ariaLabel={label}
        value={value}
        mixed={mixed}
        min={min}
        max={max}
        step={step}
        precision={PT_DECIMALS}
        unit={suffix}
        onChange={onChange}
        onScrubStart={onScrubStart}
        onScrubEnd={onScrubEnd}
      />
      {children != null && (
        // 字形钮一组：12px 间距之内是同一组，图标钮 28 圆
        <span data-glyph-group className="flex min-w-0 items-center gap-0.5">
          {children}
        </span>
      )}
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
  labelWidth?: RowLabelWidth
  overridden?: ModifiedState
  onReset?: () => void
  /** 多选且颜色不一致：色块自己画成「多个值」（`ColorField mixed`），不把其中一个当成公共色 */
  mixed?: boolean
}) {
  const label = tc('color')
  return (
    <Row
      label={labeledWithState(label, overridden)}
      labelWidth={labelWidth}
      status={overridden && overridden !== 'none' && onReset ? <ResetChip label={label} onReset={onReset} /> : undefined}
    >
      <ColorField ariaLabel={label} mixed={mixed} value={value} onChange={onChange} onGestureEnd={onGestureEnd} />
    </Row>
  )
}

export const alignmentItems = (labels: { left: string; center: string; right: string }) => [
  { value: 'left' as const, icon: <TextAlignStart size={ICON_SIZE.sm} />, tip: labels.left },
  { value: 'center' as const, icon: <TextAlignCenter size={ICON_SIZE.sm} />, tip: labels.center },
  { value: 'right' as const, icon: <TextAlignEnd size={ICON_SIZE.sm} />, tip: labels.right },
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
  labelWidth?: RowLabelWidth
  overridden?: ModifiedState
  onReset?: () => void
}) {
  const label = tc('align')
  return (
    <Row
      label={labeledWithState(label, overridden)}
      labelWidth={labelWidth}
      status={overridden && overridden !== 'none' && onReset ? <ResetChip label={label} onReset={onReset} /> : undefined}
    >
      <Segmented
        className="w-full"
        ariaLabel={label}
        value={value}
        onChange={onChange}
        items={alignmentItems(labels)}
      />
    </Row>
  )
}
