import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { createPortal } from 'react-dom'
import { usePalette } from '@/components/CommandPalette'
import { boundsOf } from '@/lib/geometry'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore, type ViewTransform } from '@/store/viewportStore'
import type { CanvasObject, PanelObject } from '@/types/document'
import { useQuickEdit } from '../quickEditStore'
import { ElementQuickActions } from './ElementBar'
import { elementHasQuick } from './elementQuick'
import { MultiSelectionBar } from './MultiSelectionBar'
import { ObjectQuickActions } from './SingleObjectBar'
import {
  MARGIN,
  barVariant,
  freeWidthOf,
  placeToolbar,
  selectionScreenRect,
  sidebarInsets,
  type Placement,
  type ScreenRect,
} from './position'
import { OpenInspectorButton } from './shared'
import { qb } from './text'

/**
 * 选中对象旁的轻量上下文工具条（Quick Edit 的可发现入口）。
 *
 * 右键弹层（QuickEdit）与属性页早就有这些能力，但都是隐形入口——普通用户
 * 发现不了（审计 P9）。这条小工具条贴着选择框出现，按目标给 3–5 个高频动作，
 * 写入与属性页**共用同一套 writer / actions**，不是第二套数据通道。
 *
 * 三种目标、一个外壳：
 *
 *   单个画布对象   → SingleObjectBar（文字 / 面板 / 标注的快捷属性）
 *   单个图内元素   → ElementBar（字号 / 线型 / 图例位置…）
 *   两个以上画布对象 → MultiSelectionBar（对齐 / 分布 / 等宽等高 / 成组 / 更多）
 *
 * 外壳负责所有目标共用的事：出现与让位的规则、落位（`position.ts`）、拖动期间
 * 隐藏（pointerdown 即藏、pointerup 再现；任何交互 kind ≠ none 也藏）、Esc 关闭
 * 本次显示（选择一变就重新允许出现）、portal。来源不区分——Shift 点、框选、
 * ⌘A、图层树、程序化选择，只看选区此刻是什么。
 */

type Mode = 'element' | 'object' | 'multi'

