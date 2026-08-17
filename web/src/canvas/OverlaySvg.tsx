import { Fragment } from 'react'
import type { ManifestElement } from '@/lib/api'
import type { Rect4 } from '@/lib/axesLayout'
import { MM_PER_PT } from '@/lib/units'
import { arrowEndpointsOf, geomTarget, panelFullRect, resolveGroup } from '@/lib/elementGeom'
import { ALL_DIRS, boundsOf, dirsFor, type ResizeDir } from '@/lib/geometry'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import {
  mmToPx,
  mmToViewX,
  mmToViewY,
  mmToWorld,
  useViewportStore,
  type ViewTransform,
} from '@/store/viewportStore'
import { useRenderStore } from '@/store/renderStore'
import type { CanvasObject, LinearObject, PanelObject } from '@/types/document'
import { isLinear, lineEndpoints, objectRotation, panelRotation } from '@/types/document'
import {
  startArrowDrag,
  startAxesDrag,
  startCropDrag,
  startEndpointDrag,
  startGroupResize,
  startGuideDrag,
  startResizeDrag,
} from './interactions'

const SEL = 'var(--color-sel)'
const HANDLE = 7

const CURSORS: Record<ResizeDir, string> = {
  nw: 'nwse-resize',
  se: 'nwse-resize',
  ne: 'nesw-resize',
  sw: 'nesw-resize',
  n: 'ns-resize',
  s: 'ns-resize',
  e: 'ew-resize',
  w: 'ew-resize',
}

interface Box {
  x: number
  y: number
  w: number
  h: number
}

const toScreen = (o: { x: number; y: number; w: number; h: number }, t: ViewTransform): Box => ({
  x: mmToViewX(o.x, t),
  y: mmToViewY(o.y, t),
  w: mmToPx(o.w, t),
  h: mmToPx(o.h, t),
})

function handlePos(box: Box, dir: ResizeDir) {
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  const x = dir.includes('w') ? box.x : dir.includes('e') ? box.x + box.w : cx
  const y = dir.includes('n') ? box.y : dir.includes('s') ? box.y + box.h : cy
  return { x, y }
}

/**
 * 对象的任意角度旋转（text/arrow/shape）：x/y/w/h 恒为未旋转包围盒，图形只靠
 * ObjectView 的 CSS rotate 呈现，所以覆盖层的框与手柄必须绕同一个中心转过去，
 * 否则转 90° 后手柄离真实图形约半个对角线。写法与下面 ElementBoxes 的 spin 一致。
 */
function spinOf(o: CanvasObject, box: Box): string | undefined {
  const rot = objectRotation(o)
  return rot ? `rotate(${rot} ${box.x + box.w / 2} ${box.y + box.h / 2})` : undefined
}

/** 顺时针八方位环，用于把手柄光标按对象旋转换档（45° 一档，四舍五入到最近的一档） */
const CURSOR_RING: ResizeDir[] = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw']

function cursorFor(dir: ResizeDir, deg: number): string {
  if (!deg) return CURSORS[dir]
  const i = CURSOR_RING.indexOf(dir)
  const steps = Math.round(deg / 45)
  return CURSORS[CURSOR_RING[(((i + steps) % 8) + 8) % 8]]
}

/**
 * 屏幕坐标系覆盖层：选择框、手柄、吸附参考线、框选、参考线、裁剪。
 * 与世界层分离，保证任何缩放下线宽恒为 1px。
 */
