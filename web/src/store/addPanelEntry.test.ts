/**
 * 加图是一条分层链（`docs/rules/frontend/canvas-objects-and-workspace.md`）：
 * `addFigureToLayout`（去重 / 聚焦）→ `addPanelToCanvas` / `addRuntimePanelToCanvas`（取景）
 * → `actions.addPanel` / `addRuntimePanel`（只改文档）。最底一层只许 `store/workspace.ts`
 * 调：直接调它的入口会绕过 `frameAddedPanel`——#706 评审里三个对话框正是这样漏掉取景的。
 *
 * 判据走 TypeScript AST，不按子串（#706 评审 P1）：追踪从 `store/actions` 导入的绑定
 * （含改名导入、namespace 导入、转手再导出），只数真正的调用表达式；注释、字符串里的
 * `addPanel(` 不算。
 */
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import { productionFiles } from '../../scripts/import-graph.mjs'

const WEB = path.resolve(__dirname, '../..')
const ACTIONS = 'src/store/actions'
const RAW = new Set(['addPanel', 'addRuntimePanel'])
/** 允许直接调用的地方：定义处与唯一的上一层 */
const ALLOWED = new Set(['src/store/actions.ts', 'src/store/workspace.ts'])

/** 模块说明符是不是 `src/store/actions`（`@/` 别名或相对路径，带不带扩展名） */
function isActions(fromRel: string, spec: string): boolean {
  let target: string
  if (spec.startsWith('@/')) target = path.posix.join('src', spec.slice(2))
  else if (spec.startsWith('.')) target = path.posix.join(path.posix.dirname(fromRel), spec)
  else return false
  return target.replace(/\.tsx?$/, '') === ACTIONS
}

/**
 * 一个文件里对 `addPanel` / `addRuntimePanel` 的直接使用：调用表达式，以及把它们转手
 * 再导出（再导出等于开了一个绕过本判据的后门）。返回 `行:写法` 便于报错时定位。
 */
function rawAddUses(fromRel: string, source: string): string[] {
  const sf = ts.createSourceFile(fromRel, source, ts.ScriptTarget.Latest, true)
  const named = new Set<string>() // 本地名，指向 addPanel / addRuntimePanel
  const namespaces = new Set<string>() // import * as X from actions
  const hits: string[] = []
  const at = (n: ts.Node, what: string) =>
    hits.push(`${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}:${what}`)

  for (const st of sf.statements) {
    if (!ts.isImportDeclaration(st) && !ts.isExportDeclaration(st)) continue
    const spec = st.moduleSpecifier
    if (!spec || !ts.isStringLiteral(spec) || !isActions(fromRel, spec.text)) continue
    if (ts.isExportDeclaration(st)) {
      if (!st.exportClause) at(st, 'export *')
      else if (ts.isNamedExports(st.exportClause)) {
        for (const el of st.exportClause.elements) {
          if (RAW.has((el.propertyName ?? el.name).text)) at(el, `export ${el.getText()}`)
        }
      }
      continue
    }
    const clause = st.importClause
    if (!clause || clause.isTypeOnly || !clause.namedBindings) continue
    if (ts.isNamespaceImport(clause.namedBindings)) {
      namespaces.add(clause.namedBindings.name.text)
    } else {
      for (const el of clause.namedBindings.elements) {
        if (!el.isTypeOnly && RAW.has((el.propertyName ?? el.name).text)) named.add(el.name.text)
      }
    }
  }

  const onNamespace = (e: ts.Expression) => ts.isIdentifier(e) && namespaces.has(e.text)
  const visit = (n: ts.Node) => {
    if (ts.isCallExpression(n)) {
      const callee = n.expression
      if (ts.isIdentifier(callee) && named.has(callee.text)) at(n, callee.text)
      else if (
        ts.isPropertyAccessExpression(callee) &&
        onNamespace(callee.expression) &&
        RAW.has(callee.name.text)
      ) {
        at(n, callee.getText())
      } else if (
        ts.isElementAccessExpression(callee) &&
        onNamespace(callee.expression) &&
        ts.isStringLiteralLike(callee.argumentExpression) &&
        RAW.has(callee.argumentExpression.text)
      ) {
        at(n, callee.getText())
      }
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return hits
}

describe('判据本身（合成源码）', () => {
  const F = 'src/components/Entry.tsx'
  it('改名导入后调用：认得出', () => {
    const src = `import { addPanel as rawAdd } from '@/store/actions'\nrawAdd(info)\n`
    expect(rawAddUses(F, src)).toEqual(['2:rawAdd'])
  })
  it('namespace 导入：点访问与下标访问都认得出', () => {
    const src = `import * as A from '../store/actions'\nA.addRuntimePanel(d)\nA['addPanel'](i)\n`
    expect(rawAddUses(F, src)).toEqual(['2:A.addRuntimePanel', "3:A['addPanel']"])
  })
  it('转手再导出：算一处', () => {
    expect(rawAddUses(F, `export { addPanel as add } from '@/store/actions'\n`)).toHaveLength(1)
  })
  it('注释、字符串、同名但不是从 actions 导入的函数：都不算', () => {
    const src = [
      `import { addPanelToCanvas } from '@/store/workspace'`,
      `// 以前这里调 addPanel(info)`,
      `const s = 'addRuntimePanel(d)'`,
      `function addPanel(x: number) { return x }`,
      `addPanel(1)`,
      `addPanelToCanvas(info)`,
    ].join('\n')
    expect(rawAddUses(F, src)).toEqual([])
  })
})

describe('加图 action 只有 workspace 一个调用方', () => {
  const files = productionFiles() as string[]
  const uses = new Map(
    files.map((rel) => [rel, rawAddUses(rel, fs.readFileSync(path.join(WEB, rel), 'utf8'))] as const),
  )

  it('尺子是活的：workspace 里认得出对 addPanel 与 addRuntimePanel 的真实调用（AST，不是注释）', () => {
    expect(files.length).toBeGreaterThan(100)
    const w = (uses.get('src/store/workspace.ts') ?? []).map((h) => h.split(':')[1])
    expect(w).toContain('addPanel')
    expect(w).toContain('addRuntimePanel')
  })

  it('除了 actions / workspace，没有生产模块直接调或转手导出 addPanel / addRuntimePanel', () => {
    const offenders = [...uses].filter(([rel, h]) => h.length > 0 && !ALLOWED.has(rel))
    expect(Object.fromEntries(offenders)).toEqual({})
  })
})
