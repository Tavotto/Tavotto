/**
 * 内嵌宿主（Codex 的 MCP 画布、/try 的浏览器 playground）不自己弹教程。
 *
 * 核实结论：`NativeTutorial`（连同 `useAutoOpen`）只由 `App.tsx` 挂载；`mcp/main.tsx` 与
 * `playground/main.tsx` 的入口都不经过 `App`，也没有任何路径引入教程层。内嵌宿主没有 Tutorial API
 * （`tutorial.ts` 的 `no_api`），首启欢迎页 / 恢复进行中教程在那里没有落点。
 *
 * 所以守卫是结构性的：这两个入口的**传递导入闭包**里不许出现教程层。将来有人把 NativeTutorial /
 * useAutoOpen 挂进 McpApp、PlaygroundApp 或它们的任何子树，这里就红。
 */
import { readFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const IMPORT_RE = /(?:import|export)\s+(?:[^'"()]*?\s+from\s+)?['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g

function resolveSpec(from: string, spec: string): string | null {
  let base: string
  if (spec.startsWith('@/')) base = resolve(SRC, spec.slice(2))
  else if (spec.startsWith('.')) base = resolve(dirname(from), spec)
  else return null
  for (const cand of [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]) {
    if (/\.(ts|tsx)$/.test(cand) && existsSync(cand)) return cand
  }
  return null
}

/** 入口的传递导入闭包（只看 src 内的 ts/tsx；测试文件不会被生产代码导入） */
function closure(entry: string): Set<string> {
  const seen = new Set<string>()
  const stack = [resolve(SRC, entry)]
  while (stack.length > 0) {
    const file = stack.pop()!
    if (seen.has(file)) continue
    seen.add(file)
    const text = readFileSync(file, 'utf8')
    for (const m of text.matchAll(IMPORT_RE)) {
      const next = resolveSpec(file, m[1] ?? m[2])
      if (next) stack.push(next)
    }
  }
  return seen
}

const rel = (set: Set<string>) => [...set].map((f) => f.slice(SRC.length + 1))
const TUTORIAL_LAYER = ['components/onboarding/NativeTutorial.tsx', 'components/onboarding/autoOpen.ts', 'App.tsx']

describe('内嵌宿主不挂教程层', () => {
  it('探针自检：App 的闭包里确实有教程层（扫描没瞎）', () => {
    const files = rel(closure('main.tsx'))
    expect(files).toContain('components/onboarding/NativeTutorial.tsx')
    expect(files).toContain('components/onboarding/autoOpen.ts')
  })

  it.each([
    ['mcp/main.tsx'],
    ['playground/main.tsx'],
  ])('%s 的导入闭包里没有 NativeTutorial / useAutoOpen / App', (entry) => {
    const files = rel(closure(entry))
    expect(files.length).toBeGreaterThan(20)
    for (const banned of TUTORIAL_LAYER) expect(files, banned).not.toContain(banned)
  })
})
