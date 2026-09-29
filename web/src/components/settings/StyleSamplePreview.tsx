import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import {
  SAMPLE_TEXT,
  SAMPLE_VIEW,
  fitSampleGeometry,
  sampleLayout,
  styleSampleGeometry,
  type SampleFace,
} from '@/lib/styleSample'

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
  // 字号超出示例预算时，字号与线宽、刻度长度等**所有**长度一起等比缩（`fitSampleGeometry`）
  const g = fitSampleGeometry(raw)
  const label = st('previewAria', {
    title: raw.titlePt,
    axis: raw.axisPt,
    tick: raw.tickPt,
    legend: raw.legendPt,
    line: raw.lineWidthPt,
    spine: raw.spinePt,
  })
  const [c1, c2] = g.colors
  // 版面与画框都按这套样式现算（`sampleLayout`）：刻度朝外伸多长、字多大，文字就往外让多少
  const lay = sampleLayout(g)
  const { box, bottom, tickIn, tickOut, legend } = lay
  return (
    <figure data-style-preview className="m-0 flex flex-col gap-1">
      <svg
        role="img"
        aria-label={label}
        viewBox={lay.viewBox.join(' ')}
        className="h-auto w-full max-w-[360px] rounded-sm border border-border bg-white"
        style={{ aspectRatio: `${SAMPLE_VIEW.w} / ${SAMPLE_VIEW.h}` }}
      >
        <text x={box.x + box.w / 2} y={lay.titleY} fontSize={g.titlePt} textAnchor="middle" fill="#111" {...faceAttrs(g.faces.title)}>
          {SAMPLE_TEXT.title}
        </text>
        <rect x={box.x} y={box.y} width={box.w} height={box.h} fill="none" stroke="#111" strokeWidth={g.spinePt} />
        {/* 刻度线 + 刻度文字 */}
        {SAMPLE_TEXT.xTicks.map((s, i) => {
          const x = box.x + (i / 2) * box.w
          return (
            <g key={s}>
              <line x1={x} y1={bottom + tickOut} x2={x} y2={bottom - tickIn} stroke="#111" strokeWidth={g.tickWidthPt} />
              <text x={x} y={lay.xTickLabelY} fontSize={g.tickPt} textAnchor="middle" fill="#111" {...faceAttrs(g.faces.tick)}>
                {s}
              </text>
            </g>
          )
        })}
        {SAMPLE_TEXT.yTicks.map((s, i) => {
          const y = bottom - (i / 2) * box.h
          return (
            <g key={s}>
              <line x1={box.x - tickOut} y1={y} x2={box.x + tickIn} y2={y} stroke="#111" strokeWidth={g.tickWidthPt} />
              <text x={lay.yTickLabelX} y={y + g.tickPt * 0.35} fontSize={g.tickPt} textAnchor="end" fill="#111" {...faceAttrs(g.faces.tick)}>
                {s}
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
        <line x1={legend.lineX1} y1={legend.lineY} x2={legend.lineX2} y2={legend.lineY} stroke={c1} strokeWidth={g.lineWidthPt} />
        <text x={legend.textX} y={legend.textY} fontSize={g.legendPt} fill="#111" {...faceAttrs(g.faces.legend)}>
          {SAMPLE_TEXT.legend}
        </text>
        {/* 轴标题 */}
        <text x={box.x + box.w / 2} y={lay.xLabelY} fontSize={g.axisPt} textAnchor="middle" fill="#111" {...faceAttrs(g.faces.axis)}>
          {SAMPLE_TEXT.xLabel}
        </text>
        <text
          transform={`translate(${lay.yLabelX} ${box.y + box.h / 2}) rotate(-90)`}
          fontSize={g.axisPt}
          textAnchor="middle"
          fill="#111"
          {...faceAttrs(g.faces.axis)}
        >
          {SAMPLE_TEXT.yLabel}
        </text>
      </svg>
    </figure>
  )
}

/** 一类文字的字面 → SVG 属性（常规字面不写属性，示例 SVG 保持干净） */
function faceAttrs(f: SampleFace) {
  return {
    fontFamily: f.fontFamily,
    ...(f.bold ? { fontWeight: 'bold' } : {}),
    ...(f.italic ? { fontStyle: 'italic' } : {}),
  }
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
