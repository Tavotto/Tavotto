/**
 * 红色状态提示门禁的 AST 扫描器（判据与规则见 `../statusErrorActions.test.ts` 头注释）。
 * 单独成模块只为能不经 vitest 直接用 node 跑（`node src/test/statusErrorScan.ts`）看清单。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'

// 单测与手跑都在 web/ 下执行（jsdom 环境里 import.meta.url 不是 file: 方案，所以不用它定位）
const SRC = join(process.cwd(), 'src')
const CALLEE_NAMES = new Set(['setStatus'])


export interface Site {
  file: string
  key: string
  line: number
  hasAction: boolean | 'unknown'
}

const SKIP_DIRS = new Set(['node_modules', 'playground', 'e2e'])

function walk(dir: string, out: string[]): void {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(p, out)
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.|\.d\.ts$/.test(name)) out.push(p)
  }
}

const strOf = (n: ts.Node | undefined): string | null =>
  n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) ? n.text : null

function calleeName(e: ts.Expression): string | null {
  if (ts.isIdentifier(e)) return e.text
  if (ts.isPropertyAccessExpression(e)) return e.name.text
  return null
}

/** 第二实参：'error' | 'not-error' | 'unknown' */
function toneKind(n: ts.Expression | undefined): 'error' | 'not-error' | 'unknown' {
  if (!n) return 'not-error'
  if (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n)) return toneKind(n.expression)
  const s = strOf(n)
  if (s !== null) return s === 'error' ? 'error' : 'not-error'
  if (ts.isConditionalExpression(n)) {
    const a = toneKind(n.whenTrue)
    const b = toneKind(n.whenFalse)
    if (a === 'error' || b === 'error') return 'error'
    return a === 'unknown' || b === 'unknown' ? 'unknown' : 'not-error'
  }
  return 'unknown'
}

/** 标识符 → 同文件里 `const x = <对象字面量>` 的初始化表达式（找不到返回 null）。 */
function resolveLocalConst(sf: ts.SourceFile, name: string): ts.Expression | null {
  let found: ts.Expression | null = null
  const visit = (n: ts.Node): void => {
    if (found) return
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.name.text === name && n.initializer) found = n.initializer
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return found
}

function actionKind(n: ts.Expression | undefined, sf?: ts.SourceFile): boolean | 'unknown' {
  if (!n) return false
  if (ts.isIdentifier(n) && n.text !== 'undefined' && sf) {
    const init = resolveLocalConst(sf, n.text)
    if (init && ts.isObjectLiteralExpression(init)) return actionKind(init)
  }
  if (ts.isObjectLiteralExpression(n)) {
    let spread = false
    for (const p of n.properties) {
      if (ts.isSpreadAssignment(p)) spread = true
      else if (p.name && ((ts.isIdentifier(p.name) && p.name.text === 'action') || strOf(p.name) === 'action')) return true
    }
    return spread ? 'unknown' : false
  }
  if (n.kind === ts.SyntaxKind.UndefinedKeyword || (ts.isIdentifier(n) && n.text === 'undefined')) return false
  return 'unknown'
}

function msgKey(n: ts.Expression | undefined): string {
  if (!n) return '?'
  const direct = strOf(n)
  if (direct !== null) return direct
  if (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n)) return msgKey(n.expression)
  if (ts.isTemplateExpression(n)) return `${n.head.text}\${}`
  if (ts.isConditionalExpression(n)) return `${msgKey(n.whenTrue)}|${msgKey(n.whenFalse)}`
  if (ts.isIdentifier(n)) return `$${n.text}`
  if (ts.isPropertyAccessExpression(n)) {
    const head = msgKey(n.expression)
    return head.startsWith('$') ? `${head}.${n.name.text}` : '?'
  }
  if (ts.isCallExpression(n)) {
    const k = msgKey(n.arguments[0])
    return k.startsWith('$') || k === '?' ? `${calleeName(n.expression) ?? '?'}()` : k
  }
  if (ts.isObjectLiteralExpression(n)) {
    for (const p of n.properties) {
      if (ts.isPropertyAssignment(p) && p.name && ((ts.isIdentifier(p.name) && p.name.text === 'key') || strOf(p.name) === 'key')) {
        return msgKey(p.initializer)
      }
    }
  }
  return '?'
}

interface Wrapper { toneIdx: number; actionIdx: number }

