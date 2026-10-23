import type { CSSProperties, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Numbers } from '@sfinterface/numbers'
import { perfCount } from '@/perf/core'
import type { PageEdge } from '@/lib/pageEdgeResize'
import { useDocumentStore } from '@/store/documentStore'
import { figureFrameFacts } from '@/lib/figureEdgeFrame'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { cn } from '@/lib/utils'
import { startFigureEdgeResize } from './figureEdgeDrag'
import { startPageEdgeResize } from './pageEdgeDrag'
import { DIM_INSET, dimSpan } from './pageEdgeReadout'

/** 命中区的屏幕宽度（px）：跟缩放倍率无关，永远是这么宽（命中区在屏幕空间画，不在世界层里） */
const HIT_PX = 10
/** 尺寸标注离页面的距离（px）与标注条的贴边下限 */
const DIM_GAP = 26
/** 单图的命中区压进图里的深度（px）：图框边上常贴着轴标题，比页面边（对称 5 px）浅 */
const FIGURE_HIT_IN = 2

const MM_SUFFIX = '\u00a0mm'
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
  if (inFigure) return null

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <EdgeStrips
        attr="data-page-edge"
        box={{ left: panX, top: panY, w, h }}
        idle={idle}
        active={active}
        onStart={(e, edge) => startPageEdgeResize(e, edge)}
      />
      <PageDimensions />
    </div>
  )
}

/**
 * 单图编辑里拖这张图的四条边改图幅：同一套命中区与尺寸标注（`EdgeStrips` / `EdgeDimensions`），框是画布上那张图。
 * 手势在 `figureEdgeDrag.startFigureEdgeResize`。挂载条件由 `CanvasStage` 管（`mode === 'fast_edit'`）；
 * 精确 manifest 缺席（或面板被裁过 / 旋转过）时整组不挂（`figureFrameFacts`）；图被隐藏 / 锁定、非选择工具、裁剪中、别的拖动占着时让位；**图内编辑里照常可用**（单图编辑打开图默认就在图内编辑里），
 * 命中区压进图里只有 `FIGURE_HIT_IN` px（其余在图外），免得盖住贴着图框的轴标题 / 刻度。
 */
export function FigureEdgeHandles({ panelId }: { panelId: string | null }) {
  perfCount('render.FigureEdgeHandles')
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const spaceDown = useViewportStore((s) => s.spaceDown)
  const o = useDocumentStore((s) => (panelId ? s.doc.objects.find((x) => x.id === panelId) : undefined))
  // 权威只认精确 manifest（ADR 0017）：选择器只回布尔，别把每次新造的 manifest 当订阅值
  const hasFrame = useRenderStore((s) =>
    o?.type === 'panel' ? figureFrameFacts(o, exactPanelManifest(s, o)) != null : false,
  )
  const tool = useUiStore((s) => s.tool)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const kind = useInteractionStore((s) => s.kind)
  const active = useInteractionStore((s) => (s.kind === 'figureedge' ? (s.pageEdge?.edge ?? null) : null))
  const preview = useInteractionStore((s) => s.figureEdgeBox)
  const pending = useInteractionStore((s) => (s.figureFramePending?.id === panelId ? s.figureFramePending.box : null))
  if (!o || o.type !== 'panel' || o.hidden || o.locked) return null
  // 松手后新图回来之前：只留虚线框占位（旧图与背纸已收起，`PanelView` / `FastEditPaper` 读同一个状态）
  if (pending) {
    const r = {
      left: panX + mmToWorld(pending.x) * zoom,
      top: panY + mmToWorld(pending.y) * zoom,
      w: mmToWorld(pending.w) * zoom,
      h: mmToWorld(pending.h) * zoom,
    }
    return (
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          data-figure-pending=""
          className="pointer-events-none absolute border border-dashed border-dim"
          style={{ left: r.left, top: r.top, width: r.w, height: r.h }}
        />
      </div>
    )
  }
  if (!hasFrame) return null
  const idle = tool === 'select' && !spaceDown && cropTargetId == null && kind === 'none'
  const toScreen = (r: { x: number; y: number; w: number; h: number }) => ({
    left: panX + mmToWorld(r.x) * zoom,
    top: panY + mmToWorld(r.y) * zoom,
    w: mmToWorld(r.w) * zoom,
    h: mmToWorld(r.h) * zoom,
  })
  const box = toScreen(o)
  const pv = preview ? toScreen(preview) : null
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <EdgeStrips
        attr="data-figure-edge"
        box={box}
        idle={idle}
        active={active}
        lead={FIGURE_HIT_IN}
        onStart={(e, edge) => startFigureEdgeResize(e, edge, o.id)}
      />
      {pv && preview && (
        <>
          <div
            data-figure-preview=""
            className="pointer-events-none absolute border border-dashed border-dim"
            style={{ left: pv.left, top: pv.top, width: pv.w, height: pv.h }}
          />
          <EdgeDimensions dimsAttr="data-figure-dims" box={pv} wMm={preview.w} hMm={preview.h} />
        </>
      )}
    </div>
  )
}

