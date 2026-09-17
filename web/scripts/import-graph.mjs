/**
 * `web/src` 的**运行时 import 图**：用 TypeScript 编译器 API 读源码，不执行任何模块。
 *
 * 回答三个问题：谁依赖谁、哪里有环、哪些边跨了层。`src/importArchitecture.test.ts` 拿它做
 * 增量门禁；`node scripts/import-graph.mjs` 打印报告。判据的主语写在每条边上：
 *
 *   kind   static（`import x from`、`export … from`、副作用 import）
 *          dynamic（`import('…')`，字面量才解析得出目标；非字面量进 unknownDynamic）
 *          type（`import type` / 全部 specifier 都是 `type` 的 / `export type … from`——
 *                 编译后不存在，不是运行时边，不参与环）
 *   asset  `?raw` / `.css` / `.json` / `.py` / `.svg` 与 `@profiles` / `@glyphcoverage`
 *          这类不是 TS 模块的目标，只记数不进图
 *
 * 只扫生产代码：`*.test.ts(x)`、`src/test/**`、`*.d.ts` 不进图（测试当然可以随便 import）。
 * 解析规则照 `tsconfig.app.json` 的 paths：`@/x` → `src/x`；相对路径按 .ts / .tsx / /index.ts
 * / /index.tsx 依次试；裸名 = 外部依赖。解析不出的相对路径进 unresolved，不会被当作「没有」。
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(WEB, 'src')
export const BASELINE = path.join(WEB, 'import-architecture-baseline.json')

const ASSET_EXT = new Set(['.css', '.json', '.py', '.svg', '.png', '.webp', '.jpg', '.gif', '.md', '.txt', '.html', '.woff', '.woff2'])
const ASSET_ALIASES = new Set(['@profiles', '@glyphcoverage'])

function isProduction(rel) {
  if (rel.endsWith('.d.ts')) return false
  if (/\.test\.tsx?$/.test(rel)) return false
  if (rel.startsWith('src/test/')) return false
  return /\.tsx?$/.test(rel)
}

function walk(dir, out) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

export function productionFiles() {
  return walk(SRC, [])
    .map((p) => path.relative(WEB, p).split(path.sep).join('/'))
    .filter(isProduction)
    .sort()
}

function resolveSpecifier(fromRel, spec) {
  const clean = spec.replace(/\?.*$/, '')
  const hadQuery = clean !== spec
  if (ASSET_ALIASES.has(clean)) return { kind: 'asset', target: clean }
  let base
  if (clean.startsWith('@/')) base = path.join(SRC, clean.slice(2))
  else if (clean.startsWith('.')) base = path.resolve(WEB, path.dirname(fromRel), clean)
  else return { kind: 'external', target: clean.split('/')[0].startsWith('@') ? clean.split('/').slice(0, 2).join('/') : clean.split('/')[0] }
  const ext = path.extname(base)
  if (ASSET_EXT.has(ext) || hadQuery) return { kind: 'asset', target: path.relative(WEB, base).split(path.sep).join('/') }
  const candidates = [base, base + '.ts', base + '.tsx', path.join(base, 'index.ts'), path.join(base, 'index.tsx')]
  if (ext === '.js') candidates.push(base.slice(0, -3) + '.ts', base.slice(0, -3) + '.tsx')
  for (const c of candidates) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) {
      const rel = path.relative(WEB, c).split(path.sep).join('/')
      return { kind: /\.tsx?$/.test(rel) ? 'module' : 'asset', target: rel }
    }
  }
  return { kind: 'unresolved', target: clean }
}

function importIsTypeOnly(decl) {
  const clause = decl.importClause
  if (!clause) return false // 副作用 import：运行时边
  if (clause.isTypeOnly) return true
  const bindings = clause.namedBindings
  if (bindings && ts.isNamedImports(bindings)) {
    if (clause.name) return false
    return bindings.elements.length > 0 && bindings.elements.every((el) => el.isTypeOnly)
  }
  return false
}

function exportIsTypeOnly(decl) {
  if (decl.isTypeOnly) return true
  const clause = decl.exportClause
  if (clause && ts.isNamedExports(clause)) {
    return clause.elements.length > 0 && clause.elements.every((el) => el.isTypeOnly)
  }
  return false
}

export function buildGraph() {
  const files = productionFiles()
  const nodes = new Set(files)
  const edges = [] // {src, dst, kind, line}
  const externals = new Map()
  const assets = []
  const unresolved = []
  const unknownDynamic = []
  for (const rel of files) {
    const text = fs.readFileSync(path.join(WEB, rel), 'utf8')
    const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    const line = (node) => sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
    const add = (spec, kind, node) => {
      const r = resolveSpecifier(rel, spec)
      if (r.kind === 'module') edges.push({ src: rel, dst: r.target, kind, line: line(node) })
      else if (r.kind === 'external') {
        if (!externals.has(rel)) externals.set(rel, new Set())
        externals.get(rel).add(r.target)
      } else if (r.kind === 'asset') assets.push({ from: rel, target: r.target, line: line(node) })
      else unresolved.push({ from: rel, spec, line: line(node) })
    }
    for (const st of sf.statements) {
      if (ts.isImportDeclaration(st) && ts.isStringLiteral(st.moduleSpecifier)) {
        add(st.moduleSpecifier.text, importIsTypeOnly(st) ? 'type' : 'static', st)
      } else if (ts.isExportDeclaration(st) && st.moduleSpecifier && ts.isStringLiteral(st.moduleSpecifier)) {
        add(st.moduleSpecifier.text, exportIsTypeOnly(st) ? 'type' : 'static', st)
      }
    }
    const visit = (n) => {
      if (ts.isCallExpression(n) && n.expression.kind === ts.SyntaxKind.ImportKeyword) {
        const arg = n.arguments[0]
        if (arg && ts.isStringLiteral(arg)) add(arg.text, 'dynamic', n)
        else unknownDynamic.push({ from: rel, line: line(n), call: n.getText(sf).replace(/\s+/g, ' ') })
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
  }
  return { nodes, edges, externals, assets, unresolved, unknownDynamic }
}

export function runtimeEdges(graph) {
  return graph.edges.filter((e) => e.kind !== 'type')
}

function adjacency(graph) {
  const adj = new Map()
  for (const n of graph.nodes) adj.set(n, new Set())
  for (const e of runtimeEdges(graph)) if (e.src !== e.dst) adj.get(e.src).add(e.dst)
  return adj
}

/** Tarjan（迭代式），只回 ≥2 节点的分量。 */
export function stronglyConnectedComponents(adj) {
  let index = 0
  const indices = new Map()
  const low = new Map()
  const stack = []
  const onStack = new Set()
  const out = []
  for (const root of [...adj.keys()].sort()) {
    if (indices.has(root)) continue
    const work = [[root, [...adj.get(root)].sort()[Symbol.iterator]()]]
    indices.set(root, index); low.set(root, index); index++
    stack.push(root); onStack.add(root)
    while (work.length) {
      const [node, it] = work[work.length - 1]
      let advanced = false
      for (let step = it.next(); !step.done; step = it.next()) {
        const nxt = step.value
        if (!indices.has(nxt)) {
          indices.set(nxt, index); low.set(nxt, index); index++
          stack.push(nxt); onStack.add(nxt)
          work.push([nxt, [...adj.get(nxt)].sort()[Symbol.iterator]()])
          advanced = true
          break
        }
        if (onStack.has(nxt)) low.set(node, Math.min(low.get(node), indices.get(nxt)))
      }
      if (advanced) continue
      work.pop()
      if (work.length) {
        const parent = work[work.length - 1][0]
        low.set(parent, Math.min(low.get(parent), low.get(node)))
      }
      if (low.get(node) === indices.get(node)) {
        const comp = new Set()
        for (;;) {
          const w = stack.pop()
          onStack.delete(w)
          comp.add(w)
          if (w === node) break
        }
        if (comp.size > 1) out.push(comp)
      }
    }
  }
  return out.sort((a, b) => ([...a].sort()[0] < [...b].sort()[0] ? -1 : 1))
}

