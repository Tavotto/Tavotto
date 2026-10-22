import type { CSSProperties, ReactNode } from 'react'
import { perfCount } from '@/perf/core'
import type { PageEdge } from '@/lib/pageEdgeResize'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { cn } from '@/lib/utils'
import { startPageEdgeResize } from './pageEdgeDrag'

/** 命中区的屏幕宽度（px）：跟缩放倍率无关，永远是这么宽（命中区在屏幕空间画，不在世界层里） */
const HIT_PX = 10
/**
 * 多图排版里拖页面四条边改画布大小：**命中区 + 悬停把手**（屏幕空间，每条边一条 `HIT_PX` 宽的透明条，不做角）。
 * 手势在 `pageEdgeDrag.startPageEdgeResize`，拖动中的尺寸标注住在标尺里（`PageEdgeRulerDim`，由 `Rulers` 挂）。
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
    </div>
  )
}
