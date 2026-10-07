import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { RowLabelWidth } from '../ui/Field'
import { displayLabel } from './roles/mathtext'
import { Eye, EyeOff, MoveDown, MoveUp, MoveVertical } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { legendEntryViews, type LegendEntryView } from '@/lib/legendModel'
import { cn } from '@/lib/utils'
import { setOverride, setOverrides, unhideElement } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { msg } from '@/i18n'
import { Badge } from '../ui/Badge'
import { dropLineClass, listRowClass } from '../ui/listRow'
import { MenuItem } from '../ui/Menu'
import { RowMenu } from '../ui/RowMenu'
import { useRowMenu } from '../ui/useRowMenu'
import { GroupHead } from './GroupHead'
import { INSPECTOR_LABEL_W } from './layout'
import { MarkerGlyph } from './controls/MarkerPicker'
import { TypographyControls } from './controls/TypographyControls'
import { FIGURE_TEXT_BATCH_PROPS, useFigureTypography } from './typographyAdapter'
import { effectiveOverride } from '@/lib/effectiveOverride'

/**
 * 图例卡（ADR 0034）：选中图例时常驻在属性区首屏的两块——
 *
 *   * **文字**：一份 Typography 控件，批量作用于全部图例项（字体 / 字号 /
 *     粗斜 / 颜色），走 `useFigureTypography` 的批量适配器；
 *   * **条目**：按显示顺序列出每一项——示意线预览、文字、「跟随 / 自定义」
 *     徽标、显隐、上下移动。点文字即选中那一项（属性页切到它，示意线样式
 *     与绑定在那里改）。
 *
 * 卡片承接掉的图例字段：`fontsize`（由 Typography 接管）与 `entry_order`
 * （由条目列表接管），通用列表要把它们让出来——同一属性不出两套控件。
 *
 * 行里没有嵌套的可交互元素：文字是一个按钮，显隐 / 上移 / 下移收进行尾的 ⋯（同一份菜单
 * 也挂在右键与 ⇧F10 上），键盘还有 ⌥↑ / ⌥↓（见 `LegendEntryList`）。
 */

/** 卡片承接掉的图例字段 */
export const LEGEND_CARD_PROPS = ['fontsize', 'entry_order'] as const