export function OverlaySvg() {
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const originX = useViewportStore((s) => s.originX)
  const originY = useViewportStore((s) => s.originY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  const t: ViewTransform = { zoom, panX, panY, originX, originY }
  const objects = useDocumentStore((s) => s.doc.objects)
  const guides = useDocumentStore((s) => s.doc.guides)
  const page = useDocumentStore((s) => s.doc.page)
  const selectedIds = useSelectionStore((s) => s.ids)
  const marquee = useInteractionStore((s) => s.marquee)
  const draft = useInteractionStore((s) => s.draft)
  const snapXs = useInteractionStore((s) => s.snapXs)
  const snapYs = useInteractionStore((s) => s.snapYs)
  const hoverId = useInteractionStore((s) => s.hoverId)
  const pendingGuide = useInteractionStore((s) => s.pendingGuide)
  const dragKind = useInteractionStore((s) => s.kind)
  const cropTargetId = useUiStore((s) => s.cropTargetId)
  const guidesLocked = useUiStore((s) => s.guidesLocked)
  const editingTextId = useUiStore((s) => s.editingTextId)
  const elementPanelId = useUiStore((s) => s.elementPanelId)

  const selected = objects.filter((o) => selectedIds.includes(o.id) && !o.hidden)
  const cropTarget = objects.find((o) => o.id === cropTargetId && o.type === 'panel')
  const elementPanel = objects.find((o) => o.id === elementPanelId && o.type === 'panel')
  // 图内编辑时不显示对象缩放手柄，避免和元素选择混淆
  const single = selected.length === 1 && !cropTarget && !elementPanel ? selected[0] : null
  const groupBounds = selected.length > 1 ? boundsOf(selected) : null
  const hovered =
    hoverId && !selectedIds.includes(hoverId) && dragKind === 'none'
      ? objects.find((o) => o.id === hoverId && !o.hidden)
      : null

  const pageBox = toScreen({ x: 0, y: 0, ...page }, t)

  return (
    <svg
      className="pointer-events-none absolute inset-0"
      width={viewW}
      height={viewH}
      style={{ shapeRendering: 'crispEdges' }}
    >
      {/* 用户参考线：细线负责显示，粗透明线负责命中；拖出页面即删除 */}
      {guides.map((g, i) => {
        const p = (g.axis === 'x' ? mmToViewX(g.pos, t) : mmToViewY(g.pos, t)) + 0.5
        const coords =
          g.axis === 'x'
            ? { x1: p, y1: 0, x2: p, y2: viewH }
            : { x1: 0, y1: p, x2: viewW, y2: p }
        return (
          <g key={`guide-${i}`}>
            <line {...coords} stroke="#2AA9A0" strokeWidth={1} />
            {!guidesLocked && (
              <line
                {...coords}
                stroke="transparent"
                strokeWidth={7}
                style={{ pointerEvents: 'stroke', cursor: g.axis === 'x' ? 'ew-resize' : 'ns-resize' }}
                onPointerDown={(e) => startGuideDrag(e, g.axis, i)}
              />
            )}
          </g>
        )
      })}

      {/* 从标尺拖出中的参考线 */}
      {pendingGuide && (
        <line
          x1={pendingGuide.axis === 'x' ? mmToViewX(pendingGuide.pos, t) + 0.5 : 0}
          y1={pendingGuide.axis === 'x' ? 0 : mmToViewY(pendingGuide.pos, t) + 0.5}
          x2={pendingGuide.axis === 'x' ? mmToViewX(pendingGuide.pos, t) + 0.5 : viewW}
          y2={pendingGuide.axis === 'x' ? viewH : mmToViewY(pendingGuide.pos, t) + 0.5}
          stroke="#2AA9A0"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
      )}

      {/* hover 预示；箭头 / 直线沿线段本身描示，不画与线对不上的包围盒框 */}
      {hovered &&
        (isLinear(hovered) ? (
          <LinearOutline obj={hovered} t={t} opacity={0.4} />
        ) : (
          <rect
            {...rectAttrs(toScreen(hovered, t))}
            transform={spinOf(hovered, toScreen(hovered, t))}
            fill="none"
            stroke={SEL}
            strokeOpacity={0.4}
            strokeWidth={1}
          />
        ))}

      {/* 选择框；箭头 / 直线同上——只有沿线的描示 + 端点手柄，没有矩形外框 */}
      {!cropTarget &&
        selected.map((o) =>
          isLinear(o) ? (
            <LinearOutline key={o.id} obj={o} t={t} />
          ) : (
            <rect
              key={o.id}
              {...rectAttrs(toScreen(o, t))}
              transform={spinOf(o, toScreen(o, t))}
              fill="none"
              stroke={SEL}
              strokeWidth={1}
              strokeDasharray={o.id === editingTextId ? '3 2' : undefined}
            />
          ),
        )}

      {groupBounds && !cropTarget && (
        <rect
          {...rectAttrs(toScreen(groupBounds, t))}
          fill="none"
          stroke={SEL}
          strokeWidth={1}
          strokeOpacity={0.45}
          strokeDasharray="4 3"
        />
      )}

      {/* 缩放手柄 + 线状对象端点（箭头 / 直线）：整组绕包围盒中心转到对象朝向 */}
      {single && !single.locked && (
        <g transform={spinOf(single, toScreen(single, t))}>
          {dirsFor(single).map((dir) => {
            const p = handlePos(toScreen(single, t), dir)
            return (
              <rect
                key={dir}
                data-handle={dir}
                x={p.x - HANDLE / 2}
                y={p.y - HANDLE / 2}
                width={HANDLE}
                height={HANDLE}
                fill="#fff"
                stroke={SEL}
                strokeWidth={1}
                style={{ pointerEvents: 'all', cursor: cursorFor(dir, objectRotation(single)) }}
                onPointerDown={(e) => startResizeDrag(e, single.id, dir)}
              />
            )
          })}
          {isLinear(single) && <LinearEndpoints obj={single} t={t} />}
        </g>
      )}

      {/* 吸附参考线 */}
      {snapXs.map((x, i) => (
        <line
          key={`sx-${i}`}
          x1={mmToViewX(x, t) + 0.5}
          y1={pageBox.y - 24}
          x2={mmToViewX(x, t) + 0.5}
          y2={pageBox.y + pageBox.h + 24}
          stroke={SEL}
          strokeWidth={1}
        />
      ))}
      {snapYs.map((y, i) => (
        <line
          key={`sy-${i}`}
          x1={pageBox.x - 24}
          y1={mmToViewY(y, t) + 0.5}
          x2={pageBox.x + pageBox.w + 24}
          y2={mmToViewY(y, t) + 0.5}
          stroke={SEL}
          strokeWidth={1}
        />
      ))}

      {/* 框选 */}
      {marquee && (
        <rect
          {...rectAttrs(toScreen(marquee, t))}
          fill={SEL}
          fillOpacity={0.07}
          stroke={SEL}
          strokeWidth={1}
          strokeDasharray="3 2"
        />
      )}

      {/* 绘制预览：箭头 / 直线画最终那条线（含箭头帽），其余仍是虚线框 */}
      {draft &&
        (draft.start && draft.end ? (
          <DraftLinePreview draft={draft} t={t} />
        ) : (
          <rect
            {...rectAttrs(toScreen(draft, t))}
            fill="none"
            stroke={SEL}
            strokeWidth={1}
            strokeDasharray="3 2"
          />
        ))}

      {cropTarget && cropTarget.type === 'panel' && <CropFrame obj={cropTarget} t={t} />}

      {elementPanel?.type === 'panel' && <ElementBoxes panel={elementPanel} t={t} />}
    </svg>
  )
}

/**
 * 箭头 / 直线的拖画预览：直接按新对象的最终样式画（默认色 + 1pt 线宽 +
 * 箭头帽），几何与 ArrowView 同一套（帽长 4×线宽、帽半宽 1.7×线宽、
 * 实心三角端线段回缩 0.75×帽长）——预览即成品。
 */
function DraftLinePreview({
  draft,
  t,
}: {
  draft: { tool: string; start?: { x: number; y: number }; end?: { x: number; y: number } }
  t: ViewTransform
}) {
  const a = { x: mmToViewX(draft.start!.x, t), y: mmToViewY(draft.start!.y, t) }
  const b = { x: mmToViewX(draft.end!.x, t), y: mmToViewY(draft.end!.y, t) }
  const sw = Math.max(mmToPx(MM_PER_PT, t), 0.5) // 新对象默认 strokePt=1
  const color = '#1B1B18' // 与 startDraw 落对象的默认色同一常量语义
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const isArrow = draft.tool === 'arrow'
  const headLen = sw * 4
  const headHalf = sw * 1.7
  const trim = isArrow ? headLen * 0.75 : 0
  const p2 = { x: b.x - ux * trim, y: b.y - uy * trim }
  return (
    <g style={{ shapeRendering: 'geometricPrecision' }}>
      <line
        x1={a.x}
        y1={a.y}
        x2={p2.x}
        y2={p2.y}
        stroke={color}
        strokeWidth={sw}
        strokeLinecap="round"
      />
      {isArrow && len > headLen && (
        <polygon
          points={`${b.x},${b.y} ${b.x - ux * headLen + -uy * headHalf},${b.y - uy * headLen + ux * headHalf} ${b.x - ux * headLen - -uy * headHalf},${b.y - uy * headLen - ux * headHalf}`}
          fill={color}
        />
      )}
    </g>
  )
}

function rectAttrs(box: Box) {
  // 半像素偏移让 1px 描边落在像素网格上
  return {
    x: Math.round(box.x) + 0.5,
    y: Math.round(box.y) + 0.5,
    width: Math.max(Math.round(box.w) - 1, 1),
    height: Math.max(Math.round(box.h) - 1, 1),
  }
}

/**
 * 箭头 / 直线的 hover / 选中描示：一条沿真实端点的细线（代替包围盒矩形——
 * 斜线的包围盒是一大块与线对不上的矩形，Illustrator 语义是描线本身）。
 * 端点为未旋转包围盒比例坐标，旋转由与选择框同一套 spinOf 处理。
 */
function LinearOutline({
  obj,
  t,
  opacity,
}: {
  obj: LinearObject
  t: ViewTransform
  opacity?: number
}) {
  const ends = lineEndpoints(obj)
  const box = toScreen(obj, t)
  return (
    <line
      x1={box.x + ends.start.rx * box.w}
      y1={box.y + ends.start.ry * box.h}
      x2={box.x + ends.end.rx * box.w}
      y2={box.y + ends.end.ry * box.h}
      transform={spinOf(obj, box)}
      stroke={SEL}
      strokeOpacity={opacity}
      strokeWidth={1.5}
      strokeLinecap="round"
      style={{ shapeRendering: 'geometricPrecision' }}
    />
  )
}

/**
 * 箭头 / 直线的两个端点圆圈：抓着它掰方向（这两类对象没有缩放手柄）。
 * 端点是未旋转包围盒里的比例坐标，转到对象朝向由外层那个 spin 组负责。
 */
function LinearEndpoints({ obj, t }: { obj: LinearObject; t: ViewTransform }) {
  const ends = lineEndpoints(obj)
  const pts: { key: 'start' | 'end'; x: number; y: number }[] = [
    {
      key: 'start',
      x: mmToViewX(obj.x + ends.start.rx * obj.w, t),
      y: mmToViewY(obj.y + ends.start.ry * obj.h, t),
    },
    {
      key: 'end',
      x: mmToViewX(obj.x + ends.end.rx * obj.w, t),
      y: mmToViewY(obj.y + ends.end.ry * obj.h, t),
    },
  ]
  return (
    <>
      {pts.map((p) => (
        <circle
          key={p.key}
          data-endpoint={p.key}
          cx={p.x}
          cy={p.y}
          r={4.5}
          fill="#fff"
          stroke={SEL}
          strokeWidth={1}
          style={{ pointerEvents: 'all', cursor: 'crosshair', shapeRendering: 'geometricPrecision' }}
          onPointerDown={(e) => startEndpointDrag(e, obj.id, p.key)}
        />
      ))}
    </>
  )
}

/** 裁剪模式：框外压暗，八个手柄改裁剪比例，框内可拖动改取景位置 */
function CropFrame({ obj, t }: { obj: CanvasObject; t: ViewTransform }) {
  const box = toScreen(obj, t)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)

  return (
    <>
      <path
        d={`M0,0 H${viewW} V${viewH} H0 Z M${box.x},${box.y} V${box.y + box.h} H${box.x + box.w} V${box.y} Z`}
        fill="rgba(27,27,24,.34)"
        fillRule="evenodd"
        style={{ pointerEvents: 'all' }}
      />
      <rect
        {...rectAttrs(box)}
        fill="transparent"
        stroke="#fff"
        strokeWidth={1}
        style={{ pointerEvents: 'all', cursor: 'move' }}
        onPointerDown={(e) => startCropDrag(e, obj.id, 'move')}
      />
      {/* 三分线 */}
      {[1, 2].map((i) => (
        <Fragment key={i}>
          <line
            x1={box.x + (box.w * i) / 3}
            y1={box.y}
            x2={box.x + (box.w * i) / 3}
            y2={box.y + box.h}
            stroke="#fff"
            strokeOpacity={0.35}
            strokeWidth={1}
          />
          <line
            x1={box.x}
            y1={box.y + (box.h * i) / 3}
            x2={box.x + box.w}
            y2={box.y + (box.h * i) / 3}
            stroke="#fff"
            strokeOpacity={0.35}
            strokeWidth={1}
          />
        </Fragment>
      ))}
      {ALL_DIRS.map((dir) => {
        const p = handlePos(box, dir)
        return (
          <rect
            key={dir}
            x={p.x - 5}
            y={p.y - 5}
            width={10}
            height={10}
            fill="#fff"
            stroke="rgba(27,27,24,.35)"
            strokeWidth={1}
            style={{ pointerEvents: 'all', cursor: CURSORS[dir] }}
            onPointerDown={(e) => startCropDrag(e, obj.id, dir)}
          />
        )
      })}
    </>
  )
}

