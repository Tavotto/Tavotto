import { useMemo } from 'react'
import { cn } from '@/lib/utils'

/**
 * 首启询问框顶部的三幅插画（A 手绘桌面 / B 点阵曲线 / C 纸飞机）。
 *
 * 只给气氛，不承载信息：整幅 `aria-hidden`。颜色一律走语义 token（Tailwind 的 stroke-* / fill-*
 * 读 `--color-*`），深色主题自动跟随，这里不出现任何 hex。
 */
export type ConsentArtId = 'A' | 'B' | 'C'

const LINE = 'fill-none stroke-ink-faint'
const OUTLINE = 'stroke-ink'
// 叶子的绿：accent 与 ok 之外没有现成的绿 token，用 ok 混 surface（与 *-surface 同一手法）
const LEAF = 'fill-[color-mix(in_oklab,var(--color-ok)_14%,var(--color-surface))]'

function DeskArt() {
  return (
    <svg width="440" height="184" viewBox="0 0 440 184" fill="none" aria-hidden="true">
      <path d="M18 172 C 120 170, 320 171, 422 172" strokeWidth="1.4" strokeLinecap="round" className={LINE} />
      <path
        d="M112 166 L 118 44 C 118.5 40, 121 38, 125 38.4 L 268 44 C 272 44.2, 274 46.5, 273.6 50 L 268 162"
        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')}
      />
      <path d="M132 70 L 130 148 L 256 152" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" className="fill-none stroke-ink-3" />
      <path
        d="M136 140 C 158 136, 168 118, 186 112 S 222 84, 250 72"
        strokeWidth="12" strokeLinecap="round" className="fill-none stroke-highlighter"
      />
      <path
        d="M136 140 C 158 136, 168 118, 186 112 S 222 84, 250 72"
        strokeWidth="1.8" strokeLinecap="round" className="fill-none stroke-accent"
      />
      {[[160, 133], [186, 112], [216, 93], [246, 74]].map(([cx, cy]) => (
        <circle key={cx} cx={cx} cy={cy} r="2.6" className="fill-accent stroke-none" />
      ))}
      <path d="M186 104 L 186 120 M 216 85 L 216 101" strokeWidth="1.2" strokeLinecap="round" className="stroke-accent" />
      <path d="M140 56 L 196 58" strokeWidth="1.3" strokeLinecap="round" className={LINE} />
      <path d="M282 160 L 330 108 L 336 113 L 289 166 Z" strokeWidth="1.4" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')} />
      <path d="M282 160 L 279 170 L 289 166" strokeWidth="1.4" strokeLinejoin="round" className={OUTLINE} />
      <path
        d="M352 120 L 354 168 C 354 171, 356 172, 359 172 L 386 172 C 389 172, 391 171, 391 168 L 393 120 Z"
        strokeWidth="1.6" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')}
      />
      <path d="M393 130 C 404 130, 406 148, 392 150" strokeWidth="1.6" strokeLinecap="round" className={OUTLINE} />
      <path d="M364 108 C 360 100, 368 96, 364 88 M 378 106 C 374 98, 382 94, 378 86" strokeWidth="1.3" strokeLinecap="round" className={LINE} />
      <path d="M60 172 C 58 150, 62 132, 70 118" strokeWidth="1.4" strokeLinecap="round" className={OUTLINE} />
      <path d="M70 118 C 58 116, 50 106, 52 96 C 64 98, 70 108, 70 118 Z" strokeWidth="1.3" strokeLinejoin="round" className={cn(OUTLINE, LEAF)} />
      <path d="M66 136 C 78 136, 88 126, 88 114 C 76 114, 68 124, 66 136 Z" strokeWidth="1.3" strokeLinejoin="round" className={cn(OUTLINE, LEAF)} />
      <path d="M46 172 L 48 156 L 76 156 L 78 172" strokeWidth="1.4" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')} />
    </svg>
  )
}

const COLS = 36
const ROWS = 13
const STEP = 12
const OX = 10
const OY = 12

