import type { CSSProperties, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Numbers } from '@sfinterface/numbers'
import { perfCount } from '@/perf/core'
import type { PageEdge } from '@/lib/pageEdgeResize'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { cn } from '@/lib/utils'
import { startPageEdgeResize } from './pageEdgeDrag'
import { READOUT_H, READOUT_W, readoutBox } from './pageEdgeReadout'

/** 命中区的屏幕宽度（px）：跟缩放倍率无关，永远是这么宽（命中区在屏幕空间画，不在世界层里） */
const HIT_PX = 10
/** 尺寸标注离页面的距离（px）与标注条的贴边下限 */
const DIM_GAP = 26

const mmText = (v: number) => String(Math.round(v * 10) / 10)

/**
 * 多图排版里拖页面四条边改画布大小：**命中区 + 悬停把手**（屏幕空间，每条边一条 `HIT_PX` 宽的透明条，不做角）。
 * 手势在 `pageEdgeDrag.startPageEdgeResize`，尺寸标注是同一层的 `PageDimensions`。
 *
 * 挂载条件由 `CanvasStage` 管（`mode === 'layout'`；单图编辑没有页面）。工具不是指针 / 按着空格平移 /
 * 正在裁剪 / 别的拖动占着时命中区让位（不吃指针事件），免得压住画图和平移。命中区在选框覆盖层
 * （`OverlaySvg`）**之下**：贴着页面边的选中对象，它的缩放手柄先于拖边。
 */
export function PageEdgeHandles() {
  perfCount('render.PageEdgeHandles')
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const pageW = useDocumentStore((s) => s.doc.page.w)
  const pageH = useDocumentStore((s) => s.doc.page.h)
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  // 图内编辑子状态（排版里双击某张图进图内）：画布让位给那一张图，不提供拖边
  const inFigure = useUiStore((s) => s.elementPanelId != null)
  const kind = useInteractionStore((s) => s.kind)
  const active = useInteractionStore((s) => s.pageEdge?.edge ?? null)
  const idle = tool === 'select' && !spaceDown && cropTargetId == null && kind === 'none'
  const w = mmToWorld(pageW) * zoom
  const h = mmToWorld(pageH) * zoom
  const half = HIT_PX / 2
  if (inFigure) return null

  const strip = (edge: PageEdge, style: CSSProperties, line: string, grip: string): ReactNode => (
    <div
      key={edge}
      data-page-edge={edge}
      data-active={active === edge || undefined}
      className={cn(
        'group absolute',
        edge === 'l' || edge === 'r' ? 'cursor-ew-resize' : 'cursor-ns-resize',
        idle || active === edge ? 'pointer-events-auto' : 'pointer-events-none',
      )}
      style={style}
      onPointerDown={(e) => startPageEdgeResize(e, edge)}
    >
      <span
        aria-hidden
        className={cn(
          'absolute bg-dim opacity-0 group-hover:opacity-100 group-data-[active]:opacity-100',
          line,
        )}
      />
      <span
        aria-hidden
        className={cn(
          'absolute rounded-full bg-dim opacity-0 group-hover:opacity-100 group-data-[active]:opacity-100',
          grip,
        )}
      />
    </div>
  )

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {strip('l', { left: panX - half, top: panY, width: HIT_PX, height: h }, 'inset-y-0 left-1/2 w-px -translate-x-1/2', 'left-1/2 top-1/2 h-6 w-1 -translate-x-1/2 -translate-y-1/2')}
      {strip('r', { left: panX + w - half, top: panY, width: HIT_PX, height: h }, 'inset-y-0 left-1/2 w-px -translate-x-1/2', 'left-1/2 top-1/2 h-6 w-1 -translate-x-1/2 -translate-y-1/2')}
      {strip('t', { left: panX, top: panY - half, width: w, height: HIT_PX }, 'inset-x-0 top-1/2 h-px -translate-y-1/2', 'left-1/2 top-1/2 h-1 w-6 -translate-x-1/2 -translate-y-1/2')}
      {strip('b', { left: panX, top: panY + h - half, width: w, height: HIT_PX }, 'inset-x-0 top-1/2 h-px -translate-y-1/2', 'left-1/2 top-1/2 h-1 w-6 -translate-x-1/2 -translate-y-1/2')}
      <PageDimensions />
    </div>
  )
}

/**
 * 拖边时的机械制图式尺寸标注：两端引出线 + 双箭头，外加被拖那一维的无框读数「宽 N mm」/「高 N mm」（数字跟着文档里的页面尺寸实时变；读数在页面内侧，不压标尺），
 * 吸上栏宽时在宽度标注上方写栏宽名。**画布叠加层**：不进文档、不进导出、不碰 SVG 预览平面；
 * 只在拖动中挂（平时不挂 `<svg>`——e2e 有「舞台里第一个 svg」这类等渲染的定位）。
 * 颜色是 `--color-dim`（亮暗两套）。
 */
