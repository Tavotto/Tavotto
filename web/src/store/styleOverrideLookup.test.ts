/**
 * 样式跟随（ADR 0081）碰过的前端代码里，**按 (gid, prop) 取 override 一律走生效的那条**
 * （`lib/effectiveOverride`，last-wins，#587）。`find` / `findIndex` / `filter(...)[0]` 回的是第一条：
 * 老文档里有重复条目、而第一条恰好等于样式值时，「已合样式」「样式写的」「用户写过」三个判断都会
 * 读错（Codex #547 r4109745742）。行为由 `styleBinding.test.ts` / `styleOwnedIssueFix.test.ts` 的
 * 重复条目用例量；这里是结构性的一道。
 *
 * **按 TypeScript AST 判，不按源码正则**（Codex #547 r4109901118）：操作数顺序反过来、回调写成块体、
 * 参数解构、`filter(...)[0]` 这些变形，正则都认不出。判据：
 *
 * - 调用是 `.find(cb)` / `.findIndex(cb)`，或 `.filter(cb)` 之后紧跟 `[0]` / `.at(0)`；
 * - `cb` 是箭头函数或函数表达式，它**自己的第一个参数**上的 `gid` 与 `prop` 都出现在相等比较
 *   （`===` / `==`，左右不限）里——参数可以是标识符（`o.gid`）或解构（`{ gid, prop }`，含改名）。
 *
 * 同时比 gid 与 prop 的，是在取一条 override；只比 gid 的是在 manifest 里找元素（gid 唯一）。嵌在
 * 里面、比的是**另一个参数**的 prop（`e.editable.some((f) => f.prop === p)`）也不算。
 * 豁免只按函数名精确点名：`EXEMPT` 里的函数体内不判（`effectiveOverride` 一族自己的实现）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const FILES = [
  'lib/stylePresets.ts',
  'lib/styleOwned.ts',
  'lib/stylePanelModel.ts',
  'lib/effectiveOverride.ts',
  'store/styleBinding.ts',
  'store/actions.ts',
  'store/issueFixActions.ts',
  'components/left/StylePanel.tsx',
]
const EXEMPT = new Set(['effectiveOverride', 'effectiveOverrideIndex', 'isEffectiveOverrideAt'])

type Key = 'gid' | 'prop'

const isEquality = (k: ts.SyntaxKind) =>
  k === ts.SyntaxKind.EqualsEqualsEqualsToken || k === ts.SyntaxKind.EqualsEqualsToken

const unwrap = (e: ts.Expression): ts.Expression => (ts.isParenthesizedExpression(e) ? unwrap(e.expression) : e)

/** 回调第一个参数上的 `gid` / `prop` 怎么读：`o.gid` 形式，或解构出来的局部名 */
function keyReader(param: ts.ParameterDeclaration): ((e: ts.Expression) => Key | null) | null {
  const name = param.name
  if (ts.isIdentifier(name)) {
    return (e) => {
      const x = unwrap(e)
      if (ts.isPropertyAccessExpression(x) && ts.isIdentifier(x.expression) && x.expression.text === name.text) {
        const k = x.name.text
        return k === 'gid' || k === 'prop' ? k : null
      }
      return null
    }
  }
  if (ts.isObjectBindingPattern(name)) {
    const local = new Map<string, Key>()
    for (const el of name.elements) {
      const key =
        el.propertyName && ts.isIdentifier(el.propertyName)
          ? el.propertyName.text
          : ts.isIdentifier(el.name)
            ? el.name.text
            : null
      if ((key === 'gid' || key === 'prop') && ts.isIdentifier(el.name)) local.set(el.name.text, key)
    }
    if (!local.size) return null
    return (e) => {
      const x = unwrap(e)
      return ts.isIdentifier(x) ? (local.get(x.text) ?? null) : null
    }
  }
  return null
}

/** 这个回调是不是「在按 gid + prop 取一条」 */
function comparesGidAndProp(cb: ts.Node): boolean {
  if (!(ts.isArrowFunction(cb) || ts.isFunctionExpression(cb)) || !cb.parameters.length) return false
  const read = keyReader(cb.parameters[0])
  if (!read) return false
  const seen = new Set<Key>()
  const walk = (n: ts.Node) => {
    if (ts.isBinaryExpression(n) && isEquality(n.operatorToken.kind)) {
      for (const side of [n.left, n.right]) {
        const k = read(side)
        if (k) seen.add(k)
      }
    }
    n.forEachChild(walk)
  }
  walk(cb.body)
  return seen.has('gid') && seen.has('prop')
}

