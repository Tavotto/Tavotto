import { Fragment } from 'react'
import type { Rect4 } from '@/lib/axesLayout'
import { geomTarget, resolveGroup } from '@/lib/elementGeom'
import { boundsOf, type ResizeDir } from '@/lib/geometry'
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
import type { ArrowObject, CanvasObject, PanelObject } from '@/types/document'
import { panelContentSize, panelRotation } from '@/types/document'
import {
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

const ALL_DIRS: ResizeDir[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

function dirsFor(obj: CanvasObject): ResizeDir[] {
  if (obj.type === 'text') return ['w', 'e']
  if (obj.type === 'shape' && obj.shape === 'line') return ['w', 'e']
  if (obj.type === 'arrow') return []
  return ALL_DIRS
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

      {/* hover 预示 */}
      {hovered && (
        <rect
          {...rectAttrs(toScreen(hovered, t))}
          fill="none"
          stroke={SEL}
          strokeOpacity={0.4}
          strokeWidth={1}
        />
      )}

      {/* 选择框 */}
      {!cropTarget &&
        selected.map((o) => (
          <rect
            key={o.id}
            {...rectAttrs(toScreen(o, t))}
            fill="none"
            stroke={SEL}
            strokeWidth={1}
            strokeDasharray={o.id === editingTextId ? '3 2' : undefined}
          />
        ))}

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

      {/* 缩放手柄 */}
      {single &&
        !single.locked &&
        dirsFor(single).map((dir) => {
          const p = handlePos(toScreen(single, t), dir)
          return (
            <rect
              key={dir}
              x={p.x - HANDLE / 2}
              y={p.y - HANDLE / 2}
              width={HANDLE}
              height={HANDLE}
              fill="#fff"
              stroke={SEL}
              strokeWidth={1}
              style={{ pointerEvents: 'all', cursor: CURSORS[dir] }}
              onPointerDown={(e) => startResizeDrag(e, single.id, dir)}
            />
          )
        })}

      {/* 箭头端点 */}
      {single?.type === 'arrow' && !single.locked && <ArrowEndpoints obj={single} t={t} />}

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

      {/* 绘制预览 */}
      {draft && (
        <rect
          {...rectAttrs(toScreen(draft, t))}
          fill="none"
          stroke={SEL}
          strokeWidth={1}
          strokeDasharray="3 2"
        />
      )}

      {cropTarget && cropTarget.type === 'panel' && <CropFrame obj={cropTarget} t={t} />}

      {elementPanel?.type === 'panel' && <ElementBoxes panel={elementPanel} t={t} />}
    </svg>
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

function ArrowEndpoints({ obj, t }: { obj: ArrowObject; t: ViewTransform }) {
  const pts: { key: 'start' | 'end'; x: number; y: number }[] = [
    {
      key: 'start',
      x: mmToViewX(obj.x + obj.start.rx * obj.w, t),
      y: mmToViewY(obj.y + obj.start.ry * obj.h, t),
    },
    {
      key: 'end',
      x: mmToViewX(obj.x + obj.end.rx * obj.w, t),
      y: mmToViewY(obj.y + obj.end.ry * obj.h, t),
    },
  ]
  return (
    <>
      {pts.map((p) => (
        <circle
          key={p.key}
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

/**
 * 面板未被裁剪时占据的完整显示矩形（mm，**内容坐标系**）——manifest 的分数
 * 坐标就摊在它上面。面板旋转时内容与包围盒长宽互换，且内容以包围盒中心为
 * 中心，所以这里统一按中心推算；画框时整组再绕同一个中心转回去。
 */
function fullRect(panel: PanelObject) {
  const c = panel.crop
  const content = panelContentSize(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  return {
    x: cx - content.w / 2 - (c ? (c.x / c.w) * content.w : 0),
    y: cy - content.h / 2 - (c ? (c.y / c.h) * content.h : 0),
    w: content.w / (c?.w ?? 1),
    h: content.h / (c?.h ?? 1),
  }
}

/** 图内元素的 hover / 选中框；拖动时跟随乐观位移 */
function ElementBoxes({ panel, t }: { panel: PanelObject; t: ViewTransform }) {
  const manifest = useRenderStore((s) => s.byFile[panel.fileId]?.manifest)
  const hoverGid = useInteractionStore((s) => s.hoverGid)
  const gidDrag = useInteractionStore((s) => s.gidDrag)
  const preview = useInteractionStore((s) => s.elementPreview)
  const selectedGids = useUiStore((s) => s.selectedGids)
  const selectedGid = selectedGids.at(-1) ?? null
  if (!manifest) return null

  const full = fullRect(panel)
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
  const picked = new Map<string, Box>()
  for (const gid of selectedGids) {
    const r = resolve(gid)
    if (r) picked.set(r.key, r.box)
  }
  const hovered = hoverGid ? resolve(hoverGid) : null
  const hover = hovered && !picked.has(hovered.key) ? hovered.box : null
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
        {hover && (
          <rect
            {...rectAttrs(hover)}
            fill="var(--color-accent)"
            fillOpacity={0.06}
            stroke="var(--color-accent)"
            strokeWidth={1}
            strokeOpacity={0.55}
          />
        )}
        {[...picked].map(([key, box]) => (
          <rect
            key={key}
            {...rectAttrs(box)}
            fill="var(--color-accent)"
            fillOpacity={0.06}
            stroke="var(--color-accent)"
            strokeWidth={1}
          />
        ))}

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