function PageDimensions() {
  const { t, i18n } = useTranslation('workspace')
  const pageEdge = useInteractionStore((s) => s.pageEdge)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const pageW = useDocumentStore((s) => s.doc.page.w)
  const pageH = useDocumentStore((s) => s.doc.page.h)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  if (!pageEdge) return null
  const L = panX
  const T = panY
  const W = mmToWorld(pageW) * zoom
  const H = mmToWorld(pageH) * zoom
  const y = Math.max(14, T - DIM_GAP)
  const x = Math.max(14, L - DIM_GAP)
  const showW = pageEdge.edge === 'l' || pageEdge.edge === 'r'
  // 读数（2026-10-22 用户反馈：白底框跳动、压在标尺线 / 刻度数字上）：无框的轻量文字，锚在被拖那条边的画布内侧，
  // 盒子宽高固定、数字 tabular-nums、位置只随边走（`pageEdgeReadout.readoutBox`），夹在舞台内所以不会碰到舞台外的标尺。
  // 只出被拖那一维的数（拖宽出宽、拖高出高）；文字外沿一圈画布色描边保证压在图上也读得清。
  // 数字是会滚的（与顶栏缩放读数同一条规矩）：逐 mm 的连续拖动即时换（duration 0，否则永远在半路），
  // 吸上 / 离开栏宽、⌥ 按下 / 松开这类离散跳变才滚（默认时长）
  const kind = showW ? 'width' : 'height'
  const mm = showW ? pageW : pageH
  const box = readoutBox(pageEdge.edge, { left: L, top: T, w: W, h: H }, { w: viewW, h: viewH })
  const readout = (
    <div
      data-dim-readout={kind}
      data-readout-edge={pageEdge.edge}
      className="type-number absolute whitespace-nowrap font-medium text-dim [text-shadow:0_0_2px_var(--color-canvas),0_0_2px_var(--color-canvas),0_0_4px_var(--color-canvas)]"
      style={{
        left: box.left,
        top: box.top,
        width: READOUT_W,
        height: READOUT_H,
        lineHeight: `${READOUT_H}px`,
        textAlign: box.align,
      }}
    >
      <Numbers
        value={Math.round(mm * 10) / 10}
        prefix={t(`pageEdge.${kind}Prefix`)}
        suffix=" mm"
        duration={pageEdge.rolls ? undefined : 0}
        format={{ useGrouping: false, maximumFractionDigits: 1 }}
        locale={i18n.language}
        label={t(`pageEdge.${kind}`, { mm: mmText(mm) })}
      />
    </div>
  )
  const arrow = (x1: number, y1: number, x2: number, y2: number) => (
    <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="currentColor" strokeWidth={1} markerStart="url(#page-dim-arrow)" markerEnd="url(#page-dim-arrow)" />
  )
  return (
    <div data-page-dims="" className="pointer-events-none absolute inset-0">
    <svg
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible text-dim"
    >
      <defs>
        <marker id="page-dim-arrow" viewBox="0 0 10 10" refX={5} refY={5} markerWidth={9} markerHeight={9} orient="auto-start-reverse">
          <path d="M0 1.5L10 5L0 8.5" fill="none" stroke="currentColor" strokeWidth={1.4} />
        </marker>
      </defs>
      <g data-dim="w" opacity={showW ? 1 : 0.45}>
        <line x1={L} y1={T - 4} x2={L} y2={y - 8} stroke="currentColor" strokeWidth={0.8} />
        <line x1={L + W} y1={T - 4} x2={L + W} y2={y - 8} stroke="currentColor" strokeWidth={0.8} />
        {arrow(L + 4, y, L + W - 4, y)}
        {pageEdge.snapLabel && showW && (
          <text data-dim-snap="" x={L + W / 2} y={y - 16} textAnchor="middle" className="fill-dim" fontSize={11} fontWeight={500}>
            {t('pageEdge.snapTo', { name: pageEdge.snapLabel })}
          </text>
        )}
      </g>
      <g data-dim="h" opacity={showW ? 0.45 : 1}>
        <line x1={L - 4} y1={T} x2={x - 8} y2={T} stroke="currentColor" strokeWidth={0.8} />
        <line x1={L - 4} y1={T + H} x2={x - 8} y2={T + H} stroke="currentColor" strokeWidth={0.8} />
        {arrow(x, T + 4, x, T + H - 4)}
      </g>
    </svg>
      {readout}
    </div>
  )
}