const lg = (key: string, values?: Record<string, unknown>) =>
  translate(`legend.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function LegendCard({
  panel,
  manifest,
  legend,
  labelWidth = INSPECTOR_LABEL_W,
}: {
  panel: PanelObject
  manifest: Manifest
  legend: ManifestElement
  labelWidth?: RowLabelWidth
}) {
  useTranslation('inspector')
  const views = legendEntryViews(panel, manifest, legend)
  const entryElements = views.map((v) => v.element)
  const typography = useFigureTypography(panel, entryElements, FIGURE_TEXT_BATCH_PROPS)
  const hasTypography = FIGURE_TEXT_BATCH_PROPS.some((p) => typography.fieldOf(p))

  const order = views.map((v) => v.info.index)
  /** 把第 from 项挪到第 to 位（上移 / 下移 / 拖动共用）：写一次 `entry_order`（原始序号的排列） */
  const moveTo = (from: number, to: number) => {
    if (to < 0 || to >= order.length || to === from) return
    const next = [...order]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    setOverride(panel.id, legend.gid, 'entry_order', next, true)
  }
  const toggleHidden = (v: LegendEntryView) => {
    if (v.hidden) unhideElement(panel.id, v.element.gid)
    else {
      setOverrides(
        panel.id,
        msg('history.hideLegendEntry', { label: v.text }, 'workspace'),
        [{ gid: v.element.gid, prop: 'visible', value: false }],
      )
    }
  }

  if (!views.length) return null

  return (
    <div className="flex flex-col gap-4">
      {hasTypography && (
        <div className="flex flex-col gap-1.5">
          <GroupHead>{lg('typography')}</GroupHead>
          <TypographyControls adapter={typography} labelWidth={labelWidth} />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {/* 名字 + meta 数字，不是「图例项（2）」（打磨 L10 / 第十九节批次 E–G） */}
        <GroupHead meta={views.length}>{lg('entries')}</GroupHead>
        {/* 按「面板 + 图例」取 key：拖动会话属于这一个图例。拖到一半在元素树里用 ↓ 换到另一个图例时
            卡片本身被复用，列表不能被复用——换身份 = 旧列表卸载，卸载收尾作废那次拖动，迟到的松手
            不给已经不显示的图例重排、不进历史（Codex #829） */}
        <LegendEntryList
          key={`${panel.id} ${legend.gid}`}
          panel={panel}
          views={views}
          order={order}
          onMove={moveTo}
          onToggleHidden={toggleHidden}
        />
      </div>
    </div>
  )
}

/**
 * 图例项列表（2026-10-07 设计审计 §9.2 P1，宪法 §21.2 的行尾纪律）：
 *
 * - 一行常驻的只有：色样（真实的标记图形）+ 名字 + 非默认的绑定徽标；拖动柄与 ⋯ 只在
 *   hover / focus-within 时浮出（此前每行常驻三颗 28px 钮、一行 4 个 Tab 停靠点）；
 * - **整张列表一个 Tab 停靠点**（roving focus）：↑ / ↓ 在项之间走，Home / End 到首尾，
 *   ⌥↑ / ⌥↓ 把这一项上移 / 下移（与 ⋯ 里的上移 / 下移同一个动作），Enter 选中那一项；
 * - 拖动柄按住上下拖：落点一条 accent 线（`dropLineClass`），松手写一次 `entry_order`；
 * - ⋯ / 右键 / ⇧F10 是同一份菜单（`useRowMenu` + `RowMenu`）：显示 / 隐藏、上移、下移。⋯ 是
 *   `tabStop={false}`——焦点在项上时 Tab 直接离开列表，不在 ⋯ 上多停一下（Codex #829）。
 */
function LegendEntryList({
  panel,
  views,
  order,
  onMove,
  onToggleHidden,
}: {
  panel: PanelObject
  views: LegendEntryView[]
  order: number[]
  onMove: (from: number, to: number) => void
  onToggleHidden: (v: LegendEntryView) => void
}) {
  const listRef = useRef<HTMLUListElement>(null)
  const [focusGid, setFocusGid] = useState<string | null>(null)
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null)
  /**
   * 进行中那次拖动的收尾（摘掉 window 监听、作废这次拖动、不提交）。列表卸载时——比如松手前按
   * Esc 退出元素编辑——必须跑它：不然迟到的 pointerup 仍会拿旧闭包给已经放弃的图例重排（Codex #829 P2）。
   * 换图例（同一张卡片复用给另一个图例）也走这里：`LegendCard` 按面板 + 图例给列表 key，身份一变就是卸载
   */
  const dragCleanupRef = useRef<(() => void) | null>(null)
  useEffect(() => () => dragCleanupRef.current?.(), [])
  const current = Math.max(0, views.findIndex((v) => v.element.gid === focusGid))
  const focusRow = (gid: string) => {
    setFocusGid(gid)
    requestAnimationFrame(() =>
      listRef.current?.querySelector<HTMLElement>(`[data-legend-entry="${CSS.escape(gid)}"] [data-legend-entry-main]`)?.focus(),
    )
  }

  const onKeyDown = (e: KeyboardEvent, i: number) => {
    const v = views[i]
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && e.altKey) {
      e.preventDefault()
      const j = i + (e.key === 'ArrowUp' ? -1 : 1)
      if (j < 0 || j >= views.length) return
      onMove(i, j)
      focusRow(v.element.gid)
      return
    }
    let j: number | null = null
    if (e.key === 'ArrowUp') j = i - 1
    else if (e.key === 'ArrowDown') j = i + 1
    else if (e.key === 'Home') j = 0
    else if (e.key === 'End') j = views.length - 1
    if (j == null) return
    e.preventDefault()
    j = Math.max(0, Math.min(views.length - 1, j))
    focusRow(views[j].element.gid)
  }

  /** 拖动柄：按行的中线算落点；同一个位置松手什么都不写 */
  const startDrag = (e: ReactPointerEvent, from: number) => {
    if (e.button !== 0) return
    e.preventDefault()
    const rows = [...(listRef.current?.querySelectorAll<HTMLElement>('[data-legend-entry]') ?? [])]
    const targetOf = (y: number) => {
      let to = rows.length - 1
      for (let k = 0; k < rows.length; k++) {
        const r = rows[k].getBoundingClientRect()
        if (y < r.top + r.height / 2) {
          to = k > from ? k - 1 : k
          break
        }
      }
      return Math.max(0, Math.min(rows.length - 1, to))
    }
    // 同一时刻只有一次拖动：上一次没收尾（理论上不该有）先作废，不提交
    dragCleanupRef.current?.()
    setDrag({ from, to: from })
    let live = true
    const detach = () => {
      live = false
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      if (dragCleanupRef.current === detach) dragCleanupRef.current = null
    }
    const move = (ev: PointerEvent) => {
      if (live) setDrag({ from, to: targetOf(ev.clientY) })
    }
    const up = (ev: PointerEvent) => {
      if (!live) return
      detach()
      setDrag(null)
      const to = targetOf(ev.clientY)
      if (to !== from) onMove(from, to)
    }
    const cancel = () => {
      detach()
      setDrag(null)
    }
    dragCleanupRef.current = detach
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
  }

  return (
    <ul ref={listRef} className="-mx-1 flex flex-col" aria-label={lg('entriesAria')} data-legend-entries={order.join(',')}>
      {views.map((v, i) => (
        <LegendEntryRow
          key={v.element.gid}
          panel={panel}
          entry={v}
          index={i}
          count={views.length}
          tabbable={i === current}
          dropLine={
            drag && drag.to !== drag.from && drag.to === i ? (drag.to > drag.from ? 'after' : 'before') : null
          }
          dragging={drag?.from === i}
          onFocusMain={() => setFocusGid(v.element.gid)}
          onKeyDown={(e) => onKeyDown(e, i)}
          onDragStart={(e) => startDrag(e, i)}
          onMove={(d) => onMove(i, i + d)}
          onToggleHidden={() => onToggleHidden(v)}
        />
      ))}
    </ul>
  )
}

function LegendEntryRow({
  panel,
  entry: v,
  index: i,
  count,
  tabbable,
  dropLine,
  dragging,
  onFocusMain,
  onKeyDown,
  onDragStart,
  onMove,
  onToggleHidden,
}: {
  panel: PanelObject
  entry: LegendEntryView
  index: number
  count: number
  tabbable: boolean
  dropLine: 'before' | 'after' | null
  dragging: boolean
  onFocusMain: () => void
  onKeyDown: (e: KeyboardEvent) => void
  onDragStart: (e: ReactPointerEvent) => void
  onMove: (delta: -1 | 1) => void
  onToggleHidden: () => void
}) {
  const menu = useRowMenu()
  const name = displayLabel(v.text)
  return (
    <li
      {...menu.rowProps}
      data-legend-entry={v.element.gid}
      data-hidden={v.hidden || undefined}
      className={cn(listRowClass({ hidden: v.hidden }), 'px-1', dragging && 'bg-surface-hover')}
    >
      <span aria-hidden className={dropLineClass(dropLine)} />
      {/* 拖动柄：只在 hover / focus-within 时出现；不进 Tab 顺序（键盘用 ⌥↑ / ⌥↓） */}
      <span
        aria-hidden
        data-legend-drag
        onPointerDown={onDragStart}
        className="flex h-5 w-3 shrink-0 cursor-grab items-center justify-center text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        <MoveVertical size={ICON_SIZE.xs} />
      </span>
      <HandleSwatch panel={panel} entry={v} />
      <button
        type="button"
        data-legend-entry-main
        tabIndex={tabbable ? 0 : -1}
        className="flex h-full min-w-0 flex-1 items-center gap-1 rounded-sm text-left text-sm outline-none hover:text-ink focus-visible:focus-ring"
        onClick={() => useUiStore.getState().setSelectedGid(v.element.gid)}
        onFocus={onFocusMain}
        onKeyDown={onKeyDown}
        aria-label={lg('selectEntry', { label: name })}
        aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
      >
        <span className={cn('min-w-0 truncate', v.hidden ? 'line-through' : 'text-ink')}>{name}</span>
        <BindingBadge binding={v.binding} />
      </button>
      {/* ⋯ 不进 Tab 顺序：整张列表一个 Tab 停靠点；键盘开菜单走 ⇧F10 / ContextMenu 键（冒泡到 li 的 rowProps） */}
      <RowMenu
        state={menu}
        label={lg('entryActions', { label: name })}
        tabStop={false}
        data-legend-entry-menu={v.element.gid}
      >
        <MenuItem icon={v.hidden ? Eye : EyeOff} data-legend-toggle-hidden onSelect={onToggleHidden}>
          {v.hidden ? lg('showEntry', { label: v.text }) : lg('hideEntry', { label: v.text })}
        </MenuItem>
        <MenuItem icon={MoveUp} disabled={i === 0} data-legend-move="up" onSelect={() => onMove(-1)}>
          {lg('moveUp', { label: v.text })}
        </MenuItem>
        <MenuItem icon={MoveDown} disabled={i === count - 1} data-legend-move="down" onSelect={() => onMove(1)}>
          {lg('moveDown', { label: v.text })}
        </MenuItem>
      </RowMenu>
    </li>
  )
}

/**
 * 「自定义 / 未关联」——只说状态，不露 gid。
 *
 * **默认态（跟随图中对象）不出徽标**（打磨 L10）：图例项默认全都跟随，每一行都挂一枚
 * 「跟随」等于没有信息量，只是把两行都加了一块灰片。形状走唯一的胶囊原语 `ui/Badge`，
 * 不再自造 32×18 的边框片。
 */
export function BindingBadge({ binding }: { binding: LegendEntryView['binding'] }) {
  if (binding === 'follow_source') return null
  const key = binding === 'custom' ? 'custom' : 'unbound'
  return <Badge tone={binding === 'custom' ? 'accent' : 'warn'}>{lg(`badge.${key}`)}</Badge>
}

const DASH: Record<string, string | undefined> = {
  '-': undefined,
  '--': '6 3',
  ':': '1.5 2.5',
  '-.': '6 2.5 1.5 2.5',
}

/**
 * 示意线的小预览：读 manifest 的 `handle_*` 字段（override 优先），画一条
 * 24×12 的线 + 一个标记。它是**读 manifest 的投影**，不是第二份样式判断——
 * 引擎给什么就画什么，没有 handle_linestyle 的项（柱 / 散点）只画一个色块。
 */
function HandleSwatch({ panel, entry }: { panel: PanelObject; entry: LegendEntryView }) {
  const el = entry.element
  const read = (prop: string): unknown => {
    const ov = effectiveOverride(panel.overrides, el.gid, prop)
    if (ov) return ov.value
    return el.editable.find((f) => f.prop === prop)?.value
  }
  const color = String(read('handle_color') ?? '#000000')
  const ls = read('handle_linestyle')
  const lw = Number(read('handle_linewidth') ?? 1.5)
  const marker = String(read('handle_marker') ?? 'None')
  // 没被改过时，图上那个标记的真实几何由引擎给：自定义 / 元组 / 路径标记名字画不出，照它画
  const markerFact = effectiveOverride(panel.overrides, el.gid, 'handle_marker')
    ? undefined
    : el.editable.find((f) => f.prop === 'handle_marker')?.marker_current
  const hasLine = el.editable.some((f) => f.prop === 'handle_linestyle')
  return (
    <svg
      width={24}
      height={12}
      viewBox="0 0 24 12"
      aria-hidden
      data-legend-swatch={el.gid}
      className={cn('shrink-0', entry.hidden && 'opacity-40')}
    >
      {hasLine ? (
        <>
          <line
            x1={1}
            y1={6}
            x2={23}
            y2={6}
            stroke={color}
            strokeWidth={Math.max(0.75, Math.min(4, lw))}
            strokeDasharray={DASH[String(ls)]}
          />
          {/* 标记按它真实的形状画（与标记选择器同一份图形），不再一律画成圆点 */}
          {marker !== 'None' && marker !== '' && (
            <g transform="translate(6 0)" style={{ color }}>
              <MarkerGlyph code={marker} fact={markerFact} />
            </g>
          )}
        </>
      ) : marker !== 'None' && marker !== '' ? (
        // 散点那种只有标记、没有线的项：画它的标记，不画一块色条
        <g transform="translate(6 0)" style={{ color }}>
          <MarkerGlyph code={marker} fact={markerFact} fallback={<rect x={2} y={2} width={8} height={8} fill="currentColor" />} />
        </g>
      ) : (
        <rect x={2} y={2} width={20} height={8} fill={color} stroke="none" />
      )}
    </svg>
  )
}
