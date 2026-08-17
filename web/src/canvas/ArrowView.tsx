import { MM_PER_PT } from '@/lib/units'
import { dashArray, hitStrokeWidth } from '@/lib/shapeGeometry'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { ArrowObject } from '@/types/document'
import { arrowHeads } from '@/types/document'

/**
 * 箭头：两端点存比例坐标；端型独立（triangle 实心 / open 开口 / bar 短线）。
 * 几何与后端 _draw_arrow 一一对应：帽长 4×线宽、帽半宽 1.7×线宽、
 * 仅 triangle 端回缩 0.75×帽长、圆线帽 + 开口端圆接角（后端 lineCap/lineJoin=1）。
 *
 * `hit` 由 ObjectView 决定：包围盒不再承担命中，命中交给下面那条沿线段的透明线。
 */
export function ArrowView({ obj, hit = 'none' }: { obj: ArrowObject; hit?: 'stroke' | 'none' }) {
  const zoom = useViewportStore((s) => s.zoom)
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)

  const a = { x: obj.start.rx * w, y: obj.start.ry * h }
  const b = { x: obj.end.rx * w, y: obj.end.ry * h }
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const nx = -uy
  const ny = ux

  const headLen = sw * 4
  const headHalf = sw * 1.7
  const heads = arrowHeads(obj)

  const trianglePts = (tip: { x: number; y: number }, sign: 1 | -1) => {
    const bx = tip.x - sign * ux * headLen
    const by = tip.y - sign * uy * headLen
    return `${tip.x},${tip.y} ${bx + nx * headHalf},${by + ny * headHalf} ${bx - nx * headHalf},${by - ny * headHalf}`
  }
  const openPath = (tip: { x: number; y: number }, sign: 1 | -1) => {
    const bx = tip.x - sign * ux * headLen
    const by = tip.y - sign * uy * headLen
    return `M ${bx + nx * headHalf},${by + ny * headHalf} L ${tip.x},${tip.y} L ${bx - nx * headHalf},${by - ny * headHalf}`
  }
  const barPath = (tip: { x: number; y: number }) =>
    `M ${tip.x + nx * headHalf},${tip.y + ny * headHalf} L ${tip.x - nx * headHalf},${tip.y - ny * headHalf}`

  // 仅实心三角端回缩线段（其余端型线到尖）
  const trim = headLen * 0.75
  const p1 = {
    x: a.x + (heads.start === 'triangle' ? ux * trim : 0),
    y: a.y + (heads.start === 'triangle' ? uy * trim : 0),
  }
  const p2 = {
    x: b.x - (heads.end === 'triangle' ? ux * trim : 0),
    y: b.y - (heads.end === 'triangle' ? uy * trim : 0),
  }

  // strokeLinejoin 必须显式给 round：后端 open 端型是 lineJoin=1，
  // SVG 缺省却是 miter，尖角对不上（同一条箭头两边画出来不一样）
  const stroke = {
    stroke: obj.color,
    strokeWidth: sw,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  const renderHead = (kind: string, tip: { x: number; y: number }, sign: 1 | -1) => {
    if (kind === 'triangle') return <polygon points={trianglePts(tip, sign)} fill={obj.color} />
    if (kind === 'open') return <path d={openPath(tip, sign)} fill="none" {...stroke} />
    if (kind === 'bar') return <path d={barPath(tip)} fill="none" {...stroke} />
    return null
  }

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      width={w}
      height={h}
    >
      {/*
        沿线段的透明命中线：可见描边只有零点几毫米宽，水平箭头的包围盒 h 还被钳到
        0.01mm（比一个物理像素还窄），靠外层包围盒 div 根本点不中；斜箭头反过来整个
        矩形吃点击、误伤下层面板。所以命中全部交给这条线（ObjectView 里外层 div 让位）。
        画 a→b 而不是回缩后的 p1→p2：帽尖就在 a/b 上，round 线帽再外扩半个宽度，
        正好把两端的箭头帽罩住；宽度取帽全宽兜底，斜向帽的侧翼也在带内。
      */}
      <line
        data-hit-line=""
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke="transparent"
        strokeWidth={hitStrokeWidth(sw, zoom, headHalf * 2)}
        strokeLinecap="round"
        style={{ pointerEvents: hit }}
      />
      <line
        x1={p1.x}
        y1={p1.y}
        x2={p2.x}
        y2={p2.y}
        strokeDasharray={dashArray(obj.dash, sw)}
        {...stroke}
      />
      {renderHead(heads.end, b, 1)}
      {renderHead(heads.start, a, -1)}
    </svg>
  )
}
