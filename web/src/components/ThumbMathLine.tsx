/**
 * 缩略图 / 导出预览里「含公式的一行」（ADR 0152 §七）。
 *
 * SVG 的 `<text>` 里放不下 `<path>`，所以这一行拆成**段**：连续的文字（含上下标 / 斜体 / 粗体的 tspan）是一个
 * `<text>`，公式是一个 `<path>`（后端排好的轮廓，`mathPathData`，与画布 / PDF 同一份），按累计宽度排成一行：
 *   - 文字段的宽度用 `getComputedTextLength()` 在挂载后量（字体到齐后再量一次）；量不到（jsdom / 老引擎）
 *     用字数估：CJK 满字宽、其余半字宽。估值只影响公式的横向落点，不影响文字本身；
 *   - 对齐（左 / 中 / 右）按一行的总宽算起点，每段都是 `text-anchor: start`，不依赖浏览器对多个 anchor 块的处理；
 *   - 竖直：段内用字母基线（`y` = 行盒中线 + 0.34 em，Liberation / Times 的 (ascent − descent)/2），公式的基线也在这条线上。
 * 没排好 / 排不出的公式按源码 `$…$` 并进相邻文字段——与画布 `MathFormula`、导出的退路相同。
 *
 * 为什么不用 foreignObject：WebKit（桌面壳是 WKWebView）对带 transform 的 foreignObject 有已知问题，
 * 浏览器的最小字号钳制（部分中文环境 12 px）在缩略图的小字号下又会让字比例失真。
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type SVGProps } from 'react'
import { mathPathData, useMathBoxes } from '@/lib/mathtext'
import { SCRIPT_SIZE, SUB_DROP, SUP_RISE, type TextRun } from '@/lib/richText'

/** 字母基线比行盒中线低多少（em）：(ascent − descent) / 2 */
const BASELINE_BELOW_MID = 0.34

/** 一段文字片段 → tspan（上下标 / 斜体 / 粗体）；纯文字原样 */
export function ThumbRun({ r, size }: { r: TextRun; size: number }): ReactNode {
  return r.script === '' && !r.italic && !r.bold ? (
    r.text
  ) : (
    <tspan
      fontStyle={r.italic ? 'italic' : undefined}
      fontWeight={r.bold ? 700 : undefined}
      fontSize={r.script ? size * SCRIPT_SIZE : undefined}
      baselineShift={r.script ? size * (r.script === 'sup' ? SUP_RISE : -SUB_DROP) : undefined}
    >
      {r.text}
    </tspan>
  )
}

type Seg = { kind: 'text'; runs: TextRun[] } | { kind: 'math'; src: string; d: string; width: number }

/** 没量到时的估宽（用户单位） */
function estimateWidth(runs: TextRun[], size: number): number {
  let w = 0
  for (const r of runs) {
    const k = r.script ? SCRIPT_SIZE : 1
    for (const ch of r.text) w += ((ch.codePointAt(0) ?? 0) > 0x2e80 ? 1 : 0.5) * size * k
  }
  return w
}

export function ThumbMathLine({
  runs,
  family,
  size,
  x,
  mid,
  align,
  textProps,
}: {
  runs: TextRun[]
  family: string
  size: number
  /** 对齐锚点：左对齐 = 起点，居中 = 中心，右对齐 = 终点 */
  x: number
  /** 行盒中线 */
  mid: number
  align: 'left' | 'center' | 'right'
  /** 字体 / 颜色等，每个文字段共用 */
  textProps: SVGProps<SVGTextElement> & { style?: CSSProperties }
}) {
  const srcs = runs.filter((r) => r.math).map((r) => r.text)
  const boxes = useMathBoxes(srcs, family)
  const segs: Seg[] = []
  let k = 0
  for (const r of runs) {
    if (r.math) {
      const box = boxes[k++]
      if (box && box.ok && box.width !== undefined) {
        segs.push({ kind: 'math', src: r.text, d: mathPathData(box), width: box.width * size })
        continue
      }
      // 还没排好 / 排不出：源码并进文字（与 MathFormula 同一个退路）
      const lit: TextRun = { text: `$${r.text}$`, script: '' }
      const last = segs[segs.length - 1]
      if (last?.kind === 'text') last.runs.push(lit)
      else segs.push({ kind: 'text', runs: [lit] })
      continue
    }
    const last = segs[segs.length - 1]
    if (last?.kind === 'text') last.runs.push(r)
    else segs.push({ kind: 'text', runs: [r] })
  }

  const refs = useRef(new Map<number, SVGTextElement>())
  const [measured, setMeasured] = useState<Record<number, number>>({})
  const [fontsTick, setFontsTick] = useState(0)
  useEffect(() => {
    const fonts = typeof document !== 'undefined' ? document.fonts : undefined
    if (!fonts?.addEventListener) return
    const bump = () => setFontsTick((n) => n + 1)
    fonts.addEventListener('loadingdone', bump)
    return () => fonts.removeEventListener('loadingdone', bump)
  }, [])
  // 签名：段内容 + 字号 + 族；变了才重量
  const sig = JSON.stringify([segs.map((s) => (s.kind === 'text' ? s.runs : 0)), size, family, fontsTick])
  useLayoutEffect(() => {
    const next: Record<number, number> = {}
    refs.current.forEach((el, i) => {
      const len = typeof el.getComputedTextLength === 'function' ? el.getComputedTextLength() : NaN
      if (Number.isFinite(len) && len > 0) next[i] = len
    })
    setMeasured((prev) => {
      const ks = Object.keys(next)
      return ks.length === Object.keys(prev).length && ks.every((i) => prev[+i] === next[+i]) ? prev : next
    })
  }, [sig])

  const widths = segs.map((s, i) => (s.kind === 'math' ? s.width : (measured[i] ?? estimateWidth(s.runs, size))))
  const total = widths.reduce((a, b) => a + b, 0)
  let cursor = align === 'center' ? x - total / 2 : align === 'right' ? x - total : x
  const baseline = mid + BASELINE_BELOW_MID * size
  return (
    <g data-thumb-math-line>
      {segs.map((s, i) => {
        const at = cursor
        cursor += widths[i]
        if (s.kind === 'math') {
          return (
            <path
              key={i}
              data-thumb-math={s.src}
              d={s.d}
              transform={`translate(${at} ${baseline}) scale(${size})`}
              fill={textProps.fill}
            />
          )
        }
        return (
          <text
            key={i}
            {...textProps}
            x={at}
            y={baseline}
            textAnchor="start"
            dominantBaseline="alphabetic"
            // 段首尾的空格要算宽度：SVG 默认会把它们折掉
            style={{ ...textProps.style, whiteSpace: 'pre' }}
            ref={(el) => {
              if (el) refs.current.set(i, el)
              else refs.current.delete(i)
            }}
          >
            {s.runs.map((r, j) => (
              <ThumbRun key={j} r={r} size={size} />
            ))}
          </text>
        )
      })}
    </g>
  )
}
