import { useRef } from 'react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { REF_POINTS, type RefPoint } from '@/lib/transform'
import { Tip } from '../ui/Tooltip'

const tr = (key: string, values?: Record<string, unknown>) => translate(`transform.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 九宫格参考点（ADR 0185 M4 第二批）：坐在 X / Y 两格之间那个 28 px 的空位里，不另占一行。
 * X / Y 报的是这个点，改 W / H、旋转、倾斜、镜像都以它为不动点。单选按钮组：点一下选中，方向键在九格里移动。
 */
export function RefPointGrid({ value, onChange }: { value: RefPoint; onChange: (rp: RefPoint) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const move = (dx: number, dy: number) => {
    const i = REF_POINTS.indexOf(value)
    const x = Math.min(2, Math.max(0, (i % 3) + dx))
    const y = Math.min(2, Math.max(0, Math.floor(i / 3) + dy))
    const next = REF_POINTS[y * 3 + x]
    onChange(next)
    ref.current?.querySelector<HTMLElement>(`[data-ref-point="${next}"]`)?.focus()
  }
  return (
    <Tip label={tr('refPointTip', { point: tr(`refPoint.${value}`) })}>
      <div
        ref={ref}
        role="radiogroup"
        aria-label={tr('refPoint.label')}
        data-ref-point-grid=""
        className="grid size-7 grid-cols-3 grid-rows-3 place-items-center rounded-md p-0.5"
        onKeyDown={(e) => {
          const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]
          if (!d) return
          e.preventDefault()
          e.stopPropagation()
          move(d[0], d[1])
        }}
      >
        {REF_POINTS.map((rp) => {
          const on = rp === value
          return (
            <button
              key={rp}
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={tr(`refPoint.${rp}`)}
              tabIndex={on ? 0 : -1}
              data-ref-point={rp}
              onClick={() => onChange(rp)}
              className="flex size-2 items-center justify-center rounded-full outline-none focus-visible:focus-ring"
            >
              <span className={cn('block rounded-full', on ? 'size-2 bg-accent' : 'size-1 bg-ink-3')} />
            </button>
          )
        })}
      </div>
    </Tip>
  )
}
