/**
 * 主图下面那一行灰字「依据」：这张图是按多少数、几组画出来的，以及有几行没算进去、为什么。封闭句式，只拼后端给的数（`facts`），不判断、不补数。
 * 纯函数：返回两段句子（key 在 `ai:dataFigureV2.basis.*`、带参数），翻译与连接（`then` / `join` / `line`）由界面做：
 * 「依据：」+ 主句（各部分用 `then` 相连）[+「；」+ 没画的行那一句] +「。」
 *
 *  - 主句：有分组 = 每组一样多 / 不一样多（趋势类再接「横轴有 X 个不同取值」）；没有分组的趋势类 =「按 N 个数值绘制，横轴有 X 个不同取值」；
 *    散点（`scatter` / `scatter_fit`）=「按 N 个点绘制，每行一个点」；其它 =「按 N 个数值绘制」
 *  - 没画的行：只有缺值 / 只有不是数 / 两种都有；旧响应没有拆开的两个数时按总数「另有 N 行未计入」；0 行没有这一句
 *  - 现成的误差列（`points_err` / `line_err`）：主句「按 N 行绘制，每行一个点」，再接「误差线取自「列名」列」
 *  - 算出来的误差线 / 带有位置只有 1 个数值：句末再接「N 处只有 1 个数值，算不出误差线（带）」（只有一处时点名：「「C」只有 1 个数值，算不出误差线」）；位置标签在 `missingFor`（整句的 title 提示里列出）
 *  - 多个 y（`series_facts`，每条线各自的数）：「N 个系列，各含 n 个有效值」/「分别含 a、b 个有效值」（超过 3 个系列用范围「各含 min–max 个有效值」）；
 *    数量相同但有效的行不是同一批再接「；有效数据覆盖位置不同」；现成误差列里有中心值却缺误差的再接「；N 处缺少误差值」。
 *    **绝不拿 `facts`（旧响应里是第一条线的数）顶替**：多个 y 却没有 `series_facts` 的旧响应不写数字那半句，误差线取自哪几列那半句照旧
 *  - 没有 `facts`、也不是多个 y = 不显示（null）
 */
import type { AiDataFacts, AiDataSeriesFacts } from '@/lib/api'

/** 散点（relationship 一族）与折线 / 趋势（trend 一族）的画法码：族只在后端配方里，响应不带，所以按码认 */
const SCATTER = ['scatter', 'scatter_fit']
const TREND = ['line_points', 'line_band', 'line_units']
/** 表里现成的误差列画成误差线：已汇总的表，一行一个点，误差取自那一列；走专用句式（不按分组数句子），属比较（`points_err`）/ 趋势（`line_err`） */
const ERR_COLUMN = ['points_err', 'line_err', 'bar_err']

export interface BasisSentence {
  key: 'rowsErr' | 'rowsBar' | 'seriesEqual' | 'seriesApart' | 'seriesEach' | 'seriesRange' | 'errGap' | 'errColumn' | 'errColumns' | 'errMissing' | 'bandMissing' | 'errMissingOne' | 'bandMissingOne' | 'points' | 'plain' | 'groupsEqual' | 'groupsRange' | 'xAlso' | 'xOnly' | 'skippedMissing' | 'skippedOther' | 'skippedBoth' | 'skippedAll'
  /** `errColumns` 的 `columns` 是列名数组（界面各加引号再相连）；别的都是数 / 字符串 */
  values: Record<string, number | string | string[]>
}

export interface Basis {
  /** 主句的各部分（界面用 `then` 连起来）；旧的多 y 响应没有数可写时是空数组 */
  main: BasisSentence[]
  /** 多个 y：有中心值却缺误差值的位置数那一句（紧跟主句）；没有 = null */
  errGap: BasisSentence | null
  /** 误差线相关的句子：取自哪一列（`*_err`）、算出来的误差线缺了几处；接在主句之后、没画的行之前 / 之后（见 `basisOf`） */
  errColumn: BasisSentence | null
  /** 没画的行那一句；没有 = null */
  skipped: BasisSentence | null
  /** 算出来的误差线 / 带缺了几处（只有 1 个数值）那一句；没有 = null */
  errMissing: BasisSentence | null
  /** 缺误差线的位置标签（给整句的 title 提示用）；没有 = 空数组 */
  missingFor: string[]
}

