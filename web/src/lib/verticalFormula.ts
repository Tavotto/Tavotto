/**
 * 竖排里一个公式的盒子与 SVG 摆法（ADR 0163）。**纯函数、不 import 任何东西**——
 * `MathFormula` 用它画；`tests/support/vertical_css_probe.mjs` 把本文件转译后装进真 Chromium，
 * 与导出（`plan._vertical_nodes`）逐点对账，所以两边读的是同一份源码。
 *
 * 导出的几何：基线 x = 列中线 − 字号 ×（上升 + 下降）/ 2，公式顺时针转 90°（上升朝右），
 * 沿列方向占 `公式宽 × 字号`。预览把盒子做成「列宽 × 公式宽」占满一整列，所以落点与浏览器怎么摆内联盒无关；
 * SVG 绝对定位，未转时左上角 = (公式起点, 上升线)，绕左上角顺时针转 90°。
 */
export interface VerticalFormulaInput {
  /** 列宽（px）= 字号 × 行距 */
  columnPx: number
  sizePx: number
  /** 公式排版结果，单位 em */
  widthEm: number
  ascentEm: number
  descentEm: number
  /** 侧躺字基线相对列中线向左偏多少 px = (上升 − 下降) / 2 × 字号（`verticalMetrics`） */
  baselineShiftPx: number
}

export interface VerticalFormulaBox {
  wrapper: { width: number; height: number }
  svg: { left: number; top: number; width: number; height: number; transform: string; transformOrigin: string }
}

export function verticalFormulaBox(i: VerticalFormulaInput): VerticalFormulaBox {
  const baseX = i.columnPx / 2 - i.baselineShiftPx
  return {
    wrapper: { width: i.columnPx, height: i.widthEm * i.sizePx },
    svg: {
      left: baseX + i.ascentEm * i.sizePx,
      top: 0,
      width: i.widthEm * i.sizePx,
      height: (i.ascentEm + i.descentEm) * i.sizePx,
      transform: 'rotate(90deg)',
      transformOrigin: '0 0',
    },
  }
}