/** 四条边的命中区 + 悬停把手（屏幕空间；页面与单图共用）。`box` 是被拖的矩形在舞台里的位置（px） */
function EdgeStrips({
  attr,
  box,
  idle,
  active,
  lead = HIT_PX / 2,
  onStart,
}: {
  attr: 'data-page-edge' | 'data-figure-edge'
  box: { left: number; top: number; w: number; h: number }
  idle: boolean
  active: PageEdge | null
  /** 命中区压进被量矩形里的深度（px，命中区从边线内侧 `lead` 起、向外共 `HIT_PX` 宽）：页面边对称（`HIT_PX / 2`），单图只压进 `FIGURE_HIT_IN` */
  lead?: number
  onStart: (e: React.PointerEvent, edge: PageEdge) => void
}) {
  const { left: panX, top: panY, w, h } = box
  const strip = (edge: PageEdge, style: CSSProperties, line: string, grip: string): ReactNode => (
    <div
      key={edge}
      {...{ [attr]: edge }}
      data-active={active === edge || undefined}
      className={cn(
        'group absolute',
        edge === 'l' || edge === 'r' ? 'cursor-ew-resize' : 'cursor-ns-resize',
        idle || active === edge ? 'pointer-events-auto' : 'pointer-events-none',
      )}
      style={{ ...style, '--lead': `${lead}px` } as CSSProperties}
      onPointerDown={(e) => onStart(e, edge)}
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
    <>
      {strip('l', { left: panX - lead, top: panY, width: HIT_PX, height: h }, 'inset-y-0 left-[var(--lead)] w-px -translate-x-1/2', 'left-[var(--lead)] top-1/2 h-6 w-1 -translate-x-1/2 -translate-y-1/2')}
      {strip('r', { left: panX + w - lead, top: panY, width: HIT_PX, height: h }, 'inset-y-0 left-[var(--lead)] w-px -translate-x-1/2', 'left-[var(--lead)] top-1/2 h-6 w-1 -translate-x-1/2 -translate-y-1/2')}
      {strip('t', { left: panX, top: panY - lead, width: w, height: HIT_PX }, 'inset-x-0 top-[var(--lead)] h-px -translate-y-1/2', 'left-1/2 top-[var(--lead)] h-1 w-6 -translate-x-1/2 -translate-y-1/2')}
      {strip('b', { left: panX, top: panY + h - lead, width: w, height: HIT_PX }, 'inset-x-0 top-[var(--lead)] h-px -translate-y-1/2', 'left-1/2 top-[var(--lead)] h-1 w-6 -translate-x-1/2 -translate-y-1/2')}
    </>
  )
}

/** 数字（11px）与尺寸线之间的间隙（px）、数字盒的厚度 */
const TEXT_GAP = 3
const TEXT_H = 14

/**
 * 拖边时的机械制图式尺寸标注：页面上方一条横向、左侧一条竖向、两端带箭头的尺寸线（引出线 + 双箭头），
 * 数字写在**尺寸线中点、线与页面之间**（横线的数字在线下方，竖线的数字转 -90° 在线右侧），离线 `TEXT_GAP` px，
 * 不压线、不断线、**无框无底色**；宽、高两条线都显示各自的数字，被拖那一维的线实心、另一条淡。
 * 页面边超出可视区时线段夹在可视区内、数字居中于可见段（`pageEdgeReadout.dimSpan`）。
 * 吸上栏宽时栏宽名跟在宽度数字后面。**画布叠加层**：不进文档、不进导出、不碰 SVG 预览平面；
 * 只在拖动中挂（平时不挂 `<svg>`——e2e 有「舞台里第一个 svg」这类等渲染的定位）。
 * 颜色是 `--color-dim`（亮暗两套）。数字是会滚的（与顶栏缩放读数同一条规矩）：逐 mm 连续拖动即时换（duration 0），
 * 吸上 / 离开栏宽、⌥ 按下 / 松开这类离散跳变才滚。
 */
function PageDimensions() {
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const pageW = useDocumentStore((s) => s.doc.page.w)
  const pageH = useDocumentStore((s) => s.doc.page.h)
  return (
    <EdgeDimensions
      dimsAttr="data-page-dims"
      box={{ left: panX, top: panY, w: mmToWorld(pageW) * zoom, h: mmToWorld(pageH) * zoom }}
      wMm={pageW}
      hMm={pageH}
    />
  )
}

