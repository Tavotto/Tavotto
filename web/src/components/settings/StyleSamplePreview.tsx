import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { sampleFitScale, styleSampleGeometry } from '@/lib/styleSample'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`profiles.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 样式页的示例图（审计 T42）：一张固定的小图，字号 / 线宽 / 边框 / 字体族按
 * 当前样式现算（`lib/styleSample.ts`），编辑字段时同步变。
 *
 * viewBox 的单位就是 pt：字号 9 就是 9 个单位高、线宽 0.5 就是 0.5 个单位粗，
 * 所以「9 pt 与 7 pt 差多少」在示例里与真图上的比例一致。它只是示例——
 * 真图里有哪些元素、配色循环到第几个，都不在这里保证。
 */
export function StyleSamplePreview({ data }: { data: Record<string, unknown> | null | undefined }) {
  useTranslation('dialogs')
  const raw = styleSampleGeometry(data)
  // 读屏那句话说的是**样式里的真实数字**，示例图画的是缩过的——缩放是示例自己
  // 的排版手段，不该被读成"这套样式的字号是 14pt"。
  const k = sampleFitScale(raw)
  const g =
    k === 1
      ? raw
      : {
          ...raw,
          titlePt: raw.titlePt * k,
          axisPt: raw.axisPt * k,
          tickPt: raw.tickPt * k,
          legendPt: raw.legendPt * k,
        }
  const label = st('previewAria', {
    title: raw.titlePt,
    axis: raw.axisPt,
    tick: raw.tickPt,
    legend: raw.legendPt,
    line: raw.lineWidthPt,
    spine: raw.spinePt,
  })
  const [c1, c2] = g.colors
  // 坐标轴框：留出标题、轴标题与刻度文字的位置
  const box = { x: 34, y: 6 + g.titlePt * 1.6, w: 150, h: 78 - g.titlePt * 0.6 }
  const bottom = box.y + box.h
  return (
    <figure data-style-preview className="m-0 flex flex-col gap-1">
      <svg
        role="img"
        aria-label={label}
        viewBox="0 0 200 128"
        className="h-auto w-full max-w-[320px] rounded-sm border border-border bg-white"
        style={{ fontFamily: g.fontFamily }}
      >
        <text x={box.x + box.w / 2} y={6 + g.titlePt} fontSize={g.titlePt} textAnchor="middle" fill="#111">
          Reaction kinetics
        </text>
        <rect x={box.x} y={box.y} width={box.w} height={box.h} fill="none" stroke="#111" strokeWidth={g.spinePt} />
        {/* 刻度线 + 刻度文字 */}
        {[0, 0.5, 1].map((f) => {
          const x = box.x + f * box.w
          return (
            <g key={f}>
              <line x1={x} y1={bottom} x2={x} y2={bottom - 3} stroke="#111" strokeWidth={g.spinePt} />
              <text x={x} y={bottom + g.tickPt + 1.5} fontSize={g.tickPt} textAnchor="middle" fill="#111">
                {f * 60}
              </text>
            </g>
          )
        })}
        {[0, 0.5, 1].map((f) => {
          const y = bottom - f * box.h
          return (
            <g key={f}>
              <line x1={box.x} y1={y} x2={box.x + 3} y2={y} stroke="#111" strokeWidth={g.spinePt} />
              <text x={box.x - 2.5} y={y + g.tickPt * 0.35} fontSize={g.tickPt} textAnchor="end" fill="#111">
                {f.toFixed(1)}
              </text>
            </g>
          )
        })}
        {/* 两条数据线：线宽按样式 */}
        <polyline
          fill="none"
          stroke={c1}
          strokeWidth={g.lineWidthPt}
          points={series(box, bottom, (t) => 1 - Math.exp(-4 * t))}
        />
        <polyline
          fill="none"
          stroke={c2}
          strokeWidth={g.lineWidthPt}
          strokeDasharray={`${Math.max(2, g.lineWidthPt * 4)} ${Math.max(1.5, g.lineWidthPt * 2.5)}`}
          points={series(box, bottom, (t) => 1 - Math.exp(-1.5 * t))}
        />
        {/* 图例 */}
        <line
          x1={box.x + box.w - 62}
          y1={box.y + 10 + g.legendPt * 0.35}
          x2={box.x + box.w - 50}
          y2={box.y + 10 + g.legendPt * 0.35}
          stroke={c1}
          strokeWidth={g.lineWidthPt}
        />
        <text x={box.x + box.w - 46} y={box.y + 10 + g.legendPt * 0.7} fontSize={g.legendPt} fill="#111">
          Catalyst
        </text>
        {/* 轴标题 */}
        <text
          x={box.x + box.w / 2}
          y={bottom + g.tickPt + 4 + g.axisPt}
          fontSize={g.axisPt}
          textAnchor="middle"
          fill="#111"
        >
          Time (min)
        </text>
        <text
          transform={`translate(${8 + g.axisPt * 0.35} ${box.y + box.h / 2}) rotate(-90)`}
          fontSize={g.axisPt}
          textAnchor="middle"
          fill="#111"
        >
          Conversion
        </text>
      </svg>
      <figcaption className="text-[11px] leading-relaxed text-ink-3">{st('previewHint')}</figcaption>
    </figure>
  )
}

/** 一条曲线的折线点：横向 0…1 采样，纵向按 fn 归一化 */
function series(
  box: { x: number; y: number; w: number; h: number },
  bottom: number,
  fn: (t: number) => number,
): string {
  const pts: string[] = []
  for (let i = 0; i <= 24; i += 1) {
    const t = i / 24
    const x = box.x + t * box.w
    const y = bottom - fn(t) * box.h * 0.92
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return pts.join(' ')
}
