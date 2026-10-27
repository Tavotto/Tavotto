import { LANES, LANE_HALF, ROW, endOf, hasInterval, pct, type TraceNode } from './utils'

interface Props {
  flat: TraceNode[]
  view: { start: number; domain: number }
  track: { left: number; width: number }
  now: number
  running: (n: TraceNode) => boolean
}

/**
 * 智能体把活交给子智能体的连线：父泳道下一个点，竖线落到子泳道，再拐进它的第一段；
 * 子智能体结束后同形状折返。画的是 span 之间的几何关系，所以是 svg（iconography 豁免登记）。
 * 只画当前可见（未折叠、未被搜索滤掉）的行。
 */
export function Handoffs({ flat, view, track, now, running }: Props) {
  if (!track.width) return null
  const index = new Map(flat.map((n, i) => [n.span.id, i]))
  const x = (time: number) => (pct(time, view) / 100) * track.width + track.left
  const r = 6
  const lines: { key: string; d: string; dot: [number, number]; lane: number }[] = []
  for (const [i, node] of flat.entries()) {
    const parent = node.parent
    if (node.span.kind !== 'agent' || parent?.span.kind !== 'agent' || !hasInterval(node.span)) continue
    const j = index.get(parent.span.id)
    if (j === undefined) continue
    const yp = j * ROW + ROW / 2 + LANE_HALF
    const yc = i * ROW + ROW / 2
    const xs = x(node.span.start)
    lines.push({ key: `${node.span.id}:out`, d: `M${xs} ${yp}V${yc - r}Q${xs} ${yc} ${xs + r} ${yc}`, dot: [xs, yp], lane: node.lane })
    if (!running(node)) {
      const xe = x(endOf(node.span, now))
      lines.push({ key: `${node.span.id}:back`, d: `M${xe - r} ${yc}Q${xe} ${yc} ${xe} ${yc - r}V${yp}`, dot: [xe, yp], lane: node.lane })
    }
  }
  return (
    <svg
      aria-hidden
      data-trace-handoffs
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      width={track.left + track.width}
      height={flat.length * ROW}
      style={{ clipPath: `inset(0 0 0 ${track.left}px)` }}
    >
      {lines.map((l) => (
        <g key={l.key} data-trace-handoff className="motion-safe:animate-fade-in">
          <path d={l.d} fill="none" strokeWidth={1.5} strokeLinecap="round" className={LANES[l.lane].stroke} />
          <circle cx={l.dot[0]} cy={l.dot[1]} r={3.5} strokeWidth={1.5} className={`${LANES[l.lane].fill} stroke-surface`} />
        </g>
      ))}
    </svg>
  )
}
