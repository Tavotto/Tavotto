/**
 * diagnostics ↔ store 那个有意的 import 环（issue #397）的安全条件：环上每个模块**只在
 * 函数体内**用对端的导出，模块顶层不许。踩破它的失败形状是求值期 `ReferenceError:
 * Cannot access 'X' before initialization`——发生在 React 挂载之前，ErrorBoundary 接不住，
 * 桌面壳空窗、网页白屏；而且三个入口（主应用 / playground / MCP）进环的顺序各不相同，
 * 单测全绿、应用白屏完全可能。
 *
 * 所以这里把环上每个成员**各当一次「第一个被求值的」**：`vi.resetModules()` 之后动态
 * import 它，顶层若用到了对端绑定，import 阶段就抛。成员表从基线读，环变了这里自动跟。
 *
 * 反证（issue #397 的验收）：在 `renderStore.ts` 顶层加一句
 * `const _probe = variantHash({} as never)`，这里必须红；去掉后绿。
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { BASELINE } from '../scripts/import-graph.mjs'

type Baseline = { cycles: { members: string[]; reason?: string }[] }
const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8')) as Baseline
const diagnosticsCycle = baseline.cycles.find((c) => c.members.includes('src/diagnostics/index.ts'))

describe('diagnostics ↔ store 环：任一成员先被求值都不炸', () => {
  it('基线里还有这个环（没有就把这条用例连同基线一起删）', () => {
    expect(diagnosticsCycle).toBeDefined()
    expect(diagnosticsCycle!.members.length).toBeGreaterThanOrEqual(5)
  })

  for (const member of diagnosticsCycle?.members ?? []) {
    it(`${member} 作为第一个被求值的模块`, async () => {
      vi.resetModules()
      const abs = path.resolve(process.cwd(), member)
      await expect(import(/* @vite-ignore */ abs)).resolves.toBeTruthy()
    })
  }
})