export function cycles(graph) {
  const adj = adjacency(graph)
  return stronglyConnectedComponents(adj).map((comp) => {
    const inside = runtimeEdges(graph).filter((e) => comp.has(e.src) && comp.has(e.dst) && e.src !== e.dst)
    const internal = [...new Set(inside.map((e) => `${e.src} -> ${e.dst}`))].sort().map((s) => s.split(' -> '))
    return { members: [...comp].sort(), edges: internal }
  })
}

// ---------------------------------------------------------------- 分层

/** 层按目录认；规则：(from 层, to 层) 不许有运行时边。 */
export const LAYERS = {
  entry: (n) => n === 'src/main.tsx' || n === 'src/App.tsx' || n === 'src/playground/main.tsx' || n === 'src/mcp/main.tsx',
  types: (n) => n.startsWith('src/types/'),
  lib: (n) => n.startsWith('src/lib/'),
  store: (n) => n.startsWith('src/store/'),
  hooks: (n) => n.startsWith('src/hooks/'),
  ui: (n) => n.startsWith('src/components/') || n.startsWith('src/canvas/'),
}

/**
 * 门禁规则：(from 层, to 层) 不许有运行时边。每条今天都是零违例——它们是这个代码库
 * **已经在遵守**的边界，门禁只是把它钉住。
 */
export const LAYER_RULES = [
  ['*', 'entry'], // 入口层（main.tsx / App.tsx / playground 与 mcp 的 main）只被入口层 import
  ['lib', 'hooks'], // lib 不许知道 React hook
  ['types', 'store'], // types 是叶子
  ['types', 'ui'],
  ['types', 'hooks'],
  ['store', 'ui'], // 状态层不许 import 组件 / 画布
]

