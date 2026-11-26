import { useId } from 'react'
import { cn } from '@/lib/utils'
import './plotSketch.css'

/** 画幅（用户单位）与作图区 */
const W = 240
const H = 120
const X0 = 40
const X1 = 200
const Y0 = 98
const Y1 = 22
/** 中线与振幅：一条衰减振荡（阻尼共振）——科研图里最常见、也最好看的那一类曲线 */
const MID = (Y0 + Y1) / 2
const AMP = (Y0 - Y1) / 2 - 3
const DECAY = 2.1
const CYCLES = 2.6
const fx = (t: number) => X0 + (X1 - X0) * t
const env = (t: number) => AMP * Math.exp(-DECAY * t)
const fy = (t: number) => MID - env(t) * Math.cos(2 * Math.PI * CYCLES * t)

/** 一串采样拼成的路径（够密就是顺滑的曲线） */
function trace(f: (t: number) => number, from = 0, to = 1, n = 160) {
  let d = ''
  for (let i = 0; i <= n; i++) {
    const t = from + ((to - from) * i) / n
    d += `${i ? 'L' : 'M'}${fx(t).toFixed(2)} ${f(t).toFixed(2)}`
  }
  return d
}
const CURVE = trace(fy)
const ENV_UP = trace((t) => MID - env(t))
const ENV_DOWN = trace((t) => MID + env(t))
/** 十三个测量点：均匀取在曲线上，带一点抖动 */
const JITTER = [1.2, -1.6, 0.8, 1.5, -1.1, 0.6, -1.3, 1, -0.7, 0.9, -0.5, 0.6, -0.4]
const POINTS = JITTER.map((j, i) => {
  const t = 0.02 + (0.96 * i) / (JITTER.length - 1)
  return [fx(t), fy(t) + j] as const
})

export type PlotStage = 'read' | 'think' | 'draw'

/**
 * 修改方案生成中，坐标纸上的一幅「正在作图」的小画（用户 2026-11-26：要传达品牌——科研作图的精密仪器——
 * 而不是糊掉的原图、原图变点阵，也不再放一颗思考小球，上面已经有了；曲线要优美）：
 * 读这张图 = 一道读数线在淡淡的旧测量点上来回扫；想怎么改 = 测量点一个个落到纸上；
 * 画出修改后的图 = 笔顺着点描出一条衰减振荡，两侧淡淡的包络线托着它。
 * 纸上的墨（`paper-ink`）：纸不随主题反相。关键帧在 `plotSketch.css`，减少动态效果时停在这一步的终态。
 */
export function PlotSketch({ stage }: { stage: PlotStage }) {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="absolute inset-0 size-full text-paper-ink" aria-hidden data-plot-sketch={stage}>
      <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
        {/* 坐标轴、刻度与一道淡淡的零线 */}
        <path d={`M ${X0} ${Y1 - 6} V ${Y0} H ${X1 + 6}`} strokeOpacity="0.55" strokeWidth="1" />
        <path d={`M ${X0} ${MID} H ${X1}`} strokeOpacity="0.16" strokeWidth="0.75" strokeDasharray="1.5 3" />
        {[0.25, 0.5, 0.75, 1].map((t) => (
          <path key={`x${t}`} d={`M ${fx(t)} ${Y0} v 3`} strokeOpacity="0.45" strokeWidth="1" />
        ))}
        {[0, 0.5, 1].map((t) => (
          <path key={`y${t}`} d={`M ${X0} ${Y0 - 3 - (Y0 - Y1 - 6) * t} h -3`} strokeOpacity="0.45" strokeWidth="1" />
        ))}
        {stage === 'read' && (
          <g key="read" style={{ ['--plot-span' as string]: `${X1 - X0}px` }}>
            <g data-plot="scan">
              <path d={`M ${X0} ${Y1 - 4} V ${Y0}`} strokeOpacity="0.4" strokeWidth="1" strokeDasharray="2 3" />
              <path d={`M ${X0 - 3} ${Y0 + 5} l 3 -4 l 3 4 z`} fill="currentColor" fillOpacity="0.55" stroke="none" />
            </g>
          </g>
        )}
        {stage === 'draw' && (
          <g key="draw">
            <path data-plot="envelope" d={ENV_UP} strokeOpacity="0.22" strokeWidth="0.75" strokeDasharray="3 3" />
            <path data-plot="envelope" d={ENV_DOWN} strokeOpacity="0.22" strokeWidth="0.75" strokeDasharray="3 3" />
            <path data-plot="curve" d={CURVE} pathLength={1} strokeOpacity="0.9" strokeWidth="1.5" />
            <circle data-plot="pen" r="2.2" cx="0" cy="0" fill="currentColor" stroke="none" style={{ offsetPath: `path('${CURVE}')` }} />
          </g>
        )}
        {POINTS.map(([x, y], i) => (
          <circle
            key={`p${i}`}
            data-plot={stage === 'think' ? 'point' : undefined}
            cx={x}
            cy={y}
            r="2"
            fill="var(--color-paper)"
            strokeOpacity={stage === 'read' ? 0.22 : stage === 'draw' ? 0.55 : 0.8}
            strokeWidth="1"
            style={stage === 'think' ? { animationDelay: `${i * 0.16}s` } : undefined}
          />
        ))}
      </g>
    </svg>
  )
}

/** 四角制图定位角标（纸上的墨，CSS 边框画，不另起 svg） */
const CORNERS = [
  'left-2 top-2 rounded-tl-xs border-l border-t',
  'right-2 top-2 rounded-tr-xs border-r border-t',
  'bottom-2 left-2 rounded-bl-xs border-b border-l',
  'bottom-2 right-2 rounded-br-xs border-b border-r',
]

/**
 * 坐标纸：细格 8px、每 5 格一道粗一点的线（粗线交在正中），四角制图定位角标；纸上的墨（`paper-ink`，两套主题同值：纸不反相）。
 * 只在修改方案生成中垫在作图小画下面——科研图的底子，而不是一块空白或糊掉的原图。
 */
export function GraphPaper() {
  const id = useId()
  return (
    <span className="absolute inset-0 text-paper-ink" aria-hidden data-change-plan-graph-paper>
      <svg className="absolute inset-0 size-full">
        <defs>
          <pattern id={`${id}-m`} x="50%" y="50%" width="8" height="8" patternUnits="userSpaceOnUse">
            <path d="M 8 0 L 0 0 0 8" fill="none" stroke="currentColor" strokeOpacity="0.06" strokeWidth="0.5" />
          </pattern>
          <pattern id={`${id}-M`} x="50%" y="50%" width="40" height="40" patternUnits="userSpaceOnUse">
            <rect width="40" height="40" fill={`url(#${id}-m)`} />
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="currentColor" strokeOpacity="0.11" strokeWidth="0.75" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${id}-M)`} />
      </svg>
      {CORNERS.map((c) => (
        <span key={c} className={cn('absolute size-2.5 border-paper-ink/30', c)} />
      ))}
    </span>
  )
}
