import type { PointerEvent as ReactPointerEvent } from 'react'
import { msg } from '@/i18n'
import { readGradient, sortedStops } from '@/lib/gradient'
import { dragEndpoint, dragStop, handleGeometry } from '@/lib/gradientHandles'
import { useDocumentStore } from '@/store/documentStore'
import { gradientEditFor, useGradientEditStore } from '@/store/gradientEditStore'
import { useActivePathEdit } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, getTransform, mmToPx, mmToViewX, mmToViewY, snapTolMm, type ViewTransform } from '@/store/viewportStore'
import type { CanvasObject, Gradient, PathObject, ShapeObject } from '@/types/document'
import { trackPointer } from './interactions'

/**
 * 画布上的渐变柄（ADR 0186 第二期）：单选一个带渐变的形状 / 路径、用选择工具时就在画布上出现——
 * 线性 = 起点 → 终点一条线，径向 = 中心 → 半径一条线 + 半径虚线圆；色标是线上的圆点，填色是它自己的颜色。
 * 端点（中心 / 半径）可拖，可吸到框的四角 / 边中点 / 中心，⇧ 锁 45°；色标可沿线拖（不越过邻居，与色带同一个 `moveStop`）。
 * 一次拖动一条历史（`beginTxn / txnUpdate / endTxn`）。编辑锚点与钢笔时不出现。
 *
 * 编哪一侧、选中哪个色标与属性栏的色带共用 `gradientEditStore`：色带上点了哪个，这里就亮哪个，反之亦然。
 */

const SEL = 'var(--color-sel)'
const END = 9
const STOP_R = 5
const hist = () => msg('history.setGradient', undefined, 'inspector')

type Mark = ShapeObject | PathObject
const isMark = (o: CanvasObject | undefined): o is Mark => !!o && (o.type === 'shape' || o.type === 'path')
const field = (t: 'fill' | 'stroke') => (t === 'fill' ? 'fillGradient' : 'strokeGradient')

/** 这个对象此刻要编的是哪一侧：优先用编辑状态里的那一侧，那一侧没有渐变就换另一侧；都没有 = null */
export function handleTarget(o: Mark): 'fill' | 'stroke' | null {
  const want = gradientEditFor(o.id).target
  const has = (t: 'fill' | 'stroke') =>
    !!readGradient(o[field(t)]) && (t === 'fill' ? !!o.fill : o.stroke !== false)
  if (has(want)) return want
  const other = want === 'fill' ? 'stroke' : 'fill'
  return has(other) ? other : null
}

function write(id: string, target: 'fill' | 'stroke', g: Gradient) {
  useDocumentStore.getState().txnUpdate((d) => {
    const o = d.objects.find((x) => x.id === id)
    if (!o || (o.type !== 'shape' && o.type !== 'path')) return
    o[field(target)] = g
    const first = sortedStops(g)[0]?.color
    if (first) {
      if (target === 'fill') o.fill = first
      else o.color = first
    }
  })
}

function startDrag(e: ReactPointerEvent, o: Mark, target: 'fill' | 'stroke', move: (page: [number, number], ev: PointerEvent) => Gradient | null) {
  e.stopPropagation()
  e.preventDefault()
  if (e.button !== 0) return
  const store = useDocumentStore.getState()
  store.beginTxn(hist())
  trackPointer(e, {
    threshold: 1,
    onMove: (ev) => {
      const p = clientToMm(ev.clientX, ev.clientY)
      const next = move([p.x, p.y], ev)
      if (next) write(o.id, target, next)
    },
    onEnd: (moved, _ev, end) => store.endTxn({ discard: !moved || end.cancelled }),
  })
}

export function GradientHandles({ t }: { t: ViewTransform }) {
  const ids = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const tool = useUiStore((s) => s.tool)
  const edit = useGradientEditStore()
  const anchorEdit = useActivePathEdit()
  if (ids.length !== 1 || tool !== 'select' || anchorEdit) return null
  const o = objects.find((x) => x.id === ids[0])
  if (!isMark(o) || o.locked || o.hidden) return null
  const target = handleTarget(o)
  if (!target) return null
  const g = o[field(target)]!
  const geo = handleGeometry(o, g)
  const v = (p: readonly [number, number]): [number, number] => [mmToViewX(p[0], t), mmToViewY(p[1], t)]
  const [ax, ay] = v(geo.a)
  const [bx, by] = v(geo.b)
  const selStop = edit.objectId === o.id && edit.target === target ? edit.stop : 0
  const base = o
  const endpoint = (which: 'a' | 'b') => (e: ReactPointerEvent) => {
    if (gradientEditFor(o.id).target !== target) useGradientEditStore.getState().set(o.id, { target })
    startDrag(e, base, target, (p, ev) => dragEndpoint(base, g, which, p, { shift: ev.shiftKey, tolMm: snapTolMm(getTransform()) }).g)
  }
  return (
    <g data-gradient-handles={target} data-gradient-kind={geo.kind}>
      {geo.kind === 'radial' && geo.radiusMm != null && (
        <circle
          cx={ax}
          cy={ay}
          r={mmToPx(geo.radiusMm, t)}
          fill="none"
          stroke={SEL}
          strokeWidth={1}
          strokeDasharray="3 3"
          style={{ pointerEvents: 'none' }}
        />
      )}
      {/* 底下一道宽一点的浅色线：深色的图上也看得出柄 */}
      <line x1={ax} y1={ay} x2={bx} y2={by} stroke="var(--handle-fill)" strokeWidth={3} style={{ pointerEvents: 'none' }} />
      <line x1={ax} y1={ay} x2={bx} y2={by} stroke={SEL} strokeWidth={1} style={{ pointerEvents: 'none' }} />
      {(['a', 'b'] as const).map((w) => {
        const [x, y] = w === 'a' ? [ax, ay] : [bx, by]
        // 正好落在端点上的色标（位置 0 / 1）就由端点代表：端点填它的颜色，点端点 = 选中它
        const k = w === 'a' ? geo.stops.findIndex((s) => s.offset === 0) : geo.stops.map((s) => s.offset).lastIndexOf(1)
        return (
          <rect
            key={w}
            data-gradient-end={w}
            x={x - END / 2}
            y={y - END / 2}
            width={END}
            height={END}
            stroke={SEL}
            strokeWidth={(k >= 0 && k === selStop) ? 2.5 : 1.5}
            data-selected={(k >= 0 && k === selStop) || undefined}
            style={{ fill: k >= 0 ? geo.stops[k].color : 'var(--handle-fill)', pointerEvents: 'all', cursor: 'move' }}
            onPointerDown={(e) => {
              if (k >= 0) useGradientEditStore.getState().set(o.id, { target, stop: k })
              endpoint(w)(e)
            }}
          />
        )
      })}
      {geo.stops.map((s, i) => {
        if (s.offset === 0 || s.offset === 1) return null // 端点代表它
        const [x, y] = v(s.at)
        return (
          <circle
            key={i}
            data-gradient-handle-stop={i}
            data-selected={i === selStop || undefined}
            cx={x}
            cy={y}
            r={STOP_R}
            stroke={i === selStop ? SEL : 'var(--handle-fill)'}
            strokeWidth={i === selStop ? 2.5 : 1.5}
            style={{ fill: s.color, pointerEvents: 'all', cursor: 'ew-resize' }}
            onPointerDown={(e) => {
              useGradientEditStore.getState().set(o.id, { target, stop: i })
              startDrag(e, base, target, (p) => dragStop(base, g, i, p))
            }}
          />
        )
      })}
    </g>
  )
}