/**
 * 只报告不门禁：`lib/` 在这个代码库里不是纯函数层——issueFocus / openRequest / clipboard /
 * onboarding/* / exportFigures 这一批是**编排**模块，按设计读写 store。把它们当违例去挡是
 * 发明一条没人定过的规则；先把数字摆出来（2026-09-17：60 + 2 条），要不要分出编排层归
 * 任务书的 PR E 拍板。
 */
export const OBSERVED_RULES = [
  ['lib', 'store'],
  ['lib', 'ui'],
]

export function layerOf(node) {
  for (const [name, pred] of Object.entries(LAYERS)) if (pred(node)) return name
  return null
}

export function layerViolations(graph, rules = LAYER_RULES) {
  const out = []
  const seen = new Set()
  for (const e of runtimeEdges(graph)) {
    const a = layerOf(e.src)
    const b = layerOf(e.dst)
    if (!b || a === b) continue
    for (const [from, to] of rules) {
      if (to !== b) continue
      if (from === '*' || from === a) {
        const key = `${e.src} -> ${e.dst}`
        if (!seen.has(key)) {
          seen.add(key)
          out.push({ from: e.src, to: e.dst, rule: `${from} → ${to}`, line: e.line, kind: e.kind })
        }
      }
    }
  }
  return out
}

// ---------------------------------------------------------------- 报告

export function fan(graph, node) {
  const ins = [...new Set(runtimeEdges(graph).filter((e) => e.dst === node && e.src !== node).map((e) => e.src))].sort()
  const outs = [...new Set(runtimeEdges(graph).filter((e) => e.src === node && e.dst !== node).map((e) => e.dst))].sort()
  return { ins, outs }
}

export const DEFAULT_FOCUS = [
  'src/store/actions.ts',
  'src/hooks/useEngineSync.ts',
  'src/store/documentStore.ts',
  'src/store/renderStore.ts',
]

export function report(graph, focus = DEFAULT_FOCUS) {
  const lines = []
  const rt = runtimeEdges(graph)
  const ext = new Set([...graph.externals.values()].flatMap((s) => [...s]))
  lines.push(`模块 ${graph.nodes.size}，运行时边 ${rt.length}（type-only ${graph.edges.length - rt.length}），外部依赖 ${ext.size} 个包，资源引用 ${graph.assets.length}`)
  const cyc = cycles(graph)
  lines.push(`\n== 环（强连通分量）：${cyc.length}`)
  for (const c of cyc) {
    lines.push(`  ${c.members.join(' ↔ ')}`)
    for (const [a, b] of c.edges) {
      const at = rt.filter((e) => e.src === a && e.dst === b).map((e) => `${e.kind}:${e.line}`).join(', ')
      lines.push(`      ${a} → ${b}  (${at})`)
    }
  }
  const viol = layerViolations(graph)
  lines.push(`\n== 跨层反向边（门禁规则）：${viol.length}`)
  for (const v of viol) lines.push(`  ${v.from}:${v.line} → ${v.to}  违反 ${v.rule}`)
  const observed = layerViolations(graph, OBSERVED_RULES)
  const byRule = new Map()
  for (const v of observed) byRule.set(v.rule, (byRule.get(v.rule) ?? 0) + 1)
  lines.push(`\n== 只报告不门禁（lib 里的编排模块）：${observed.length}`)
  for (const [rule, n] of byRule) lines.push(`  ${rule}：${n} 条，涉及 ${new Set(observed.filter((v) => v.rule === rule).map((v) => v.from)).size} 个 lib 模块`)
  lines.push(`\n== 未解析的动态 import：${graph.unknownDynamic.length}`)
  for (const u of graph.unknownDynamic) lines.push(`  ${u.from}:${u.line}  ${u.call}`)
  lines.push(`\n== 解析不了的相对路径：${graph.unresolved.length}`)
  for (const u of graph.unresolved) lines.push(`  ${u.from}:${u.line}  ${u.spec}`)
  for (const node of focus) {
    const { ins, outs } = fan(graph, node)
    lines.push(`\n== ${node}：入 ${ins.length} / 出 ${outs.length}`)
    lines.push(`  入 ← ${ins.join(', ')}`)
    lines.push(`  出 → ${outs.join(', ')}`)
  }
  return lines.join('\n')
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const g = buildGraph()
  if (process.argv.includes('--json')) {
    console.log(JSON.stringify({ nodes: [...g.nodes], edges: g.edges, cycles: cycles(g), layerViolations: layerViolations(g), unknownDynamic: g.unknownDynamic, unresolved: g.unresolved }, null, 1))
  } else {
    console.log(report(g))
  }
}
