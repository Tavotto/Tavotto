import { MM_PER_PT } from '@/lib/units'
import { arrowGeometry, dashArray, hitStrokeWidth, type ArrowHeadShape } from '@/lib/shapeGeometry'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { ArrowObject } from '@/types/document'

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

  const { a, b, p1, p2, headHalf, startHead, endHead } = arrowGeometry(obj, w, h, sw)

  // strokeLinejoin 必须显式给 round：后端 open 端型是 lineJoin=1，
  // SVG 缺省却是 miter，尖角对不上（同一条箭头两边画出来不一样）
  const stroke = {
    stroke: obj.color,
    strokeWidth: sw,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  const renderHead = (head: ArrowHeadShape | null) => {
    if (!head) return null
    if (head.kind === 'triangle') return <polygon points={head.points} fill={obj.color} />
    return <path d={head.d} fill="none" {...stroke} />
  }

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      // 竖直 / 水平箭头的包围盒被钳到 0.01mm（≈0.04 世界 px）：Chrome 对亚像素
      // 尺寸的 <svg> 视口整个跳过绘制，overflow:visible 也救不了——箭头凭空消失。
      // 视口钳到 ≥1px 只影响绘制区域，内部坐标仍按真实 w/h 计算，几何不变。
      width={Math.max(w, 1)}
      height={Math.max(h, 1)}
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
      {renderHead(endHead)}
      {renderHead(startHead)}
    </svg>
  )
}
