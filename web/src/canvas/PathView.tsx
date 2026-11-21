import { MM_PER_PT } from '@/lib/units'
import { useId } from 'react'
import { outsideClipPad, PATH_DEFAULT_JOIN, pathD, pathGeometry, pathStrokeAlign } from '@/lib/pathGeom'
import { hitStrokeWidth, isFilledHead, strokeOpacityOf, svgStroke, type ArrowHeadShape } from '@/lib/shapeGeometry'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { PathObject } from '@/types/document'
import { readPath } from '@/types/document'

/**
 * 自由路径（ADR 0185）：几何只在 `lib/pathGeom.pathGeometry` 一处算（与后端 `geometry.path_segments` 同源）。
 * 描边属性只走 `svgStroke()`（与 ShapeView / ArrowView 同一个出口）。
 *
 * 画法与导出（`geometry.path_paths`）一一对应：没有端型时一条 `<path>` 又填又描；有端型时填充单独一条，
 * 描边 + 端型放进一个 `<g opacity>`——描边不透明度作用于「线 + 端型」这一个整体，杆伸进实心端型的重叠处
 * 不叠深（与箭头同一做法，ADR 0174）。
 *
 * 命中：外层 div 让位（ObjectView），交给沿真实轮廓的透明层——有填充（或既不填也不描）时整块可点、
 * 填充区域按 `fill-rule`；空心的只在描边附近命中，命中带不窄于 `hitStrokeWidth`、也罩住端型。
 */
export function PathView({ obj, hit = 'none' }: { obj: PathObject; hit?: 'stroke' | 'none' }) {
  const zoom = useViewportStore((s) => s.zoom)
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const sw = Math.max(mmToWorld(obj.strokePt * MM_PER_PT), 0.05)
  const r = readPath(obj)
  const g = pathGeometry(obj, w, h, sw)
  const fillD = pathD(g.fill)
  const fillRule = r.fillRule === 'evenodd' ? ('evenodd' as const) : undefined
  const filledHit = !!obj.fill || !r.strokeOn

  return (
    <svg
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      // 与 ShapeView 同一钳制：水平 / 竖直直线路径的包围盒薄到亚像素时 Chrome 整个跳过 <svg> 的绘制
      width={Math.max(w, 1)}
      height={Math.max(h, 1)}
    >
      <PathBody obj={obj} sw={sw} w={w} h={h} />
      {hit !== 'none' && fillD && (
        <path
          data-hit-path=""
          d={fillD}
          fill={filledHit ? 'transparent' : 'none'}
          fillRule={fillRule}
          stroke="transparent"
          strokeWidth={hitStrokeWidth(sw, zoom, g.headHalf * 2)}
          strokeLinejoin="round"
          strokeLinecap="round"
          style={{ pointerEvents: filledHit ? 'all' : 'stroke' }}
        />
      )}
    </svg>
  )
}

/**
 * 路径的可见图元（画布 `PathView` 与导出预览 `ThumbShape` 共用；`w` / `h` / `sw` 同单位）。
 * 无描边又无填充：什么都不画（与形状同一条，ADR 0174）。
 */
export function PathBody({ obj, sw, w, h }: { obj: PathObject; sw: number; w: number; h: number }) {
  const clipId = `pclip-${useId().replace(/:/g, '')}`
  const r = readPath(obj)
  const g = pathGeometry(obj, w, h, sw)
  if (!g.fill.length) return null
  const fill = obj.fill ?? 'none'
  const fillOpacity = obj.fill ? (obj.fillOpacity ?? 1) : undefined
  const fillRule = r.fillRule === 'evenodd' ? ('evenodd' as const) : undefined
  if (!r.strokeOn) {
    return obj.fill ? <path d={pathD(g.fill)} fill={fill} fillOpacity={fillOpacity} fillRule={fillRule} /> : null
  }
  const align = pathStrokeAlign(obj)
  if (align) {
    // 描边对齐（ADR 0185 M2）：两倍线宽的描边裁到路径里面（内侧）/ 外面（外侧，大矩形 + 路径按奇偶）——与后端
    // `geometry.path_paths` 同一画法。虚线长度按原线宽算（只把 strokeWidth 换成两倍）
    const d = pathD(g.fill)
    const pad = outsideClipPad(w, h, sw)
    const clipD = align === 'inside' ? d : `M${-pad} ${-pad}H${w + pad}V${h + pad}H${-pad}Z ${d}`
    return (
      <>
        {obj.fill && <path d={d} fill={fill} fillOpacity={fillOpacity} fillRule={fillRule} />}
        <clipPath id={clipId}>
          <path d={clipD} clipRule={align === 'inside' ? fillRule : 'evenodd'} />
        </clipPath>
        <path
          data-stroke-align={align}
          d={d}
          fill="none"
          clipPath={`url(#${clipId})`}
          {...svgStroke(obj, sw, PATH_DEFAULT_JOIN)}
          strokeWidth={2 * sw}
        />
      </>
    )
  }
  if (!g.heads.length) {
    return (
      <path
        d={pathD(g.fill)}
        fill={fill}
        fillOpacity={fillOpacity}
        fillRule={fillRule}
        {...svgStroke(obj, sw, PATH_DEFAULT_JOIN)}
      />
    )
  }
  const bare = { ...obj, strokeOpacity: undefined }
  const headStroke = svgStroke(bare, sw, 'round', false)
  const renderHead = (head: ArrowHeadShape, i: number) =>
    head.kind === 'dot' ? (
      <circle key={i} cx={head.cx} cy={head.cy} r={head.r} fill={obj.color} />
    ) : isFilledHead(head) ? (
      <polygon key={i} points={head.points} fill={obj.color} />
    ) : (
      // 短线端型沿用箭头的旧产物（尖角）；开口 V 缺省圆角——与后端 `arrow_paths` 的 `head_strokes` 同一张表
      <path key={i} d={head.d} fill="none" {...headStroke} strokeLinejoin={obj.lineJoin ?? (head.kind === 'bar' ? 'miter' : 'round')} />
    )
  return (
    <>
      {obj.fill && <path d={pathD(g.fill)} fill={fill} fillOpacity={fillOpacity} fillRule={fillRule} />}
      <g opacity={strokeOpacityOf(obj.strokeOpacity)}>
        <path d={pathD(g.stroke)} fill="none" {...svgStroke(bare, sw, PATH_DEFAULT_JOIN)} />
        {g.heads.map(renderHead)}
      </g>
    </>
  )
}
