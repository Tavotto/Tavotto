/**
 * 通知轨的语气必须由调用方说出口（Codex #821 P2）：`setStatus` 未给 tone 时退回 info（ⓘ），
 * 于是一个忘了写 tone 的「已撤销 / 已复制 / 已保存」会在升级后悄悄从 ✓ 变成 ⓘ。
 * 这里扫一遍源码：每个 `setStatus(…)` 调用都必须带第二个参数（`'done' | 'progress' | 'info' | 'error'`
 * 或算出它的表达式）。`setStatus(null)`（清空）与 AiPanel 历史里那个同名的筛选 setter 不在此列。
 *
 * 上面按名字扫，于是名字本身也得钉住（Codex #821 P1）：把 store 的 `setStatus` 取出来另起名
 * （`const notify = useUiStore.getState().setStatus`、`({ setStatus: say }) => …`、当实参传走）
 * 会让后面的调用躲过扫描。所以第二条：`.setStatus` 只能被直接调用，或绑定到同名的 `setStatus`。
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

/** 去掉注释，免得文档里提到 `uiStore.setStatus` 被当成绑定 */
const stripComments = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' ')).replace(/(^|[^:])\/\/.*$/gm, '$1')

function aliased(path: string, src: string): string[] {
  const code = stripComments(src)
  const lines = code.split('\n')
  const out: string[] = []
  lines.forEach((line, i) => {
    const at = `${path}:${i + 1}`
    // 取方法而不调用：只许 `const setStatus = …s.setStatus` 这一种写法
    if (/\.setStatus\b(?!\s*\()/.test(line) && !/^\s*(?:const|let)\s+setStatus\s*=/.test(line)) out.push(at)
    // 解构改名：`{ setStatus: notify }`
    if (/\bsetStatus\s*:\s*[A-Za-z_$]/.test(line)) out.push(at)
  })
  return out
}

const PRODUCTION = () =>
  Object.entries(SOURCES).filter(([p]) => !/\.test\.tsx?$/.test(p) && !p.endsWith('/store/uiStore.ts'))

describe('通知轨语气', () => {
  it('store 的 setStatus 不被改名、不被当值传走（否则上面的按名扫描会漏）', () => {
    expect(PRODUCTION().flatMap(([p, src]) => aliased(p, src))).toEqual([])
  })

  it('改名检测本身会报（自检）', () => {
    expect(aliased('x', 'const notify = useUiStore.getState().setStatus')).toEqual(['x:1'])
    expect(aliased('x', 'const n = useUiStore((s) => s.setStatus)')).toEqual(['x:1'])
    expect(aliased('x', 'const { setStatus: say } = useUiStore.getState()')).toEqual(['x:1'])
    expect(aliased('x', 'run(useUiStore.getState().setStatus)')).toEqual(['x:1'])
    expect(aliased('x', 'const setStatus = useUiStore((s) => s.setStatus)')).toEqual([])
    expect(aliased('x', 'useUiStore.getState().setStatus(m, "done")')).toEqual([])
    expect(aliased('x', '/** see `uiStore.setStatus` */')).toEqual([])
  })

  it('每个 setStatus 调用都显式给出 tone', () => {
    const bad = PRODUCTION().flatMap(([p, src]) => untoned(p, src))
    expect(bad).toEqual([])
  })
})
