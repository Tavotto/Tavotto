import { t as translate } from '@/i18n'
import { formatNumber } from '@/i18n/format'
import type { AiEntry } from '@/store/aiStore'

/**
 * 改图助手转录的纯数据层（不含组件，fast refresh 才生效）：状态名、耗时格式、过程分组、unified diff 解析。
 * 形态说明在 `Transcript.tsx` / `DiffView.tsx` 与宪法第十八节「2026-10-07 重做」。
 */

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/** 会话状态名；未知状态原样透出（后端加了新状态也不会变成空白） */
export const statusLabel = (status: string) => translate(`status.${status}`, { ns: 'ai', defaultValue: status })

/** 耗时：10s 以内带一位小数（0.2s / 1.8s），一分钟以内取整（18s），再长写成「2m 05s」 */
export function formatDuration(ms: number): string {
  const s = Math.max(0, ms) / 1000
  if (s < 10) return ai('duration.seconds', { value: formatNumber(Math.round(s * 10) / 10, { maximumFractionDigits: 1 }) })
  const whole = Math.round(s)
  if (whole < 60) return ai('duration.seconds', { value: formatNumber(whole) })
  const m = Math.floor(whole / 60)
  return ai('duration.minutes', { m, s: String(whole - m * 60).padStart(2, '0') })
}


/* ------------------------------------------------------------------ 分组 */

/** 一步：起止时刻都可能缺（旧夹具 / 没有 at 的条目）——缺了就不说耗时，绝不编一个数 */
export interface Step {
  kind: string
  text: string
  start?: number
  end?: number
}

type Group =
  | { type: 'message'; text: string; streaming?: boolean }
  | { type: 'process'; items: Step[] }

/** 把连续的 thinking / action 折成一组「过程」，正文单独成条；每步的终点是下一条的到达时刻（最后一条用会话结束） */
export function groupEntries(entries: AiEntry[], finishedAt?: number): Group[] {
  const groups: Group[] = []
  entries.forEach((e, i) => {
    if (e.kind === 'message' || e.kind === 'delta') {
      groups.push({ type: 'message', text: e.text, streaming: e.streaming })
      return
    }
    const end = i + 1 < entries.length ? entries[i + 1].at : finishedAt
    const step: Step = { kind: e.kind, text: e.text, start: e.at, end }
    const last = groups.at(-1)
    if (last?.type === 'process') last.items.push(step)
    else groups.push({ type: 'process', items: [step] })
  })
  return groups
}

export const spanOf = (s: Step) => (s.start != null && s.end != null ? s.end - s.start : null)

/* ------------------------------------------------------------------ 解析 */

export interface LineRow {
  kind: 'add' | 'del' | 'ctx'
  text: string
  no: number
  pair?: [number, number]
}

export type DiffRow =
  | LineRow
  /** 两段 hunk 之间（或第一段之前）没进 diff 的未改动行数——内容不在 diff 里，只能说「有几行」 */
  | { kind: 'gap'; count: number }

const WORD = /\w/

/*
 * 前后缀是按 UTF-16 码元比的：「𝛼 → 𝛽」「😀 → 🙀」共用高代理位，比出来的边界会落在一个字的两半之间，
 * 两半进了不同的文本节点就各自画成「�」。所以比完把边界退到字素边界上（只会让高亮那一截变宽）：
 * 有 `Intl.Segmenter` 按字素（连 ZWJ emoji、组合附加符一起），没有就至少不劈开代理对。
 */
const graphemes: Intl.Segmenter | null =
  typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null
const splitsPair = (s: string, i: number) =>
  i > 0 && i < s.length && /[\uD800-\uDBFF]/.test(s[i - 1]) && /[\uDC00-\uDFFF]/.test(s[i])
/** s 里 ≤ i 的最近字素边界 */
function floorBoundary(s: string, i: number): number {
  if (i <= 0 || i >= s.length) return i
  if (!graphemes) return splitsPair(s, i) ? i - 1 : i
  let b = 0
  for (const { index } of graphemes.segment(s)) {
    if (index > i) break
    b = index
  }
  return b
}
/** s 里 ≥ i 的最近字素边界 */
function ceilBoundary(s: string, i: number): number {
  if (i <= 0 || i >= s.length) return i
  if (!graphemes) return splitsPair(s, i) ? i + 1 : i
  for (const { index } of graphemes.segment(s)) if (index >= i) return index
  return s.length
}
const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/

