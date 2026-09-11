import {
  forwardRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowDownToLine,
  ArrowUpToLine,
  Clipboard,
  ClipboardPaste,
  Group,
  MoveDown,
  MoveHorizontal,
  MoveUp,
  MoveVertical,
  Ungroup,
} from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Select } from '@/components/ui/Select'
import { t as translate } from '@/i18n'
import { MOD } from '@/lib/utils'
import {
  alignModeLabel,
  alignRefLabel,
  alignSelectedTo,
  changeZOrder,
  copySelectionStyle,
  createLayoutGroup,
  dissolveLayoutGroup,
  groupSelected,
  pasteSelectionStyle,
  reflowLayoutGroup,
  selectionHasGroup,
  setSelectionSpacing,
  styleClipKind,
  toggleLayoutPinned,
  ungroupSelected,
  updateLayoutGroup,
  type AlignRef,
  type ZMove,
} from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import type { CanvasObject, LayoutGroup } from '@/types/document'
import { Disclosure, Row, Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import {
  ALIGN_BUTTONS,
  ALIGN_REFS,
  DISTRIBUTE_BUTTONS,
  SIZE_BUTTONS,
  type ArrangeButton,
} from './arrangeButtons'
import { useSelectedObjects } from './common'

/** 本组的文案在 inspector:arrange.* 下；对齐动作名复用 inspector:alignMode.* */
const ar = (key: string, values?: Record<string, unknown>) =>
  translate(`arrange.${key}`, { ns: 'inspector', ...(values ?? {}) })

type AlignMode = Parameters<typeof alignSelectedTo>[0]

/**
 * 排列面板的行网格：左列定宽短标签，右列 minmax(0,1fr) 操作区。
 * 常驻区与「更多排列」各自一张网格，但列宽一致，展开后起点不错位。
 * 标签列 3.75rem：中文四字、英文 Distribute / Match size 都放得下；
 * 280px 侧栏里右侧仍留得出六个 28px 对齐键 + 一根分隔线。
 */
function ArrangeGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-[3.75rem_minmax(0,1fr)] items-center gap-x-2 gap-y-2">
      {children}
    </div>
  )
}

