/**
 * 竖排里一个公式的盒子与 SVG 摆法（ADR 0163）。**纯函数、不 import 任何东西**——
 * `MathFormula` 用它画；`tests/support/vertical_css_probe.mjs` 把本文件转译后装进真 Chromium，
 * 与导出（`plan._vertical_nodes`）逐点对账，所以两边读的是同一份源码。
 *
 * 导出的几何：基线 x = 列中线 − `baselineShiftPx`，公式顺时针转 90°（上升朝右），沿列方向占 `公式宽 × 字号`；
 * 公式比列宽还厚时撑开所在的那一列（`vertical.column_box`，ADR 0152 修订）。
 *
 * 预览把盒子做成**恰好包住公式墨迹**的行内块：横向从 `基线 − 下降` 到 `基线 + 上升`。浏览器在竖排里把没有基线的
 * 行内块按中线对齐（盒子横向中点落在列中线上），`verticalAlign` 再把它往右（line-over）挪 `(右 − 左) / 2`，
 * 盒子就正好落在基线两侧；比列宽厚时行盒取二者的并——撑开这一列，下一列跟着往左让，与导出同一个算法。
 * SVG 绝对定位，未转时左上角 = (公式起点, 上升线)，绕左上角顺时针转 90°。
 */
export interface VerticalFormulaInput {
  sizePx: number
  /** 公式排版结果，单位 em */
  widthEm: number
  ascentEm: number
  descentEm: number
  /** 侧躺字基线在列中线左边多少 px（`verticalMetrics`） */
  baselineShiftPx: number
}

export interface VerticalFormulaBox {
  wrapper: { width: number; height: number; verticalAlign: number }
  svg: { left: number; top: number; width: number; height: number; transform: string; transformOrigin: string }
}

export function verticalFormulaBox(i: VerticalFormulaInput): VerticalFormulaBox {
  // 墨迹相对列中线：左边 = 中线左 (下降 + 基线偏移)，右边 = 中线右 (上升 − 基线偏移)
  const left = i.descentEm * i.sizePx + i.baselineShiftPx
  const right = i.ascentEm * i.sizePx - i.baselineShiftPx
  return {
    wrapper: { width: left + right, height: i.widthEm * i.sizePx, verticalAlign: (right - left) / 2 },
    svg: {
      left: left - i.baselineShiftPx + i.ascentEm * i.sizePx,
      top: 0,
      width: i.widthEm * i.sizePx,
      height: (i.ascentEm + i.descentEm) * i.sizePx,
      transform: 'rotate(90deg)',
      transformOrigin: '0 0',
    },
  }
}
