/**
 * 主图下面那一行灰字「依据」：这张图是按多少数、几组画出来的，以及有几行没算进去、为什么。封闭句式，只拼后端给的数（`facts`），不判断、不补数。
 * 纯函数：返回两段句子（key 在 `ai:dataFigureV2.basis.*`、带参数），翻译与连接（`then` / `join` / `line`）由界面做：
 * 「依据：」+ 主句（各部分用 `then` 相连）[+「；」+ 没画的行那一句] +「。」
 *
 *  - 主句：有分组 = 每组一样多 / 不一样多（趋势类再接「横轴有 X 个不同取值」）；没有分组的趋势类 =「按 N 个数值绘制，横轴有 X 个不同取值」；
 *    散点（`scatter` / `scatter_fit`）=「按 N 个点绘制，每行一个点」；其它 =「按 N 个数值绘制」
 *  - 没画的行：只有缺值 / 只有不是数 / 两种都有；旧响应没有拆开的两个数时按总数「另有 N 行未计入」；0 行没有这一句
 *  - 没有 `facts` = 不显示（null）
 */
import type { AiDataFacts } from '@/lib/api'

/** 散点（relationship 一族）与折线 / 趋势（trend 一族）的画法码：族只在后端配方里，响应不带，所以按码认 */
const SCATTER = ['scatter', 'scatter_fit']
const TREND = ['line_points', 'line_band', 'line_units']

export interface BasisSentence {
  key: 'points' | 'plain' | 'groupsEqual' | 'groupsRange' | 'xAlso' | 'xOnly' | 'skippedMissing' | 'skippedOther' | 'skippedBoth' | 'skippedAll'
  values: Record<string, number>
}

export interface Basis {
  /** 主句的各部分（界面用 `then` 连起来） */
  main: BasisSentence[]
  /** 没画的行那一句；没有 = null */
  skipped: BasisSentence | null
}

export function basisOf(c: { title_code: string; facts?: AiDataFacts | null; unplotted?: { count: number } | null }): Basis | null {
  const f = c.facts
  if (!f) return null
  const trend = TREND.includes(c.title_code)
  const main: BasisSentence[] = []
  if (SCATTER.includes(c.title_code)) {
    main.push({ key: 'points', values: { drawn: f.drawn } })
  } else if (f.groups != null && f.n_min != null && f.n_max != null) {
    main.push(
      f.n_min === f.n_max
        ? { key: 'groupsEqual', values: { groups: f.groups, n: f.n_min } }
        : { key: 'groupsRange', values: { groups: f.groups, min: f.n_min, max: f.n_max } },
    )
    if (trend && f.x_values != null) main.push({ key: 'xAlso', values: { x: f.x_values } })
  } else if (trend && f.x_values != null) {
    main.push({ key: 'xOnly', values: { drawn: f.drawn, x: f.x_values } })
  } else {
    main.push({ key: 'plain', values: { drawn: f.drawn } })
  }
  return { main, skipped: skippedOf(f, c.unplotted?.count ?? 0) }
}

function skippedOf(f: AiDataFacts, total: number): BasisSentence | null {
  if (f.skipped_missing === undefined && f.skipped_other === undefined) return total > 0 ? { key: 'skippedAll', values: { count: total } } : null
  const m = f.skipped_missing ?? 0
  const o = f.skipped_other ?? 0
  if (m > 0 && o > 0) return { key: 'skippedBoth', values: { missing: m, other: o } }
  if (m > 0) return { key: 'skippedMissing', values: { count: m } }
  if (o > 0) return { key: 'skippedOther', values: { count: o } }
  return null
}
