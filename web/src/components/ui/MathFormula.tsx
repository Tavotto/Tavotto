import { mathPathData, useMathBox } from '@/lib/mathtext'
import { sidewaysBaselineShift } from '@/lib/verticalMetrics'
import { verticalFormulaBox } from '@/lib/verticalFormula'

/**
 * 一个标注公式（ADR 0152 PR 4）：后端 matplotlib mathtext 排出的轮廓，与导出同一份。
 *
 * 行盒：画布文字（给了 `lineBox`）里公式**撑开它所在的那一行**（ADR 0152 修订，2026-11-16）——外层是真高的
 * inline-block 坐在基线上，行盒 = 普通字的行盒 ∪ 公式盒，与导出 `typography.line_box` 同一个算法。没给 `lineBox`
 * （属性页的文字框）时仍是零高、不撑行。字号用绝对像素算（`sizePx`），理由同上下标。
 * 还没排好 / 排不出来时照源码原样显示（`$…$`），与导出的退路相同；排不出来的带一条红色波浪线和原因。
 */
export function MathFormula({
  src,
  family,
  sizePx,
  vertical,
  lineBox,
}: {
  src: string
  family: string
  sizePx: number
  /**
   * 竖排（ADR 0163）：公式与侧躺的拉丁字同样**顺时针转 90° 整块侧躺**，沿列方向占公式的宽度
   * （导出 `plan._vertical_nodes`：基线 = 列中线 − 字号 ×（上升 + 下降）/ 2，公式顺时针转 90°）。
   * `columnPx` = 列宽（字号 × 行距），盒子占满一整列，所以公式落在列中线上、与浏览器怎么摆内联盒无关；
   * `stack` = 正文的字体栈，用来量侧躺字的基线位置（与同列侧躺字同一条基线）。
   */
  vertical?: { columnPx: number; stack: string }
  /**
   * 横排画布文字：公式撑开所在行。`shiftPx` = 宿主给整段内容的基线补差（`horizontalBaselineFixPx`）：
   * 浏览器的行盒按它自己的字体度量摆，宿主再把内容整体往下挪 `shiftPx`；公式的布局盒先往下错开同样的量，
   * 撑出来的行盒才与导出（基线在中线下固定 em）逐行一致，轮廓本身仍画在基线上。
   */
  lineBox?: { shiftPx: number }
}) {
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
  // 竖排：外层盒与 SVG 的摆法来自 `verticalFormulaBox`（预览与对账探针读同一份）；横排保持原样
  const g = vertical
    ? verticalFormulaBox({
        columnPx: vertical.columnPx,
        sizePx,
        widthEm: box.width,
        ascentEm: asc,
        descentEm: desc,
        baselineShiftPx: sidewaysBaselineShift(vertical.stack, sizePx),
      })
    : null
  return (
    <span
      data-math-src={src}
      data-math-state="ok"
      data-math-vertical={g ? '' : undefined}
      aria-label={src}
      role="img"
      style={
        g
          ? { display: 'inline-block', position: 'relative', ...g.wrapper }
          : lineBox
            ? {
                display: 'inline-block',
                position: 'relative',
                width: box.width * sizePx,
                height: (asc + desc) * sizePx,
                // 无行内内容的 inline-block 以下边缘为基线：下边缘落在基线下 desc + 补差
                verticalAlign: -(desc * sizePx + lineBox.shiftPx),
              }
            : { display: 'inline-block', position: 'relative', width: box.width * sizePx, height: 0, verticalAlign: 'baseline' }
      }
    >
      <svg
        viewBox={`0 ${-asc} ${box.width} ${asc + desc}`}
        width={box.width * sizePx}
        height={(asc + desc) * sizePx}
        style={
          g
            ? {
                position: 'absolute',
                left: g.svg.left,
                top: g.svg.top,
                transformOrigin: g.svg.transformOrigin,
                transform: g.svg.transform,
                overflow: 'visible',
              }
            : lineBox
              ? { position: 'absolute', left: 0, top: -lineBox.shiftPx, overflow: 'visible' }
              : { position: 'absolute', left: 0, bottom: -desc * sizePx, overflow: 'visible' }
        }
        aria-hidden
      >
        <path d={mathPathData(box)} fill="currentColor" />
      </svg>
    </span>
  )
}