/**
 * unified diff → 行。行号：删除行用旧文件行号，新增 / 上下文行用新文件行号（单列，OpenBitFun 同款）。
 * `---` / `+++` 文件头不进行（文件名在卡头说）。hunk 头不画成一行：它换成「⋯ 未改动 N 行」的分隔，
 * N 由前后两段 hunk 的旧行号算出（后端 `difflib.unified_diff(n=3)`，hunk 之间的行不在 diff 里）。
 * 相邻的一段删除 + 一段新增按顺序配对（`pair` = 共同前缀 / 后缀之外那一截的起止），给字级高亮。
 */
export function parseUnifiedDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  let oldNo = 0
  let newNo = 0
  let nextOld = 1 // 上一段 hunk 结束后的下一行旧行号：算间隔用
  let inHunk = false
  for (const line of diff.replace(/\n$/, '').split('\n')) {
    // 文件头只在第一段 hunk 之前：hunk 里「--- x」是一行被删掉的「-- x」，不是文件头
    if (!inHunk && (line.startsWith('+++') || line.startsWith('---'))) continue
    if (line.startsWith('\\')) continue
    const h = HUNK.exec(line)
    if (h) {
      oldNo = Number(h[1])
      newNo = Number(h[3])
      const gap = oldNo - nextOld
      if (gap > 0) rows.push({ kind: 'gap', count: gap })
      const oldLen = h[2] == null ? 1 : Number(h[2])
      nextOld = oldNo + oldLen
      inHunk = true
      continue
    }
    if (line.startsWith('+')) rows.push({ kind: 'add', text: line.slice(1), no: newNo++ })
    else if (line.startsWith('-')) rows.push({ kind: 'del', text: line.slice(1), no: oldNo++ })
    else if (inHunk) {
      rows.push({ kind: 'ctx', text: line.slice(1), no: newNo++ })
      oldNo++
    }
  }
  pairWords(rows)
  return rows
}

/** 一段连续删除后紧跟一段新增：按顺序逐行配对（多出来的行不配），标出共同前缀 / 后缀之外的那一截 */
function pairWords(rows: DiffRow[]) {
  for (let i = 0; i < rows.length; ) {
    if (rows[i].kind !== 'del') {
      i++
      continue
    }
    let d = i
    while (d < rows.length && rows[d].kind === 'del') d++
    let a = d
    while (a < rows.length && rows[a].kind === 'add') a++
    const dels = d - i
    const adds = a - d
    if (dels > 0 && adds > 0) {
      for (let k = 0; k < Math.min(dels, adds); k++) {
        const del = rows[i + k] as LineRow
        const add = rows[d + k] as LineRow
        const x = del.text
        const y = add.text
        let pre = 0
        while (pre < x.length && pre < y.length && x[pre] === y[pre]) pre++
        let suf = 0
        while (suf < x.length - pre && suf < y.length - pre && x[x.length - 1 - suf] === y[y.length - 1 - suf]) suf++
        // 对齐到词边界：「linear → log」标整个词，不标「inear → og」（前后缀两边相同，按 x 判就够）
        const midWord = (c: string | undefined, d: string | undefined) => WORD.test(c ?? '') || WORD.test(d ?? '')
        if (midWord(x[pre], y[pre])) while (pre > 0 && WORD.test(x[pre - 1])) pre--
        if (midWord(x[x.length - 1 - suf], y[y.length - 1 - suf])) while (suf > 0 && WORD.test(x[x.length - suf])) suf--
        pre = Math.min(floorBoundary(x, pre), floorBoundary(y, pre))
        suf = Math.min(x.length - ceilBoundary(x, x.length - suf), y.length - ceilBoundary(y, y.length - suf))
        // 整行都不同（或只差空白的一两处）就不画字级：整行底色已经说清楚了
        if (x.length - pre - suf > 0 && pre + suf > 0) del.pair = [pre, x.length - suf]
        if (y.length - pre - suf > 0 && pre + suf > 0) add.pair = [pre, y.length - suf]
      }
    }
    i = Math.max(a, i + 1)
  }
}
