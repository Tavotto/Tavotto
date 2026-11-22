import { useId, type ReactNode } from 'react'
import { gradientGeometry, type GradientGeometry } from '@/lib/gradient'
import type { Gradient } from '@/types/document'

/**
 * 形状 / 路径的填充与描边颜料（ADR 0186）：有渐变就给 `url(#…)` 与 `<defs>` 里的渐变定义，没有就是原来的纯色。
 * 画布（`ShapeView` / `PathBody`）与导出预览（`ThumbShape`）同一个出口；几何只在 `lib/gradient.gradientGeometry`
 * （与后端 `geometry.gradient_paint` 同源）。`w` / `h` 与图元同单位，渐变用 `userSpaceOnUse` 落在框空间里。
 */
export function GradientDef({ id, g }: { id: string; g: GradientGeometry }) {
  const stops = g.stops.map((s, i) => <stop key={i} offset={s.offset} stopColor={s.color} stopOpacity={s.opacity} />)
  return g.kind === 'linear' ? (
    <linearGradient id={id} gradientUnits="userSpaceOnUse" x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2}>
      {stops}
    </linearGradient>
  ) : (
    <radialGradient id={id} gradientUnits="userSpaceOnUse" cx={g.cx} cy={g.cy} r={g.r}>
      {stops}
    </radialGradient>
  )
}

export interface Paints {
  fill: string
  fillOpacity: number | undefined
  /** 叠在 `svgStroke()` 的结果上：有描边渐变时换掉 stroke 与 strokeOpacity */
  strokeOverride: { stroke?: string; strokeOpacity?: number }
  defs: ReactNode
}

export function usePaints(
  obj: { fill: string | null; fillOpacity?: number; fillGradient?: Gradient; strokeGradient?: Gradient; strokeOpacity?: number },
  w: number,
  h: number,
  strokeOn: boolean,
): Paints {
  const base = useId().replace(/:/g, '')
  const fg = obj.fill ? gradientGeometry(obj.fillGradient, w, h, obj.fillOpacity ?? 1) : null
  const sg = strokeOn ? gradientGeometry(obj.strokeGradient, w, h, obj.strokeOpacity ?? 1) : null
  const fid = `gf-${base}`
  const sid = `gs-${base}`
  return {
    fill: fg ? `url(#${fid})` : (obj.fill ?? 'none'),
    fillOpacity: fg ? undefined : obj.fill ? (obj.fillOpacity ?? 1) : undefined,
    strokeOverride: sg ? { stroke: `url(#${sid})`, strokeOpacity: undefined } : {},
    defs:
      fg || sg ? (
        <defs>
          {fg && <GradientDef id={fid} g={fg} />}
          {sg && <GradientDef id={sid} g={sg} />}
        </defs>
      ) : null,
  }
}