/** 网格里的一行：标签 + 操作区。传 htmlFor 时标签是真正的 <label>。 */
function ArrangeRow({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor?: string
  children: ReactNode
}) {
  const cls = 'min-w-0 truncate text-xs text-ink-2'
  return (
    <>
      {htmlFor ? (
        <label htmlFor={htmlFor} className={cls} title={label}>
          {label}
        </label>
      ) : (
        <span className={cls} title={label}>
          {label}
        </span>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">{children}</div>
    </>
  )
}

/**
 * 面板内的轻量工具键：无边框、无阴影，hover 才有底色。
 * 不走共享 Button——这里要的是确定的 28px 方块 / 28px 高文字键，
 * 不受它的默认变体与拉伸影响。forwardRef 让 Tip 能 asChild 挂上来。
 */
type ToolButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { square?: boolean }
const ToolButton = forwardRef<HTMLButtonElement, ToolButtonProps>(function ToolButton(
  { square = false, className = '', type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-sm text-xs text-ink-2 hover:bg-border/60 hover:text-ink active:bg-border focus-visible:focus-ring disabled:pointer-events-none disabled:text-ink-faint ${square ? 'w-7' : 'px-2'} ${className}`}
      {...rest}
    />
  )
})

const ZORDER: { move: ZMove; icon: typeof MoveUp; key: string; shortcut?: string }[] = [
  { move: 'top', icon: ArrowUpToLine, key: 'zTop', shortcut: `⇧${MOD}]` },
  { move: 'up', icon: MoveUp, key: 'zUp', shortcut: `${MOD}]` },
  { move: 'down', icon: MoveDown, key: 'zDown', shortcut: `${MOD}[` },
  { move: 'bottom', icon: ArrowDownToLine, key: 'zBottom', shortcut: `⇧${MOD}[` },
]

/** 层级：按 ZORDER 实际数量组成的紧凑键组，不再占六列 */
function ZOrderToolbar() {
  useTranslation('inspector')
  return (
    <div role="toolbar" aria-label={ar('zorderLabel')} className="flex items-center gap-0.5">
      {ZORDER.map(({ move, icon: Icon, key, shortcut }) => {
        const tip = ar(key)
        return (
          <Tip key={move} label={tip} shortcut={shortcut} side="left">
            <ToolButton square onClick={() => changeZOrder(move)} aria-label={tip}>
              <Icon size={ICON_SIZE.md} />
            </ToolButton>
          </Tip>
        )
      })}
    </div>
  )
}

const H_ALIGN = new Set<string>(['left', 'hcenter', 'right'])

/** 六向对齐：水平三键 + 垂直三键各成一组，中间一根淡竖线；窄栏时整组换行 */
function AlignToolbar({
  label,
  nameFor,
  tipFor,
  onPick,
}: {
  label: string
  nameFor: (mode: AlignMode) => string
  tipFor: (mode: AlignMode) => string
  onPick: (mode: AlignMode) => void
}) {
  const horizontal = ALIGN_BUTTONS.filter((b) => H_ALIGN.has(b.mode))
  const vertical = ALIGN_BUTTONS.filter((b) => !H_ALIGN.has(b.mode))
  const render = ({ mode, icon: Icon }: (typeof ALIGN_BUTTONS)[number]) => (
    <Tip key={mode} label={tipFor(mode)} side="left">
      <ToolButton square onClick={() => onPick(mode)} aria-label={nameFor(mode)}>
        <Icon size={ICON_SIZE.md} />
      </ToolButton>
    </Tip>
  )
  return (
    // 六颗按钮撑满整行、等距分布（2026-09-11 用户反馈）：两组各占一半，组内 justify-between
    <div role="toolbar" aria-label={label} className="flex w-full items-center gap-x-1.5">
      <div className="flex flex-1 items-center justify-between">{horizontal.map(render)}</div>
      {horizontal.length > 0 && vertical.length > 0 && (
        <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
      )}
      <div className="flex flex-1 items-center justify-between">{vertical.map(render)}</div>
    </div>
  )
}

/** 六向对齐，参照整个画布 —— 单选时唯一说得通的对齐 */
export function AlignToCanvasRow() {
  useTranslation('inspector')
  const name = (mode: AlignMode) => ar('alignRelativeCanvas', { mode: alignModeLabel(mode) })
  return (
    <AlignToolbar
      label={ar('alignToolbar')}
      nameFor={name}
      tipFor={name}
      onPick={(mode) => alignSelectedTo(mode, 'page')}
    />
  )
}

/**
 * 排列：紧凑无外框工具带。
 *
 * **单选只常驻层级**，六向「对齐到画布」收进「更多排列」——一个箭头、一段
 * 文字最常做的是调外观，整套排列摆在那里只是让面板更长（审计 T28：层级压成
 * 一组，完整排列仅在相关任务出现）。**能力一条不减**，展开就是同一批按钮。
 * 单选面板连层级都只给层级：对齐已经在它自己的位置组里。
 * 多选是「相关任务」：对齐 / 分布 / 尺寸常驻，参照、间距、成组与样式搬运
 * 收进「更多排列」。
 */
export function ArrangeSection({
  count,
  multi = false,
  zOnly = false,
}: {
  count: number
  multi?: boolean
  zOnly?: boolean
}) {
  useTranslation('inspector')
  const [moreOpen, setMoreOpen] = useState(false)

  if (zOnly) {
    return (
      <Section title={ar('zorderLabel')}>
        <ZOrderToolbar />
      </Section>
    )
  }

  if (!multi) {
    return (
      <>
        {/* `data-arrange-section`：浮动栏「更多」滚到这里；属性页没有 section 路由 */}
        <Section title={ar('zorderLabel')} className="scroll-mt-2" data-arrange-section="">
          <ZOrderToolbar />
        </Section>
        <Disclosure title={ar('more')} open={moreOpen} onToggle={() => setMoreOpen((v) => !v)}>
          <div data-single-align>
            <AlignToCanvasRow />
          </div>
        </Disclosure>
      </>
    )
  }

  return (
    <>
      <Section
        title={ar('titleMulti', { count })}
        className="scroll-mt-2"
        data-arrange-section=""
      >
        <ArrangeGrid>
          <MultiAlignRows count={count} />
          <ArrangeRow label={ar('zorderLabel')}>
            <ZOrderToolbar />
          </ArrangeRow>
        </ArrangeGrid>
      </Section>
      <Disclosure title={ar('more')} open={moreOpen} onToggle={() => setMoreOpen((v) => !v)}>
        <MultiArrangeExtras />
      </Disclosure>
    </>
  )
}

function MultiAlignRows({ count }: { count: number }) {
  useTranslation('inspector')
  // 对齐参照与画布上的多选浮动栏共用 arrangeStore：这边切了那边当场就是新值
  const ref = useArrangeStore((s) => s.alignRef)
  const setRef = useArrangeStore((s) => s.setAlignRef)

  return (
    <>
      {/* 参照只是对齐的一个设置：一行标签 + 紧凑下拉，不再像整块面板的主导航 */}
      {/* 参照是个普通下拉（ui/Select，全仓唯一的下拉控件——`nativeSelect.test` 守着）。
          可达名走 ariaLabel：`<label for>` 指向 Radix 的 `<button>` 不算取名（webkit）。
          当前取值的一句解释挂在触发器的 title 上。 */}
      <ArrangeRow label={ar('refLabel')}>
        <Select
          value={ref}
          onChange={setRef}
          ariaLabel={ar('refLabel')}
          title={ar(`refTip.${ref}`)}
          options={ALIGN_REFS.map((r) => ({ value: r, label: alignRefLabel(r) }))}
          className="w-auto min-w-0 max-w-full"
        />
      </ArrangeRow>

      <ArrangeRow label={ar('align')}>
        <AlignToolbar
          label={ar('alignToolbar')}
          nameFor={(mode) => alignModeLabel(mode)}
          tipFor={(mode) =>
            ar('alignRelativeRef', { mode: alignModeLabel(mode), ref: alignRefLabel(ref) })
          }
          onPick={(mode) => alignSelectedTo(mode, ref)}
        />
      </ArrangeRow>

      {/*
        均匀分布与等宽等高是两件事（审计 T29）：前者动位置、后者动尺寸，
        挤在同一条工具带里只能靠猜图标分辨。拆成两条各自带名字的行。
      */}
      <ArrangeRow label={ar('distributeToolbar')}>
        <ArrangeToolbar
          label={ar('distributeToolbar')}
          buttons={DISTRIBUTE_BUTTONS}
          refName={ref}
          count={count}
        />
      </ArrangeRow>
      <ArrangeRow label={ar('sizeToolbar')}>
        <ArrangeToolbar
          label={ar('sizeToolbar')}
          buttons={SIZE_BUTTONS}
          refName={ref}
          count={count}
        />
      </ArrangeRow>
    </>
  )
}

/** 一条命名的排列工具带；等宽等高的提示要报出参照，分布不用（它只看选区） */
function ArrangeToolbar({
  label,
  buttons,
  refName,
  count,
}: {
  label: string
  buttons: readonly ArrangeButton[]
  refName: AlignRef
  count: number
}) {
  return (
    <div role="toolbar" aria-label={label} className="flex items-center gap-0.5">
      {buttons.map(({ mode, icon: Icon, tipKey, min }) => {
        const tip = tipKey ? ar(tipKey) : alignModeLabel(mode)
        return (
          <Tip
            key={mode}
            label={
              mode === 'samew' || mode === 'sameh'
                ? ar('alignRelativeRef', { mode: tip, ref: alignRefLabel(refName) })
                : tip
            }
            side="left"
          >
            <ToolButton
              square
              disabled={count < min}
              onClick={() => alignSelectedTo(mode, refName)}
              aria-label={tip}
            >
              <Icon size={ICON_SIZE.md} />
            </ToolButton>
          </Tip>
        )
      })}
    </div>
  )
}

function MultiArrangeExtras() {
  useTranslation('inspector')
  const objs = useSelectedObjects()
  // 样式剪贴板不在 store 里（不属于文档），复制后自己触发一次重渲染
  const [, bump] = useState(0)
  const clip = styleClipKind()
  const grouped = selectionHasGroup()

  return (
    <ArrangeGrid>
      {/*
        间距的两个方向以前写作 H / V——而 H 在上面的尺寸里是「高度」，同一个
        字母在同一个面板里代表两件事（审计 T29）。换成方向图示 + 明确的名字，
        名字进 aria-label 与提示，图示只是视觉。
        两个复合输入等宽（图标 · 数值 · mm 同在一个框内）；窄栏时整个输入换行，
        绝不缩成一条细缝。
      */}
      <ArrangeRow label={ar('spacing')}>
        <div className="grid w-full min-w-0 grid-cols-[repeat(auto-fit,minmax(6.5rem,1fr))] gap-1.5">
          <NumberField
            className="w-full min-w-0"
            prefix={<MoveHorizontal size={ICON_SIZE.xs} aria-hidden />}
            suffix="mm"
            ariaLabel={ar('spacingH')}
            step={0.5}
            precision={1}
            value={spacingOf(objs, 'x') ?? 0}
            mixed={spacingOf(objs, 'x') === undefined}
            title={ar('spacingHTitle')}
            onChange={(v) => setSelectionSpacing('x', v)}
          />
          <NumberField
            className="w-full min-w-0"
            prefix={<MoveVertical size={ICON_SIZE.xs} aria-hidden />}
            suffix="mm"
            ariaLabel={ar('spacingV')}
            step={0.5}
            precision={1}
            value={spacingOf(objs, 'y') ?? 0}
            mixed={spacingOf(objs, 'y') === undefined}
            title={ar('spacingVTitle')}
            onChange={(v) => setSelectionSpacing('y', v)}
          />
        </div>
      </ArrangeRow>

      {/* 成组 / 取消成组都是即时命令：点完不留选中态 */}
      <ArrangeRow label={ar('group')}>
        <div className="flex gap-0.5">
          <Tip label={ar('groupTip')}>
            <ToolButton square aria-label={ar('group')} onClick={groupSelected}>
              <Group size={ICON_SIZE.md} />
            </ToolButton>
          </Tip>
          <Tip label={ar('ungroupTip')}>
            <ToolButton
              square
              aria-label={ar('ungroup')}
              disabled={!grouped}
              onClick={ungroupSelected}
            >
              <Ungroup size={ICON_SIZE.md} />
            </ToolButton>
          </Tip>
        </div>
      </ArrangeRow>

      <LayoutGroupControls />

      {/* 样式搬运暂留在这里（不跨面板迁移），放最底、同样轻量 */}
      <ArrangeRow label={translate('group.style', { ns: 'inspector' })}>
        <div className="flex flex-wrap gap-0.5">
          <Tip label={ar('copyStyleTip')}>
            <ToolButton
              onClick={() => {
                copySelectionStyle()
                bump((n) => n + 1)
              }}
            >
              <Clipboard size={ICON_SIZE.sm} />
              {ar('copyStyle')}
            </ToolButton>
          </Tip>
          <Tip
            label={
              clip
                ? ar('pasteStyleTip', { kind: translate(`objectType.${clip}`) })
                : ar('pasteStyleEmpty')
            }
          >
            <ToolButton disabled={!clip} onClick={pasteSelectionStyle}>
              <ClipboardPaste size={ICON_SIZE.sm} />
              {ar('pasteStyle')}
            </ToolButton>
          </Tip>
        </div>
      </ArrangeRow>
    </ArrangeGrid>
  )
}

/**
 * 结构化布局组：行 / 列 / 网格约束。创建即按阅读顺序排一次；
 * 之后改间距 / 列数 / 对齐即时重排，替换素材、改面板比例后自动归位。
 * 成员可单独「固定位置」不随重排。
 */
function LayoutGroupControls() {
  useTranslation('inspector')
  const selIds = useSelectionStore((s) => s.ids)
  const group = useDocumentStore((s): LayoutGroup | null => {
    const gs = s.doc.layoutGroups
    if (!gs?.length) return null
    return (
      gs.find((g) => s.doc.objects.some((o) => selIds.includes(o.id) && o.groupId === g.id)) ??
      null
    )
  })
  const anyPinned = useDocumentStore((s) =>
    s.doc.objects.some((o) => selIds.includes(o.id) && o.layoutPinned),
  )

  if (!group) {
    // 行 / 列 / 网格是「创建」命令，不是模式：轻量文字键，点完不留选中态
    return (
      <ArrangeRow label={ar('layoutGroup')}>
        <div className="flex flex-wrap gap-0.5">
          {(['row', 'col', 'grid'] as const).map((kind) => {
            const label = ar(
              kind === 'row' ? 'layoutRow' : kind === 'col' ? 'layoutCol' : 'layoutGrid',
            )
            return (
              <Tip key={kind} label={ar('createLayoutTip', { kind: label })}>
                <ToolButton onClick={() => createLayoutGroup(kind)}>{label}</ToolButton>
              </Tip>
            )
          })}
        </div>
      </ArrangeRow>
    )
  }

  // 已有布局组：这是一块有状态的设置区，横跨两列
  return (
    <div className="col-span-2 flex flex-col gap-1.5 rounded-sm border border-border p-1.5">
      <Row label={ar('layout')}>
        <Segmented
          className="w-full"
          value={group.kind}
          onChange={(kind) => updateLayoutGroup(group.id, { kind })}
          items={[
            { value: 'row', label: ar('layoutRow') },
            { value: 'col', label: ar('layoutCol') },
            { value: 'grid', label: ar('layoutGrid') },
          ]}
        />
      </Row>
      <Row label={ar('spacing')}>
        <NumberField
          className="min-w-0 flex-1"
          suffix="mm"
          ariaLabel={ar('spacing')}
          value={group.gap}
          min={0}
          max={50}
          step={0.5}
          onChange={(gap) => updateLayoutGroup(group.id, { gap })}
        />
        {group.kind === 'grid' && (
          <NumberField
            prefix={ar('columns')}
            value={group.cols ?? 2}
            min={1}
            max={8}
            step={1}
            onChange={(cols) => updateLayoutGroup(group.id, { cols: Math.round(cols) })}
          />
        )}
      </Row>
      <Row label={ar('align')}>
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.align}
          onChange={(align) => updateLayoutGroup(group.id, { align })}
          items={[
            { value: 'start', label: ar(group.kind === 'col' ? 'alignStartCol' : 'alignStartRow') },
            { value: 'center', label: ar('alignCenter') },
            { value: 'end', label: ar(group.kind === 'col' ? 'alignEndCol' : 'alignEndRow') },
          ]}
        />
      </Row>
      <Row label={ar('uniform')}>
        <Segmented
          className="w-full"
          tone="quiet"
          value={group.uniform ?? 'none'}
          onChange={(v) =>
            updateLayoutGroup(group.id, { uniform: v === 'none' ? null : (v as 'width' | 'height') })
          }
          items={[
            { value: 'none', label: ar('uniformNone') },
            { value: 'width', label: ar('uniformWidth') },
            { value: 'height', label: ar('uniformHeight') },
          ]}
        />
      </Row>
      <label className="flex items-center gap-1.5 text-xs text-ink-2">
        <Toggle
          aria-label={ar('pinMembers')}
          checked={anyPinned}
          onChange={() => toggleLayoutPinned(selIds)}
        />
        {ar('pinMembers')}
      </label>
      <div className="flex flex-wrap gap-0.5">
        <ToolButton title={ar('reflowTitle')} onClick={() => reflowLayoutGroup(group.id)}>
          {ar('reflow')}
        </ToolButton>
        <ToolButton title={ar('dissolveTitle')} onClick={() => dissolveLayoutGroup(group.id)}>
          {ar('dissolve')}
        </ToolButton>
      </div>
    </div>
  )
}

/** 当前相邻间距；对象间距不一致时返回 undefined（输入框显示「多个值」） */
function spacingOf(objs: CanvasObject[], axis: 'x' | 'y'): number | undefined {
  if (objs.length < 2) return undefined
  const s = axis === 'x' ? 'w' : 'h'
  const sorted = objs.slice().sort((a, b) => a[axis] - b[axis])
  const gaps: number[] = []
  for (let i = 1; i < sorted.length; i++) {
    gaps.push(sorted[i][axis] - (sorted[i - 1][axis] + sorted[i - 1][s]))
  }
  return gaps.every((g) => Math.abs(g - gaps[0]) < 0.01) ? Math.round(gaps[0] * 100) / 100 : undefined
}
