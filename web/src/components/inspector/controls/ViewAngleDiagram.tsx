import { t as translate } from '@/i18n'
import { viewAxes2d } from '@/lib/viewAngle'

/**
 * 三维子图的方向示意（审计 T24）：三条坐标轴此刻在屏幕上指向哪里。
 *
 * 静态内联 SVG，随俯仰 / 方位 / 侧倾的数值重画，**没有动画**；方向由
 * `lib/viewAngle.viewAxes2d` 算（与 matplotlib 同一套约定），这里只负责画。
 * 它是数值框的旁注，不是第二个控件：改角度仍在数值框里改。
 */
const SIZE = 64
const CENTER = SIZE / 2
const LEN = 22

export function ViewAngleDiagram({ elev, azim, roll = 0 }: { elev: number; azim: number; roll?: number }) {
  const axes = viewAxes2d(elev, azim, roll)
  const label = translate('control.viewAngle', { ns: 'inspector', elev, azim, roll })
  const entries: { name: 'x' | 'y' | 'z'; dir: [number, number] }[] = [
    { name: 'x', dir: axes.x },
    { name: 'y', dir: axes.y },
    { name: 'z', dir: axes.z },
  ]
  return (
    <svg
      role="img"
      aria-label={label}
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      className="shrink-0 rounded-sm border border-border bg-surface text-ink-2"
      data-view-angle
    >
      {entries.map(({ name, dir }) => {
        // 屏幕 y 向下；正对视线的轴长度趋近 0，只剩一个点
        const x2 = CENTER + dir[0] * LEN
        const y2 = CENTER - dir[1] * LEN
        const len = Math.hypot(dir[0], dir[1])
        return (
          <g key={name} data-axis={name} data-dx={dir[0]} data-dy={dir[1]}>
            <line
              x1={CENTER}
              y1={CENTER}
              x2={x2}
              y2={y2}
              stroke="currentColor"
              strokeWidth={name === 'z' ? 1.5 : 1}
              strokeLinecap="round"
            />
            {len > 0.15 && (
              <circle cx={x2} cy={y2} r={1.6} fill="currentColor" />
            )}
            <text
              x={CENTER + dir[0] * (LEN + 7)}
              y={CENTER - dir[1] * (LEN + 7) + 3}
              fontSize={8}
              textAnchor="middle"
              fill="currentColor"
              className="select-none"
            >
              {name.toUpperCase()}
            </text>
          </g>
        )
      })}
      <circle cx={CENTER} cy={CENTER} r={1.5} fill="currentColor" />
    </svg>
  )
}