/** 图内元素的 hover / 选中框；拖动时跟随乐观位移 */
function ElementBoxes({ panel, t }: { panel: PanelObject; t: ViewTransform }) {
  const manifest = useRenderStore((s) => s.byFile[panel.fileId]?.manifest)
  const hoverGid = useInteractionStore((s) => s.hoverGid)
  const gidDrag = useInteractionStore((s) => s.gidDrag)
  const preview = useInteractionStore((s) => s.elementPreview)
  const arrowPreview = useInteractionStore((s) => s.arrowPreview)
  const selectedGids = useUiStore((s) => s.selectedGids)
  const selectedGid = selectedGids.at(-1) ?? null
  if (!manifest) return null

  const full = panelFullRect(panel)
  const toBox = (r: Rect4) =>
    toScreen({ x: full.x + r[0] * full.w, y: full.y + r[1] * full.h, w: r[2] * full.w, h: r[3] * full.h }, t)
  /**
   * 元素的框一律画在它的几何落点上：位图没有自己的几何属性，
   * 拖它动的是宿主子图，框也就该是宿主子图的 bbox（与单选时一致）。
   */
  const resolve = (gid: string) => {
    const el = manifest.elements.find((e) => e.gid === gid)
    if (!el || el.gid === 'figure') return null
    const target = geomTarget(manifest, el)
    const [bx, by, bw, bh] = preview?.boxes[target.gid] ?? target.bbox
    const dx = gidDrag?.gid === target.gid ? gidDrag.dfx : 0
    const dy = gidDrag?.gid === target.gid ? gidDrag.dfy : 0
    return { key: target.gid, target, box: toBox([bx + dx, by + dy, bw, bh]) }
  }

  // 所有选中元素画同一种框；位图与宿主子图落在同一个几何目标上，只画一次
  const picked = new Map<string, { target: ManifestElement; box: Box }>()
  for (const gid of selectedGids) {
    const r = resolve(gid)
    if (r) picked.set(r.key, { target: r.target, box: r.box })
  }
  const hovered = hoverGid ? resolve(hoverGid) : null
  const hover = hovered && !picked.has(hovered.key) ? hovered : null
  const panelBox = toScreen(panel, t)
  const primary = selectedGid ? resolve(selectedGid) : null
  const layout = { width: mmToWorld(full.w), height: mmToWorld(full.h) }
  // 内容坐标系里算好的框，整组绕包围盒中心转到面板当前的朝向
  const rot = panelRotation(panel)
  const spin = rot
    ? `rotate(${rot} ${panelBox.x + panelBox.w / 2} ${panelBox.y + panelBox.h / 2})`
    : undefined
  // 多选且全是子图 → 组包围框接管手柄，成组缩放
  const group = resolveGroup(panel, manifest, selectedGids)
  const groupBox = group ? toBox(preview?.group ?? group.box) : null
  // 单选子图仍是它自己的八个手柄
  const axesBox = !groupBox && primary?.target.resizable ? primary.box : null
  // 单选图内独立箭头 → 两个端点手柄（画布原生箭头的同款交互）
  const arrowEl = !groupBox && primary?.target.arrow_endpoints ? primary.target : null
  const arrowPts = arrowEl ? arrowEndpointsOf(panel, arrowEl) : null
  const toPoint = (p: [number, number]) => {
    const b = toBox([p[0], p[1], 0, 0])
    return { x: b.x, y: b.y }
  }

  /**
   * 图内独立箭头的 hover / 选中描示：沿真实端点画线（画布箭头 LinearOutline 的
   * 同款语义）——斜线的 bbox 是一大块与线对不上的矩形，不画它。
   * 整体拖动跟随乐观位移；拖单端点时下方的虚线预览（arrowPreview）接管，这里不画。
   */
  const arrowOutline = (target: ManifestElement, opacity?: number) => {
    const pts = arrowEndpointsOf(panel, target)
    if (!pts || arrowPreview?.gid === target.gid) return null
    const dx = gidDrag?.gid === target.gid ? gidDrag.dfx : 0
    const dy = gidDrag?.gid === target.gid ? gidDrag.dfy : 0
    const a = toPoint([pts[0][0] + dx, pts[0][1] + dy])
    const b = toPoint([pts[1][0] + dx, pts[1][1] + dy])
    return (
      <line
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke="var(--color-accent)"
        strokeOpacity={opacity}
        strokeWidth={1.5}
        strokeLinecap="round"
        style={{ shapeRendering: 'geometricPrecision' }}
      />
    )
  }

  return (
    <>
      {/* 编辑态的面板轮廓，提示「现在在图内」 */}
      <rect
        {...rectAttrs(panelBox)}
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth={1}
        strokeDasharray="4 3"
        strokeOpacity={0.7}
      />
      <g transform={spin}>
        {hover &&
          (hover.target.arrow_endpoints ? (
            arrowOutline(hover.target, 0.5)
          ) : (
            <rect
              {...rectAttrs(hover.box)}
              fill="var(--color-accent)"
              fillOpacity={0.06}
              stroke="var(--color-accent)"
              strokeWidth={1}
              strokeOpacity={0.55}
            />
          ))}
        {[...picked].map(([key, r]) =>
          r.target.arrow_endpoints ? (
            <Fragment key={key}>{arrowOutline(r.target)}</Fragment>
          ) : (
            <rect
              key={key}
              {...rectAttrs(r.box)}
              fill="var(--color-accent)"
              fillOpacity={0.06}
              stroke="var(--color-accent)"
              strokeWidth={1}
            />
          ),
        )}

        {/* 组包围框：只有细虚线 + 手柄，不加底色，与单元素选中框区分 */}
        {groupBox && (
          <rect
            {...rectAttrs(groupBox)}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth={1}
            strokeDasharray="4 2"
          />
        )}

        {groupBox &&
          group &&
          ALL_DIRS.map((dir) => (
            <Handle
              key={dir}
              box={groupBox}
              dir={dir}
              onPointerDown={(e) => startGroupResize(e, panel, group, layout, dir)}
            />
          ))}

        {axesBox &&
          primary &&
          ALL_DIRS.map((dir) => (
            <Handle
              key={dir}
              box={axesBox}
              dir={dir}
              onPointerDown={(e) => startAxesDrag(e, panel, primary.target, layout, dir)}
            />
          ))}

        {arrowEl && arrowPts && (
          <>
            {arrowPreview?.gid === arrowEl.gid && (
              <line
                x1={toPoint(arrowPreview.a).x}
                y1={toPoint(arrowPreview.a).y}
                x2={toPoint(arrowPreview.b).x}
                y2={toPoint(arrowPreview.b).y}
                stroke="var(--color-accent)"
                strokeWidth={1}
                strokeDasharray="4 3"
              />
            )}
            {(
              [
                ['start', arrowPreview?.gid === arrowEl.gid ? arrowPreview.a : arrowPts[0]],
                ['end', arrowPreview?.gid === arrowEl.gid ? arrowPreview.b : arrowPts[1]],
              ] as const
            ).map(([key, p]) => {
              const shifted: [number, number] =
                gidDrag?.gid === arrowEl.gid
                  ? [p[0] + gidDrag.dfx, p[1] + gidDrag.dfy]
                  : [p[0], p[1]]
              const pt = toPoint(shifted)
              return (
                <circle
                  key={key}
                  data-arrow-endpoint={key}
                  cx={pt.x}
                  cy={pt.y}
                  r={4.5}
                  fill="#fff"
                  stroke="var(--color-accent)"
                  strokeWidth={1}
                  style={{
                    pointerEvents: 'all',
                    cursor: 'crosshair',
                    shapeRendering: 'geometricPrecision',
                  }}
                  onPointerDown={(e) => startArrowDrag(e, panel, arrowEl, layout, key)}
                />
              )
            })}
          </>
        )}
      </g>
    </>
  )
}

/** 图内元素 / 组包围框的缩放手柄 */
function Handle({
  box,
  dir,
  onPointerDown,
}: {
  box: Box
  dir: ResizeDir
  onPointerDown: (e: React.PointerEvent) => void
}) {
  const p = handlePos(box, dir)
  return (
    <rect
      x={p.x - HANDLE / 2}
      y={p.y - HANDLE / 2}
      width={HANDLE}
      height={HANDLE}
      fill="#fff"
      stroke="var(--color-accent)"
      strokeWidth={1}
      style={{ pointerEvents: 'all', cursor: CURSORS[dir] }}
      onPointerDown={onPointerDown}
    />
  )
}
