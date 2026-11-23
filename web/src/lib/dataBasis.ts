/**
 * 主图下面那一行灰字「依据」：这张图是按多少数、几组画出来的。封闭句式，只拼后端给的数（`facts`），不判断、不补数。
 * 纯函数：返回句子清单（key 在 `ai:dataFigureV2.basis.*`、带参数），翻译与连接由界面做。
 *
 *  - 散点（`scatter` / `scatter_fit`）：「N 个点，每行一个。」
 *  - 有分组：每组一样多 / 不一样多两句；折线 / 趋势且有横轴取值数时再接「横轴有 X 个不同取值。」
 *  - 没有分组的折线 / 趋势：「N 个值，横轴有 X 个不同取值。」
 *  - `unplotted.count > 0` 时句尾接「另有 N 行没画。」
 *  - 没有 `facts` = 不显示（null）；有 facts 但前面都不适用（直方图一类没有分组）= 只在有没画的行时留那一句，否则 null
 */
import type { AiDataFacts } from '@/lib/api'

/** 散点（relationship 一族）与折线 / 趋势（trend 一族）的画法码：族只在后端配方里，响应不带，所以按码认 */
const SCATTER = ['scatter', 'scatter_fit']
const TREND = ['line_points', 'line_band', 'line_units']

export interface BasisSentence {
  key: 'points' | 'groupsEqual' | 'groupsRange' | 'xAlso' | 'xOnly' | 'unplotted'
  values: Record<string, number>
}

export function basisSentences(c: { title_code: string; facts?: AiDataFacts | null; unplotted?: { count: number } | null }): BasisSentence[] | null {
  const f = c.facts
  if (!f) return null
  const out: BasisSentence[] = []
  const trend = TREND.includes(c.title_code)
  if (SCATTER.includes(c.title_code)) {
    out.push({ key: 'points', values: { drawn: f.drawn } })
  } else if (f.groups != null && f.n_min != null && f.n_max != null) {
    out.push(
      f.n_min === f.n_max
        ? { key: 'groupsEqual', values: { drawn: f.drawn, groups: f.groups, n: f.n_min } }
        : { key: 'groupsRange', values: { drawn: f.drawn, groups: f.groups, min: f.n_min, max: f.n_max } },
    )
    if (trend && f.x_values != null) out.push({ key: 'xAlso', values: { x: f.x_values } })
  } else if (trend && f.x_values != null) {
    out.push({ key: 'xOnly', values: { drawn: f.drawn, x: f.x_values } })
  }
  const missing = c.unplotted?.count ?? 0
  if (missing > 0) out.push({ key: 'unplotted', values: { count: missing } })
  return out.length ? out : null
}
