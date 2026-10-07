/**
 * 通知轨的语气必须由调用方说出口（Codex #821 P2）：`setStatus` 未给 tone 时退回 info（ⓘ），
 * 于是一个忘了写 tone 的「已撤销 / 已复制 / 已保存」会在升级后悄悄从 ✓ 变成 ⓘ。
 * 这里扫一遍源码：每个 `setStatus(…)` 调用都必须带第二个参数（`'done' | 'progress' | 'info' | 'error'`
 * 或算出它的表达式）。`setStatus(null)`（清空）与 AiPanel 历史里那个同名的筛选 setter 不在此列。
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** 本地 useState 的同名 setter，不是通知轨 */
const NOT_STATUS_RAIL = ['/src/components/ai/AiPanel.tsx:setStatus(v === ALL_STATUSES']

function untoned(path: string, src: string): string[] {
  const out: string[] = []
  const re = /\bsetStatus\(/g
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    let depth = 1
    let commas = 0
    let j = m.index + m[0].length
    for (; j < src.length && depth; j++) {
      const c = src[j]
      if ('([{'.includes(c)) depth++
      else if (')]}'.includes(c)) depth--
      else if (c === ',' && depth === 1) commas++
    }
    const body = src.slice(m.index + m[0].length, j - 1).trim()
    if (body === '' || body === 'null') continue
    const trailing = body.endsWith(',') ? 1 : 0
    if (commas - trailing >= 1) continue
    const head = `${path}:setStatus(${body.slice(0, 26)}`
    if (NOT_STATUS_RAIL.some((n) => head.startsWith(n))) continue
    out.push(`${path}:${src.slice(0, m.index).split('\n').length}`)
  }
  return out
}

describe('通知轨语气', () => {
  it('每个 setStatus 调用都显式给出 tone', () => {
    const bad = Object.entries(SOURCES)
      .filter(([p]) => !/\.test\.tsx?$/.test(p) && !p.endsWith('/store/uiStore.ts'))
      .flatMap(([p, src]) => untoned(p, src))
    expect(bad).toEqual([])
  })
})