/** 尺寸标注本体（页面与单图共用）：`box` 是被量的矩形在舞台里的位置（px），`wMm` / `hMm` 是它此刻的尺寸 */
function EdgeDimensions({
  dimsAttr,
  box,
  wMm: pageW,
  hMm: pageH,
}: {
  dimsAttr: 'data-page-dims' | 'data-figure-dims'
  box: { left: number; top: number; w: number; h: number }
  wMm: number
  hMm: number
}) {
  const { t, i18n } = useTranslation('workspace')
  const pageEdge = useInteractionStore((s) => s.pageEdge)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  if (!pageEdge) return null
  const { left: L, top: T, w: W, h: H } = box
  const y = Math.max(14, T - DIM_GAP)
  const x = Math.max(14, L - DIM_GAP)
  const showW = pageEdge.edge === 'l' || pageEdge.edge === 'r'
  const gw = dimSpan(L, L + W, viewW)
  const gh = dimSpan(T, T + H, viewH)
  const number = (kind: 'width' | 'height', mm: number) => (
    <>
      <span aria-hidden>{t(`pageEdge.${kind}Prefix`).replace(/\s+$/, '\u00a0')}</span>
      <Numbers
        value={Math.round(mm * 10) / 10}
        duration={pageEdge.rolls ? undefined : 0}
        format={{ useGrouping: false, maximumFractionDigits: 1 }}
        locale={i18n.language}
        label={t(`pageEdge.${kind}`, { mm: mmText(mm) })}
      />
      <span aria-hidden>{MM_SUFFIX}</span>
    </>
  )
  const textBase = 'type-number absolute whitespace-nowrap font-medium text-dim'
  const arrow = (a: number, b: number, at: number, horiz: boolean, startArrow: boolean, endArrow: boolean) => {
    // 两端对称内缩 4 px（线段中点 = 可见段中点，数字就居中在线的中点）；被夹到可视区边缘的那一端不是页面真正的边：不画箭头
    const [x1, y1, x2, y2] = horiz ? [a, at, b, at] : [at, a, at, b]
    return (
      <line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke="currentColor"
        strokeWidth={1}
        markerStart={startArrow ? 'url(#page-dim-arrow)' : undefined}
        markerEnd={endArrow ? 'url(#page-dim-arrow)' : undefined}
      />
    )
  }
  return (
    <div {...{ [dimsAttr]: '' }} className="pointer-events-none absolute inset-0 overflow-hidden">
      <svg aria-hidden className="pointer-events-none absolute inset-0 h-full w-full overflow-visible text-dim">
        <defs>
          <marker id="page-dim-arrow" viewBox="0 0 10 10" refX={5} refY={5} markerWidth={9} markerHeight={9} orient="auto-start-reverse">
            <path d="M0 1.5L10 5L0 8.5" fill="none" stroke="currentColor" strokeWidth={1.4} />
          </marker>
        </defs>
        <g data-dim="w" opacity={showW ? 1 : 0.45}>
          <line x1={L} y1={T - 4} x2={L} y2={y - 8} stroke="currentColor" strokeWidth={0.8} />
          <line x1={L + W} y1={T - 4} x2={L + W} y2={y - 8} stroke="currentColor" strokeWidth={0.8} />
          {gw && arrow(gw.a + DIM_INSET, gw.b - DIM_INSET, y, true, gw.startArrow, gw.endArrow)}
        </g>
        <g data-dim="h" opacity={showW ? 0.45 : 1}>
          <line x1={L - 4} y1={T} x2={x - 8} y2={T} stroke="currentColor" strokeWidth={0.8} />
          <line x1={L - 4} y1={T + H} x2={x - 8} y2={T + H} stroke="currentColor" strokeWidth={0.8} />
          {gh && arrow(gh.a + DIM_INSET, gh.b - DIM_INSET, x, false, gh.startArrow, gh.endArrow)}
        </g>
      </svg>
      {gw && (
        <div
          data-dim-readout="width"
          data-readout-edge={pageEdge.edge}
          className={textBase}
          style={{
            left: gw.center,
            top: y + TEXT_GAP,
            height: TEXT_H,
            lineHeight: `${TEXT_H}px`,
            transform: 'translateX(-50%)',
            opacity: showW ? 1 : 0.45,
          }}
        >
          {number('width', pageW)}
          {pageEdge.snapLabel && showW && (
            <span data-dim-snap="" className="ml-1">
              · {pageEdge.snapLabel}
            </span>
          )}
        </div>
      )}
      {gh && (
        <div
          data-dim-readout="height"
          data-readout-edge={pageEdge.edge}
          className={textBase}
          style={{
            left: x + TEXT_GAP + TEXT_H / 2,
            top: gh.center,
            height: TEXT_H,
            lineHeight: `${TEXT_H}px`,
            transform: 'translate(-50%, -50%) rotate(-90deg)',
            opacity: showW ? 0.45 : 1,
          }}
        >
          {number('height', pageH)}
        </div>
      )}
    </div>
  )
}