const isZero = (e: ts.Expression) => ts.isNumericLiteral(e) && e.text === '0'

/** `.filter(cb)` 之后紧跟 `[0]` / `.at(0)` */
function takesFirst(call: ts.CallExpression): boolean {
  const p = call.parent
  if (ts.isElementAccessExpression(p) && p.expression === call) return isZero(p.argumentExpression)
  return (
    ts.isPropertyAccessExpression(p) &&
    p.expression === call &&
    p.name.text === 'at' &&
    ts.isCallExpression(p.parent) &&
    p.parent.arguments.length === 1 &&
    isZero(p.parent.arguments[0])
  )
}

/** 源码里违规的调用（返回源码片段） */
function firstMatchLookups(src: string, fileName = 'sample.ts'): string[] {
  const kind = fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sf = ts.createSourceFile(fileName, src, ts.ScriptTarget.Latest, true, kind)
  const out: string[] = []
  const visit = (n: ts.Node) => {
    if (ts.isFunctionDeclaration(n) && n.name && EXEMPT.has(n.name.text)) return
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && n.arguments.length) {
      const method = n.expression.name.text
      const cb = n.arguments[0]
      const hit =
        method === 'find' || method === 'findIndex'
          ? comparesGidAndProp(cb)
          : method === 'filter' && comparesGidAndProp(cb) && takesFirst(n)
      if (hit) out.push(n.getText(sf))
    }
    n.forEachChild(visit)
  }
  visit(sf)
  return out
}

describe('按 (gid, prop) 取 override 走生效的那条（#587 last-wins，TS AST 判）', () => {
  it.each([
    ['顺序反过来', 'xs.find((o) => o.prop === prop && o.gid === gid)'],
    ['常量在左', 'xs.findIndex((o) => gid === o.gid && prop === o.prop)'],
    ['块体', 'xs.find((o) => { const a = o.gid === g; return a && o.prop === p })'],
    ['函数表达式', 'xs.findIndex(function (o) { return o.gid === g && o.prop === p })'],
    ['解构', 'xs.find(({ gid, prop }) => gid === g && prop === p)'],
    ['解构改名', 'xs.find(({ gid: a, prop: b }) => b === p && a === g)'],
    ['不带括号的单参数', 'xs.find(o => o.gid === g && o.prop === p)'],
    ['filter()[0]', 'const x = xs.filter((o) => o.gid === g && o.prop === p)[0]'],
    ['filter().at(0)', 'const x = xs.filter((o) => o.prop === p && o.gid === g).at(0)'],
    ['括号包着的操作数', 'xs.find((o) => (o.gid) === g && (o.prop) === p)'],
  ])('违规：%s', (_name, src) => {
    expect(firstMatchLookups(src)).toHaveLength(1)
  })

  it.each([
    ['走 effectiveOverride', 'const x = effectiveOverride(xs, g, p)'],
    ['找元素（只比 gid）', 'm.elements.find((e) => e.gid === g)'],
    ['嵌套里比的是别的参数的 prop', 'm.elements.find((e) => e.gid === g && e.editable.some((f) => f.prop === p))'],
    ['只判存在（some）', 'xs.some((o) => o.gid === g && o.prop === p)'],
    ['删除用的 filter（不取 [0]）', 'xs = xs.filter((o) => !(o.gid === g && o.prop === p))'],
    ['filter 之后取最后一条', 'const x = xs.filter((o) => o.gid === g && o.prop === p).at(-1)'],
    ['注释里的反例', '// xs.find((o) => o.gid === g && o.prop === p)'],
    [
      '豁免：按函数名点名的实现',
      'export function effectiveOverrideIndex(xs, gid, prop) { return xs.findIndex((o) => o.gid === gid && o.prop === prop) }',
    ],
  ])('放行：%s', (_name, src) => {
    expect(firstMatchLookups(src)).toEqual([])
  })

  it('豁免只认点名的函数：同样的写法换个函数名就是违规', () => {
    expect(
      firstMatchLookups('function lookup(xs, gid, prop) { return xs.findIndex((o) => o.gid === gid && o.prop === prop) }'),
    ).toHaveLength(1)
  })

  it.each(FILES)('%s 里没有按 (gid, prop) 取第一条的写法', (file) => {
    const src = fs.readFileSync(path.join(SRC, file), 'utf8')
    expect(firstMatchLookups(src, file)).toEqual([])
  })
})
