/**
 * 新手教程的演示图（ADR 0136）：**受控教学演示，不是编辑器**。
 *
 * 教程不执行 Python（P0：不需要用户的环境、不跑脚本、不联网），而 Tavotto 的图内编辑
 * 每一次改动都要由 worker 重跑脚本重放 override（`engine/tutorial.py`：脚本只在进入图内
 * 编辑、要求渲染那一刻才跑）。所以三步教程里的那张图不进 documentStore、不写 override、
 * 不进撤销历史、不发后端——它是一份**按真实脚本量出来的几何**（`demoFigure.json`，唯一
 * 产生者 `scripts/gen_tutorial_demo_figure.py`，权威是 `resources/tutorial_project/fig1_kinetics.py`，
 * `tests/test_tutorial_demo_figure.py` 看护同源）加上两处演示改动（`onboardingStore.demo`）。
 *
 * 这里只有纯函数：数据、夹取、字号校验、mathtext 切段。画图在 `components/onboarding/DemoFigure`。
 * 单位一律 pt，原点在图幅左上（与 SVG 的 viewBox 同一套），与窗口大小 / 缩放 / DPI 无关。
 */
import raw from './demoFigure.json'

type Box = [number, number, number, number]

export interface DemoText {
  text: string
  /** [左, 上, 宽, 高]（pt） */
  box: Box
  sizePt: number
  bold: boolean
  rotation: number
}

export interface DemoTick {
  at: number
  label: DemoText
}

export interface DemoLine {
  label: string
  color: string
  widthPt: number
  dash: number[] | null
  points: [number, number][]
}

export interface DemoLegendEntry {
  /** 句柄线段两端，相对图例外框左上 */
  handle: [[number, number], [number, number]]
  text: string
  /** 文字外框，相对图例外框左上 */
  box: Box
  sizePt: number
}

export interface DemoFigureData {
  script: string
  stem: string
  figure: { width: number; height: number }
  axes: { box: Box; spineWidthPt: number }
  ticks: {
    style: { lengthPt: number; widthPt: number; inward: boolean }
    x: DemoTick[]
    y: DemoTick[]
  }
  xlabel: DemoText
  ylabel: DemoText
  title: { text: string; sizePt: number; anchor: [number, number] }
  lines: DemoLine[]
  legend: { box: Box; entries: DemoLegendEntry[] }
  snippet: string[][]
}

export const DEMO_FIGURE = raw as unknown as DemoFigureData

/* --------------------------------- 标题字号 -------------------------------- */

/**
 * 字号的允许区间（pt）。下限 7：再小就落到出版规范「绝对下限」以下（`font-below-absolute-floor`）；
 * 上限 11：脚本把子图上沿固定在图幅 89% 处，标题基线离图幅上沿只有 8.6 pt，12 pt 的大写字母
 * 已经贴着上沿（实测截图），再大就出了图幅（真实的 matplotlib 输出会被裁掉）——教程不演示一个坏结果。
 */
export const TITLE_SIZE = {
  initial: DEMO_FIGURE.title.sizePt,
  min: 7,
  max: 11,
  step: 1,
} as const

/** 字号吸附到 0.5 pt（输入框里打 10.3 当 10.5） */
const snapHalf = (v: number) => Math.round(v * 2) / 2

/**
 * 输入框交来的数 → 合法字号。NaN / Infinity 回 null（调用方保留上一个有效值）；有限数
 * 吸附到 0.5 pt 再夹进区间——越界的提交由 `NumberField` 自己闪一下边框告诉用户「到头了」。
 */
export function normalizeTitleSize(v: number): number | null {
  if (!Number.isFinite(v)) return null
  return Math.min(TITLE_SIZE.max, Math.max(TITLE_SIZE.min, snapHalf(v)))
}

/** 加减按钮的一步：在区间里走、到边就停 */
export function stepTitleSize(current: number, dir: 1 | -1): number {
  return normalizeTitleSize(current + dir * TITLE_SIZE.step) ?? current
}

/* ---------------------------------- 图例 ---------------------------------- */

/** 图例移动的最小有效距离（pt）：小于它的拖动当作手抖，不算「换了位置」 */
export const LEGEND_MIN_MOVE_PT = 6

/**
 * 夹取图例位移：外框始终留在坐标轴框里（与原型「受画面边界约束」同一条；
 * 不许拖出图幅——那样的「成果」在真图上会被裁掉）。
 */
export function clampLegendOffset(offset: [number, number]): [number, number] {
  const [ax, ay, aw, ah] = DEMO_FIGURE.axes.box
  const [lx, ly, lw, lh] = DEMO_FIGURE.legend.box
  const minDx = ax - lx
  const maxDx = ax + aw - lw - lx
  const minDy = ay - ly
  const maxDy = ay + ah - lh - ly
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
  const round = (v: number) => Math.round(v * 100) / 100
  return [round(clamp(offset[0], minDx, maxDx)), round(clamp(offset[1], minDy, maxDy))]
}

export const legendDistance = (offset: [number, number] | null): number =>
  offset ? Math.hypot(offset[0], offset[1]) : 0

/** 图例外框此刻在哪（pt） */
export function legendBox(offset: [number, number] | null): Box {
  const [x, y, w, h] = DEMO_FIGURE.legend.box
  const [dx, dy] = offset ?? [0, 0]
  return [x + dx, y + dy, w, h]
}

/* -------------------------------- mathtext -------------------------------- */

export interface TextRun {
  text: string
  sup: boolean
}

/**
 * 只认脚本里实际出现的那一种 mathtext：`$^{...}$` 上标（`min$^{-1}$`）。其余原样。
 * 上标里的 ASCII 减号换成真正的减号（matplotlib 的 mathtext 也是这么排的）。
 */
export function mathRuns(text: string): TextRun[] {
  const runs: TextRun[] = []
  const re = /\$\^\{([^}]*)\}\$/g
  let last = 0
  for (const m of text.matchAll(re)) {
    if (m.index > last) runs.push({ text: text.slice(last, m.index), sup: false })
    runs.push({ text: m[1].replace(/-/g, '−'), sup: true })
    last = m.index + m[0].length
  }
  if (last < text.length) runs.push({ text: text.slice(last), sup: false })
  return runs
}

/** 给读屏的纯文本（上标写成 ⁻¹ 这类 Unicode 上标字） */
const SUPERSCRIPT: Record<string, string> = { '-': '⁻', '−': '⁻', '0': '⁰', '1': '¹', '2': '²', '3': '³' }
export const plainText = (text: string): string =>
  mathRuns(text)
    .map((r) => (r.sup ? [...r.text].map((c) => SUPERSCRIPT[c] ?? c).join('') : r.text))
    .join('')
