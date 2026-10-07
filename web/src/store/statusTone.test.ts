/**
 * 通知轨的语气必须由调用方说出口（Codex #821 P2）：`setStatus` 未给 tone 时退回 info（ⓘ），
 * 于是一个忘了写 tone 的「已撤销 / 已复制 / 已保存」会在升级后悄悄从 ✓ 变成 ⓘ。
 *
 * 按名字扫会被改名绕过（Codex #821 P1 两轮：`const notify = …setStatus`、再转一手
 * `const notify = setStatus`），所以这里用 TypeScript 的 AST 跟踪绑定：
 *   - 「store 的 setStatus」= `.setStatus` 成员访问、`s => s.setStatus` 选择器调用的结果、
 *     解构出来的 `setStatus`，以及任何以它们为初值 / 被赋值的局部名（传递闭包，直到不动点）；
 *   - 每次调用它都必须带第二个参数（`setStatus(null)` 清空除外）；
 *   - 除了「直接调用」「绑定给一个局部名」「React 依赖数组」，它不许出现在别处
 *     （当实参传走、放进对象、return 出去）——那样调用点就不在本文件的视野里了。
 * 按文件、按名字合并作用域，宁可多报：AiPanel 里 useState 解出来的同名 setter 不是 store 的，
 * 因为它来自数组解构，不进闭包。
 */
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const HOOKS_WITH_DEPS = new Set(['useEffect', 'useLayoutEffect', 'useCallback', 'useMemo', 'useInsertionEffect'])

const unwrap = (e: ts.Expression): ts.Expression =>
  ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isNonNullExpression(e) || ts.isSatisfiesExpression(e)
    ? unwrap(e.expression)
    : e

/** 向上跳过只改类型 / 括号的包裹，返回真正的语法父节点 */
function outer(n: ts.Node): ts.Node {
  let cur = n
  while (
    cur.parent &&
    (ts.isParenthesizedExpression(cur.parent) ||
      ts.isAsExpression(cur.parent) ||
      ts.isNonNullExpression(cur.parent) ||
      ts.isSatisfiesExpression(cur.parent))
  )
    cur = cur.parent
  return cur
}