export function ContextBar() {
  useTranslation('workspace')
  const ids = useSelectionStore((s) => s.ids)
  const gids = useUiStore((s) => s.selectedGids)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const editingText = useUiStore((s) => s.editingTextId)
  const cropTarget = useUiStore((s) => s.cropTargetId)
  const tool = useUiStore((s) => s.tool)
  // 模态浮层（导出 / 设置 / 确认框…）与命令面板盖着画布时让位：它们的遮罩与
  // 工具条同一层（z-40），后挂进 DOM 的那个会压在上面
  const modalOpen = useUiStore(
    (s) =>
      s.exportOpen ||
      s.layoutOpen ||
      s.versionsOpen ||
      s.stylesOpen ||
      s.registryOpen ||
      s.shortcutHelpOpen ||
      s.settingsOpen ||
      s.confirm != null,
  )
  const paletteOpen = usePalette((s) => s.open)
  const quickOpen = useQuickEdit((s) => s.target)
  const kind = useInteractionStore((s) => s.kind)
  const objects = useDocumentStore((s) => s.doc.objects)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const originX = useViewportStore((s) => s.originX)
  const originY = useViewportStore((s) => s.originY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const rightWidth = useUiStore((s) => s.rightWidth)
  const leftOpen = useUiStore((s) => s.leftOpen)
  const leftWidth = useUiStore((s) => s.leftWidth)
  const layout = useUiStore((s) => s.layout)

  const [dragging, setDragging] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [pos, setPos] = useState<Placement | null>(null)
  // 窗口尺寸一变就重算落位与宽窄档（断点没变时 layout 不会动，得自己听）
  const [resizeTick, setResizeTick] = useState(0)
  // 完整栏量出来比两侧之间还宽（英文文案、大字号）：降成压缩档。可用宽度一变就
  // 清掉重量——这是「量了才知道」的第二道判据，静态阈值是第一道
  const [overflow, setOverflow] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // 目标解析：图内编辑态看 gid，否则看画布选区（顺序即选择顺序，末位主选）
  const panel = objects.find(
    (o): o is PanelObject => o.id === elementPanelId && o.type === 'panel',
  )
  const gid = panel && gids.length === 1 ? gids[0] : null
  const selected: CanvasObject[] = panel
    ? []
    : ids
        .map((id) => objects.find((o) => o.id === id))
        .filter((o): o is CanvasObject => o != null && !o.hidden)
  const obj: CanvasObject | null = !panel && ids.length === 1 ? (selected[0] ?? null) : null
  // 图内编辑态里 shift 加选的标注是混排选区（归 ElementInspector 的对齐工具条管），
  // 不出多选栏——判据是「在不在图内编辑」，不是「那张面板还在不在」
  const multi: CanvasObject[] | null =
    !panel && !elementPanelId && selected.length >= 2 ? selected : null
  const mode: Mode | null = panel && gid ? 'element' : obj ? 'object' : multi ? 'multi' : null
  // 只有真给得出高频动作才出现——一个孤零零的「全部属性」按钮不值得盖住画布
  const manifest = usePanelDisplayManifest(panel)
  const element = gid ? (manifest?.elements.find((e) => e.gid === gid) ?? null) : null
  const hasActions =
    mode === 'element'
      ? !!element && !!element.editable.length && elementHasQuick(element)
      : mode != null
  const idsKey = ids.join(',')
  const targetKey =
    mode === 'element'
      ? `el:${panel!.id}:${gid ?? ''}`
      : mode === 'object'
        ? `obj:${obj!.id}`
        : mode === 'multi'
          ? `multi:${idsKey}`
          : ''
  // narrow 断点下侧栏是盖在画布上的覆盖式抽屉（z-30），portal 出来的工具条
  // （z-40）会压住并拦截抽屉里的控件；抽屉本来就把属性带到了眼前，此时让位
  const overlayDrawerOpen = layout === 'narrow' && (leftOpen || rightOpen)
  const active =
    !!targetKey &&
    hasActions &&
    !editingText &&
    !cropTarget &&
    !quickOpen &&
    !overlayDrawerOpen &&
    !modalOpen &&
    !paletteOpen &&
    tool === 'select' &&
    !dismissed
  // 拖动 / 缩放 / 框选 / 平移 / 参考线 / 绘制期间不出现；交互一结束、选区还在就回来
  const visible = active && !dragging && kind === 'none'

  const insets = sidebarInsets({ layout, leftOpen, leftWidth, rightOpen, rightWidth })
  const freeWidth = freeWidthOf(window.innerWidth, insets)
  const variant = overflow ? 'compact' : barVariant(freeWidth)

  useLayoutEffect(() => {
    setOverflow(false)
  }, [freeWidth, idsKey])

  // Esc 关闭只作用于**这一次选择**；选择一变就重新出现
  useEffect(() => {
    setDismissed(false)
  }, [targetKey])

  // 拖动 / 框选期间藏起来：任何画布上的 pointerdown 都算（点工具条自己除外）
  useEffect(() => {
    const down = (e: PointerEvent) => {
      const node = e.target as Element | null
      if (node?.closest?.('[data-context-bar]')) return
      if (node?.closest?.('[data-radix-popper-content-wrapper]')) return
      setDragging(true)
    }
    const up = () => setDragging(false)
    window.addEventListener('pointerdown', down, true)
    window.addEventListener('pointerup', up, true)
    return () => {
      window.removeEventListener('pointerdown', down, true)
      window.removeEventListener('pointerup', up, true)
    }
  }, [])

  useEffect(() => {
    const onResize = () => setResizeTick((n) => n + 1)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // 不抢全局 Esc 的职责（退编辑态、清选区等）：只有焦点在工具条里才拦下来；
      // 那时只关本次显示，选区一个字不动
      if (ref.current?.contains(document.activeElement)) {
        e.preventDefault()
        e.stopPropagation()
      }
      setDismissed(true)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [active])

  // 量锚点、贴上去。依赖里带上 objects / 视口 / 侧栏：对象挪动、画布缩放平移、
  // 侧栏开合都重贴。多选的锚点不查 DOM——与 OverlaySvg 的联合框算同一份几何
  useLayoutEffect(() => {
    if (!visible) {
      setPos(null)
      return
    }
    let anchor: ScreenRect | null = null
    if (mode === 'multi' && multi) {
      const b = boundsOf(multi)
      const t: ViewTransform = { zoom, panX, panY, originX, originY }
      anchor = b ? selectionScreenRect(b, t) : null
    } else {
      const node =
        mode === 'element' && panel
          ? ((gid &&
              document.querySelector(
                `[data-element-svg="${CSS.escape(panel.id)}"] [id="${CSS.escape(gid)}"]`,
              )) ||
            document.querySelector(`[data-object-id="${CSS.escape(panel.id)}"]`))
          : obj
            ? document.querySelector(`[data-object-id="${CSS.escape(obj.id)}"]`)
            : null
      if (node) {
        const r = (node as Element).getBoundingClientRect()
        anchor = { left: r.left, top: r.top, width: r.width, height: r.height }
      }
    }
    if (!anchor) {
      setPos(null)
      return
    }
    const w = ref.current?.offsetWidth ?? 220
    const h = ref.current?.offsetHeight ?? 36
    if (mode === 'multi' && variant === 'full' && w > freeWidth - 2 * MARGIN) {
      setOverflow(true)
      return
    }
    const next = placeToolbar(
      anchor,
      { w, h },
      { width: window.innerWidth, height: window.innerHeight },
      insets,
    )
    setPos((prev) =>
      prev && prev.x === next.x && prev.y === next.y && prev.placement === next.placement
        ? prev
        : next,
    )
    // insets 由 layout / 两侧开合与宽度决定，multi 由 idsKey + objects 决定：都已在依赖里
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    visible,
    mode,
    panel,
    gid,
    obj,
    idsKey,
    objects,
    zoom,
    panX,
    panY,
    originX,
    originY,
    viewW,
    viewH,
    layout,
    leftOpen,
    leftWidth,
    rightOpen,
    rightWidth,
    variant,
    freeWidth,
    resizeTick,
  ])

  if (!visible) return null

  return createPortal(
    <div
      ref={ref}
      data-context-bar
      data-context-bar-mode={mode ?? undefined}
      data-multi-selection-context-bar={mode === 'multi' ? '' : undefined}
      data-variant={mode === 'multi' ? variant : undefined}
      data-placement={pos?.placement}
      role="toolbar"
      aria-label={mode === 'multi' ? qb('multiAria') : qb('aria')}
      style={pos ? { left: pos.x, top: pos.y } : { left: -9999, top: -9999 }}
      className={cn(
        // w-max：fixed 盒子的 width:auto 会被「left 到视口右沿」的可用宽度压扁，
        // 量出来的就不是它的自然宽度；落位与宽窄档都靠这个量
        'fixed z-40 flex w-max items-center gap-1 rounded-md border border-border bg-surface p-1',
        'text-xs text-ink shadow-pop',
        pos ? 'animate-pop-in' : 'invisible',
      )}
      onContextMenu={(e) => e.preventDefault()}
    >
      {mode === 'element' && panel && gid ? (
        <>
          <ElementQuickActions panel={panel} gid={gid} />
          <OpenInspectorButton />
        </>
      ) : mode === 'object' && obj ? (
        <>
          <ObjectQuickActions obj={obj} />
          <OpenInspectorButton />
        </>
      ) : mode === 'multi' && multi ? (
        <MultiSelectionBar objs={multi} variant={variant} />
      ) : null}
    </div>,
    document.body,
  )
}
