#!/usr/bin/env node
/**
 * e2e 用例的「跳过修饰」扫描（功能登记表门禁的 AST 那一半，ADR 0097）。
 *
 *     node scripts/e2e-skip-scan.mjs              # 扫 e2e/*.spec.ts，JSON 打到 stdout
 *     node scripts/e2e-skip-scan.mjs --self-test  # 用内置样例反证扫描器自己
 *
 * 主语：**每一条 `test(...)` 声明**（按它在源码里的 file:line:col，与 Playwright
 * `--list` 报的位置同一个坐标），问的是「运行时有没有一条 skip / fixme / fail 修饰
 * 能落到它身上」。`--list` 看得见声明期的 `test.skip(title, fn)`（expectedStatus
 * = skipped），看不见**运行期**的 `test.skip(cond)`——那只有读源码才知道，所以这里读。
 *
 * 一条修饰落到哪些用例上，按它在语法树里的**归属**判（沿祖先往上找到的第一个
 * 声明）：
 *
 *   * 在某条 `test(...)` 的回调里 → 只落在那一条；
 *   * 在 `test.describe(...)` 的回调里（含其中的 beforeEach / beforeAll）→ 落在
 *     这个 describe 里的每一条；
 *   * 在文件顶层（含顶层 hook、以及顶层定义的辅助函数里）→ 落在整个文件。
 *
 * 顶层辅助函数里的 skip 会被判成「整个文件」：宁可多报（红得看得见、改得了），
 * 不猜它被谁调用。**看不见的**：从别的模块 import 进来的辅助函数里的 skip——那一类
 * 由 CI 上的运行报告兜底（`scripts/ci/feature_registry.py verify-run`：合并态那次
 * 真跑里，登记的用例必须是 passed，skipped 判红）。
 *
 * 认的修饰：`test.skip / test.fixme / test.fail`（修饰形态：没有「标题 + 回调」
 * 这对参数）、`test.describe.skip / .fixme`、`test.skip(title, fn)` 这类声明形态、
 * 回调第二个参数（testInfo）上的 `.skip / .fixme / .fail`、`test.info().skip(...)`。
 * 判的是真实的 CallExpression，不是子串——注释、字符串、`// test.skip` 都不算。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const E2E = path.join(WEB, 'e2e')
const MODIFIERS = new Set(['skip', 'fixme', 'fail'])

/** `test` / `test.only` / `test.skip` … 的「声明形态」：第一参是标题，某个参数是函数。 */
function isFunctionLike(n) {
  return n && (ts.isArrowFunction(n) || ts.isFunctionExpression(n))
}
function isTitleLike(n) {
  return n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateExpression(n))
}
function titleText(n) {
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text
  return null // 模板串：静态拼不出，位置才是身份
}

/** callee 的点号链：`test.describe.skip` → ['test', 'describe', 'skip']；认不出返回 null。 */
function chain(expr) {
  const out = []
  let e = expr
  while (ts.isPropertyAccessExpression(e)) {
    out.unshift(e.name.text)
    e = e.expression
  }
  if (ts.isIdentifier(e)) {
    out.unshift(e.text)
    return out
  }
  // test.info().skip(...)
  if (ts.isCallExpression(e)) {
    const inner = chain(e.expression)
    if (inner && inner.join('.') === 'test.info') return ['test.info()', ...out]
  }
  return null
}

/**
 * 分类一个 CallExpression：
 *   { kind: 'test', modifier } —— 用例声明（modifier = skip / fixme / fail / null）
 *   { kind: 'describe', modifier }
 *   { kind: 'modifier', name } —— 运行期修饰（test.skip(cond) 之类）
 *   null —— 与本扫描无关
 */
function classify(call, testInfoNames) {
  const c = chain(call.expression)
  if (!c) return null
  const args = call.arguments
  const declShape = isTitleLike(args[0]) && args.some(isFunctionLike)
  if (c[0] === 'test') {
    if (c.length === 1) return declShape ? { kind: 'test', modifier: null } : null
    if (c[1] === 'describe') {
      const last = c[c.length - 1]
      if (declShape) return { kind: 'describe', modifier: MODIFIERS.has(last) ? last : null }
      return null
    }
    if (c.length === 2 && (MODIFIERS.has(c[1]) || c[1] === 'only')) {
      if (declShape) return { kind: 'test', modifier: MODIFIERS.has(c[1]) ? c[1] : null }
      if (MODIFIERS.has(c[1])) return { kind: 'modifier', name: c[1] }
    }
    return null
  }
  if (c[0] === 'test.info()' && c.length === 2 && MODIFIERS.has(c[1])) {
    return { kind: 'modifier', name: c[1] }
  }
  if (c.length === 2 && testInfoNames.has(c[0]) && MODIFIERS.has(c[1])) {
    return { kind: 'modifier', name: c[1] }
  }
  return null
}