function statusToneViolations(path: string, src: string): string[] {
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, kind)
  const tracked = new Set<string>()

  const isStoreRef = (raw: ts.Expression): boolean => {
    const e = unwrap(raw)
    if (ts.isPropertyAccessExpression(e)) return e.name.text === 'setStatus'
    if (ts.isElementAccessExpression(e))
      return ts.isStringLiteralLike(e.argumentExpression) && e.argumentExpression.text === 'setStatus'
    if (ts.isIdentifier(e)) return tracked.has(e.text)
    if (ts.isConditionalExpression(e)) return isStoreRef(e.whenTrue) || isStoreRef(e.whenFalse)
    if (ts.isBinaryExpression(e) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(e.operatorToken.kind))
      return isStoreRef(e.left) || isStoreRef(e.right)
    // 选择器：useUiStore((s) => s.setStatus)
    if (ts.isCallExpression(e)) return e.arguments.some((a) => isSelector(a))
    return false
  }
  const isSelector = (a: ts.Expression): boolean => {
    const f = unwrap(a)
    return ts.isArrowFunction(f) && !ts.isBlock(f.body) && isStoreRef(f.body)
  }

  // 1. 绑定的传递闭包
  for (let grew = true; grew; ) {
    grew = false
    const add = (name: string) => {
      if (!tracked.has(name)) {
        tracked.add(name)
        grew = true
      }
    }
    const visit = (n: ts.Node) => {
      if (ts.isVariableDeclaration(n) && n.initializer && ts.isIdentifier(n.name) && isStoreRef(n.initializer)) add(n.name.text)
      if (
        ts.isBinaryExpression(n) &&
        n.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
        ts.isIdentifier(n.left) &&
        isStoreRef(n.right)
      )
        add(n.left.text)
      if (ts.isBindingElement(n) && ts.isObjectBindingPattern(n.parent) && ts.isIdentifier(n.name)) {
        const prop = n.propertyName ?? n.name
        if ((ts.isIdentifier(prop) || ts.isStringLiteral(prop)) && prop.text === 'setStatus') add(n.name.text)
        else if (n.initializer && isStoreRef(n.initializer)) add(n.name.text)
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
  }

  const out: string[] = []
  const at = (n: ts.Node, why: string) =>
    out.push(`${path}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1} ${why}`)

  /** 一个「store 的 setStatus」表达式出现在 n 处：它的去处是否在允许的几种之内 */
  const checkUse = (n: ts.Expression) => {
    const self = outer(n)
    const p = self.parent
    if (ts.isCallExpression(p) && p.expression === self) {
      const args = p.arguments
      const clears = args.length === 1 && args[0].kind === ts.SyntaxKind.NullKeyword
      if (args.length < 2 && !clears) at(p, 'untoned')
      return
    }
    if (ts.isVariableDeclaration(p) && p.initializer === self && ts.isIdentifier(p.name)) return
    if (ts.isBinaryExpression(p) && p.right === self && p.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isIdentifier(p.left))
      return
    // 外层还是 store 引用（选择器、三元、??）：交给外层判
    if (ts.isConditionalExpression(p) || ts.isBinaryExpression(p)) {
      if (isStoreRef(p as ts.Expression)) return
    }
    if (ts.isArrowFunction(p) && p.body === self && ts.isCallExpression(outer(p).parent)) return
    if (
      ts.isArrayLiteralExpression(p) &&
      ts.isCallExpression(outer(p).parent) &&
      HOOKS_WITH_DEPS.has((outer(p).parent as ts.CallExpression).expression.getText(sf))
    )
      return
    at(n, 'escapes')
  }

  const walk = (n: ts.Node) => {
    if (ts.isPropertyAccessExpression(n) && n.name.text === 'setStatus') checkUse(n)
    else if (ts.isIdentifier(n) && tracked.has(n.text)) {
      const p = n.parent
      const isDecl =
        (ts.isVariableDeclaration(p) && p.name === n) ||
        (ts.isBindingElement(p) && (p.name === n || p.propertyName === n)) ||
        (ts.isPropertyAccessExpression(p) && p.name === n) ||
        (ts.isPropertyAssignment(p) && p.name === n) ||
        (ts.isParameter(p) && p.name === n) ||
        (ts.isBinaryExpression(p) && p.left === n && p.operatorToken.kind === ts.SyntaxKind.EqualsToken)
      if (!isDecl) checkUse(n)
    } else if (ts.isCallExpression(n) && n.arguments.some((a) => isSelector(a))) checkUse(n)
    ts.forEachChild(n, walk)
  }
  walk(sf)
  return out
}

const PRODUCTION = () =>
  Object.entries(SOURCES).filter(([p]) => !/\.test\.tsx?$/.test(p) && !p.endsWith('/store/uiStore.ts'))

describe('通知轨语气', () => {
  it('每次调用 store 的 setStatus 都显式给出 tone；它不被当值传出本文件的视野', () => {
    expect(PRODUCTION().flatMap(([p, src]) => statusToneViolations(p, src))).toEqual([])
  }, 60_000) // 逐文件建 AST：冷启动的 CI 上要几秒

  it('自检：各种写法都被看见', () => {
    const v = (src: string) => statusToneViolations('x.ts', src).map((s) => s.split(' ')[1])
    // 直接调用
    expect(v('useUiStore.getState().setStatus("m")')).toEqual(['untoned'])
    expect(v('useUiStore.getState().setStatus("m", "done")')).toEqual([])
    expect(v('useUiStore.getState().setStatus(null)')).toEqual([])
    // 同名 / 改名 / 转手（Codex #821 P1 两轮）
    expect(v('const setStatus = useUiStore((s) => s.setStatus)\nsetStatus("m")')).toEqual(['untoned'])
    expect(v('const notify = useUiStore.getState().setStatus\nnotify("m")')).toEqual(['untoned'])
    expect(v('const setStatus = useUiStore.getState().setStatus\nconst notify = setStatus\nnotify("m")')).toEqual(['untoned'])
    expect(v('const a = useUiStore.getState().setStatus\nconst b = a\nconst c = b\nc("m", "done")')).toEqual([])
    expect(v('let n\nn = useUiStore.getState().setStatus\nn("m")')).toEqual(['untoned'])
    expect(v('const n = (useUiStore.getState().setStatus as F)\nn("m")')).toEqual(['untoned'])
    // 解构
    expect(v('const { setStatus: say } = useUiStore.getState()\nsay("m")')).toEqual(['untoned'])
    expect(v('const { setStatus } = useUiStore.getState()\nsetStatus("m", "info")')).toEqual([])
    // 逃出视野
    expect(v('run(useUiStore.getState().setStatus)')).toEqual(['escapes'])
    expect(v('const s = useUiStore.getState().setStatus\nrun(s)')).toEqual(['escapes'])
    expect(v('const s = useUiStore.getState().setStatus\nconst o = { s }')).toEqual(['escapes'])
    expect(v('const s = useUiStore.getState().setStatus\nfunction f() { return s }')).toEqual(['escapes'])
    // React 依赖数组不算逃出；useState 解出来的同名 setter 不是 store 的
    expect(v('const s = useUiStore((x) => x.setStatus)\nuseCallback(() => s("m", "done"), [s])')).toEqual([])
    expect(v('const [status, setStatus] = useState(1)\nsetStatus(2)')).toEqual([])
    // 注释里提到不算
    expect(v('/** see `uiStore.setStatus` */')).toEqual([])
  })
})
