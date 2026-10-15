/**
 * 草稿 / 提交账本只活在「有助手面板」的宿主里（ADR 0132）：Codex 内嵌画布（`mcp/main.tsx`）
 * 与浏览器 playground（`playground/main.tsx`）没有 /api/ai，也不得有任何助手数据的落盘。
 * 判据的主语：从这两个入口出发、沿**运行时 import 边**可达的生产模块集合——可达就意味着被打进包。
 * 尺子：先证明从桌面入口 `main.tsx` 确实走得到这些模块（否则「不可达」可能只是图没建全）。
 */
import { describe, expect, it } from 'vitest'
import { buildGraph, runtimeEdges } from '../../scripts/import-graph.mjs'

const graph = buildGraph()
const adj = new Map<string, string[]>()
for (const e of runtimeEdges(graph)) {
  const list = adj.get(e.src) ?? []
  list.push(e.dst)
  adj.set(e.src, list)
}
const reach = (entry: string): Set<string> => {
  const seen = new Set<string>([entry])
  const stack = [entry]
  while (stack.length) {
    for (const next of adj.get(stack.pop()!) ?? []) {
      if (!seen.has(next)) {
        seen.add(next)
        stack.push(next)
      }
    }
  }
  return seen
}

const AI_PERSISTENCE = [
  'src/lib/aiDraft.ts',
  'src/lib/aiDraftLedger.ts',
  'src/components/ai/useAiDraft.ts',
  'src/components/ai/AiPanel.tsx',
]

describe('助手草稿 / 提交账本的宿主边界', () => {
  it('对照：桌面入口走得到它们（尺子量得到）', () => {
    const desktop = reach('src/main.tsx')
    for (const f of AI_PERSISTENCE) expect(desktop.has(f), `${f} 应在桌面包里`).toBe(true)
  })

  it.each(['src/mcp/main.tsx', 'src/playground/main.tsx'])('%s 的包里没有助手草稿 / 账本', (entry) => {
    expect(graph.nodes.has(entry), `${entry} 不在图里：入口改名了？`).toBe(true)
    const r = reach(entry)
    expect(r.size).toBeGreaterThan(50) // 入口不是空壳
    for (const f of AI_PERSISTENCE) expect(r.has(f), `${entry} 不该打进 ${f}`).toBe(false)
  })
})