/** 扫一份源码 → 每条用例声明及落在它身上的修饰。 */
export function scanSource(fileName, text) {
  const sf = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const pos = (node) => {
    const { line, character } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
    return { line: line + 1, column: character + 1 }
  }
  /** 声明节点 → 记录；describe 记录里有它包住的用例 */
  const tests = []
  const decls = new Map()
  const modifiers = [] // { owner: node | sf, name, line }
  // 回调第二参（testInfo）的名字：只在用例 / hook 的回调里成立，全文件收集即可
  const testInfoNames = new Set()

  function collectInfoNames(node) {
    if (ts.isCallExpression(node)) {
      for (const a of node.arguments) {
        if (isFunctionLike(a) && a.parameters.length >= 2 && ts.isIdentifier(a.parameters[1].name)) {
          const c = chain(node.expression)
          if (c && c[0] === 'test') testInfoNames.add(a.parameters[1].name.text)
        }
      }
    }
    ts.forEachChild(node, collectInfoNames)
  }
  collectInfoNames(sf)

  function ownerOf(node) {
    for (let p = node.parent; p; p = p.parent) {
      if (decls.has(p)) return p
    }
    return sf
  }

  function visit(node) {
    if (ts.isCallExpression(node)) {
      const k = classify(node, testInfoNames)
      if (k && (k.kind === 'test' || k.kind === 'describe')) {
        const rec = { kind: k.kind, node, modifier: k.modifier, ...pos(node) }
        if (k.kind === 'test') {
          rec.title = titleText(node.arguments[0])
          tests.push(rec)
        }
        decls.set(node, rec)
      } else if (k && k.kind === 'modifier') {
        modifiers.push({ node, name: k.name, ...pos(node) })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)

  const out = []
  for (const t of tests) {
    const hits = []
    if (t.modifier) hits.push({ kind: t.modifier, line: t.line, scope: 'declaration' })
    // 祖先链上的 describe 声明形态修饰
    for (let p = t.node.parent; p; p = p.parent) {
      const d = decls.get(p)
      if (d && d.kind === 'describe' && d.modifier) {
        hits.push({ kind: d.modifier, line: d.line, scope: 'describe' })
      }
    }
    for (const m of modifiers) {
      const owner = ownerOf(m.node)
      const applies =
        owner === sf ||
        owner === t.node ||
        (decls.get(owner)?.kind === 'describe' && isAncestor(owner, t.node))
      if (applies) {
        hits.push({
          kind: m.name,
          line: m.line,
          scope: owner === sf ? 'file' : owner === t.node ? 'test' : 'describe',
        })
      }
    }
    out.push({ line: t.line, column: t.column, title: t.title, modifiers: hits })
  }
  return out
}

function isAncestor(a, b) {
  for (let p = b.parent; p; p = p.parent) if (p === a) return true
  return false
}

export function scanDir(dir = E2E) {
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.spec.ts')).sort()
  const result = []
  for (const f of files) {
    for (const t of scanSource(f, fs.readFileSync(path.join(dir, f), 'utf8'))) {
      result.push({ file: f, ...t })
    }
  }
  return result
}

/**
 * 反证扫描器自己：每个样例都先断言「那条用例被找到了」（落点），再断言修饰判得对。
 * 空扫描（一条用例都没找到）比误报更坏——它会让门禁对着空集合报平安。
 */
function selfTest() {
  const cases = [
    ['plain', `test('a', async ({ page }) => { await page.goto('/') })`, []],
    ['comment-and-string', `test('a', async () => {\n  // test.skip(true)\n  const s = 'test.skip(true)'\n})`, []],
    ['decl-skip', `test.skip('a', async () => {})`, ['skip']],
    ['decl-fixme', `test.fixme('a', async () => {})`, ['fixme']],
    ['body-skip', `test('a', async () => {\n  test.skip(process.platform === 'win32', 'x')\n})`, ['skip']],
    ['body-fixme-noarg', `test('a', async () => { test.fixme() })`, ['fixme']],
    ['body-fail', `test('a', async () => { test.fail(true) })`, ['fail']],
    ['testinfo-skip', `test('a', async ({ page }, info) => { info.skip(true) })`, ['skip']],
    ['test-info-call', `test('a', async () => { test.info().skip() })`, ['skip']],
    ['describe-skip', `test.describe.skip('d', () => {\n  test('a', async () => {})\n})`, ['skip']],
    ['describe-body', `test.describe('d', () => {\n  test.skip(({ browserName }) => browserName === 'webkit')\n  test('a', async () => {})\n})`, ['skip']],
    ['hook-in-describe', `test.describe('d', () => {\n  test.beforeEach(async () => { test.skip(true) })\n  test('a', async () => {})\n})`, ['skip']],
    ['file-top', `test.skip(true)\ntest('a', async () => {})`, ['skip']],
    ['sibling-not-mine', `test('b', async () => { test.skip(true) })\ntest('a', async () => {})`, [], 'a'],
    ['other-describe', `test.describe('x', () => { test.skip(true); test('b', async () => {}) })\ntest('a', async () => {})`, [], 'a'],
    ['tagged', `test('a', { tag: '@feature:x' }, async () => {})`, []],
  ]
  let bad = 0
  for (const [name, src, want, pick = 'a'] of cases) {
    const got = scanSource(`${name}.spec.ts`, src).filter((t) => t.title === pick)
    if (got.length !== 1) {
      console.error(`✗ ${name}: 应当恰好找到一条标题为 ${pick} 的用例，找到 ${got.length} 条`)
      bad++
      continue
    }
    const kinds = got[0].modifiers.map((m) => m.kind).sort()
    if (JSON.stringify(kinds) !== JSON.stringify([...want].sort())) {
      console.error(`✗ ${name}: 修饰应为 ${JSON.stringify(want)}，实际 ${JSON.stringify(kinds)}`)
      bad++
    }
  }
  if (bad) {
    console.error(`e2e-skip-scan 自测：${bad}/${cases.length} 条不对`)
    return 1
  }
  console.log(`e2e-skip-scan 自测：${cases.length}/${cases.length} 条都对`)
  return 0
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--self-test')) process.exit(selfTest())
  const dirArg = process.argv.indexOf('--dir')
  const dir = dirArg >= 0 ? path.resolve(process.argv[dirArg + 1]) : E2E
  const tests = scanDir(dir)
  if (!tests.length) {
    console.error(`e2e-skip-scan：${dir} 里一条用例声明都没找到——扫描器坏了还是目录错了？`)
    process.exit(2)
  }
  process.stdout.write(JSON.stringify({ dir, tests }, null, 1) + '\n')
}
