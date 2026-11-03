import { mathPathData, useMathBox } from '@/lib/mathtext'

/**
 * 一个标注公式（ADR 0152 PR 4）：后端 matplotlib mathtext 排出的轮廓，与导出同一份。
 *
 * **不撑开行盒**：外层是零高的 inline-block 坐在基线上，SVG 绝对定位从基线往上下伸——与导出一致
 * （导出里公式也只占宽度、不改行距）。字号用绝对像素算（`sizePx`），理由同上下标。
 * 还没排好 / 排不出来时照源码原样显示（`$…$`），与导出的退路相同；排不出来的带一条红色波浪线和原因。
 */
export function MathFormula({ src, family, sizePx }: { src: string; family: string; sizePx: number }) {
  const box = useMathBox(src, family)
  if (!box || !box.ok || box.width === undefined) {
    return (
      <span
        data-math-src={src}
        data-math-state={box ? 'error' : 'pending'}
        title={box?.error}
        style={box ? { textDecoration: 'underline wavy', textDecorationColor: 'var(--color-danger)' } : undefined}
      >
        {`$${src}$`}
      </span>
    )
  }
  const asc = box.ascent ?? 0
  const desc = box.descent ?? 0
  return (
    <span
      data-math-src={src}
      data-math-state="ok"
      aria-label={src}
      role="img"
      style={{ display: 'inline-block', position: 'relative', width: box.width * sizePx, height: 0, verticalAlign: 'baseline' }}
    >
      <svg
        viewBox={`0 ${-asc} ${box.width} ${asc + desc}`}
        width={box.width * sizePx}
        height={(asc + desc) * sizePx}
        style={{ position: 'absolute', left: 0, bottom: -desc * sizePx, overflow: 'visible' }}
        aria-hidden
      >
        <path d={mathPathData(box)} fill="currentColor" />
      </svg>
    </span>
  )
}
