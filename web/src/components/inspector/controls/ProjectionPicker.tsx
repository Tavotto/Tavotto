import { optionLabel } from '../roles/registry'
import { OptionGrid, type GridOption } from './OptionGrid'

/**
 * 三维子图的投影方式：透视 / 正交，各一个小立方体预览（审计 T24）。
 * 写入值仍是 matplotlib 的 `proj_type`（persp / ortho）；manifest 没给的档不渲染。
 */

function Cube({ persp }: { persp: boolean }) {
  // 前面一个正方形，后面一个：透视时更小（远处变小），正交时同尺寸只是错开
  const front = { x: 3, y: 7, s: 10 }
  const back = persp ? { x: 10, y: 3, s: 6 } : { x: 8, y: 2, s: 10 }
  const corners = (r: { x: number; y: number; s: number }) => [
    [r.x, r.y],
    [r.x + r.s, r.y],
    [r.x + r.s, r.y + r.s],
    [r.x, r.y + r.s],
  ]
  const f = corners(front)
  const b = corners(back)
  return (
    <svg width={24} height={20} viewBox="0 0 24 20" aria-hidden className="text-current">
      <rect x={back.x} y={back.y} width={back.s} height={back.s} fill="none" stroke="currentColor" strokeWidth={0.9} opacity={0.6} />
      {f.map(([fx, fy], i) => (
        <line key={i} x1={fx} y1={fy} x2={b[i][0]} y2={b[i][1]} stroke="currentColor" strokeWidth={0.8} opacity={0.6} />
      ))}
      <rect x={front.x} y={front.y} width={front.s} height={front.s} fill="none" stroke="currentColor" strokeWidth={1.2} />
    </svg>
  )
}

export function ProjectionPicker({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string | null
  options: string[]
  onChange: (v: string) => void
  ariaLabel: string
}) {
  const items: GridOption[] = options.map((v) => ({
    value: v,
    label: optionLabel('proj_type', v),
    preview: (
      <span className="flex items-center gap-1 px-1">
        <Cube persp={v === 'persp'} />
        <span className="text-xs">{optionLabel('proj_type', v)}</span>
      </span>
    ),
  }))
  return (
    <OptionGrid
      value={value}
      options={items}
      onChange={onChange}
      columns={Math.max(1, Math.min(2, items.length))}
      ariaLabel={ariaLabel}
    />
  )
}