interface Dot {
  x: number
  y: number
  r: number
  /** 填充不透明度；曲线上的点满色，曲线下方渐淡，其余是底纹 */
  o: number
  tone: 'accent' | 'faint'
}

/** logistic 曲线上的点阵：纯函数，导出给测试 */
export function buildDots(): Dot[] {
  const curve = (i: number) => 128 - 92 / (1 + Math.exp(-(i - 17) / 4.2))
  const dots: Dot[] = []
  for (let i = 0; i < COLS; i++) {
    const cy = curve(i)
    for (let j = 0; j < ROWS; j++) {
      const x = OX + i * STEP
      const y = OY + j * STEP
      const d = Math.abs(y - cy)
      if (d < 6) dots.push({ x, y, r: 3.2, o: 1, tone: 'accent' })
      else if (y > cy && d < 40) dots.push({ x, y, r: 2.2, o: Number((0.38 - d / 130).toFixed(2)), tone: 'accent' })
      else dots.push({ x, y, r: 1.3, o: 0.45, tone: 'faint' })
    }
  }
  return dots
}

function DotsArt() {
  const dots = useMemo(buildDots, [])
  return (
    <svg width="440" height="168" viewBox="0 0 440 168" aria-hidden="true">
      {dots.map((d) => (
        <circle
          key={`${d.x}-${d.y}`}
          cx={d.x} cy={d.y} r={d.r} fillOpacity={d.o}
          className={d.tone === 'accent' ? 'fill-accent' : 'fill-ink-faint'}
        />
      ))}
    </svg>
  )
}

function PlaneArt() {
  return (
    <svg width="440" height="184" viewBox="0 0 440 184" fill="none" aria-hidden="true">
      <path d="M60 170 C 140 168, 300 169, 380 170" strokeWidth="1.4" strokeLinecap="round" className={LINE} />
      <path
        d="M90 164 L 96 118 C 130 108, 170 112, 196 124 C 222 112, 262 108, 296 118 L 302 164 C 270 154, 228 154, 196 166 C 164 154, 122 154, 90 164 Z"
        strokeWidth="1.6" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')}
      />
      <path d="M196 124 L 196 166" strokeWidth="1.3" strokeLinecap="round" className={OUTLINE} />
      <path
        d="M112 128 C 130 124, 156 124, 178 130 M 112 140 C 130 136, 150 136, 172 140 M 214 130 C 236 124, 262 124, 280 128"
        strokeWidth="1.2" strokeLinecap="round" className={LINE}
      />
      <path d="M216 148 C 232 146, 244 140, 254 134 S 272 126, 282 128" strokeWidth="1.6" strokeLinecap="round" className="fill-none stroke-accent" />
      <path
        d="M200 110 C 220 80, 260 60, 300 52 C 320 48, 336 44, 350 38"
        strokeWidth="1.3" strokeLinecap="round" strokeDasharray="2 6" className={LINE}
      />
      <path d="M350 38 L 402 20 L 372 60 Z" strokeWidth="1.5" strokeLinejoin="round" className={cn(OUTLINE, 'fill-surface')} />
      <path d="M402 20 L 362 48 L 372 60" strokeWidth="1.3" strokeLinejoin="round" className={OUTLINE} />
      <path d="M362 48 L 350 38" strokeWidth="1.3" strokeLinecap="round" className={OUTLINE} />
      <path d="M368 52 L 360 66 L 372 60" strokeWidth="1.2" strokeLinejoin="round" className={cn(OUTLINE, 'fill-accent-subtle')} />
    </svg>
  )
}

export function ConsentArt({ id }: { id: ConsentArtId }) {
  return (
    <div
      data-telemetry-art={id}
      className={cn('flex h-[200px] shrink-0 justify-center overflow-hidden bg-surface-2', id === 'B' ? 'items-center' : 'items-end')}
    >
      {id === 'A' ? <DeskArt /> : id === 'B' ? <DotsArt /> : <PlaneArt />}
    </div>
  )
}