export function basisOf(c: {
  title_code: string
  facts?: AiDataFacts | null
  series_facts?: AiDataSeriesFacts | null
  values?: { y?: string[] } | null
  unplotted?: { count: number } | null
  error_column?: string
  error_columns?: { column: string }[]
  error_missing_for?: string[]
}): Basis | null {
  const sf = c.series_facts
  // 多个 y 的趋势图：有 `series_facts`、或趋势画法配方的 y 不止一列、或各配各的误差列（不能只看 `error_columns`：普通折线也可以多个 y）。
  // 别的族的多列纵轴（区间 / 构成 / 把列当分组……）不在此列，照旧按 `facts` 写；旧响应的多 y 趋势图有 `facts`（第一条线的数）而没有 `series_facts`
  const multi = !!sf || ((TREND.includes(c.title_code) || c.title_code === 'line_err') && ((c.values?.y?.length ?? 0) > 1 || (c.error_columns?.length ?? 0) > 1))
  const f = multi ? null : c.facts
  if (!f && !(sf || (multi && c.facts))) return null
  const viaColumn = ERR_COLUMN.includes(c.title_code)
  const main: BasisSentence[] = []
  let errGap: BasisSentence | null = null
  if (multi) {
    if (sf) {
      main.push(...seriesSentences(sf))
      errGap = errGapOf(sf)
    }
  } else if (f) {
    const trend = TREND.includes(c.title_code)
    if (viaColumn) {
      // 柱不是「点」：`bar_err` 写「每行一根柱」
      main.push({ key: c.title_code === 'bar_err' ? 'rowsBar' : 'rowsErr', values: { drawn: f.drawn } })
    } else if (SCATTER.includes(c.title_code)) {
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
  }
  const missingFor = c.error_missing_for ?? []
  // 多个 y 的没画的行优先用 `series_facts` 里拆好的数；旧响应退回 `facts`（那两个数是整张表的，不是第一条线的）
  const skipSrc = sf ?? c.facts ?? null
  const out: Basis = {
    main,
    errGap,
    errColumn: viaColumn ? errColumnOf(c) : null,
    skipped: skippedOf(skipSrc, c.unplotted?.count ?? 0),
    errMissing: missingOf(missingFor, c.title_code === 'line_band'),
    missingFor,
  }
  return out.main.length || out.errGap || out.errColumn || out.skipped || out.errMissing ? out : null
}

/** 多个系列的主句：数量相同且有效的行是同一批 =「各含 n 个」；数量相同但不是同一批 = 再注明覆盖不同；数量不同 = 至多 3 个系列逐个列出，更多用范围 */
function seriesSentences(sf: AiDataSeriesFacts): BasisSentence[] {
  const ns = sf.series.map((s) => s.n)
  if (ns.length < 2) return []
  const k = ns.length
  const lo = Math.min(...ns)
  const hi = Math.max(...ns)
  if (lo === hi) return [{ key: sf.rows_all === sf.rows_any ? 'seriesEqual' : 'seriesApart', values: { k, n: lo } }]
  if (k > 3) return [{ key: 'seriesRange', values: { k, min: lo, max: hi } }]
  return [{ key: 'seriesEach', values: { k, counts: ns.map(String) } }]
}

/** 有中心值却缺误差值的位置数（各系列相加）；没有配现成误差列（`err_missing` 不在）或没有缺 = null */
function errGapOf(sf: AiDataSeriesFacts): BasisSentence | null {
  if (sf.series.every((s) => s.err_missing === undefined)) return null
  const count = sf.series.reduce((a, s) => a + (s.err_missing ?? 0), 0)
  return count > 0 ? { key: 'errGap', values: { count } } : null
}

/** 误差线取自哪些列：多个 y 各配各的时写成「a_sd」「b_sd」（逐列事实在 `error_columns`），单个 y 是 `error_column` */
function errColumnOf(c: { error_column?: string; error_columns?: { column: string }[] }): BasisSentence | null {
  if (c.error_columns && c.error_columns.length > 1) return { key: 'errColumns', values: { columns: c.error_columns.map((x) => x.column) } }
  const one = c.error_column ?? c.error_columns?.[0]?.column
  return one ? { key: 'errColumn', values: { column: one } } : null
}

function skippedOf(f: Pick<AiDataFacts, 'skipped_missing' | 'skipped_other'> | null, total: number): BasisSentence | null {
  if (!f || (f.skipped_missing === undefined && f.skipped_other === undefined)) return total > 0 ? { key: 'skippedAll', values: { count: total } } : null
  const m = f.skipped_missing ?? 0
  const o = f.skipped_other ?? 0
  if (m > 0 && o > 0) return { key: 'skippedBoth', values: { missing: m, other: o } }
  if (m > 0) return { key: 'skippedMissing', values: { count: m } }
  if (o > 0) return { key: 'skippedOther', values: { count: o } }
  return null
}

/** 只有一处时直接说是哪一处（用户不用再去 title 里找）；多处说个数，位置在 title 提示里 */
function missingOf(labels: string[], band: boolean): BasisSentence | null {
  if (!labels.length) return null
  if (labels.length === 1) return { key: band ? 'bandMissingOne' : 'errMissingOne', values: { label: labels[0] } }
  return { key: band ? 'bandMissing' : 'errMissing', values: { count: labels.length } }
}