function parseFile(f: string): ts.SourceFile {
  return ts.createSourceFile(f, readFileSync(f, 'utf8'), ts.ScriptTarget.Latest, true, f.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
}

const identsIn = (n: ts.Node, out: Set<string> = new Set()): Set<string> => {
  if (ts.isIdentifier(n)) out.add(n.text)
  ts.forEachChild(n, (c) => identsIn(c, out))
  return out
}

/** 函数形的名字：`const f = (…) => …` / `function f(…)`。 */
function fnNameAndParams(n: ts.Node): { name: string; params: string[] } | null {
  let fn: ts.FunctionLikeDeclaration | null = null
  let name: string | null = null
  if (ts.isFunctionDeclaration(n) && n.name) { fn = n; name = n.name.text }
  else if ((ts.isArrowFunction(n) || ts.isFunctionExpression(n)) && ts.isVariableDeclaration(n.parent) && ts.isIdentifier(n.parent.name)) {
    fn = n; name = n.parent.name.text
  }
  if (!fn || !name) return null
  return { name, params: fn.parameters.map((p) => (ts.isIdentifier(p.name) ? p.name.text : '')) }
}

/**
 * 第一遍：找「包装 setStatus 的助手」——函数里有一处 `setStatus(m, <形参>, …)`，tone 是它自己的形参。
 * 认出的包装助手（如 `sayDrop` / `sayNotebook` / `actions.ts` 的 `status`）之后在第二遍按同样的判据当调用点：
 * 第 toneIdx 个实参是 'error'，且（包装有 action 形参时）第 actionIdx 个实参给没给。
 */
function findWrappers(files: string[]): Map<string, Wrapper> {
  const out = new Map<string, Wrapper>()
  for (const f of files) {
    const sf = parseFile(f)
    const visit = (node: ts.Node, enclosing: { name: string; params: string[] } | null): void => {
      const here = fnNameAndParams(node) ?? enclosing
      if (ts.isCallExpression(node) && enclosing) {
        const name = calleeName(node.expression)
        if (name && CALLEE_NAMES.has(name)) {
          const [, tone, opts] = node.arguments
          if (tone && ts.isIdentifier(tone)) {
            const toneIdx = enclosing.params.indexOf(tone.text)
            if (toneIdx >= 0) {
              const used = opts ? identsIn(opts) : new Set<string>()
              const actionIdx = enclosing.params.findIndex((p, i) => i !== toneIdx && p !== '' && used.has(p))
              out.set(enclosing.name, { toneIdx, actionIdx })
            }
          }
        }
      }
      ts.forEachChild(node, (c) => visit(c, here))
    }
    visit(sf, null)
  }
  // 别名：`const say = sayDrop`
  for (const f of files) {
    const sf = parseFile(f)
    const visit = (n: ts.Node): void => {
      if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer && ts.isIdentifier(n.initializer)) {
        const w = out.get(n.initializer.text)
        if (w) out.set(n.name.text, w)
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
  }
  return out
}

export function scan(): { errors: Site[]; undeterminedTone: Site[]; wrappers: string[] } {
  const files: string[] = []
  walk(SRC, files)
  const wrappers = findWrappers(files)
  const errors: Site[] = []
  const undeterminedTone: Site[] = []
  for (const f of files) {
    const sf = parseFile(f)
    const file = relative(SRC, f).split('\\').join('/')
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node)) {
        const name = calleeName(node.expression)
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
        if (name && CALLEE_NAMES.has(name) && node.arguments.length > 0) {
          const [m, tone, opts] = node.arguments
          const site: Site = { file, key: msgKey(m), line, hasAction: actionKind(opts, sf) }
          const k = toneKind(tone)
          const insideWrapper = tone && ts.isIdentifier(tone) && [...wrappers.values()].length > 0 && isWrapperParam(node, tone.text)
          if (k === 'error') errors.push(site)
          else if (k === 'unknown' && !insideWrapper) undeterminedTone.push(site)
        } else if (name && wrappers.has(name)) {
          const w = wrappers.get(name)!
          const args = node.arguments
          if (toneKind(args[w.toneIdx]) === 'error') {
            const act = w.actionIdx < 0 ? false : actionArg(args[w.actionIdx])
            errors.push({ file, key: `${name}:${msgKey(args[0])}`, line, hasAction: act })
          }
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return { errors, undeterminedTone, wrappers: [...wrappers.keys()].sort() }
}

/** 包装助手的 action 实参：缺省 / undefined = 不带；别的（含 `feedbackAction()` 这种运行期可能返回 undefined 的）= 带。 */
function actionArg(n: ts.Expression | undefined): boolean {
  if (!n) return false
  return !(n.kind === ts.SyntaxKind.UndefinedKeyword || (ts.isIdentifier(n) && n.text === 'undefined'))
}

/** 这次 setStatus 调用是不是包装助手自己的函数体（tone 是它的形参）——那一处不算调用点。 */
function isWrapperParam(node: ts.Node, ident: string): boolean {
  for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
    const f = fnNameAndParams(p)
    if (f) return f.params.includes(ident)
  }
  return false
}

if (process.argv[1] && process.argv[1].endsWith('statusErrorScan.ts')) {
  console.log(JSON.stringify(scan(), null, 1))
}
