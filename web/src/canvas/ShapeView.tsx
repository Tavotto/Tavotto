import { MM_PER_PT } from '@/lib/units'
import { cornerRadius, dashArray, hitStrokeWidth, polygonPoints } from '@/lib/shapeGeometry'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { ShapeObject } from '@/types/document'
import { lineEndpoints } from '@/types/document'

/**
 * 形状：几何公式与后端 _draw_shape 一一对应（engine 侧同名注释），
 * 显示与 PDF 导出必须逐点一致。
 *
 * `hit` 由 ObjectView 决定，只有 line 用得上：它和箭头一样是细长线状对象，
 * 沿拖动方向铺开时包围盒另一边被钳到 0.01mm，命中得交给沿线段的透明线。
 */
export function ShapeView({ obj, hit = 'none' }: { obj: ShapeObject; hit?: 'stroke' | 'none' }) {
  const zoom = useViewportStore((s) => s.zoom)
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)
  const inset = sw / 2 // 描边居中，内缩半个线宽让外沿正好贴合包围盒
  const fill = obj.fill ?? 'none'
  const fillOpacity = obj.fill ? (obj.fillOpacity ?? 1) : undefined
  const dash = dashArray(obj.dash, sw)
  // 描边内缩后的矩形（与后端 rect 同一个 Rect），圆角钳制也以它为准
  const rectW = Math.max(w - sw, 0.001)
  const rectH = Math.max(h - sw, 0.001)
  const rectR = obj.cornerRadius
    ? cornerRadius(mmToWorld(obj.cornerRadius), rectW, rectH)
    : undefined

  const stroke = {
    stroke: obj.color,
    strokeWidth: sw,
    strokeDasharray: dash,
    strokeLinejoin: 'round' as const,
    // dotted 的线段长只有 0.01×线宽，「点」全靠圆线帽画出来；缺省 butt 线帽下
    // 点是零面积，整圈描边直接不可见。与后端 _draw_shape 的 lineCap=1 同源。
    strokeLinecap: 'round' as const,
  }

  const poly = (pts: [number, number][]) => (
    <polygon
      points={pts.map(([x, y]) => `${x},${y}`).join(' ')}
      fill={fill}
      fillOpacity={fillOpacity}
      {...stroke}
    />
  )

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      // 与 ArrowView 同一钳制：竖直 / 水平直线的包围盒薄到亚像素时，
      // Chrome 会整个跳过 <svg> 的绘制；视口钳到 ≥1px，内部坐标不变。
      width={Math.max(w, 1)}
      height={Math.max(h, 1)}
    >
      {obj.shape === 'rect' && (
        <rect
          x={inset}
          y={inset}
          width={rectW}
          height={rectH}
          // rx/ry 同值：SVG 只写 rx 时两个方向会各自按半宽/半高钳制，宽高悬殊
          // 时退化成椭圆角，而后端画的是正圆角。钳制公式见 lib/cornerRadius。
          rx={rectR}
          ry={rectR}
          fill={fill}
          fillOpacity={fillOpacity}
          {...stroke}
        />
      )}
      {obj.shape === 'ellipse' && (
        <ellipse
          cx={w / 2}
          cy={h / 2}
          rx={Math.max(w / 2 - inset, 0.001)}
          ry={Math.max(h / 2 - inset, 0.001)}
          fill={fill}
          fillOpacity={fillOpacity}
          {...stroke}
        />
      )}
      {obj.shape === 'line' &&
        (() => {
          // 端点与箭头同构（比例坐标）；旧文档没有 start/end 时兜底成水平中线
          const { start: s, end: e } = lineEndpoints(obj)
          const a = { x: s.rx * w, y: s.ry * h }
          const b = { x: e.rx * w, y: e.ry * h }
          return (
            <>
              {/* 与 ArrowView 同一手法：线状对象的包围盒又薄又大，命中只能交给这条
                  沿真实端点走的透明线（端点一动它就跟着动） */}
              <line
                data-hit-line=""
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="transparent"
                strokeWidth={hitStrokeWidth(sw, zoom)}
                strokeLinecap="round"
                style={{ pointerEvents: hit }}
              />
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...stroke} />
            </>
          )
        })()}
      {obj.shape === 'triangle' &&
        poly([
          [w / 2, inset],
          [w - inset, h - inset],
          [inset, h - inset],
        ])}
      {obj.shape === 'diamond' &&
        poly([
          [w / 2, inset],
          [w - inset, h / 2],
          [w / 2, h - inset],
          [inset, h / 2],
        ])}
      {obj.shape === 'polygon' &&
        poly(polygonPoints(obj.sides ?? 6, w, h, inset))}
      {obj.shape === 'brace' && (
        <path d={bracePath(w, h, inset)} fill="none" {...stroke} />
      )}
    </svg>
  )
}

/**
 * 大括号「{」：右侧两钩 + 中部左尖，用二次贝塞尔；
 * 旋转（rotationDeg）覆盖其它方向。与后端 _brace_segments 同一构造。
 */
function bracePath(w: number, h: number, inset: number): string {
  const cx = w / 2
  const tipGap = Math.min(h * 0.06, h / 2 - inset)
  return [
    `M ${w - inset},${inset}`,
    `Q ${cx},${inset} ${cx},${h * 0.25}`,
    `L ${cx},${h / 2 - tipGap}`,
    `Q ${cx},${h / 2} ${inset},${h / 2}`,
    `Q ${cx},${h / 2} ${cx},${h / 2 + tipGap}`,
    `L ${cx},${h * 0.75}`,
    `Q ${cx},${h - inset} ${w - inset},${h - inset}`,
  ].join(' ')
}
